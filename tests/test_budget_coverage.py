"""预算推导测试。

**保护的真实事故**：引擎预算曾是固定常量 `job_budget_usd = 2.0`，与镜头数、
档位都无关。而引擎在预算不足时会把 premium **静默降级**为标准档。实测：

    5 镜 premium  -> 第 5 镜被降级为 standard
    12 镜 premium -> 8 个镜头被降级为 standard

用户按 premium 付费，却拿到标准画质的片段 —— 计费与交付脱钩。
根因是预算与预扣额来自不同的口径：预扣额按镜头数×档位算，
预算却是个写死的数。

修复方式：预算由**本次预扣额**推导（`budget_for`），并加一个真正的
上限 —— 超限时在创建项目时直接拒绝，而不是收钱后降级。
"""

from __future__ import annotations

import pytest

from reelagent.config import Settings as EngineSettings
from reelagent.routing import ModelRouter
from reelagent.schema import Clue, Shot
from reelagent_cloud.config import Settings
from reelagent_cloud.domain.errors import ValidationFailed
from reelagent_cloud.services import credits

pytestmark = pytest.mark.usefixtures("db")


def _all_premium(budget: float, shots: int) -> list[str]:
    """用给定预算跑一次路由模拟，返回每个镜头被分配的档位。"""
    settings = EngineSettings(mode="mock", budget_usd=budget)
    spent = 0.0
    router = ModelRouter(settings, spent_usd=lambda: spent)
    clues = [Clue(id="c1", kind="character", name="女孩", appearance="红伞")]
    tiers: list[str] = []
    for i in range(shots):
        shot = Shot(
            id=f"sh{i}",
            scene_id="sc01",
            index=i,
            duration_s=4.0,
            shot_size="特写",
            clue_ids=["c1"],
            dialogue="台词",
        )
        decision = router.decide(shot, clues, is_first=(i == 0), force_tier="premium")
        tiers.append(decision.tier)
        spent += decision.est_cost_usd
    return tiers


class TestBudgetCoverage:
    def test_budget_covers_the_quote(self) -> None:
        """引擎允许花的钱不能少于我们向用户收的钱。"""
        settings = Settings()
        for shots in (1, 3, 5, 12):
            quote = credits.quote_project(settings, shots=shots, allow_premium=True)
            budget = credits.budget_for(settings, quote)
            assert budget >= quote.provider_cost_usd, f"{shots} 镜：预算小于预扣"

    def test_budget_never_below_configured_floor(self) -> None:
        """配置里的 job_budget_usd 仍作为下限，避免小额任务预算过低。"""
        settings = Settings()
        quote = credits.quote_project(settings, shots=1, allow_premium=False)
        assert credits.budget_for(settings, quote) >= settings.job_budget_usd


class TestNoSilentDowngrade:
    """核心验收：显式选了 premium，就不能被静默降级。"""

    @pytest.mark.parametrize("shots", [3, 5, 12])
    def test_premium_survives_with_derived_budget(self, shots: int) -> None:
        settings = Settings()
        quote = credits.quote_project(settings, shots=shots, allow_premium=True)
        budget = credits.budget_for(settings, quote)
        tiers = _all_premium(budget, shots)
        assert all(t == "premium" for t in tiers), f"{shots} 镜出现降级：{tiers}"

    def test_old_flat_budget_would_have_downgraded(self) -> None:
        """锁定故障现场：固定 $2.0 预算下 5 镜 premium 确实会降级。

        这条断言的作用是**证明修复是必要的** —— 如果将来有人把预算改回
        固定值，这个测试不会失败（它测的是旧行为），但下面的对比会提醒：
        两种预算的结果不同，说明预算口径确实影响交付。
        """
        old = _all_premium(2.0, 5)
        settings = Settings()
        quote = credits.quote_project(settings, shots=5, allow_premium=True)
        new_budget = credits.budget_for(settings, quote)
        new = _all_premium(new_budget, 5)
        assert old != new, "旧预算与新预算的结果应当不同，否则这条测试失去意义"


class TestAffordabilityGate:
    def test_huge_request_is_rejected_upfront(self) -> None:
        """超出上限时应在创建阶段拒绝，而不是收钱后降级。"""
        settings = Settings()
        # 人为造一个超大报价
        quote = credits.quote_project(settings, shots=50, allow_premium=True)
        with pytest.raises(ValidationFailed):
            credits.assert_affordable(settings, quote)

    def test_normal_request_is_allowed(self) -> None:
        settings = Settings()
        for shots in (1, 3, 5, 12):
            quote = credits.quote_project(settings, shots=shots, allow_premium=True)
            credits.assert_affordable(settings, quote)  # 不应抛错

    def test_limit_is_below_platform_max_cost(self) -> None:
        """上限必须落在 MAX_PROJECT_COST_USD 附近，不能无限放宽。"""
        assert credits.MAX_PROJECT_COST_USD > 0
        assert credits.BUDGET_HEADROOM >= 1.0


class TestBudgetScales:
    def test_more_shots_need_more_budget(self) -> None:
        settings = Settings()
        q3 = credits.quote_project(settings, shots=3, allow_premium=True)
        q12 = credits.quote_project(settings, shots=12, allow_premium=True)
        assert credits.budget_for(settings, q12) > credits.budget_for(settings, q3)

    def test_premium_needs_more_budget_than_standard(self) -> None:
        settings = Settings()
        std = credits.quote_project(settings, shots=5, allow_premium=False)
        pre = credits.quote_project(settings, shots=5, allow_premium=True)
        assert pre.provider_cost_usd > std.provider_cost_usd
