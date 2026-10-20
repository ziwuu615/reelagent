"""定价常量测试。

**这个文件的存在理由**：成本估算直接影响预算保护与额度预扣，
估低了用户会在跑到一半时被判额度不足，估高了会多扣钱。
而这些常量很容易被"顺手改一下"而没人发现 —— 所以在这里钉死，
并且每个断言都带**官方来源**，改动时必须同步更新来源说明。

真实事故：视频单价曾经只有一个 `0.045` 的常量，且估算函数不收分辨率参数。
于是 720P 按 480P 计价，低估约 45%；实测用户花了约 19 元，
而系统内部记录的估算只有约 13 元 —— 差额无法解释，直到逐项核对官方定价才发现。
"""

from __future__ import annotations

import pytest

from reelagent.providers.base import (
    CNY_PER_USD,
    IMAGE_UNIT_COST,
    TTS_UNIT_COST_PER_1K_CHAR,
    VIDEO_PRICE_PER_S,
    estimate_image_cost,
    estimate_tts_cost,
    estimate_video_cost,
)


class TestRatesMatchOfficialPricing:
    """每个断言对应一条官方定价页上的原文。"""

    def test_image_price_matches_official(self) -> None:
        """百炼 wan2.5-t2i-preview：0.2 元/张。

        来源：https://help.aliyun.com/zh/model-studio/wan2-5-t2i
        """
        assert pytest.approx(0.2, abs=1e-6) == IMAGE_UNIT_COST * CNY_PER_USD

    def test_tts_price_matches_official(self) -> None:
        """百炼 qwen3-tts-flash：0.8 元/万字符 = 0.08 元/千字符。

        来源：https://help.aliyun.com/zh/model-studio/qwen3-tts-flash
        """
        assert pytest.approx(0.08, abs=1e-6) == TTS_UNIT_COST_PER_1K_CHAR * CNY_PER_USD

    @pytest.mark.parametrize(
        ("resolution", "usd_per_second"),
        [
            ("480P", 0.041256),
            ("720P", 0.082513),
            ("1080P", 0.165025),
        ],
    )
    def test_video_price_matches_official(self, resolution: str, usd_per_second: float) -> None:
        """百炼 wan3.0-video（华北2 北京）按分辨率计价。

        来源：https://www.alibabacloud.com/help/en/model-studio/wan3-0-video
        """
        assert VIDEO_PRICE_PER_S[resolution] == pytest.approx(usd_per_second, abs=1e-9)


class TestVideoPriceScalesWithResolution:
    """价差必须体现在估算里 —— 这是本文件要保护的核心命题。"""

    def test_higher_resolution_costs_more(self) -> None:
        low = estimate_video_cost(5, "480P")
        mid = estimate_video_cost(5, "720P")
        high = estimate_video_cost(5, "1080P")
        assert low < mid < high

    def test_720p_costs_about_double_480p(self) -> None:
        """官方定价里 720P **约**为 480P 的两倍。

        注意是「约」：官方数值是 0.041256 / 0.082513，
        精确 2 倍应为 0.082512 —— 官方多出 0.000001（定价页自身的取整）。
        断言写成精确相等会失败。**断言要反映真实数值，而不是我们希望的关系。**
        """
        assert estimate_video_cost(5, "720P") == pytest.approx(estimate_video_cost(5, "480P") * 2, rel=1e-4)

    def test_1080p_costs_about_quadruple_480p(self) -> None:
        """同样只是「约 4 倍」：0.165025 对比精确值 0.165024。"""
        assert estimate_video_cost(5, "1080P") == pytest.approx(estimate_video_cost(5, "480P") * 4, rel=1e-4)

    def test_default_resolution_is_the_cheapest_tier(self) -> None:
        """不传分辨率时按 480P，且绝不能因此把贵档位算便宜。"""
        assert estimate_video_cost(5) == estimate_video_cost(5, "480P")

    def test_unknown_resolution_falls_back_to_480p(self) -> None:
        assert estimate_video_cost(5, "4K") == estimate_video_cost(5, "480P")

    def test_resolution_is_case_insensitive(self) -> None:
        assert estimate_video_cost(5, "720p") == estimate_video_cost(5, "720P")


class TestEstimatorsAreLinear:
    def test_video_scales_with_duration(self) -> None:
        assert estimate_video_cost(10, "720P") == pytest.approx(estimate_video_cost(5, "720P") * 2, rel=1e-9)

    def test_image_scales_with_count(self) -> None:
        assert estimate_image_cost(5) == pytest.approx(IMAGE_UNIT_COST * 5)

    def test_tts_scales_with_length(self) -> None:
        assert estimate_tts_cost("字" * 2000) == pytest.approx(TTS_UNIT_COST_PER_1K_CHAR * 2)

    def test_tts_empty_text_is_free(self) -> None:
        assert estimate_tts_cost("") == 0.0

    def test_duration_has_a_floor(self) -> None:
        """极短时长也按最小计费单位算，避免出现 0 成本。"""
        assert estimate_video_cost(0, "480P") > 0


class TestRealWorldRunEstimate:
    """整单估算 — 与实测对账。

    2026-10 实测：一次 premium 单镜头（5 秒 720P + 5 张图 + 少量 TTS）
    在百炼账户上约 4.2 元。这里断言估算落在同一量级，
    避免出现"估算说几毛、实际花几块"这种情况。
    """

    def test_premium_single_shot_estimate_matches_observed(self) -> None:
        video = estimate_video_cost(5, "720P")
        images = estimate_image_cost(5)
        tts = estimate_tts_cost("字" * 3000)
        total_cny = (video + images + tts) * CNY_PER_USD
        assert 3.0 < total_cny < 6.0, f"整单估算 {total_cny:.2f} 元与实测量级不符"
