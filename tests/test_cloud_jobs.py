"""任务队列测试。

覆盖的是「任务系统一旦出错就会让用户看到卡死或重复扣费」的那些路径：
领取互斥、租约回收、重试退避、失败退还、按实际成本结算。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from reelagent_cloud.config import Settings
from reelagent_cloud.db.base import utcnow
from reelagent_cloud.db.models import Job, Organization, Project
from reelagent_cloud.db.session import session_scope
from reelagent_cloud.domain.enums import JobKind, JobStage, JobStatus, ProjectStatus
from reelagent_cloud.domain.errors import InvalidState, NotFound
from reelagent_cloud.domain.ids import PREFIX, new_id
from reelagent_cloud.services import credits, jobs

pytestmark = pytest.mark.usefixtures("db")


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
async def _make_org(balance: int = 10_000) -> str:
    org_id = new_id(PREFIX["org"])
    async with session_scope() as session:
        session.add(Organization(id=org_id, name="测试组织", slug=f"t-{org_id[-6:]}", credits_balance=0))
        await session.flush()
    async with session_scope() as session:
        await credits.grant(session, org_id=org_id, amount=balance)
    return org_id


async def _make_project(org_id: str) -> str:
    project_id = new_id(PREFIX["project"])
    async with session_scope() as session:
        session.add(
            Project(
                id=project_id,
                org_id=org_id,
                title="测试项目",
                seed_text="深夜的便利店只剩下最后一盏灯。",
                status=ProjectStatus.DRAFT,
            )
        )
        await session.flush()
    return project_id


@pytest.fixture
async def org_and_project() -> tuple[str, str]:
    org_id = await _make_org()
    project_id = await _make_project(org_id)
    return org_id, project_id


# --------------------------------------------------------------------------- #
# 入队
# --------------------------------------------------------------------------- #
class TestEnqueue:
    async def test_enqueue_sets_pending_and_project_queued(self, settings: Settings, org_and_project):
        org_id, project_id = org_and_project
        async with session_scope() as session:
            job = await jobs.enqueue(
                session,
                settings,
                org_id=org_id,
                kind=JobKind.GENERATE_PROJECT,
                project_id=project_id,
                payload={"max_shots": 6},
            )
            assert job.status is JobStatus.PENDING
            assert job.stage is JobStage.QUEUED
            assert job.attempts == 0
        async with session_scope() as session:
            project = await session.get(Project, project_id)
            assert project is not None and project.status is ProjectStatus.QUEUED

    async def test_enqueue_is_idempotent(self, settings: Settings, org_and_project):
        org_id, project_id = org_and_project
        async with session_scope() as session:
            first = await jobs.enqueue(
                session,
                settings,
                org_id=org_id,
                kind=JobKind.GENERATE_PROJECT,
                project_id=project_id,
                idempotency_key="req-abc",
            )
        async with session_scope() as session:
            second = await jobs.enqueue(
                session,
                settings,
                org_id=org_id,
                kind=JobKind.GENERATE_PROJECT,
                project_id=project_id,
                idempotency_key="req-abc",
            )
            assert second.id == first.id
            count = len((await session.execute(select(Job).where(Job.org_id == org_id))).scalars().all())
            assert count == 1

    async def test_same_key_different_org_is_allowed(self, settings: Settings):
        """幂等键的作用域是组织，不能跨租户互相阻塞。"""
        a = await _make_org()
        b = await _make_org()
        async with session_scope() as session:
            await jobs.enqueue(
                session, settings, org_id=a, kind=JobKind.GENERATE_PROJECT, idempotency_key="k"
            )
        async with session_scope() as session:
            second = await jobs.enqueue(
                session, settings, org_id=b, kind=JobKind.GENERATE_PROJECT, idempotency_key="k"
            )
            assert second.org_id == b

    async def test_delay_defers_run_after(self, settings: Settings, org_and_project):
        org_id, _ = org_and_project
        async with session_scope() as session:
            job = await jobs.enqueue(
                session,
                settings,
                org_id=org_id,
                kind=JobKind.GENERATE_PROJECT,
                delay_seconds=600,
            )
            assert job.run_after > utcnow() + timedelta(seconds=500)


# --------------------------------------------------------------------------- #
# 领取
# --------------------------------------------------------------------------- #
class TestClaim:
    async def test_claim_marks_running_with_lease(self, settings: Settings, org_and_project):
        org_id, project_id = org_and_project
        async with session_scope() as session:
            job = await jobs.enqueue(
                session,
                settings,
                org_id=org_id,
                kind=JobKind.GENERATE_PROJECT,
                project_id=project_id,
            )
            job_id = job.id
        async with session_scope() as session:
            claimed = await jobs.claim(session, settings, worker_id="w1")
            assert claimed is not None and claimed.id == job_id
            assert claimed.status is JobStatus.RUNNING
            assert claimed.locked_by == "w1"
            assert claimed.lease_expires_at is not None
            assert claimed.attempts == 1
            assert claimed.started_at is not None

    async def test_claim_is_exclusive(self, settings: Settings, org_and_project):
        """同一任务不能被两个 worker 同时领取——否则会重复扣费、重复生成。"""
        org_id, _ = org_and_project
        async with session_scope() as session:
            await jobs.enqueue(session, settings, org_id=org_id, kind=JobKind.GENERATE_PROJECT)

        async def try_claim(worker: str) -> str | None:
            async with session_scope() as session:
                job = await jobs.claim(session, settings, worker_id=worker)
                return job.id if job else None

        results = await asyncio.gather(*[try_claim(f"w{i}") for i in range(5)])
        winners = [r for r in results if r]
        assert len(winners) == 1, f"应当只有一个 worker 抢到，实际 {winners}"

    async def test_claim_respects_priority(self, settings: Settings, org_and_project):
        org_id, _ = org_and_project
        async with session_scope() as session:
            await jobs.enqueue(
                session,
                settings,
                org_id=org_id,
                kind=JobKind.GENERATE_PROJECT,
                priority=0,
                idempotency_key="low",
            )
            await jobs.enqueue(
                session,
                settings,
                org_id=org_id,
                kind=JobKind.GENERATE_PROJECT,
                priority=10,
                idempotency_key="high",
            )
        async with session_scope() as session:
            claimed = await jobs.claim(session, settings, worker_id="w1")
            assert claimed is not None and claimed.idempotency_key == "high"

    async def test_claim_skips_future_jobs(self, settings: Settings, org_and_project):
        org_id, _ = org_and_project
        async with session_scope() as session:
            await jobs.enqueue(
                session,
                settings,
                org_id=org_id,
                kind=JobKind.GENERATE_PROJECT,
                delay_seconds=3600,
            )
        async with session_scope() as session:
            assert await jobs.claim(session, settings, worker_id="w1") is None

    async def test_claim_filters_by_kind(self, settings: Settings, org_and_project):
        org_id, _ = org_and_project
        async with session_scope() as session:
            await jobs.enqueue(session, settings, org_id=org_id, kind=JobKind.GENERATE_PROJECT)
        async with session_scope() as session:
            assert (
                await jobs.claim(session, settings, worker_id="w1", kinds=[JobKind.REGENERATE_SHOT]) is None
            )

    async def test_claim_returns_none_when_empty(self, settings: Settings):
        async with session_scope() as session:
            assert await jobs.claim(session, settings, worker_id="w1") is None


# --------------------------------------------------------------------------- #
# 心跳与租约
# --------------------------------------------------------------------------- #
class TestLeaseAndHeartbeat:
    async def test_heartbeat_renews_lease(self, settings: Settings, org_and_project):
        org_id, _ = org_and_project
        async with session_scope() as session:
            job = await jobs.enqueue(session, settings, org_id=org_id, kind=JobKind.GENERATE_PROJECT)
            job_id = job.id
        async with session_scope() as session:
            claimed = await jobs.claim(session, settings, worker_id="w1")
            assert claimed is not None
            original = claimed.lease_expires_at
        await asyncio.sleep(0.05)
        async with session_scope() as session:
            alive = await jobs.heartbeat(session, settings, job_id=job_id, worker_id="w1")
            assert alive is True
        async with session_scope() as session:
            refreshed = await session.get(Job, job_id)
            assert refreshed is not None
            assert refreshed.lease_expires_at >= original

    async def test_heartbeat_reports_cancel_request(self, settings: Settings, org_and_project):
        org_id, _ = org_and_project
        async with session_scope() as session:
            job = await jobs.enqueue(session, settings, org_id=org_id, kind=JobKind.GENERATE_PROJECT)
            job_id = job.id
        async with session_scope() as session:
            await jobs.claim(session, settings, worker_id="w1")
        async with session_scope() as session:
            await jobs.cancel(session, job_id=job_id, org_id=org_id)
        async with session_scope() as session:
            alive = await jobs.heartbeat(session, settings, job_id=job_id, worker_id="w1")
            assert alive is False, "取消请求必须通过心跳回传给 worker"

    async def test_heartbeat_rejects_wrong_worker(self, settings: Settings, org_and_project):
        org_id, _ = org_and_project
        async with session_scope() as session:
            job = await jobs.enqueue(session, settings, org_id=org_id, kind=JobKind.GENERATE_PROJECT)
            job_id = job.id
        async with session_scope() as session:
            await jobs.claim(session, settings, worker_id="w1")
        async with session_scope() as session:
            assert await jobs.heartbeat(session, settings, job_id=job_id, worker_id="intruder") is False

    async def test_expired_lease_is_reaped(self, settings: Settings, org_and_project):
        """worker 崩溃后任务必须能被重新领取，否则用户永远等不到结果。"""
        org_id, _ = org_and_project
        async with session_scope() as session:
            job = await jobs.enqueue(session, settings, org_id=org_id, kind=JobKind.GENERATE_PROJECT)
            job_id = job.id
        async with session_scope() as session:
            await jobs.claim(session, settings, worker_id="doomed")
        async with session_scope() as session:
            stale = await session.get(Job, job_id)
            assert stale is not None
            stale.lease_expires_at = utcnow() - timedelta(seconds=1)
            await session.flush()

        async with session_scope() as session:
            reaped = await jobs.reap_expired_leases(session)
            assert reaped == 1
        async with session_scope() as session:
            recovered = await session.get(Job, job_id)
            assert recovered is not None
            assert recovered.status is JobStatus.FAILED
            assert recovered.error_code == "lease_expired"
            assert recovered.locked_by is None
        async with session_scope() as session:
            reclaimed = await jobs.claim(session, settings, worker_id="fresh")
            assert reclaimed is not None and reclaimed.id == job_id
            assert reclaimed.attempts == 2

    async def test_reap_ignores_healthy_lease(self, settings: Settings, org_and_project):
        org_id, _ = org_and_project
        async with session_scope() as session:
            await jobs.enqueue(session, settings, org_id=org_id, kind=JobKind.GENERATE_PROJECT)
        async with session_scope() as session:
            await jobs.claim(session, settings, worker_id="w1")
        async with session_scope() as session:
            assert await jobs.reap_expired_leases(session) == 0


# --------------------------------------------------------------------------- #
# 成功与失败
# --------------------------------------------------------------------------- #
class TestCompletion:
    async def test_complete_marks_project_ready(self, settings: Settings, org_and_project):
        org_id, project_id = org_and_project
        async with session_scope() as session:
            job = await jobs.enqueue(
                session,
                settings,
                org_id=org_id,
                kind=JobKind.GENERATE_PROJECT,
                project_id=project_id,
            )
            job_id = job.id
        async with session_scope() as session:
            await jobs.claim(session, settings, worker_id="w1")
        async with session_scope() as session:
            done = await jobs.complete(
                session, job_id=job_id, result={"artifacts": ["a"]}, provider_cost_usd=0.42
            )
            assert done.status is JobStatus.SUCCEEDED
            assert done.progress == 1.0
            assert done.provider_cost_usd == 0.42
            assert done.locked_by is None
        async with session_scope() as session:
            project = await session.get(Project, project_id)
            assert project is not None and project.status is ProjectStatus.READY

    async def test_failure_schedules_retry_with_backoff(self, settings: Settings, org_and_project):
        org_id, _ = org_and_project
        async with session_scope() as session:
            job = await jobs.enqueue(
                session, settings, org_id=org_id, kind=JobKind.GENERATE_PROJECT, max_attempts=3
            )
            job_id = job.id
        async with session_scope() as session:
            claimed = await jobs.claim(session, settings, worker_id="w1")
            assert claimed is not None
        async with session_scope() as session:
            failed = await jobs.fail(
                session, job_id=job_id, error="供应商 503", error_code="provider_failure"
            )
            assert failed.status is JobStatus.FAILED, "可重试错误应保持 FAILED 以便重新领取"
            assert failed.run_after > utcnow()
            assert failed.locked_by is None

    async def test_non_retryable_error_goes_dead(self, settings: Settings, org_and_project):
        org_id, project_id = org_and_project
        async with session_scope() as session:
            job = await jobs.enqueue(
                session,
                settings,
                org_id=org_id,
                kind=JobKind.GENERATE_PROJECT,
                project_id=project_id,
            )
            job_id = job.id
        async with session_scope() as session:
            await jobs.claim(session, settings, worker_id="w1")
        async with session_scope() as session:
            dead = await jobs.fail(
                session,
                job_id=job_id,
                error="参数非法",
                error_code="validation_failed",
                retryable=False,
            )
            assert dead.status is JobStatus.DEAD
        async with session_scope() as session:
            project = await session.get(Project, project_id)
            assert project is not None and project.status is ProjectStatus.FAILED

    async def test_retry_backoff_defers_reclaim(self, settings: Settings, org_and_project):
        """失败后不能立刻被重新领取——否则会形成忙等风暴，把供应商的错误放大。"""
        org_id, _ = org_and_project
        async with session_scope() as session:
            job = await jobs.enqueue(
                session, settings, org_id=org_id, kind=JobKind.GENERATE_PROJECT, max_attempts=3
            )
            job_id = job.id
        async with session_scope() as session:
            await jobs.claim(session, settings, worker_id="w1")
        async with session_scope() as session:
            await jobs.fail(session, job_id=job_id, error="boom", error_code="provider_failure")

        async with session_scope() as session:
            assert await jobs.claim(session, settings, worker_id="w1") is None, "退避期内不应被领取"

        # 把 run_after 拨到过去，模拟退避到期
        async with session_scope() as session:
            row = await session.get(Job, job_id)
            assert row is not None
            row.run_after = utcnow() - timedelta(seconds=1)
            await session.flush()

        async with session_scope() as session:
            retried = await jobs.claim(session, settings, worker_id="w1")
            assert retried is not None and retried.id == job_id
            assert retried.attempts == 2

    async def test_exhausted_attempts_go_dead(self, settings: Settings, org_and_project):
        """超过重试上限必须置为 dead，避免无限重试持续烧供应商的钱。"""
        org_id, _ = org_and_project
        async with session_scope() as session:
            job = await jobs.enqueue(
                session, settings, org_id=org_id, kind=JobKind.GENERATE_PROJECT, max_attempts=1
            )
            job_id = job.id
        async with session_scope() as session:
            await jobs.claim(session, settings, worker_id="w1")
        async with session_scope() as session:
            final = await jobs.fail(session, job_id=job_id, error="boom", error_code="provider_failure")
            assert final.status is JobStatus.DEAD
            assert final.attempts == 1
        async with session_scope() as session:
            assert await jobs.claim(session, settings, worker_id="w1") is None, "dead 任务不再可领取"


# --------------------------------------------------------------------------- #
# 退款与结算
# --------------------------------------------------------------------------- #
class TestSettlement:
    async def _prepare(self, settings: Settings, reserved: int) -> tuple[str, str]:
        org_id = await _make_org(balance=1000)
        project_id = await _make_project(org_id)
        async with session_scope() as session:
            job = await jobs.enqueue(
                session,
                settings,
                org_id=org_id,
                kind=JobKind.GENERATE_PROJECT,
                project_id=project_id,
                credits_reserved=reserved,
            )
            job_id = job.id
        async with session_scope() as session:
            await credits.consume(
                session,
                org_id=org_id,
                amount=reserved,
                idempotency_key=f"reserve:{job_id}",
            )
        return org_id, job_id

    async def test_failure_refunds_reserved(self, settings: Settings):
        org_id, job_id = await self._prepare(settings, reserved=300)
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == 700
        async with session_scope() as session:
            await jobs.claim(session, settings, worker_id="w1")
        async with session_scope() as session:
            await jobs.fail(session, job_id=job_id, error="挂了", error_code="provider_failure")
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == 1000, "失败必须全额退还"
            assert await credits.reconcile(session, org_id) is not None

    async def test_settle_refunds_difference_when_cheaper(self, settings: Settings):
        """按实际成本结算：实际比预估便宜就退还差额。"""
        org_id, job_id = await self._prepare(settings, reserved=500)
        async with session_scope() as session:
            await jobs.claim(session, settings, worker_id="w1")
        # 实际成本 0.5 美元 -> 0.5 * 3 * 100 = 150 点
        async with session_scope() as session:
            result = await jobs.settle(session, settings, job_id=job_id, actual_provider_cost_usd=0.5)
            assert result["reserved"] == 500
            assert result["actual"] == 150
            assert result["refunded"] == 350
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == 1000 - 150
            report = await credits.reconcile(session, org_id)
            assert report["consistent"] is True

    async def test_settle_charges_extra_when_pricier(self, settings: Settings):
        org_id, job_id = await self._prepare(settings, reserved=100)
        async with session_scope() as session:
            await jobs.claim(session, settings, worker_id="w1")
        # 实际 1.0 美元 -> 300 点，比预扣的 100 多
        async with session_scope() as session:
            result = await jobs.settle(session, settings, job_id=job_id, actual_provider_cost_usd=1.0)
            assert result["extra_charged"] == 200
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == 700

    async def test_settle_does_not_break_when_balance_insufficient(self, settings: Settings):
        """超出预估但余额不够时，成片照常交付，超出部分记为欠账——不能因此让用户拿不到片子。"""
        org_id = await _make_org(balance=100)
        project_id = await _make_project(org_id)
        async with session_scope() as session:
            job = await jobs.enqueue(
                session,
                settings,
                org_id=org_id,
                kind=JobKind.GENERATE_PROJECT,
                project_id=project_id,
                credits_reserved=100,
            )
            job_id = job.id
        async with session_scope() as session:
            await credits.consume(session, org_id=org_id, amount=100, idempotency_key=f"r:{job_id}")
        async with session_scope() as session:
            await jobs.claim(session, settings, worker_id="w1")
        async with session_scope() as session:
            result = await jobs.settle(session, settings, job_id=job_id, actual_provider_cost_usd=5.0)
            assert result.get("extra_uncollected", 0) > 0
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == 0

    async def test_settle_is_idempotent(self, settings: Settings):
        org_id, job_id = await self._prepare(settings, reserved=500)
        async with session_scope() as session:
            await jobs.claim(session, settings, worker_id="w1")
        for _ in range(3):
            async with session_scope() as session:
                await jobs.settle(session, settings, job_id=job_id, actual_provider_cost_usd=0.5)
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == 850, "重复结算不能重复退款"


# --------------------------------------------------------------------------- #
# 取消
# --------------------------------------------------------------------------- #
class TestCancel:
    async def test_cancel_pending_refunds(self, settings: Settings, org_and_project):
        org_id, project_id = org_and_project
        async with session_scope() as session:
            job = await jobs.enqueue(
                session,
                settings,
                org_id=org_id,
                kind=JobKind.GENERATE_PROJECT,
                project_id=project_id,
                credits_reserved=200,
            )
            job_id = job.id
        async with session_scope() as session:
            await credits.consume(session, org_id=org_id, amount=200, idempotency_key=f"r:{job_id}")
        before = None
        async with session_scope() as session:
            before = await credits.get_balance(session, org_id)
        async with session_scope() as session:
            cancelled = await jobs.cancel(session, job_id=job_id, org_id=org_id)
            assert cancelled.status is JobStatus.CANCELLED
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == before + 200
            project = await session.get(Project, project_id)
            assert project is not None and project.status is ProjectStatus.DRAFT

    async def test_cancel_running_only_flags(self, settings: Settings, org_and_project):
        org_id, _ = org_and_project
        async with session_scope() as session:
            job = await jobs.enqueue(session, settings, org_id=org_id, kind=JobKind.GENERATE_PROJECT)
            job_id = job.id
        async with session_scope() as session:
            await jobs.claim(session, settings, worker_id="w1")
        async with session_scope() as session:
            flagged = await jobs.cancel(session, job_id=job_id, org_id=org_id)
            assert flagged.status is JobStatus.RUNNING
            assert flagged.cancel_requested is True
        async with session_scope() as session:
            await jobs.mark_cancelled(session, job_id=job_id)
            final = await session.get(Job, job_id)
            assert final is not None and final.status is JobStatus.CANCELLED

    async def test_cancel_finished_job_rejected(self, settings: Settings, org_and_project):
        org_id, _ = org_and_project
        async with session_scope() as session:
            job = await jobs.enqueue(session, settings, org_id=org_id, kind=JobKind.GENERATE_PROJECT)
            job_id = job.id
        async with session_scope() as session:
            await jobs.claim(session, settings, worker_id="w1")
        async with session_scope() as session:
            await jobs.complete(session, job_id=job_id)
        async with session_scope() as session:
            with pytest.raises(InvalidState):
                await jobs.cancel(session, job_id=job_id, org_id=org_id)

    async def test_cancel_is_tenant_scoped(self, settings: Settings, org_and_project):
        """不能取消别的组织的任务。"""
        org_id, _ = org_and_project
        other = await _make_org()
        async with session_scope() as session:
            job = await jobs.enqueue(session, settings, org_id=org_id, kind=JobKind.GENERATE_PROJECT)
            job_id = job.id
        async with session_scope() as session:
            with pytest.raises(NotFound):
                await jobs.cancel(session, job_id=job_id, org_id=other)


# --------------------------------------------------------------------------- #
# 查询与运维
# --------------------------------------------------------------------------- #
class TestQueries:
    async def test_tenant_scoped_get(self, settings: Settings, org_and_project):
        org_id, _ = org_and_project
        other = await _make_org()
        async with session_scope() as session:
            job = await jobs.enqueue(session, settings, org_id=org_id, kind=JobKind.GENERATE_PROJECT)
            job_id = job.id
        async with session_scope() as session:
            assert (await jobs.get(session, job_id, org_id=org_id)).id == job_id
            with pytest.raises(NotFound):
                await jobs.get(session, job_id, org_id=other)

    async def test_active_count(self, settings: Settings, org_and_project):
        org_id, _ = org_and_project
        async with session_scope() as session:
            for i in range(3):
                await jobs.enqueue(
                    session,
                    settings,
                    org_id=org_id,
                    kind=JobKind.GENERATE_PROJECT,
                    idempotency_key=f"k{i}",
                )
        async with session_scope() as session:
            assert await jobs.active_count(session, org_id) == 3
        async with session_scope() as session:
            claimed = await jobs.claim(session, settings, worker_id="w1")
            assert claimed is not None
        async with session_scope() as session:
            assert await jobs.active_count(session, org_id) == 3, "running 也算活跃"

    async def test_queue_depths(self, settings: Settings, org_and_project):
        org_id, _ = org_and_project
        async with session_scope() as session:
            await jobs.enqueue(session, settings, org_id=org_id, kind=JobKind.GENERATE_PROJECT)
        async with session_scope() as session:
            depths = await jobs.queue_depths(session)
        assert depths["pending"] == 1
        assert depths["running"] == 0

    async def test_progress_update(self, settings: Settings, org_and_project):
        org_id, _ = org_and_project
        async with session_scope() as session:
            job = await jobs.enqueue(session, settings, org_id=org_id, kind=JobKind.GENERATE_PROJECT)
            job_id = job.id
        async with session_scope() as session:
            await jobs.update_progress(session, job_id=job_id, stage=JobStage.GENERATING)
        async with session_scope() as session:
            row = await session.get(Job, job_id)
            assert row is not None
            assert row.stage is JobStage.GENERATING
            assert 0 < row.progress < 1
