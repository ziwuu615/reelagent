"""租户配额：并发数 + 每日上限。

这两条是「防一个账号刷爆成本」的唯一有效手段。请求级限流挡不住
「每个请求都合法、加起来过量」的用法，所以配额必须单独存在且可测。

测试要钉住的边界：
  - 并发到上限时拒绝，且用的是 Forbidden（引导用户等待/升级）
  - 每日到上限时拒绝，且用的是 ValidationFailed（资源状态，不是权限）
  - 已终止的任务不占并发额度（否则失败一次就永久占坑）
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from reelagent_cloud.db.base import utcnow
from reelagent_cloud.db.models import Job, Organization, Project
from reelagent_cloud.db.session import session_scope
from reelagent_cloud.domain.enums import JobKind, JobStatus, PlanTier
from reelagent_cloud.domain.errors import Forbidden, ValidationFailed
from reelagent_cloud.domain.ids import PREFIX, new_id
from reelagent_cloud.services import quotas

pytestmark = pytest.mark.usefixtures("db")


async def _org(plan: PlanTier = PlanTier.FREE) -> Organization:
    org_id = new_id(PREFIX["org"])
    project_id = new_id(PREFIX["project"])
    async with session_scope() as session:
        org = Organization(
            id=org_id,
            name="t",
            slug=f"s-{org_id[-6:]}",
            credits_balance=1000,
            plan=plan,
        )
        session.add(org)
        session.add(
            Project(
                id=project_id,
                org_id=org_id,
                title="p",
                seed_text="x",
                style="cinematic",
                aspect_ratio="9:16",
            )
        )
        await session.flush()
        return org


async def _job(org_id: str, *, status: JobStatus, age_hours: float = 0.0) -> str:
    job_id = new_id(PREFIX["job"])
    async with session_scope() as session:
        job = Job(
            id=job_id,
            org_id=org_id,
            kind=JobKind.GENERATE_PROJECT,
            status=status,
        )
        if age_hours:
            job.created_at = utcnow() - timedelta(hours=age_hours)
        session.add(job)
        await session.flush()
    return job_id


async def test_concurrency_limit_raises_forbidden() -> None:
    org = await _org(PlanTier.FREE)  # FREE: max_concurrent_jobs = 1
    await _job(org.id, status=JobStatus.RUNNING)

    async with session_scope() as session:
        fresh = await session.get(Organization, org.id)
        assert fresh is not None
        with pytest.raises(Forbidden):
            await quotas.check_can_create(session, org=fresh)


async def test_finished_jobs_do_not_hold_slots() -> None:
    org = await _org(PlanTier.FREE)
    await _job(org.id, status=JobStatus.SUCCEEDED)
    await _job(org.id, status=JobStatus.FAILED)

    async with session_scope() as session:
        fresh = await session.get(Organization, org.id)
        assert fresh is not None
        await quotas.check_can_create(session, org=fresh)  # 不应抛错


async def test_daily_limit_raises_validation_failed() -> None:
    org = await _org(PlanTier.FREE)
    limit = quotas.DAILY_JOB_LIMIT[PlanTier.FREE]
    for _ in range(limit):
        await _job(org.id, status=JobStatus.SUCCEEDED)

    async with session_scope() as session:
        fresh = await session.get(Organization, org.id)
        assert fresh is not None
        with pytest.raises(ValidationFailed) as exc:
            await quotas.check_can_create(session, org=fresh)
    assert "24 小时" in str(exc.value)


async def test_jobs_older_than_24h_not_counted() -> None:
    org = await _org(PlanTier.FREE)
    limit = quotas.DAILY_JOB_LIMIT[PlanTier.FREE]
    for _ in range(limit):
        await _job(org.id, status=JobStatus.SUCCEEDED, age_hours=25)

    async with session_scope() as session:
        fresh = await session.get(Organization, org.id)
        assert fresh is not None
        await quotas.check_can_create(session, org=fresh)


async def test_usage_snapshot_has_denominators() -> None:
    """只给剩余量用户无法判断紧张程度，必须带分母。"""
    org = await _org(PlanTier.STARTER)
    await _job(org.id, status=JobStatus.RUNNING)

    async with session_scope() as session:
        fresh = await session.get(Organization, org.id)
        assert fresh is not None
        snap = await quotas.usage_snapshot(session, org=fresh)

    assert snap["concurrent"]["used"] >= 1
    assert snap["concurrent"]["limit"] >= 1
    assert snap["daily_jobs"]["limit"] == quotas.DAILY_JOB_LIMIT[PlanTier.STARTER]
    assert snap["plan"] == PlanTier.STARTER.value
