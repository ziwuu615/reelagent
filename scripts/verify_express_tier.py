"""验证 express 档的价格与预扣是否正确。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reelagent.providers.base import VIDEO_PRICE_PER_S, VIDEO_PRICE_PER_S_FAST  # noqa: E402
from reelagent.routing import (  # noqa: E402
    TIER_DRAFT,
    TIER_EXPRESS,
    TIER_PREMIUM,
    TIER_STANDARD,
    TIERS,
)
from reelagent_cloud.config import Settings  # noqa: E402
from reelagent_cloud.services import credits  # noqa: E402

print("=" * 80)
print("官方单价（华北2 北京）")
print("=" * 80)
print(f"  {'分辨率':<8}{'基础版/s':>12}{'优速版/s':>12}{'溢价':>8}")
for res in ("480P", "720P", "1080P"):
    base = VIDEO_PRICE_PER_S[res]
    fast = VIDEO_PRICE_PER_S_FAST[res]
    print(f"  {res:<8}{base:>12.6f}{fast:>12.6f}{fast / base:>7.2f}x")

print()
print("=" * 80)
print("四个档位的配置")
print("=" * 80)
print(f"  {'档位':<10}{'分辨率':>8}{'scale':>8}{'模型':>10}")
for tier in (TIER_DRAFT, TIER_STANDARD, TIER_PREMIUM, TIER_EXPRESS):
    model = (
        "优速版"
        if TIERS[tier].use_fast_model
        else ("不调用" if not TIERS[tier].use_video_model else "基础版")
    )
    print(f"  {tier:<10}{TIERS[tier].resolution:>8}{TIERS[tier].resolution_scale:>8.1f}{model:>10}")

print()
print("=" * 80)
print("5 镜项目的预估（实测校准后的公式）")
print("=" * 80)
settings = Settings()
print(f"  {'档位':<10}{'预扣(USD)':>12}{'预算(USD)':>12}{'折人民币':>12}")
for tier in (TIER_DRAFT, TIER_STANDARD, TIER_PREMIUM, TIER_EXPRESS):
    quote = credits.quote_project(settings, shots=5, allow_premium=True, tier=tier)
    budget = credits.budget_for(settings, quote)
    print(
        f"  {tier:<10}{quote.provider_cost_usd:>12.4f}{budget:>12.4f}{quote.provider_cost_usd * 7.2:>11.1f}元"
    )

print()
print("  说明：express 与 premium 同分辨率（720P），贵在模型更快的部分。")
