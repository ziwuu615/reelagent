"""额度预估的准确性测试。

**这个文件保护的是一个真实事故**：`quote_project` 曾用一个凭印象填的
「每镜均价 $0.14」常量，用真实运行数据核对后发现：

    standard 单镜真实成本  $0.31   （0.14 低估 121%）
    premium  单镜真实成本  $0.56   （0.14 低估 298%）

它之所以没出事，是因为上面还叠了 `× 1.8` 上浮和 `× 1.15` 安全系数 ——
**是巧合掩盖了错误，不是估对了**。一旦有人调低那些系数，用户就会在
生成中途被判定额度不足。

现在预估改为按已验证的官方单价逐项计算，这里断言它落在真实成本的合理区间。
"""

from __future__ import annotations

import pytest

from reelagent_cloud.config import Settings
from reelagent_cloud.services import credits

pytestmark = pytest.mark.usefixtures("db")

#: 真实运行观测到的整单成本（USD）。
#: 3 镜那条是在**修复定价之后**跑的，最可信。
OBSERVED = {
    ("standard", 3): 0.6109,
    ("standard", 1): 0.3158,
}


def _quote(shots: int, *, premium: bool = False, model: str | None = None) -> float:
    settings = Settings()
    if model:
        settings.providers.llm.model = model
    quote = credits.quote_project(settings, shots=shots, allow_premium=premium)
    return quote.provider_cost_usd


class TestQuoteMatchesObserved:
    """预估必须接近真实成本，且**偏高**而非偏低。"""

    @pytest.mark.parametrize(("tier", "shots"), list(OBSERVED))
    def test_within_reasonable_band(self, tier: str, shots: int) -> None:
        estimated = _quote(shots, premium=tier == "premium")
        real = OBSERVED[(tier, shots)]
        deviation = estimated / real - 1
        # 允许 -10% ~ +40%：偏低危险（用户中途断粮），偏高可接受（只是多预留）
        assert -0.10 <= deviation <= 0.40, f"预估 ${estimated:.4f} 与实测 ${real:.4f} 偏差 {deviation:+.0%}"

    def test_never_wildly_under_estimates(self) -> None:
        """核心断言：宁可多预留，绝不能像旧实现那样低估 2–4 倍。"""
        for (tier, shots), real in OBSERVED.items():
            estimated = _quote(shots, premium=tier == "premium")
            assert estimated >= real * 0.9, f"{tier} {shots} 镜预估 ${estimated:.4f} 明显低于实测 ${real:.4f}"


class TestQuoteScalesSensibly:
    def test_more_shots_cost_more(self) -> None:
        assert _quote(1) < _quote(3) < _quote(6)

    def test_premium_costs_more_than_standard(self) -> None:
        assert _quote(3) < _quote(3, premium=True)

    def test_zero_shots_is_not_free(self) -> None:
        """即使 0 镜，也会有 planner 与基准图开销 —— 不能算出 0。"""
        assert _quote(0) > 0


class TestQuoteUsesVerifiedPrices:
    """预估必须跟随价格表，而不是写死的常量。"""

    def test_cheap_model_lowers_llm_portion(self) -> None:
        """LLM 在总额里占比很小，但换便宜模型应让总额不升高。"""
        expensive = _quote(3, model="qwen-max")
        cheap = _quote(3, model="qwen-plus")
        assert cheap <= expensive

    def test_unknown_model_uses_fallback_not_crash(self) -> None:
        assert _quote(2, model="some-model-that-does-not-exist") > 0

    def test_breakdown_has_all_capabilities(self) -> None:
        settings = Settings()
        quote = credits.quote_project(settings, shots=2, allow_premium=False)
        assert {"image", "video", "tts", "llm"} <= set(quote.breakdown)
        assert all(v >= 0 for v in quote.breakdown.values())
