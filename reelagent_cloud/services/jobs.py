"""任务队列（Postgres `FOR UPDATE SKIP LOCKED`）。

为什么不用 Celery / RQ / Kafka：
  - **事务一致性**：任务入队与业务写入在同一个事务里。用外部 broker 时，
    「业务提交了但入队失败」或「入队了但业务回滚」都需要额外的 outbox 模式来兜底。
  - **少一个组件**：不需要 Redis/RabbitMQ 就能跑，自托管部署成本更低。
  - **SKIP LOCKED 天然并发**：多个 worker 无锁竞争，谁抢到算谁的。
代价：吞吐受数据库限制。本项目「一条成片跑几分钟」的量级完全够用；
若将来要到每秒上千任务，再换成专用 broker 即可（接口已抽象在上述函数里）。

可靠性三件套：
  - **租约（lease）**：worker 崩溃后租约到期，任务可被重新领取，无需额外故障检测。
  - **心跳（heartbeat）**：长任务周期性续约，避免被误判为死亡。
  - **幂等键**：同一请求重复提交只创建一个任务。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.base import utcnow
from ..db.locks import is_sqlite as _is_sqlite
from ..db.locks import loop_lock
from ..db.models import Job, Project
from ..domain.enums import JobKind, JobStage, JobStatus, ProjectStatus
from ..domain.errors import InvalidState, NotFound
from ..domain.ids import PREFIX, new_id
from ..observability import (
    get_logger,
    job_lease_expired_total,
    job_queue_depth,
    job_retries_total,
    jobs_total,
)
from . import credits

log = get_logger(__name__)

#: 出错后可重试的错误码（供应商抖动、网络问题）
RETRYABLE_CODES = {
    "provider_failure",
    "provider_timeout",
    "provider_rate_limited",
    "service_unavailable",
    "lease_expired",
}


# --------------------------------------------------------------------------- #
# 入队
# --------------------------------------------------------------------------- #
async def enqueue(
    session: AsyncSession,
    settings: Settings,
    *,
    org_id: str,
    kind: JobKind,
    project_id: str | None = None,
    created_by: str | None = None,
    payload: dict[str, Any] | None = None,
    priority: int = 0,
    idempotency_key: str | None = None,
    credits_reserved: int = 0,
    max_attempts: int | None = None,
    delay_seconds: int = 0,
) -> Job:
    """创建任务。若 idempotency_key 已存在，直接返回既有任务（不重复扣费）。"""
    if idempotency_key:
        existing = (
            await session.execute(
                select(Job).where(Job.org_id == org_id, Job.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            log.info("job_enqueue_idempotent", job_id=existing.id, key=idempotency_key)
            return existing

    run_after = utcnow() + timedelta(seconds=max(0, delay_seconds))
    job = Job(
        id=new_id(PREFIX["job"]),
        org_id=org_id,
        project_id=project_id,
        created_by=created_by,
        kind=kind,
        status=JobStatus.PENDING,
        stage=JobStage.QUEUED,
        progress=0.0,
        priority=priority,
        payload=payload or {},
        run_after=run_after,
        max_attempts=max_attempts if max_attempts is not None else settings.worker.max_attempts,
        idempotency_key=idempotency_key,
        credits_reserved=max(0, credits_reserved),
    )
    session.add(job)

    if project_id:
        await session.execute(
            update(Project).where(Project.id == project_id).values(status=ProjectStatus.QUEUED)
        )

    # 必须 flush：列默认值（attempts / max_attempts / run_after / created_at）
    # 是 INSERT 时才由 SQLAlchemy 填充的，不 flush 的话调用方拿到的对象上一堆 None。
    await session.flush()

    log.info(
        "job_enqueued",
        job_id=job.id,
        kind=kind.value,
        org_id=org_id,
        project_id=project_id,
        priority=priority,
        credits_reserved=credits_reserved,
    )
    return job


# --------------------------------------------------------------------------- #
# 领取 / 续约
# --------------------------------------------------------------------------- #
async def claim(
    session: AsyncSession,
    settings: Settings,
    *,
    worker_id: str,
    kinds: list[JobKind] | None = None,
) -> Job | None:
    """原子领取一个待执行任务。

    `FOR UPDATE SKIP LOCKED` 让并发 worker 不会互相阻塞：
    拿不到锁的直接跳过这条去抢下一条，而不是排队等待。

    **SQLite 方言不支持 FOR UPDATE**（SQLAlchemy 会静默忽略），
    因此单进程下并发领取可能出现「同一任务被两个协程同时抢到」。
    这里对 SQLite 补一层进程内锁；Postgres 走原生行锁，多实例也安全。
    """
    if _is_sqlite(session):
        async with loop_lock("jobs.claim"):
            return await _claim_locked(session, settings, worker_id=worker_id, kinds=kinds)
    return await _claim_locked(session, settings, worker_id=worker_id, kinds=kinds)


async def _claim_locked(
    session: AsyncSession,
    settings: Settings,
    *,
    worker_id: str,
    kinds: list[JobKind] | None,
) -> Job | None:
    now = utcnow()
    lease = timedelta(seconds=settings.worker.lease_seconds)

    stmt = (
        select(Job)
        .where(
            Job.status.in_([JobStatus.PENDING, JobStatus.FAILED]),
            Job.run_after <= now,
            or_(Job.locked_by.is_(None), Job.lease_expires_at < now),
        )
        .order_by(Job.priority.desc(), Job.run_after.asc())
        .limit(1)
    )
    if kinds:
        stmt = stmt.where(Job.kind.in_(kinds))
    # SQLite 方言会忽略 FOR UPDATE，本地单 worker 场景仍然正确
    stmt = stmt.with_for_update(skip_locked=True)

    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        return None

    job.status = JobStatus.RUNNING
    job.locked_by = worker_id
    job.locked_at = now
    job.lease_expires_at = now + lease
    job.heartbeat_at = now
    job.started_at = job.started_at or now
    job.attempts += 1
    if job.attempts > 1:
        job_retries_total.labels(kind=job.kind.value).inc()

    if job.project_id:
        await session.execute(
            update(Project).where(Project.id == job.project_id).values(status=ProjectStatus.RUNNING)
        )

    await session.flush()
    log.info("job_claimed", job_id=job.id, worker_id=worker_id, attempt=job.attempts, kind=job.kind.value)
    return job


async def heartbeat(session: AsyncSession, settings: Settings, *, job_id: str, worker_id: str) -> bool:
    """续约。返回 False 表示任务已被他人接管或要求取消。"""
    now = utcnow()
    lease = timedelta(seconds=settings.worker.lease_seconds)
    result = await session.execute(
        update(Job)
        .where(Job.id == job_id, Job.locked_by == worker_id, Job.status == JobStatus.RUNNING)
        .values(heartbeat_at=now, lease_expires_at=now + lease)
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        return False
    job = await session.get(Job, job_id)
    return not (job and job.cancel_requested)


async def update_progress(
    session: AsyncSession, *, job_id: str, stage: JobStage, progress: float | None = None
) -> None:
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(stage=stage, progress=progress if progress is not None else stage.progress)
    )


async def reap_expired_leases(session: AsyncSession) -> int:
    """把租约过期（worker 崩溃）的 RUNNING 任务退回到可领取状态。"""
    now = utcnow()
    result = await session.execute(
        update(Job)
        .where(
            Job.status == JobStatus.RUNNING,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at < now,
        )
        .values(
            status=JobStatus.FAILED,
            error="worker 租约过期（进程可能崩溃），任务已被重新排队",
            error_code="lease_expired",
            locked_by=None,
            locked_at=None,
            lease_expires_at=None,
        )
    )
    count = result.rowcount or 0  # type: ignore[attr-defined]
    if count:
        job_lease_expired_total.inc(count)
        log.warning("job_lease_reaped", count=count)
    return count


# --------------------------------------------------------------------------- #
# 结束
# --------------------------------------------------------------------------- #
async def complete(
    session: AsyncSession,
    *,
    job_id: str,
    result: dict[str, Any] | None = None,
    provider_cost_usd: float = 0.0,
) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise NotFound("任务不存在")
    job.status = JobStatus.SUCCEEDED
    job.stage = JobStage.DONE
    job.progress = 1.0
    job.result = result or {}
    job.provider_cost_usd = round(provider_cost_usd, 6)
    job.finished_at = utcnow()
    job.locked_by = None
    job.lease_expires_at = None
    job.error = ""
    job.error_code = ""

    if job.project_id:
        await session.execute(
            update(Project)
            .where(Project.id == job.project_id)
            .values(status=ProjectStatus.READY, provider_cost_usd=round(provider_cost_usd, 6))
        )
    jobs_total.labels(kind=job.kind.value, status="succeeded").inc()
    log.info("job_succeeded", job_id=job.id, cost_usd=round(provider_cost_usd, 4))
    return job


async def fail(
    session: AsyncSession,
    *,
    job_id: str,
    error: str,
    error_code: str = "job_failed",
    retryable: bool | None = None,
    refund: bool = True,
) -> Job:
    """标记失败。可重试且未达上限则重新排队，否则置为 dead。"""
    job = await session.get(Job, job_id)
    if job is None:
        raise NotFound("任务不存在")

    can_retry = retryable if retryable is not None else error_code in RETRYABLE_CODES
    exhausted = job.attempts >= job.max_attempts

    job.error = error[:2000]
    job.error_code = error_code
    job.locked_by = None
    job.lease_expires_at = None
    job.finished_at = utcnow()

    if can_retry and not exhausted:
        backoff = min(30 * (2 ** (job.attempts - 1)), 600)
        job.status = JobStatus.FAILED
        job.run_after = utcnow() + timedelta(seconds=backoff)
        job.stage = JobStage.QUEUED
        log.warning(
            "job_retry_scheduled", job_id=job.id, attempt=job.attempts, backoff_s=backoff, error=error[:200]
        )
    else:
        job.status = JobStatus.DEAD
        job.stage = JobStage.QUEUED
        if job.project_id:
            await session.execute(
                update(Project).where(Project.id == job.project_id).values(status=ProjectStatus.FAILED)
            )
        jobs_total.labels(kind=job.kind.value, status="dead").inc()
        log.error("job_dead", job_id=job.id, error=error[:300], error_code=error_code)

    # 失败退还预扣额度：钱不能白花，这是产品信誉问题
    if refund and job.credits_reserved > 0:
        await credits.refund(
            session,
            org_id=job.org_id,
            amount=job.credits_reserved,
            description=f"任务失败退还（{job.id}）",
            project_id=job.project_id,
            job_id=job.id,
            idempotency_key=f"refund:{job.id}",
        )
        job.credits_reserved = 0

    return job


async def settle(
    session: AsyncSession,
    settings: Settings,
    *,
    job_id: str,
    actual_provider_cost_usd: float,
    description: str = "",
) -> dict[str, int]:
    """按实际成本结算。

    预扣是按预估额度扣的，实际可能更省或更贵：
      - 实际 < 预扣 → 退还差额
      - 实际 > 预扣 → 补扣差额（受余额约束，不足时扣到 0 并记录欠账）
    """
    job = await session.get(Job, job_id)
    if job is None:
        raise NotFound("任务不存在")

    # **幂等保护**：settle 会清零 credits_reserved，若不加这个判断，
    # 重复调用会把「全部实际成本」当成超出预估的补扣，白扣用户一次钱。
    if int(job.credits_reserved) == 0 and int(job.credits_charged) > 0:
        log.info("settle_already_done", job_id=job_id, charged=job.credits_charged)
        return {
            "reserved": 0,
            "actual": int(job.credits_charged),
            "delta": 0,
            "already_settled": True,
        }

    actual_credits = credits.usd_to_credits(settings, actual_provider_cost_usd)
    reserved = int(job.credits_reserved)
    delta = actual_credits - reserved

    result = {"reserved": reserved, "actual": actual_credits, "delta": delta}

    if delta < 0:
        await credits.refund(
            session,
            org_id=job.org_id,
            amount=-delta,
            description=f"按实际用量退还 {(-delta)} 点（{job.id}）",
            project_id=job.project_id,
            job_id=job.id,
            idempotency_key=f"settle-refund:{job.id}",
        )
        result["refunded"] = -delta
    elif delta > 0:
        try:
            await credits.consume(
                session,
                org_id=job.org_id,
                amount=delta,
                description=f"超出预估补扣 {delta} 点（{job.id}）",
                project_id=job.project_id,
                job_id=job.id,
                idempotency_key=f"settle-extra:{job.id}",
            )
            result["extra_charged"] = delta
        except Exception as exc:
            # 余额不够时不让任务失败：已生成的成片照常交付，超出部分记为欠账
            log.warning("settle_extra_failed", job_id=job.id, delta=delta, error=str(exc))
            result["extra_uncollected"] = delta

    job.credits_charged = actual_credits
    job.credits_reserved = 0
    job.provider_cost_usd = round(actual_provider_cost_usd, 6)
    if description:
        job.result = {**job.result, "settlement_note": description}
    return result


async def cancel(session: AsyncSession, *, job_id: str, org_id: str) -> Job:
    """请求取消。已在执行中的任务通过 cancel_requested 标志由 worker 自行退出。"""
    job = (
        await session.execute(select(Job).where(Job.id == job_id, Job.org_id == org_id))
    ).scalar_one_or_none()
    if job is None:
        raise NotFound("任务不存在")
    if job.status.is_terminal:
        raise InvalidState("任务已结束，无法取消")

    job.cancel_requested = True
    if job.status == JobStatus.PENDING:
        job.status = JobStatus.CANCELLED
        job.stage = JobStage.QUEUED
        job.finished_at = utcnow()
        if job.credits_reserved > 0:
            await credits.refund(
                session,
                org_id=job.org_id,
                amount=job.credits_reserved,
                description=f"取消任务退还（{job.id}）",
                project_id=job.project_id,
                job_id=job.id,
                idempotency_key=f"cancel-refund:{job.id}",
            )
            job.credits_reserved = 0
        if job.project_id:
            await session.execute(
                update(Project).where(Project.id == job.project_id).values(status=ProjectStatus.DRAFT)
            )
        jobs_total.labels(kind=job.kind.value, status="cancelled").inc()
    log.info("job_cancel_requested", job_id=job.id, already_running=job.status == JobStatus.RUNNING)
    return job


async def mark_cancelled(session: AsyncSession, *, job_id: str, refund: bool = True) -> None:
    job = await session.get(Job, job_id)
    if job is None:
        return
    job.status = JobStatus.CANCELLED
    job.finished_at = utcnow()
    job.locked_by = None
    job.lease_expires_at = None
    if refund and job.credits_reserved > 0:
        await credits.refund(
            session,
            org_id=job.org_id,
            amount=job.credits_reserved,
            description=f"取消任务退还（{job.id}）",
            project_id=job.project_id,
            job_id=job.id,
            idempotency_key=f"cancel-refund:{job.id}",
        )
        job.credits_reserved = 0
    jobs_total.labels(kind=job.kind.value, status="cancelled").inc()
    log.info("job_cancelled", job_id=job.id)


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
async def get(session: AsyncSession, job_id: str, org_id: str | None = None) -> Job:
    stmt = select(Job).where(Job.id == job_id)
    if org_id:
        stmt = stmt.where(Job.org_id == org_id)
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        raise NotFound("任务不存在")
    return job


async def list_for_project(session: AsyncSession, project_id: str) -> list[Job]:
    rows = await session.execute(
        select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc())
    )
    return list(rows.scalars())


async def list_for_org(
    session: AsyncSession, org_id: str, *, limit: int = 30, active_only: bool = False
) -> list[Job]:
    stmt = select(Job).where(Job.org_id == org_id).order_by(Job.created_at.desc()).limit(limit)
    if active_only:
        stmt = stmt.where(Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING, JobStatus.FAILED]))
    return list((await session.execute(stmt)).scalars())


async def active_count(session: AsyncSession, org_id: str) -> int:
    return int(
        await session.scalar(
            select(func.count(Job.id)).where(
                Job.org_id == org_id,
                Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
            )
        )
        or 0
    )


async def queue_depths(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(select(Job.status, func.count(Job.id)).group_by(Job.status))
    depths = {status.value: 0 for status in JobStatus}
    for status, count in rows.all():
        depths[status.value if hasattr(status, "value") else str(status)] = int(count)
    for key, value in depths.items():
        job_queue_depth.labels(status=key).set(value)
    return depths


async def find_stale(session: AsyncSession, *, older_than_seconds: int = 7200) -> list[Job]:
    """长时间无心跳的 RUNNING 任务，用于运维排障。"""
    cutoff = utcnow() - timedelta(seconds=older_than_seconds)
    rows = await session.execute(
        select(Job).where(
            and_(
                Job.status == JobStatus.RUNNING,
                or_(Job.heartbeat_at.is_(None), Job.heartbeat_at < cutoff),
            )
        )
    )
    return list(rows.scalars())
