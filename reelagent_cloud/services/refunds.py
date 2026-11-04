"""用户主动退款。

## 与「失败自动退」是两件事

`credits.refund` 解决的是**结算退还**：任务失败或取消时把预扣的钱退回去，
由 job 结算自动触发，用户不需要申请。

这里解决的是另一件事：**成片已经交付了，用户不满意或没使用，主动申请退款**。
它必须带四个约束，否则就是资金漏洞：

  1. 只能退自己的项目（越权检查）
  2. 同一项目只能退一次（幂等键 `refund:{project_id}`）
  3. 退款额不得超过该项目**实际消耗**（不是预估、不是套餐额度）
  4. 有时效窗口（超过窗口只能走人工，避免「半年前用的片子今天来退」）

## 为什么退款额从账本算，而不是从报价算

报价是**预估**（带 1.15 安全系数、按平均镜长假设），账本是**事实**。
按报价退会退多：实跑 7 镜 premium 预扣 804 点、按实际成本结算后可能只花了 731 点，
退款必须按实际扣掉的数来。这也是为什么这里直接查 `ledger_entries`
里该项目的 CONSUME 行求和，而不是重算一遍报价。

## 为什么退款是「再记一笔账」而不是「删掉原账」

账本只追加。删除会让「这笔钱曾经扣过」这个事实消失，对账再也解释不清差额。
正向补一笔 REFUND 行，`SUM(账本) == 余额` 依然成立，且历史完整可审计。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import utcnow
from ..db.models import LedgerEntry, Project
from ..domain.enums import AuditAction, LedgerKind, ProjectStatus
from ..domain.errors import Forbidden, NotFound, ValidationFailed
from ..observability import get_logger
from . import audit, credits
from . import projects as projects_svc

log = get_logger(__name__)

#: 可申请退款的时效窗口（天）。
#:
#: 取 7 天：够用户看完片子并决定，又不至于无限期挂着一笔可退的债。
#: 产品上也可以做成套餐权益（高套餐窗口更长），但**必须有一个上限**——
#: 无上限的可退余额在财务上是一笔永远无法核销的负债。
REFUND_WINDOW_DAYS = 7

#: 一个项目最多能申请几次退款。1 次，靠幂等键保证。
_REFUND_KEY_PREFIX = "refund:"


async def consumed_for_project(session: AsyncSession, *, org_id: str, project_id: str) -> int:
    """该项目实际消耗了多少点（正数）。

    只统计 CONSUME 行。REFUND 行是负向支出，如果把它们也算进来，
    重复申请时会把已退的部分再退一次。
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.org_id == org_id,
            LedgerEntry.project_id == project_id,
            LedgerEntry.kind == LedgerKind.CONSUME,
        )
    )
    return abs(int(total or 0))


async def already_refunded(session: AsyncSession, *, org_id: str, project_id: str) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.org_id == org_id,
            LedgerEntry.project_id == project_id,
            LedgerEntry.kind == LedgerKind.REFUND,
        )
    )
    return int(total or 0)


async def request_refund(
    session: AsyncSession,
    *,
    org_id: str,
    user_id: str | None,
    project_id: str,
    reason: str = "",
) -> dict[str, Any]:
    """为某个已交付项目申请退款。

    幂等：同一项目重复调用不会重复退款，第二次直接返回首次结果
    （`replayed=True`），因为幂等键由 project_id 决定，撞唯一约束即短路。
    """
    project = await projects_svc.get_project(session, org_id=org_id, project_id=project_id)

    if project.deleted_at is not None:
        raise NotFound("项目不存在或已删除")

    consumed = await consumed_for_project(session, org_id=org_id, project_id=project_id)
    if consumed <= 0:
        raise ValidationFailed("该项目没有可退的消耗（可能尚未结算或已全额退还）")

    refunded = await already_refunded(session, org_id=org_id, project_id=project_id)
    key = f"{_REFUND_KEY_PREFIX}{project_id}"

    # 幂等判定必须在「是否已全额退款」之前。
    #
    # 顺序反了会出现：第一次退款成功把可退余额清零，用户因为网络超时重试，
    # 第二次直接被判「已全额退款」而拿到 422 —— 幂等接口对重试应当返回
    # **和首次相同的结果**，而不是一个看似合理的错误。这不是体验问题：
    # 客户端会把 422 当成「退款失败」，然后去人工工单，白耗一路。
    existing = (
        await session.scalars(
            select(LedgerEntry).where(LedgerEntry.org_id == org_id, LedgerEntry.idempotency_key == key)
        )
    ).first()
    if existing is not None:
        return {
            "project_id": project_id,
            "refunded": int(existing.amount),
            "consumed": consumed,
            "previously_refunded": refunded,
            "idempotency_key": key,
            "replayed": True,
        }

    refundable = consumed - refunded
    if refundable <= 0:
        raise ValidationFailed("该项目已全额退款，不能重复申请")

    # 时效窗口。用 updated_at 作为「交付/最后变动时间」的近似：
    # 真实业务里应该在成片落库时单独记一个 delivered_at，
    # 这里不新建字段是为了避免迁移，判断口径写在文档里。
    window_start = (project.updated_at or project.created_at) + timedelta(days=REFUND_WINDOW_DAYS)
    if utcnow() > window_start:
        raise Forbidden(f"已超过 {REFUND_WINDOW_DAYS} 天退款窗口，请联系客服人工处理")

    if project.status not in {
        ProjectStatus.READY,
        ProjectStatus.FAILED,
        ProjectStatus.ARCHIVED,
    }:
        raise Forbidden("项目仍在生成中，请等任务结束后再申请退款")

    await credits.refund(
        session,
        org_id=org_id,
        amount=refundable,
        description=f"用户申请退款（项目 {project_id}）",
        project_id=project_id,
        idempotency_key=key,
    )

    await audit.record(
        session,
        action=AuditAction.CREDITS_GRANTED,
        org_id=org_id,
        actor_user_id=user_id,
        target_type="project",
        target_id=project_id,
        meta={
            "refund": True,
            "amount": refundable,
            "consumed": consumed,
            "already_refunded": refunded,
            "reason": reason[:200],
        },
    )
    log.info(
        "refund_issued",
        org_id=org_id,
        project_id=project_id,
        amount=refundable,
        consumed=consumed,
    )
    return {
        "project_id": project_id,
        "refunded": refundable,
        "consumed": consumed,
        "previously_refunded": refunded,
        "idempotency_key": key,
        "replayed": False,
    }


async def refundable_amount(session: AsyncSession, *, org_id: str, project_id: str) -> int:
    """当前可退金额（给界面展示按钮与金额用）。"""
    consumed = await consumed_for_project(session, org_id=org_id, project_id=project_id)
    refunded = await already_refunded(session, org_id=org_id, project_id=project_id)
    return max(0, consumed - refunded)


async def list_refundable(session: AsyncSession, *, org_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """列出可退项目，供控制台展示。"""
    rows = (
        await session.scalars(
            select(Project)
            .where(Project.org_id == org_id, Project.deleted_at.is_(None))
            .order_by(Project.created_at.desc())
            .limit(limit)
        )
    ).all()
    out: list[dict[str, Any]] = []
    for project in rows:
        amount = await refundable_amount(session, org_id=org_id, project_id=project.id)
        if amount > 0:
            out.append({"project_id": project.id, "title": project.title, "refundable": amount})
    return out
