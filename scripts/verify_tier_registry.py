"""验证档位配置收敛到 TierSpec 之后，各档位预估仍正确。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reelagent.routing import TIERS, tier_spec  # noqa: E402
from reelagent_cloud.config import Settings  # noqa: E402
from reelagent_cloud.services import credits  # noqa: E402

settings = Settings()

print("=" * 74)
print("档位规格（唯一来源：routing.TIERS）")
print("=" * 74)
print(f"  {'档位':<10}{'分辨率':>8}{'scale':>7}{'调模型':>8}{'优速版':>8}")
for spec in TIERS.values():
    print(
        f"  {spec.tier:<10}{spec.resolution:>8}{spec.resolution_scale:>7.1f}"
        f"{spec.use_video_model!s:>8}{spec.use_fast_model!s:>8}"
    )

print()
print("=" * 74)
print("5 镜预估（计价侧只查 TIERS，不再自己写分支）")
print("=" * 74)
print(f"  {'档位':<10}{'预扣(USD)':>12}{'预算(USD)':>12}{'人民币':>10}")
for tier in (*TIERS.keys(), None):
    label = tier or "auto"
    quote = credits.quote_project(settings, shots=5, allow_premium=True, tier=tier)
    budget = credits.budget_for(settings, quote)
    print(
        f"  {label:<10}{quote.provider_cost_usd:>12.4f}{budget:>12.4f}{quote.provider_cost_usd * 7.2:>9.1f}元"
    )

print()
print(f"  未知档位回落: tier_spec('nope').tier = {tier_spec('nope').tier}")
print(f"  None 回落    : tier_spec(None).tier = {tier_spec(None).tier}")
