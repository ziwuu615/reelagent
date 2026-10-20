"""验证：预算由预扣额推导后，5 镜 premium 不再被静默降级。

这是本次修复的核心验收：
  修复前 —— 固定预算 $2.0，5 镜 premium 的第 5 镜被降级为 standard
  修复后 —— 预算 $2.73，5 镜全部保持 premium
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reelagent.config import Settings  # noqa: E402
from reelagent.routing import ModelRouter  # noqa: E402
from reelagent.schema import Clue, Shot  # noqa: E402
from reelagent_cloud.config import Settings as CloudSettings  # noqa: E402
from reelagent_cloud.services import credits  # noqa: E402


def simulate(budget: float, shots: int) -> tuple[list[str], float]:
    settings = Settings(mode="mock", budget_usd=budget)
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
    return tiers, spent


cloud = CloudSettings()

for shots in (3, 5, 12):
    quote = credits.quote_project(cloud, shots=shots, allow_premium=True)
    new_budget = credits.budget_for(cloud, quote)

    old_tiers, _ = simulate(2.0, shots)
    new_tiers, spent = simulate(new_budget, shots)

    old_ok = all(t == "premium" for t in old_tiers)
    new_ok = all(t == "premium" for t in new_tiers)

    print(f"{shots} 镜 premium")
    print(f"  预扣 ${quote.provider_cost_usd:.4f}")
    print(f"  旧预算 $2.00  -> 全部 premium? {old_ok}   档位={old_tiers}")
    print(f"  新预算 ${new_budget:.4f} -> 全部 premium? {new_ok}   档位={new_tiers}")
    print(f"  引擎累计估算 ${spent:.4f}")
    print()
