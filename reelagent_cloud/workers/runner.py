"""Worker 主循环。

职责：
  - 领取任务、并发执行、心跳续约、优雅停机
  - 定期回收过期租约（崩溃 worker 留下的僵尸任务）
  - 投递邮件发件箱

**优雅停机**是这里最容易被忽略但生产上必须有的能力：
收到 SIGTERM 后不能立刻退出，否则正在生成的成片会半途而废、
任务要等租约过期（15 分钟）才能重试，用户体验很差。
正确做法是停止领取新任务，把在跑的做完，超过宽限期才强退。
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from ..config import Settings, get_settings
from ..db.session import dispose_engine, session_scope
from ..domain.enums import JobKind
from ..observability import (
    configure_logging,
    get_logger,
    job_duration,
    jobs_total,
    worker_active,
    worker_jobs_running,
)
from ..security.tokens import new_worker_id
from ..services import email as email_svc
from ..services import jobs as jobs_svc
from ..services import pipeline as pipeline_svc

log = get_logger(__name__)

JobHandler = Callable[..., Awaitable[Any]]

#: 租约回收的检查间隔
REAP_INTERVAL_SECONDS = 120
#: 邮件投递的轮询间隔
EMAIL_INTERVAL_SECONDS = 30


class Worker:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        worker_id: str | None = None,
        concurrency: int | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.worker_id = worker_id or new_worker_id()
        self.concurrency = concurrency or self.settings.worker.concurrency
        self._stop = asyncio.Event()
        self._running: dict[str, asyncio.Task[Any]] = {}
        #: 每个任务一个取消信号，供 pipeline 的 should_cancel 读取
        self._cancel_flags: dict[str, asyncio.Event] = {}
        self._last_reap = 0.0
        self._last_email = 0.0

    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        log.info(
            "worker_starting",
            worker_id=self.worker_id,
            concurrency=self.concurrency,
            env=self.settings.env.value,
        )
        worker_active.inc()
        try:
            await self._loop()
        finally:
            worker_active.dec()
            await self._drain()
            await dispose_engine()
            log.info("worker_stopped", worker_id=self.worker_id)

    def request_stop(self) -> None:
        log.info("worker_stop_requested", worker_id=self.worker_id)
        self._stop.set()

    # ------------------------------------------------------------------ #
    async def _loop(self) -> None:
        poll = self.settings.worker.poll_interval_seconds
        while not self._stop.is_set():
            try:
                await self._housekeeping()

                capacity = self.concurrency - len(self._running)
                if capacity <= 0:
                    await self._sleep(poll)
                    continue

                claimed = await self._claim_batch(capacity)
                if not claimed:
                    await self._sleep(poll)
                    continue

                for job_id, kind in claimed:
                    task = asyncio.create_task(self._run_job(job_id, kind), name=f"job:{job_id}")
                    self._running[job_id] = task
                    self._cancel_flags[job_id] = asyncio.Event()
                    task.add_done_callback(partial(self._on_done, job_id))

            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 主循环绝不因单次异常退出
                log.exception("worker_loop_error", error=str(exc))
                await self._sleep(min(poll * 3, 30))

    async def _sleep(self, seconds: float) -> None:
        """可被停机信号打断的休眠。"""
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    async def _claim_batch(self, capacity: int) -> list[tuple[str, JobKind]]:
        claimed: list[tuple[str, JobKind]] = []
        for _ in range(capacity):
            async with session_scope() as session:
                job = await jobs_svc.claim(session, self.settings, worker_id=self.worker_id)
                if job is None:
                    break
                claimed.append((job.id, job.kind))
        return claimed

    def _on_done(self, job_id: str, task: asyncio.Task[Any]) -> None:
        self._running.pop(job_id, None)
        self._cancel_flags.pop(job_id, None)
        worker_jobs_running.labels(worker_id=self.worker_id).set(len(self._running))
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("job_task_crashed", job_id=job_id, error=f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ #
    async def _run_job(self, job_id: str, kind: JobKind) -> None:
        cancel_event = self._cancel_flags[job_id]
        worker_jobs_running.labels(worker_id=self.worker_id).set(len(self._running))
        started = time.perf_counter()

        heartbeat = asyncio.create_task(self._heartbeat_loop(job_id, cancel_event))
        # 失败时保留工作区（里面有一次失败的全部现场），成功时清掉。
        # 用标志位而不是在 finally 里判断异常类型 —— 成功路径最后一步是
        # _finish_success，它自己也可能抛，混在一起会误判。
        succeeded = False
        try:
            if kind is JobKind.GENERATE_PROJECT:
                outcome = await pipeline_svc.execute_generation(
                    self.settings,
                    job_id=job_id,
                    worker_id=self.worker_id,
                    should_cancel=cancel_event.is_set,
                )
            elif kind is JobKind.REGENERATE_SHOT:
                outcome = await pipeline_svc.execute_shot_regeneration(
                    self.settings,
                    job_id=job_id,
                    worker_id=self.worker_id,
                    should_cancel=cancel_event.is_set,
                )
            else:
                raise NotImplementedError(f"暂不支持的任务类型：{kind.value}")

            await self._finish_success(job_id, outcome)
            succeeded = True
            job_duration.labels(kind=kind.value).observe(time.perf_counter() - started)

        except pipeline_svc.GenerationCancelled as exc:
            log.info("job_cancelled_by_user", job_id=job_id, reason=str(exc))
            async with session_scope() as session:
                await jobs_svc.mark_cancelled(session, job_id=job_id, refund=True)
            jobs_total.labels(kind=kind.value, status="cancelled").inc()

        except Exception as exc:
            await self._finish_failure(job_id, kind, exc)

        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            # 清掉临时工作区（失败时保留，见 cleanup_workspace 的说明）
            pipeline_svc.cleanup_workspace(self.settings.engine_workspace / job_id, keep=not succeeded)

    async def _finish_success(self, job_id: str, outcome: pipeline_svc.GenerationOutcome) -> None:
        async with session_scope() as session:
            settle = await jobs_svc.settle(
                session,
                self.settings,
                job_id=job_id,
                actual_provider_cost_usd=outcome.provider_cost_usd,
            )
            await jobs_svc.complete(
                session,
                job_id=job_id,
                result={
                    "artifacts": outcome.artifacts,
                    "shots_total": outcome.shots_total,
                    "shots_finished": outcome.shots_finished,
                    "evaluation_gate": (outcome.evaluation.get("gate") or {}).get("passed"),
                    "settlement": settle,
                },
                provider_cost_usd=outcome.provider_cost_usd,
            )
        log.info("job_settled", job_id=job_id, **{k: v for k, v in settle.items() if isinstance(v, int)})

    async def _finish_failure(self, job_id: str, kind: JobKind, exc: Exception) -> None:
        from ..domain.errors import AppError

        if isinstance(exc, AppError):
            code, message, retryable = exc.code, exc.message, getattr(exc, "retryable", True)
        else:
            code = "unexpected_error"
            message = f"{type(exc).__name__}: {exc}"
            retryable = True

        log.exception("job_failed", job_id=job_id, kind=kind.value, error_code=code)
        try:
            async with session_scope() as session:
                await jobs_svc.fail(
                    session, job_id=job_id, error=message, error_code=code, retryable=retryable
                )
        except Exception as inner:  # pragma: no cover
            log.error("job_fail_marking_failed", job_id=job_id, error=str(inner))

    # ------------------------------------------------------------------ #
    async def _heartbeat_loop(self, job_id: str, cancel_event: asyncio.Event) -> None:
        """周期性续约。同时把「用户请求取消」翻译成协程内可见的信号。"""
        interval = max(5, self.settings.worker.heartbeat_seconds)
        while True:
            await asyncio.sleep(interval)
            try:
                async with session_scope() as session:
                    alive = await jobs_svc.heartbeat(
                        session, self.settings, job_id=job_id, worker_id=self.worker_id
                    )
                if not alive:
                    # 要么被取消，要么租约被别的 worker 接管
                    cancel_event.set()
                    log.info("job_heartbeat_lost", job_id=job_id)
                    return
            except Exception as exc:  # pragma: no cover
                log.warning("job_heartbeat_error", job_id=job_id, error=str(exc))

    # ------------------------------------------------------------------ #
    async def _housekeeping(self) -> None:
        now = time.monotonic()

        if now - self._last_reap > REAP_INTERVAL_SECONDS:
            self._last_reap = now
            try:
                async with session_scope() as session:
                    await jobs_svc.reap_expired_leases(session)
                    await jobs_svc.queue_depths(session)
            except Exception as exc:  # pragma: no cover
                log.warning("reap_failed", error=str(exc))
            # 失败任务的工作区会保留以便排查，但必须设上限 ——
            # 一个反复失败的任务会一直攒，最终把磁盘撑满。
            try:
                pipeline_svc.sweep_stale_workspaces(self.settings.engine_workspace)
            except Exception as exc:  # pragma: no cover
                log.warning("workspace_sweep_error", error=str(exc))

        if self.settings.email.enabled and now - self._last_email > EMAIL_INTERVAL_SECONDS:
            self._last_email = now
            try:
                await email_svc.deliver_pending(self.settings, limit=20)
            except Exception as exc:  # pragma: no cover
                log.warning("email_delivery_failed", error=str(exc))

    # ------------------------------------------------------------------ #
    async def _drain(self) -> None:
        """优雅停机：等在跑的任务收尾，超过宽限期则取消。"""
        if not self._running:
            return
        grace = self.settings.worker.shutdown_grace_seconds
        log.info("worker_draining", running=len(self._running), grace_seconds=grace)
        done, pending = await asyncio.wait(
            set(self._running.values()), timeout=grace, return_when=asyncio.ALL_COMPLETED
        )
        if pending:
            log.warning("worker_force_cancel", pending=len(pending))
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        log.info("worker_drained", completed=len(done), cancelled=len(pending))

    # ------------------------------------------------------------------ #
    async def run_once(self) -> str | None:
        """执行单个任务后返回（供测试与 CLI `worker --once` 使用）。"""
        async with session_scope() as session:
            job = await jobs_svc.claim(session, self.settings, worker_id=self.worker_id)
            if job is None:
                return None
            job_id, kind = job.id, job.kind
        self._cancel_flags[job_id] = asyncio.Event()
        await self._run_job(job_id, kind)
        self._cancel_flags.pop(job_id, None)
        return job_id


# --------------------------------------------------------------------------- #
# 信号处理
# --------------------------------------------------------------------------- #
def install_signal_handlers(worker: Worker) -> None:
    loop = asyncio.get_running_loop()

    def _handler(signum: int) -> None:
        log.info("signal_received", signal=signum)
        worker.request_stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        # Windows 的事件循环不支持 add_signal_handler，此时退回默认的
        # KeyboardInterrupt 行为即可，不需要额外处理。
        with contextlib.suppress(NotImplementedError, AttributeError, ValueError):
            loop.add_signal_handler(sig, _handler, sig)


async def run_worker(settings: Settings | None = None, *, once: bool = False) -> int:
    settings = settings or get_settings()
    configure_logging(
        level=settings.observability.log_level,
        fmt=settings.observability.log_format,
    )
    worker = Worker(settings)

    if once:
        job_id = await worker.run_once()
        print(f"processed: {job_id}" if job_id else "no job available")
        await dispose_engine()
        return 0

    install_signal_handlers(worker)
    await worker.start()
    return 0


def main() -> int:
    """`reelagent-worker` 入口。"""
    import argparse

    parser = argparse.ArgumentParser(prog="reelagent-worker", description="ReelAgent 任务执行进程")
    parser.add_argument("--once", action="store_true", help="只跑一个任务后退出（调试用）")
    parser.add_argument("--concurrency", type=int, default=None, help="覆盖并发度")
    parser.add_argument("--worker-id", default=None)
    args = parser.parse_args()

    settings = get_settings()
    if args.concurrency:
        settings.worker.concurrency = args.concurrency

    try:
        return asyncio.run(run_worker(settings, once=args.once))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
