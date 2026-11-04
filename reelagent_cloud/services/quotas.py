"""租户配额：防「一个账号刷爆成本」。

## 为什么限流不够，必须另做配额

现有的限流是**请求级**的：限制每秒能打多少次接口。它挡得住脚本狂刷，
但挡不住一个正常频率的用户把额度换成大量任务——每个请求都合法，
可加起来会把并发数、供应商配额和服务器资源吃满。

配额是**资源级**的：并发任务数、每日任务数。两者是不同维度的防护，
只有请求级限流时，一次「合法但过量」的使用就能让整站变慢。

## 为什么并发配额比日配额更重要

日配额的后果是「今天用完了」，用户能理解也能等。
并发配额的后果是**别人的任务排在你前面**——一个帐号挂 50 个任务，
所有其他客户的可感知延迟立刻劣化。所以并发必须先卡。

## 为什么用现有表统计，而不是新建计数表

`jobs` 表已经有 `org_id / status / created_at`，一次 `COUNT` 就能算出
「当前活跃任务数」与「今日已建任务数」。新加一张计数表就要面对
「计数表与事实表不一致时信谁」的问题——这正是配额系统最经典的 bug 来源。
用事实表统计，天然一致。

代价是每次创建要做两次 COUNT。按 `(org_id, status)` 与
`(org_id, created_at)` 的现有索引，这个开销可以接受；真到瓶颈再加
缓存或计数表，而不是现在就引入一致性风险。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.base import utcnow
from ..db.models import Job, Organization
from ..domain.enums import JobStatus, PlanTier
from ..domain.errors import Forbidden, ValidationFailed
from ..domain.plans import Plan, get_plan

#: 每日可创建任务数上限（按套餐）。
#:
#: 这是**产品决策**而不是外部真值：它要卡住滥用，又不能卡住正常用户。
#: 取值的依据是「典型用户一天能出几条片」——创作者一天 2~5 条，
#: 给到 20 已是很宽松的余量（10 倍），同时把「一晚建 1000 个任务」的
#: 滥用挡住。改这个数请同步改定价页的套餐说明。
DAILY_JOB_LIMIT: dict[PlanTier, int] = {
    PlanTier.FREE: 5,
    PlanTier.STARTER: 20,
    PlanTier.PRO: 100,
    PlanTier.STUDIO: 500,
}

#: 视为「占用并发额度」的状态。dead / succeeded / failed / cancelled 都不占。
_ACTIVE_STATUS = (JobStatus.PENDING, JobStatus.RUNNING)


async def active_job_count(session: AsyncSession, org_id: str) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(Job).where(Job.org_id == org_id, Job.status.in_(_ACTIVE_STATUS))
        )
        or 0
    )


async def jobs_created_today(session: AsyncSession, org_id: str, *, now=None) -> int:
    now = now or utcnow()
    since = now - timedelta(hours=24)
    return int(
        await session.scalar(
            select(func.count()).select_from(Job).where(Job.org_id == org_id, Job.created_at >= since)
        )
        or 0
    )


async def check_can_create(session: AsyncSession, *, org: Organization, plan: Plan | None = None) -> None:
    """创建任务前的配额校验。不满足直接抛错（带可行动文案）。

    两类超限用**不同的异常**，因为它们要引导用户做不同的事：
      并发超限 -> Forbidden（等一等，或升级套餐提高并发）
      每日超限 -> ValidationFailed（这是资源状态，不是权限问题）

    早期只做了并发校验、没有每日上限，结果是「一个账号一天建几百个任务」
    完全不受限 —— 请求级限流拦不住这种「每个请求都合法」的滥用。
    """
    plan = plan or get_plan(org.plan)

    active = await active_job_count(session, org.id)
    limit = max(1, plan.max_concurrent_jobs)
    if active >= limit:
        raise Forbidden(f"{plan.name}同时只能有 {limit} 个生成任务在跑，请等待完成或升级套餐")

    daily_limit = DAILY_JOB_LIMIT.get(plan.tier, 20)
    created = await jobs_created_today(session, org.id)
    if created >= daily_limit:
        raise ValidationFailed(f"24 小时内创建的任务已达上限（{daily_limit} 个），请稍后再试或升级套餐")


async def usage_snapshot(
    session: AsyncSession, *, org: Organization, settings: Settings | None = None
) -> dict[str, Any]:
    """当前用量快照，给仪表盘与排障用。

    返回「已用 / 上限」而不是只返回剩余量：只给剩余量时，用户无法判断
    「还剩 3 个」是宽松还是紧张，必须带上分母。
    """
    plan = get_plan(org.plan)
    active = await active_job_count(session, org.id)
    created = await jobs_created_today(session, org.id)
    return {
        "plan": plan.tier.value,
        "concurrent": {"used": active, "limit": max(1, plan.max_concurrent_jobs)},
        "daily_jobs": {"used": created, "limit": DAILY_JOB_LIMIT.get(plan.tier, 20)},
        "credits_balance": int(org.credits_balance or 0),
    }
