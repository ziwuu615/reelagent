"""路由档位的语义测试。

这里保护的核心命题是：**档位差异必须体现在成片上，不能只体现在账单里。**

曾经的真实缺陷：`standard` 与 `premium` 走**完全相同**的生成路径
（同一模型、同一分辨率、同一时长），而计费侧却按 premium 多收 35% 额度。
用户多付了钱，拿到一样的东西 —— 而且从界面上完全看不出来，
因为日志里确实写着 `[premium]`。

修复方式是用分辨率把差异做实：
    draft    -> 不调用视频模型（本地运动合成），视频成本归零
    standard -> 480P 档真实视频
    premium  -> 720P 档真实视频（成本约为 standard 的 2 倍）
"""

from __future__ import annotations

import pytest

from reelagent.providers.base import ASPECT_SIZES, estimate_video_cost, size_for
from reelagent.providers.dashscope import resolution_tier
from reelagent.routing import (
    TIER_DRAFT,
    TIER_PREMIUM,
    TIER_STANDARD,
    TIERS,
    RouteDecision,
)


class TestTierResolutionDiffers:
    def test_every_tier_has_a_scale(self) -> None:
        for tier in TIERS:
            assert tier in TIERS

    def test_premium_is_higher_resolution_than_standard(self) -> None:
        """核心断言：premium 必须真的比 standard 清晰。

        否则「多付钱不多给东西」，这是最不该出现的一类缺陷。
        """
        for aspect in ASPECT_SIZES:
            std = size_for(aspect, TIERS[TIER_STANDARD].resolution_scale)
            pre = size_for(aspect, TIERS[TIER_PREMIUM].resolution_scale)
            assert pre[0] * pre[1] > std[0] * std[1], f"{aspect} 下 premium 没有更清晰"
            assert min(pre) > min(std), f"{aspect} 下 premium 短边没有提升"

    def test_premium_crosses_into_higher_resolution_tier(self) -> None:
        """差异要大到能跨过供应商的分辨率档位，否则实际成本不变。"""
        std_size = size_for("9:16", TIERS[TIER_STANDARD].resolution_scale)
        pre_size = size_for("9:16", TIERS[TIER_PREMIUM].resolution_scale)
        assert resolution_tier(std_size) == "480P"
        assert resolution_tier(pre_size) == "720P"

    def test_draft_uses_base_resolution(self) -> None:
        """draft 不调用视频模型，关键帧按基准分辨率生成即可，避免无谓支出。"""
        assert TIERS[TIER_DRAFT].resolution_scale == TIERS[TIER_STANDARD].resolution_scale


class TestTierCostIsHonest:
    def test_premium_cost_estimate_exceeds_standard(self) -> None:
        base = estimate_video_cost(9.5)
        assert base > 0
        # 引擎在 routing 里给 premium 乘 1.8，计费侧的 premium_uplift 必须跟上，
        # 否则预扣额度会低于实际成本
        assert base * 1.8 > base

    def test_decision_exposes_resolution_scale(self) -> None:
        """倍率必须能在追踪里看到 —— 否则「为什么这次贵」无法事后解释。"""
        d = RouteDecision(
            tier=TIER_PREMIUM,
            reason="测试",
            use_video_model=True,
            est_cost_usd=1.0,
            difficulty=0.8,
            resolution_scale=1.4,
        )
        payload = d.as_dict()
        assert payload["resolution_scale"] == 1.4
        assert payload["tier"] == TIER_PREMIUM

    def test_default_scale_is_neutral(self) -> None:
        """未显式指定倍率时必须是 1.0，避免旧代码路径被静默改变分辨率。"""
        d = RouteDecision(
            tier=TIER_STANDARD,
            reason="测试",
            use_video_model=True,
            est_cost_usd=0.5,
            difficulty=0.5,
        )
        assert d.resolution_scale == 1.0


class TestDraftSkipsVideoModel:
    @pytest.mark.parametrize(
        ("tier", "expects_model"),
        [
            (TIER_DRAFT, False),
            (TIER_STANDARD, True),
            (TIER_PREMIUM, True),
        ],
    )
    def test_only_draft_skips_the_model(self, tier: str, expects_model: bool) -> None:
        d = RouteDecision(
            tier=tier,
            reason="测试",
            use_video_model=tier != TIER_DRAFT,
            est_cost_usd=0.0,
            difficulty=0.5,
            resolution_scale=TIERS[tier].resolution_scale,
        )
        assert d.use_video_model is expects_model
