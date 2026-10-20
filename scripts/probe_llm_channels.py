"""探测两条 LLM 通路的当前可用性，为 P0 跑批选定配置。

背景：`.env` 里 LLM 仍指向智谱（glm-5.3-flash），而图像/视频/TTS 已切百炼。
智谱此前报过 `1113 余额不足` —— 但那是**图像**调用报的，LLM 一直在正常计费。
所以「智谱能不能用」不能一概而论，必须分能力探测。

成本：只有 LLM token，约 $0.0001/次，可忽略。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROMPT = "用一句话描述雨夜的便利店，不超过 20 字。"


def env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _, value = stripped.partition("=")
            values[key.strip()] = value.strip()
    return values


def probe(label: str, provider: str, model: str, api_key: str, base_url: str = "") -> None:
    from reelagent.config import ProviderConfig, Settings
    from reelagent.llm import build_llm

    if not api_key:
        print(f"  {label:<28} 跳过：没有 Key")
        return

    settings = Settings(
        mode="live",
        llm=ProviderConfig(name=provider, model=model, api_key=api_key, base_url=base_url),
    )
    started = time.perf_counter()
    try:
        client = build_llm(settings)
        messages = [{"role": "user", "content": PROMPT}]
        result = client.chat(messages, max_tokens=60)
        elapsed = (time.perf_counter() - started) * 1000
        text = (result.text or "").strip().replace("\n", " ")[:40]
        print(f"  {label:<28} OK   {elapsed:>6.0f}ms  ${result.cost_usd:.6f}  输出={text!r}")
    except Exception as exc:
        elapsed = (time.perf_counter() - started) * 1000
        print(f"  {label:<28} 失败 {type(exc).__name__}: {str(exc)[:150]}")


def main() -> int:
    values = env()
    print("=" * 80)
    print("LLM 通路探测")
    print("=" * 80)

    # 当前配置：智谱
    probe(
        "智谱 glm-5.3-flash（当前）",
        "zhipu",
        values.get("REEL_PROVIDERS__LLM__MODEL", "glm-5.3-flash"),
        values.get("REEL_PROVIDERS__LLM__API_KEY", ""),
        values.get("REEL_PROVIDERS__LLM__BASE_URL", ""),
    )

    # 备选：百炼 qwen-plus（与图像/视频同一把 Key）
    ds_key = values.get("REEL_PROVIDERS__VIDEO__API_KEY", "")
    for model in ("qwen-plus", "qwen-turbo", "qwen-flash"):
        probe(
            f"百炼 {model}",
            "dashscope",
            model,
            ds_key,
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
