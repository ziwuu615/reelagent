"""账本全量巡检。

单机构 `reconcile()` 已有测试，这里补的是**巡检**特有的三件事：

  1. 只返回不一致的机构（告警规则才能直接写 count > 0）
  2. 漂移方向和金额要对（正负号决定是「多扣」还是「少扣」）
  3. 没有漂移时返回空，且不误报
"""

from __future__ import annotations

import pytest

from reelagent_cloud.db.models import Organization
from reelagent_cloud.db.session import session_scope
from reelagent_cloud.domain.ids import PREFIX, new_id
from reelagent_cloud.services import credits

pytestmark = pytest.mark.usefixtures("db")


async def _org(balance: int = 0) -> str:
    org_id = new_id(PREFIX["org"])
    async with session_scope() as session:
        session.add(
            Organization(
                id=org_id,
                name="t",
                slug=f"s-{org_id[-6:]}",
                credits_balance=balance,
            )
        )
        await session.flush()
    return org_id


async def test_clean_ledger_reports_no_drift() -> None:
    await _org(balance=0)

    async with session_scope() as session:
        report = await credits.reconcile_all_orgs(session)

    assert report["drifted_count"] == 0
    assert report["drifts"] == []
    assert report["max_abs_drift"] == 0


async def test_drift_is_detected_with_sign() -> None:
    """余额 100 但账本为空 -> drift = +100（多给了用户）。"""
    org_id = await _org(balance=100)

    async with session_scope() as session:
        report = await credits.reconcile_all_orgs(session)

    assert report["drifted_count"] == 1
    row = report["drifts"][0]
    assert row["org_id"] == org_id
    assert row["drift"] == 100
    assert report["max_abs_drift"] == 100


async def test_consistent_org_is_excluded_after_grant() -> None:
    """正常发放额度后账本与余额一致，不应出现在漂移列表里。"""
    org_id = await _org(balance=0)
    from reelagent_cloud.domain.enums import LedgerKind

    async with session_scope() as session:
        await credits.grant(session, org_id=org_id, amount=500, kind=LedgerKind.SIGNUP_GRANT)

    async with session_scope() as session:
        report = await credits.reconcile_all_orgs(session)

    assert all(d["org_id"] != org_id for d in report["drifts"])
