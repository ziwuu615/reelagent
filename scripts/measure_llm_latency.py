"""测量引擎在当前 LLM 供应商上的真实延迟与成本。

用途：选择供应商时，「一次 planner 调用要多久、花多少钱」比跑分更有决策价值。
实测数据（智谱 glm-5.3-flash，planner 尺寸请求）：**96 秒**。
这个数字解释了「端到端看起来卡住」的现象 —— 它只是在等模型吐完一大段 JSON，
而不是故障。没有这个基线，很容易误判成死锁并去改代码。

用法：
    python scripts/measure_llm_latency.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reelagent.config import Settings  # noqa: E402
from reelagent.llm import build_llm  # noqa: E402
from reelagent.llm.base import Message  # noqa: E402
from reelagent.llm.prompts import planner_user  # noqa: E402

SEED = "一个女孩在雨夜便利店门口等人，霓虹灯闪烁，她手里拿着一把红色的伞。"


def timed(label: str, client: object, system: str, user: str, max_tokens: int) -> None:
    messages = [
        Message(role="system", content=system),
        Message(role="user", content=user),
    ]
    started = time.perf_counter()
    try:
        resp = client.chat(messages, max_tokens=max_tokens)  # type: ignore[attr-defined]
    except Exception as exc:
        print(
            f"  NG  {label:<14} {time.perf_counter() - started:6.1f}s  {type(exc).__name__}: {str(exc)[:220]}"
        )
        return

    elapsed = time.perf_counter() - started
    text = getattr(resp, "text", "") or ""
    usage = getattr(resp, "usage", None)
    cost = getattr(resp, "cost_usd", None)
    print(f"  OK  {label:<14} {elapsed:6.1f}s  输出 {len(text):>5} 字符  usage={usage}  cost={cost}")
    if text:
        print(f"      前 150 字符: {text[:150]!r}")


def main() -> int:
    settings = Settings.load()
    client = build_llm(settings)

    print(f"供应商   : {settings.llm.base_url}")
    print(f"模型     : {settings.llm.model}")
    print(f"客户端   : {type(client).__name__}")
    print(f"超时     : {getattr(client, 'timeout', '?')}s")
    print(f"最大重试 : {getattr(client, 'max_retries', '?')}")
    print()

    timed("最小请求", client, "你是助手。", "回复：OK", 16)
    timed(
        "planner 尺寸",
        client,
        "你是专业短视频编剧，只输出 JSON。",
        planner_user(SEED, "cinematic", "16:9", 2),
        4000,
    )

    print()
    print(f"累计 tokens: {getattr(client, 'total_usage', '?')}")
    print(f"累计成本   : ${getattr(client, 'total_cost_usd', 0.0):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
