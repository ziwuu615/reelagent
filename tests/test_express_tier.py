"""express（极速）档的测试。

**为什么单独设一档**：速度与画质是两种不同的需求，压成一档会迫使
「想要高画质又想省钱」的用户二选一。

实测依据（2026-10-16，480P/2s，同提示词并行提交）：
    wan3.0-video        95.0 秒
    wan3.0-video-prime  58.3 秒   -> 快 63%
官方定价：优速版比基础版贵约 54%（同分辨率）。

所以 express 与 premium **同分辨率（720P）、同画质**，
差别只在模型速度 —— 这些断言确保这个定义不会被悄悄改掉。
"""

from __future__ import annotations

import pytest

from reelagent.providers.base import (
    VIDEO_PRICE_PER_S,
    VIDEO_PRICE_PER_S_FAST,
    estimate_video_cost,
)
from reelagent.routing import (
    TIER_DRAFT,
    TIER_EXPRESS,
    TIER_PREMIUM,
    TIER_STANDARD,
    TIERS,
)
from reelagent_cloud.config import Settings
from reelagent_cloud.domain.errors import ValidationFailed
from reelagent_cloud.services import credits
from reelagent_cloud.services.projects import normalize_tier

pytestmark = pytest.mark.usefixtures("db")


class TestExpressIsAccepted:
    def test_normalize_accepts_express(self) -> None:
        assert normalize_tier("express") == TIER_EXPRESS

    def test_normalize_is_case_insensitive(self) -> None:
        assert normalize_tier("EXPRESS") == TIER_EXPRESS

    def test_unknown_still_rejected(self) -> None:
        with pytest.raises(ValidationFailed):
            normalize_tier("turbo")


class TestExpressSharesPremiumQuality:
    """express 与 premium 画质相同 —— 差异只在速度。"""

    def test_same_resolution_as_premium(self) -> None:
        assert TIERS[TIER_EXPRESS].resolution == TIERS[TIER_PREMIUM].resolution

    def test_same_resolution_scale_as_premium(self) -> None:
        assert TIERS[TIER_EXPRESS].resolution_scale == TIERS[TIER_PREMIUM].resolution_scale

    def test_higher_than_standard(self) -> None:
        assert TIERS[TIER_EXPRESS].resolution_scale > TIERS[TIER_STANDARD].resolution_scale


class TestExpressPricing:
    def test_fast_price_is_about_54_percent_higher(self) -> None:
        """官方定价里三个分辨率的溢价都是约 1.54 倍。"""
        for resolution in ("480P", "720P", "1080P"):
            ratio = VIDEO_PRICE_PER_S_FAST[resolution] / VIDEO_PRICE_PER_S[resolution]
            assert ratio == pytest.approx(1.54, rel=0.01)

    def test_estimate_reflects_fast_flag(self) -> None:
        normal = estimate_video_cost(5, "720P")
        fast = estimate_video_cost(5, "720P", fast=True)
        assert fast > normal

    def test_express_costs_more_than_premium_in_quote(self) -> None:
        settings = Settings()
        premium = credits.quote_project(settings, shots=3, allow_premium=True, tier=TIER_PREMIUM)
        express = credits.quote_project(settings, shots=3, allow_premium=True, tier=TIER_EXPRESS)
        assert express.provider_cost_usd > premium.provider_cost_usd

    def test_express_budget_covers_its_quote(self) -> None:
        settings = Settings()
        for shots in (1, 3, 5):
            quote = credits.quote_project(settings, shots=shots, allow_premium=True, tier="express")
            assert credits.budget_for(settings, quote) >= quote.provider_cost_usd


class TestSingleSourceOfTruth:
    """档位规格只能有一个来源。

    真实事故：加 express 档时改了 `TIER_RESOLUTION_SCALE`，忘了改
    `TIER_VIDEO_RESOLUTION`，跑到 `TIER_VIDEO_RESOLUTION['express']` 才 KeyError。
    而当时单元测试**全绿** —— 因为它们只检查各自表里的键，没有检查跨表一致性。

    现在合并成一个 `TIERS` 注册表，并在这里断言旧的多表常量确实不存在，
    防止有人（包括未来的我）再把它们拆回去。
    """

    def test_tiers_registry_is_the_only_source(self) -> None:
        import reelagent.routing as routing

        for legacy in ("TIER_RESOLUTION_SCALE", "TIER_VIDEO_RESOLUTION", "TIER_VIDEO_MODEL"):
            assert not hasattr(routing, legacy), f"{legacy} 又出现了，档位规格应当只在 TIERS 里"

    def test_every_spec_is_self_consistent(self) -> None:
        """每个规格的字段都要彼此自洽，不能出现矛盾组合。"""
        for tier, spec in TIERS.items():
            assert spec.tier == tier, "注册表的键与 TierSpec.tier 必须一致"
            assert spec.resolution in {"480P", "720P", "1080P"}
            assert spec.resolution_scale > 0
            if not spec.use_video_model:
                assert not spec.use_fast_model, "不调用视频模型时不可能使用优速版"

    def test_resolution_and_scale_agree(self) -> None:
        """分辨率档名与尺寸倍率必须指向同一档位，不能一个 480P 一个 1.4。"""
        from reelagent.providers.base import size_for
        from reelagent.providers.dashscope import resolution_tier

        for tier, spec in TIERS.items():
            actual = resolution_tier(size_for("9:16", spec.resolution_scale))
            assert actual == spec.resolution, (
                f"{tier}: scale={spec.resolution_scale} 实际算出 {actual}，"
                f"但 spec.resolution 声明 {spec.resolution}"
            )


class TestAllTiersAreConsistent:
    """四个档位必须都在注册表里，缺一个就会在运行时 KeyError。"""

    @pytest.mark.parametrize("tier", [TIER_DRAFT, TIER_STANDARD, TIER_PREMIUM, TIER_EXPRESS])
    def test_tier_present_in_all_tables(self, tier: str) -> None:
        assert tier in TIERS

    @pytest.mark.parametrize("tier", [TIER_DRAFT, TIER_STANDARD, TIER_PREMIUM, TIER_EXPRESS])
    def test_quote_works_for_every_tier(self, tier: str) -> None:
        settings = Settings()
        quote = credits.quote_project(settings, shots=2, allow_premium=True, tier=tier)
        assert quote.provider_cost_usd >= 0
        assert quote.credits >= 0

    def test_draft_has_zero_video_cost(self) -> None:
        """draft 不调用视频模型，视频部分应为 0。"""
        settings = Settings()
        quote = credits.quote_project(settings, shots=3, allow_premium=True, tier=TIER_DRAFT)
        # breakdown 里 video 换算成点数后应为 0
        assert quote.breakdown["video"] == 0


class TestTierOrdering:
    """成本必须随档位递增 —— 这是定价逻辑的基本不变量。"""

    def test_cost_order(self) -> None:
        settings = Settings()
        order = [TIER_DRAFT, TIER_STANDARD, TIER_PREMIUM, TIER_EXPRESS]
        costs = [
            credits.quote_project(settings, shots=3, allow_premium=True, tier=t).provider_cost_usd
            for t in order
        ]
        assert costs == sorted(costs), f"档位成本未单调递增：{dict(zip(order, costs, strict=True))}"
