"""对账差异工单：落库、去重、自动关闭。

检测与告警只能说明「知道」，差异处理需要一张能回答
「什么时候开始的、几次了、处理完了吗」的表。

三个容易做错的点在这里钉住：
  1. 每小时跑一次，持续漂移不能每小时插一条新行（否则真差异被噪音淹没）
  2. 金额变化要另起一行（根因可能不同），不能当成同一条累加
  3. 漂移消失要自动关闭（手工关单总会被忘掉，未解决队列会烂掉）
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from reelagent_cloud.db.models import Organization, ReconciliationDiff
from reelagent_cloud.db.session import session_scope
from reelagent_cloud.domain.ids import PREFIX, new_id
from reelagent_cloud.services import credits

pytestmark = pytest.mark.usefixtures("db")


async def _org(balance: int) -> str:
    org_id = new_id(PREFIX["org"])
    async with session_scope() as session:
        session.add(Organization(id=org_id, name="t", slug=f"s-{org_id[-6:]}", credits_balance=balance))
        await session.flush()
    return org_id


async def _open_diffs(org_id: str) -> list[ReconciliationDiff]:
    async with session_scope() as session:
        return list(
            (
                await session.scalars(
                    select(ReconciliationDiff).where(
                        ReconciliationDiff.org_id == org_id,
                        ReconciliationDiff.resolved_at.is_(None),
                    )
                )
            ).all()
        )


async def test_drift_creates_a_ticket() -> None:
    org_id = await _org(balance=500)  # 账本为空 -> 漂移 +500

    async with session_scope() as session:
        await credits.reconcile_all_orgs(session)

    rows = await _open_diffs(org_id)
    assert len(rows) == 1
    assert rows[0].drift == 500
    assert rows[0].occurrences == 1


async def test_repeated_detection_does_not_duplicate() -> None:
    """每小时跑一次，持续漂移只应有一条未解决工单。"""
    org_id = await _org(balance=500)

    for _ in range(3):
        async with session_scope() as session:
            await credits.reconcile_all_orgs(session)

    rows = await _open_diffs(org_id)
    assert len(rows) == 1, "持续漂移不能每次插新行"
    assert rows[0].occurrences == 3, "次数要累计，用于判断修了多久还没好"


async def test_amount_change_opens_a_new_ticket() -> None:
    org_id = await _org(balance=500)
    async with session_scope() as session:
        await credits.reconcile_all_orgs(session)

    async with session_scope() as session:
        org = await session.get(Organization, org_id)
        assert org is not None
        org.credits_balance = 700  # 漂移金额变了，根因可能不同

    async with session_scope() as session:
        await credits.reconcile_all_orgs(session)

    rows = await _open_diffs(org_id)
    assert len(rows) == 2
    assert sorted(r.drift for r in rows) == [500, 700]


async def test_drift_disappearing_auto_resolves() -> None:
    """漂移被修复后，未解决工单必须自动关闭。"""
    org_id = await _org(balance=500)
    async with session_scope() as session:
        await credits.reconcile_all_orgs(session)
    assert len(await _open_diffs(org_id)) == 1

    # 人工补账把余额改回 0（与空账本一致）
    async with session_scope() as session:
        org = await session.get(Organization, org_id)
        assert org is not None
        org.credits_balance = 0

    async with session_scope() as session:
        await credits.reconcile_all_orgs(session)

    assert await _open_diffs(org_id) == []
    async with session_scope() as session:
        all_rows = list(
            (
                await session.scalars(select(ReconciliationDiff).where(ReconciliationDiff.org_id == org_id))
            ).all()
        )
    assert all_rows[0].resolved_at is not None
    assert all_rows[0].resolution == "auto_resolved_no_longer_drifting"


async def test_list_open_diffs_orders_oldest_first() -> None:
    """先拖最久的先处理。"""
    a = await _org(balance=500)
    b = await _org(balance=300)

    async with session_scope() as session:
        await credits.reconcile_all_orgs(session)

    async with session_scope() as session:
        rows = await credits.list_open_diffs(session)
    assert {r.org_id for r in rows} >= {a, b}
    detected = [r.detected_at for r in rows]
    assert detected == sorted(detected)
