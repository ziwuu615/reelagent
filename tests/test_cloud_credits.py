"""额度账本测试。

这是**直接对应钱**的模块，因此测试覆盖得最细：
并发安全、幂等、对账、余额不足、失败退还。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from reelagent_cloud.config import Settings
from reelagent_cloud.db.models import LedgerEntry, Organization
from reelagent_cloud.db.session import session_scope
from reelagent_cloud.domain.enums import LedgerKind
from reelagent_cloud.domain.errors import NotFound, PaymentRequired
from reelagent_cloud.domain.ids import PREFIX, new_id
from reelagent_cloud.services import credits

# 本模块所有用例都需要数据库就绪。
# 注意：`session_scope()` 在未初始化引擎时会惰性创建——那会指向真实配置里的库，
# 表现为 "no such table"。因此必须显式声明依赖 `db` fixture。
pytestmark = pytest.mark.usefixtures("db")


async def _make_org(balance: int = 0) -> str:
    org_id = new_id(PREFIX["org"])
    async with session_scope() as session:
        session.add(
            Organization(
                id=org_id,
                name="测试组织",
                slug=f"test-{org_id[-6:]}",
                credits_balance=0,
            )
        )
        await session.flush()
    if balance:
        async with session_scope() as session:
            await credits.grant(session, org_id=org_id, amount=balance, kind=LedgerKind.SIGNUP_GRANT)
    return org_id


# --------------------------------------------------------------------------- #
# 计价
# --------------------------------------------------------------------------- #
class TestPricing:
    def test_usd_to_credits_applies_markup(self, settings: Settings):
        # 默认 markup=3.0, credits_per_usd=100 -> 1 美元 = 300 点
        assert credits.usd_to_credits(settings, 1.0) == 300
        assert credits.usd_to_credits(settings, 0.1) == 30

    def test_zero_and_negative_cost(self, settings: Settings):
        assert credits.usd_to_credits(settings, 0.0) == 0
        assert credits.usd_to_credits(settings, -5.0) == 0

    def test_rounds_up_never_zero_for_tiny_cost(self, settings: Settings):
        """极小的成本也必须至少扣 1 点，否则会被刷成免费。"""
        assert credits.usd_to_credits(settings, 0.0000001) == 1

    def test_quote_scales_with_shots(self, settings: Settings):
        few = credits.quote_project(settings, shots=3, allow_premium=False)
        many = credits.quote_project(settings, shots=10, allow_premium=False)
        assert many.credits > few.credits
        assert few.breakdown["video"] > 0

    def test_premium_costs_more(self, settings: Settings):
        base = credits.quote_project(settings, shots=6, allow_premium=False)
        premium = credits.quote_project(settings, shots=6, allow_premium=True)
        assert premium.credits > base.credits


# --------------------------------------------------------------------------- #
# 基本记账
# --------------------------------------------------------------------------- #
class TestLedger:
    async def test_grant_and_balance(self, settings: Settings):
        org_id = await _make_org()
        async with session_scope() as session:
            await credits.grant(session, org_id=org_id, amount=500, description="开户赠送")
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == 500

    async def test_consume_reduces_balance(self, settings: Settings):
        org_id = await _make_org(balance=300)
        async with session_scope() as session:
            entry = await credits.consume(session, org_id=org_id, amount=120, description="生成")
            assert entry.amount == -120
            assert entry.balance_after == 180
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == 180

    async def test_entry_records_balance_after(self, settings: Settings):
        org_id = await _make_org(balance=100)
        async with session_scope() as session:
            await credits.consume(session, org_id=org_id, amount=30)
            await credits.consume(session, org_id=org_id, amount=20)
        async with session_scope() as session:
            rows = list(
                (
                    await session.execute(
                        select(LedgerEntry)
                        .where(LedgerEntry.org_id == org_id)
                        .order_by(LedgerEntry.created_at)
                    )
                ).scalars()
            )
        assert [r.balance_after for r in rows] == [100, 70, 50]

    async def test_insufficient_funds_blocks_and_preserves_balance(self, settings: Settings):
        org_id = await _make_org(balance=50)
        async with session_scope() as session:
            with pytest.raises(PaymentRequired) as exc:
                await credits.consume(session, org_id=org_id, amount=100)
            assert exc.value.details["available"] == 50
            assert exc.value.details["required"] == 100
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == 50, "失败不能改动余额"
            assert await credits.reconcile(session, org_id) is not None

    async def test_exact_balance_allowed(self, settings: Settings):
        org_id = await _make_org(balance=100)
        async with session_scope() as session:
            await credits.consume(session, org_id=org_id, amount=100)
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == 0

    async def test_unknown_org_raises(self, settings: Settings):
        async with session_scope() as session:
            with pytest.raises(NotFound):
                await credits.get_balance(session, "org_does_not_exist")

    @pytest.mark.parametrize("amount", [0, -1])
    async def test_invalid_amounts_rejected(self, settings: Settings, amount: int):
        org_id = await _make_org(balance=100)
        async with session_scope() as session:
            with pytest.raises(ValueError):
                await credits.consume(session, org_id=org_id, amount=amount)


# --------------------------------------------------------------------------- #
# 幂等
# --------------------------------------------------------------------------- #
class TestIdempotency:
    async def test_consume_is_idempotent(self, settings: Settings):
        """网络超时重试时，同一幂等键绝不能重复扣费。"""
        org_id = await _make_org(balance=100)
        async with session_scope() as session:
            first = await credits.consume(session, org_id=org_id, amount=40, idempotency_key="op-1")
        async with session_scope() as session:
            second = await credits.consume(session, org_id=org_id, amount=40, idempotency_key="op-1")
            assert second.id == first.id
            assert await credits.get_balance(session, org_id) == 60

    async def test_grant_is_idempotent(self, settings: Settings):
        """Stripe 会重复投递 invoice.paid，重复发放额度等于送钱。"""
        org_id = await _make_org()
        async with session_scope() as session:
            await credits.grant(session, org_id=org_id, amount=1000, idempotency_key="invoice-9")
        async with session_scope() as session:
            await credits.grant(session, org_id=org_id, amount=1000, idempotency_key="invoice-9")
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == 1000

    async def test_different_keys_apply_separately(self, settings: Settings):
        org_id = await _make_org(balance=100)
        async with session_scope() as session:
            await credits.consume(session, org_id=org_id, amount=10, idempotency_key="a")
        async with session_scope() as session:
            await credits.consume(session, org_id=org_id, amount=10, idempotency_key="b")
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == 80

    async def test_no_key_always_applies(self, settings: Settings):
        org_id = await _make_org(balance=100)
        for _ in range(3):
            async with session_scope() as session:
                await credits.consume(session, org_id=org_id, amount=10)
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == 70


# --------------------------------------------------------------------------- #
# 退还与调整
# --------------------------------------------------------------------------- #
class TestRefundAndAdjust:
    async def test_refund_restores(self, settings: Settings):
        org_id = await _make_org(balance=100)
        async with session_scope() as session:
            await credits.consume(session, org_id=org_id, amount=60)
        async with session_scope() as session:
            await credits.refund(session, org_id=org_id, amount=60, description="任务失败退还")
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == 100

    async def test_refund_is_idempotent(self, settings: Settings):
        org_id = await _make_org(balance=100)
        async with session_scope() as session:
            await credits.consume(session, org_id=org_id, amount=60)
        for _ in range(3):
            async with session_scope() as session:
                await credits.refund(session, org_id=org_id, amount=60, idempotency_key="refund-1")
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == 100

    async def test_zero_refund_is_recorded_not_rejected(self, settings: Settings):
        org_id = await _make_org(balance=100)
        async with session_scope() as session:
            entry = await credits.refund(session, org_id=org_id, amount=0)
            assert entry.amount == 0
            assert entry.kind is LedgerKind.REFUND

    async def test_manual_adjustment(self, settings: Settings):
        org_id = await _make_org(balance=100)
        async with session_scope() as session:
            await credits.adjust(
                session, org_id=org_id, amount=250, reason="客服补偿", created_by="usr_admin"
            )
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == 350

    async def test_adjustment_cannot_go_negative(self, settings: Settings):
        org_id = await _make_org(balance=100)
        async with session_scope() as session:
            with pytest.raises(PaymentRequired):
                await credits.adjust(
                    session, org_id=org_id, amount=-500, reason="扣罚", created_by="usr_admin"
                )


# --------------------------------------------------------------------------- #
# 对账
# --------------------------------------------------------------------------- #
class TestReconciliation:
    async def test_consistent_after_mixed_operations(self, settings: Settings):
        org_id = await _make_org()
        async with session_scope() as session:
            await credits.grant(session, org_id=org_id, amount=1000, kind=LedgerKind.SIGNUP_GRANT)
        async with session_scope() as session:
            await credits.consume(session, org_id=org_id, amount=300)
        async with session_scope() as session:
            await credits.refund(session, org_id=org_id, amount=120)
        async with session_scope() as session:
            await credits.adjust(session, org_id=org_id, amount=50, reason="补偿", created_by="u")
        async with session_scope() as session:
            report = await credits.reconcile(session, org_id)
        assert report["consistent"] is True, report
        assert report["balance"] == 1000 - 300 + 120 + 50
        assert report["drift"] == 0

    async def test_detects_drift(self, settings: Settings):
        """人为制造不一致，验证对账能发现——这是兜底断言的价值所在。"""
        org_id = await _make_org(balance=100)
        async with session_scope() as session:
            org = await session.get(Organization, org_id)
            assert org is not None
            org.credits_balance = 999  # 模拟被外部脚本改坏
            await session.flush()
        async with session_scope() as session:
            report = await credits.reconcile(session, org_id)
        assert report["consistent"] is False
        assert report["drift"] == 899


# --------------------------------------------------------------------------- #
# 并发
# --------------------------------------------------------------------------- #
class TestConcurrency:
    async def test_concurrent_consumes_do_not_lose_updates(self, settings: Settings):
        """并发扣费必须串行化，否则会出现「余额只剩 100 却扣了 5 次 40」。"""
        org_id = await _make_org(balance=1000)

        async def spend() -> bool:
            try:
                async with session_scope() as session:
                    await credits.consume(session, org_id=org_id, amount=100)
                return True
            except PaymentRequired:
                return False

        results = await asyncio.gather(*[spend() for _ in range(20)])
        succeeded = sum(1 for r in results if r)

        async with session_scope() as session:
            balance = await credits.get_balance(session, org_id)
            report = await credits.reconcile(session, org_id)

        assert succeeded == 10, f"应当恰好成功 10 次，实际 {succeeded}"
        assert balance == 0
        assert report["consistent"] is True, report

    async def test_concurrent_grants_all_apply(self, settings: Settings):
        org_id = await _make_org()

        async def add() -> None:
            async with session_scope() as session:
                await credits.grant(session, org_id=org_id, amount=10)

        await asyncio.gather(*[add() for _ in range(15)])
        async with session_scope() as session:
            assert await credits.get_balance(session, org_id) == 150


# --------------------------------------------------------------------------- #
# 流水查询
# --------------------------------------------------------------------------- #
class TestHistory:
    async def test_history_is_newest_first(self, settings: Settings):
        org_id = await _make_org(balance=100)
        async with session_scope() as session:
            for i in range(5):
                await credits.consume(session, org_id=org_id, amount=10, description=f"第{i}笔")
        async with session_scope() as session:
            rows = await credits.history(session, org_id, limit=3)
        assert len(rows) == 3
        assert "第4笔" in rows[0].description

    async def test_history_limit_is_capped(self, settings: Settings):
        org_id = await _make_org(balance=10)
        async with session_scope() as session:
            rows = await credits.history(session, org_id, limit=10_000)
        assert len(rows) <= 200, "必须给 limit 设上限，避免被用来拖库"
