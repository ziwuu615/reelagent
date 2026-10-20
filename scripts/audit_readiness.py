"""项目就绪度审计：逐项核对「已用真实数据验证」vs「仍是推断/未验证」。

回答「项目完善了吗」这种问题，不能凭感觉。这份脚本只做一件事：
把每一处需要外部事实支撑的地方列出来，标注证据来源。
没有证据的，就如实标成「未验证」。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reelagent.llm.base import DEFAULT_PRICE, MODEL_PRICES, price_for  # noqa: E402
from reelagent.providers.base import (  # noqa: E402
    CNY_PER_USD,
    estimate_image_cost,
    estimate_video_cost,
)

print("=" * 84)
print("1. LLM 单价表（是否与官方一致）")
print("=" * 84)
print(f"  DEFAULT_PRICE = {DEFAULT_PRICE}  (USD per 1M tokens, 输入/输出)")
print(f"  已知模型 {len(MODEL_PRICES)} 个：")
for model, (p_in, p_out) in list(MODEL_PRICES.items())[:8]:
    print(f"    {model:<34} in={p_in:<8} out={p_out}")
print()
print("  当前配置的 LLM 模型取到的价格：")
for m in ("glm-5.3-flash", "qwen-plus", "qwen-turbo", "glm-4-flash"):
    print(f"    {m:<22} -> {price_for(m)}")

print()
print("=" * 84)
print("2. 额度预估的 0.14/镜 vs 真实单镜成本")
print("=" * 84)
images = estimate_image_cost(5)
llm = 0.006
for tier, res, secs in (("standard", "480P", 4), ("premium", "720P", 5)):
    total = estimate_video_cost(secs, res) + images + llm
    print(f"  {tier:<10} 视频{secs}s {res} + 图5张 + LLM = ${total:.4f}")
    print(f"  {'':<10} 预估口径 0.14  ->  偏差 {total - 0.14:+.4f} ({(total / 0.14 - 1) * 100:+.0f}%)")

print()
print("=" * 84)
print("3. 预算上限 vs 真实多镜成本")
print("=" * 84)
for shots in (1, 3, 5):
    std = (estimate_video_cost(4, "480P") + images / 5) * shots
    pre = (estimate_video_cost(5, "720P") + images / 5) * shots
    print(
        f"  {shots} 镜  standard≈${std:.2f} ({std * CNY_PER_USD:.1f}元)   "
        f"premium≈${pre:.2f} ({pre * CNY_PER_USD:.1f}元)"
    )
print("  默认 job_budget_usd = 2.0  ->  超出即被路由降级或截断")

print()
print("=" * 84)
print("4. 真实运行记录（历史上跑过几个镜头、什么结果）")
print("=" * 84)
con = sqlite3.connect(ROOT / "workspace" / "cloud.db")
con.row_factory = sqlite3.Row
rows = con.execute(
    """SELECT p.id, p.title, p.total_shots, p.finished_shots, p.provider_cost_usd,
              p.status, j.payload
       FROM projects p LEFT JOIN jobs j ON j.project_id = p.id
       ORDER BY p.created_at DESC LIMIT 12"""
).fetchall()
max_shots = 0
for r in rows:
    try:
        tier = json.loads(r["payload"] or "{}").get("force_tier")
    except Exception:
        tier = None
    max_shots = max(max_shots, int(r["total_shots"] or 0))
    print(
        f"  {r['id'][:30]:<32} {r['total_shots']!s:>2}镜 "
        f"完成{r['finished_shots']!s:>2} tier={tier!s:<9} "
        f"${r['provider_cost_usd'] or 0:.4f} {r['status']}"
    )
print(f"\n  历史上最多跑过 {max_shots} 个镜头")
con.close()

print()
print("=" * 84)
print("5. 仍是推断 / 未验证的项")
print("=" * 84)
UNVERIFIED = [
    ("LLM 单价 MODEL_PRICES", "未核对官方定价页；compute_cost 依赖它"),
    ("额度预估 0.14/镜", "与真实成本偏差见第 2 节"),
    ("job_budget_usd = 2.0", "是否覆盖真实多镜成本，见第 3 节"),
    ("3 镜以上完整运行", f"历史最多 {max_shots} 镜，多镜路径未验证"),
    ("zhipu/ark 适配器的视频估价", "未传分辨率参数，仍是默认 480P 价"),
    ("真实部署（域名/HTTPS）", "从未实际部署过"),
]
for name, note in UNVERIFIED:
    print(f"  [未验证] {name}")
    print(f"           {note}")
