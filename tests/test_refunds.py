"""用户主动退款。

与「失败自动退」不同，这条路径是用户对**已交付**的成片申请退款，
所以它必须是资金安全的：不得越权、不得重复退、不得超过实际消耗、有窗口。

测试重点放在「不该退的时候会不会退」——退款类 bug 的代价是直接损失。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from reelagent_cloud.config import Settings
from reelagent_cloud.db.base import utcnow
from reelagent_cloud.db.models import LedgerEntry, Organization, Project
from reelagent_cloud.db.session import session_scope
from reelagent_cloud.domain.enums import LedgerKind, ProjectStatus
from reelagent_cloud.domain.errors import Forbidden, NotFound, ValidationFailed
from reelagent_cloud.domain.ids import PREFIX, new_id
from reelagent_cloud.services import credits, refunds

pytestmark = pytest.mark.usefixtures("db")


async def _setup(*, consumed: int = 800, age_days: int = 0, status=ProjectStatus.READY):
    org_id = new_id(PREFIX["org"])
    project_id = new_id(PREFIX["project"])
    async with session_scope() as session:
        session.add(Organization(id=org_id, name="t", slug=f"s-{org_id[-6:]}", credits_balance=0))
        project = Project(
            id=project_id,
            org_id=org_id,
            title="p",
            seed_text="x",
            style="cinematic",
            aspect_ratio="9:16",
            status=status,
        )
        if age_days:
            project.updated_at = utcnow() - timedelta(days=age_days)
        session.add(project)
        await session.flush()
        await credits.grant(session, org_id=org_id, amount=5000, kind=LedgerKind.SIGNUP_GRANT)
        if consumed:
            await credits.consume(
                session,
                org_id=org_id,
                amount=consumed,
                project_id=project_id,
                idempotency_key=f"pre:{project_id}",
            )
    return org_id, project_id


async def test_refund_returns_consumed_amount() -> None:
    org_id, project_id = await _setup(consumed=800)

    async with session_scope() as session:
        result = await refunds.request_refund(
            session, org_id=org_id, user_id=None, project_id=project_id, reason="不满意"
        )

    assert result["refunded"] == 800
    assert result["consumed"] == 800
    assert result["replayed"] is False

    async with session_scope() as session:
        assert await refunds.refundable_amount(session, org_id=org_id, project_id=project_id) == 0


async def test_second_request_is_idempotent() -> None:
    """同一项目第二次申请不能重复退钱。"""
    org_id, project_id = await _setup(consumed=800)

    async with session_scope() as session:
        first = await refunds.request_refund(session, org_id=org_id, user_id=None, project_id=project_id)
    async with session_scope() as session:
        second = await refunds.request_refund(session, org_id=org_id, user_id=None, project_id=project_id)

    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["refunded"] == first["refunded"]

    async with session_scope() as session:
        total = await session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
                LedgerEntry.org_id == org_id,
                LedgerEntry.project_id == project_id,
                LedgerEntry.kind == LedgerKind.REFUND,
            )
        )
    assert int(total or 0) == 800, "只应产生一笔退款"


async def test_outside_window_is_rejected() -> None:
    org_id, project_id = await _setup(consumed=800, age_days=refunds.REFUND_WINDOW_DAYS + 1)

    async with session_scope() as session:
        with pytest.raises(Forbidden):
            await refunds.request_refund(session, org_id=org_id, user_id=None, project_id=project_id)


async def test_no_consumption_is_rejected() -> None:
    org_id, project_id = await _setup(consumed=0)

    async with session_scope() as session:
        with pytest.raises(ValidationFailed):
            await refunds.request_refund(session, org_id=org_id, user_id=None, project_id=project_id)


async def test_other_org_cannot_refund() -> None:
    """越权：拿别人的 project_id 来退款必须失败。"""
    _org_id, project_id = await _setup(consumed=800)
    other_org = new_id(PREFIX["org"])
    async with session_scope() as session:
        session.add(Organization(id=other_org, name="o", slug=f"o-{other_org[-6:]}", credits_balance=100))
        await session.flush()

    async with session_scope() as session:
        with pytest.raises(NotFound):
            await refunds.request_refund(session, org_id=other_org, user_id=None, project_id=project_id)


async def test_running_project_cannot_be_refunded() -> None:
    org_id, project_id = await _setup(consumed=800, status=ProjectStatus.RUNNING)

    async with session_scope() as session:
        with pytest.raises(Forbidden):
            await refunds.request_refund(session, org_id=org_id, user_id=None, project_id=project_id)


async def test_list_refundable_only_shows_refundable(settings: Settings) -> None:
    org_id, project_id = await _setup(consumed=800)

    async with session_scope() as session:
        items = await refunds.list_refundable(session, org_id=org_id)
    assert [i["project_id"] for i in items] == [project_id]
    assert items[0]["refundable"] == 800
