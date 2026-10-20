"""核对代码里的单价与官方定价页是否一致。

每个数字都必须能追溯到官方来源。这里把「代码值」与「官方原文」并排打印，
不一致就说明有人改了代码没同步文档（或反过来）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reelagent.providers.base import (  # noqa: E402
    CNY_PER_USD,
    IMAGE_UNIT_COST,
    TTS_UNIT_COST_PER_1K_CHAR,
    VIDEO_PRICE_PER_S,
    estimate_video_cost,
)

print(f"  汇率 CNY_PER_USD = {CNY_PER_USD}（用于把元价换算成美元）")
print()
print(f"  {'项目':<24}{'代码值(USD)':>14}{'官方原文':>26}")
print("  " + "-" * 66)

rows = [
    ("图像 wan2.5-t2i-preview", IMAGE_UNIT_COST, "0.2 元/张"),
    ("TTS qwen3-tts-flash", TTS_UNIT_COST_PER_1K_CHAR, "0.8 元/万字符"),
    ("视频 480P（每秒）", VIDEO_PRICE_PER_S["480P"], "0.041256 USD/s"),
    ("视频 720P（每秒）", VIDEO_PRICE_PER_S["720P"], "0.082513 USD/s"),
    ("视频 1080P（每秒）", VIDEO_PRICE_PER_S["1080P"], "0.165025 USD/s"),
]
for name, value, source in rows:
    print(f"  {name:<24}{value:>14.6f}{source:>26}")

print()
print("  换算核对：")
img_cny = IMAGE_UNIT_COST * CNY_PER_USD
tts_cny = TTS_UNIT_COST_PER_1K_CHAR * CNY_PER_USD
print(f"    图像 每张        {img_cny:.4f} 元   （官方 0.2）")
print(f"    TTS  每千字符    {tts_cny:.4f} 元   （官方 0.08）")
print()
print("  派生值：")
print("    一次 premium（5 秒视频 + 5 张图 + TTS）估算：")
total = estimate_video_cost(5, "720P") + 5 * IMAGE_UNIT_COST + 3 * TTS_UNIT_COST_PER_1K_CHAR
print(f"      ${total:.4f}  ≈ {total * CNY_PER_USD:.2f} 元")
