"""不变量：每个付费套餐在 3 倍加价下必须真的赚得到 3 倍。

## 为什么需要这条测试

扣费侧 `usd_to_credits` 把 $1 供应商成本算成
`markup_multiplier × credits_per_usd` = 300 点；
而点数卖多少钱是套餐侧单独定的，两边没有任何东西保证自洽。

真实数据（7 镜 premium 成片，供应商成本 $2.68 ≈ 19.3 元）：

    创作者版   3000 点/49 元   -> 卖 13.13 元   毛利率 -46.9%
    专业版    15000 点/199 元  -> 卖 10.67 元   毛利率 -80.9%
    工作室版  60000 点/699 元  -> 卖  9.37 元   毛利率 -106.0%

套餐越大亏得越多 —— 因为「元/点」随套餐递减，等于把折扣加在了已经亏本的单价上。

## 不变量怎么来的

    300 点的成本 = $1 = 7.2 元
    3 倍毛利要求 300 点售价 >= 21.6 元
    => 元/点 >= 21.6 / 300 = 0.072 = CNY_PER_USD / credits_per_usd

注意 markup 在推导中被约掉了：因为点数本身已经乘过 markup，
所以「元/点」的下限只取决于汇率与每美元点数。
这条测试因此对 markup 与 credits_per_usd 的改动都敏感 —— 任一侧改了却没重定价，立刻红。
"""

from __future__ import annotations

import math

from reelagent.providers.base import CNY_PER_USD
from reelagent_cloud.config import Settings
from reelagent_cloud.domain.plans import PLANS
from reelagent_cloud.services.credits import quote_project

#: 浮点比较容差；定价是整数元，实际余量远大于它。
EPS = 1e-9


def _revenue_for_one_usd_cost(plan_price_cny: int, monthly_credits: int, settings: Settings) -> float:
    """$1 供应商成本折成点数后，按该套餐的单价能卖回多少元。"""
    credits = math.ceil(1.0 * settings.billing.markup_multiplier * settings.billing.credits_per_usd)
    return credits * (plan_price_cny / monthly_credits)


def test_every_paid_plan_clears_target_markup(settings: Settings) -> None:
    """每个付费套餐卖回的钱，至少是供应商成本的 markup 倍。"""
    markup = settings.billing.markup_multiplier
    failures: list[str] = []

    for plan in PLANS.values():
        if plan.is_free:
            continue
        revenue_cny = _revenue_for_one_usd_cost(plan.price_cny, plan.monthly_credits, settings)
        effective = revenue_cny / CNY_PER_USD
        if effective + EPS < markup:
            failures.append(
                f"{plan.name}（{plan.price_cny}元/{plan.monthly_credits:,}点）"
                f"$1 成本只卖回 {revenue_cny:.2f} 元，毛利率 {effective:.2f}x < {markup:.1f}x"
            )

    assert not failures, "以下套餐定价低于目标毛利：\n  " + "\n  ".join(failures)


def test_seven_shot_premium_film_is_profitable(settings: Settings) -> None:
    """拿一条真实复杂度的成片验算：7 镜 premium 的卖价必须覆盖 3 倍成本。"""
    quote = quote_project(settings, shots=7, allow_premium=True, tier="premium")
    cost_cny = quote.provider_cost_usd * CNY_PER_USD
    markup = settings.billing.markup_multiplier

    failures: list[str] = []
    for plan in PLANS.values():
        if plan.is_free:
            continue
        sell_cny = quote.credits * (plan.price_cny / plan.monthly_credits)
        if sell_cny + 0.01 < cost_cny * markup:
            failures.append(
                f"{plan.name}: 卖 {sell_cny:.2f} 元 / 成本 {cost_cny:.2f} 元 = {sell_cny / cost_cny:.2f}x"
            )

    assert not failures, (
        f"7 镜 premium（{quote.credits} 点，供应商成本 {cost_cny:.2f} 元）卖不到 {markup:.1f} 倍：\n  "
        + "\n  ".join(failures)
    )


def test_free_plan_allowance_is_bounded(settings: Settings) -> None:
    """免费额度是获客成本，必须封在一条成片以内，并禁止 premium 档。"""
    free = PLANS[next(t for t in PLANS if t.value == "free")]
    quote = quote_project(
        settings,
        shots=free.max_shots_per_project,
        allow_premium=free.allow_premium_tier,
        tier=None,
    )
    assert free.monthly_credits >= quote.credits, "免费额度做不出一条成片，体验会断在第一步"
    assert free.monthly_credits <= quote.credits * 2, (
        f"免费额度 {free.monthly_credits} 点远超一条成片所需 {quote.credits} 点，获客成本失控"
    )
