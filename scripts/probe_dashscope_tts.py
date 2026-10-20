"""探测百炼（DashScope）的 TTS 正确端点。

背景：OpenAI 兼容端点 `/compatible-mode/v1/audio/speech` 返回 404 ——
说明百炼没有实现 OpenAI 的音频合成协议，得走它自己的原生接口。
逐个试，让服务端告诉我们哪个对。
"""

from __future__ import annotations

import contextlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CANDIDATES = [
    (
        "原生多模态生成（qwen3-tts-flash 的官方路径）",
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        {
            "model": "qwen3-tts-flash",
            "input": {"text": "测试语音合成", "voice": "Cherry"},
        },
    ),
    (
        "原生语音合成（旧版路径）",
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2speech/speech-synthesis",
        {
            "model": "qwen-tts",
            "input": {"text": "测试语音合成", "voice": "Cherry"},
        },
    ),
    (
        "OpenAI 兼容 /audio/speech（已知 404，作对照）",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/audio/speech",
        {"model": "qwen3-tts-flash", "input": "测试语音合成", "voice": "Cherry"},
    ),
]


def load_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("REEL_PROVIDERS__VIDEO__API_KEY=") and line.split("=", 1)[1].strip():
            return line.split("=", 1)[1].strip()
    raise SystemExit("找不到百炼 Key")


def post(url: str, key: str, payload: dict) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", "ignore")[:420]
    except urllib.error.HTTPError as exc:
        body = ""
        with contextlib.suppress(Exception):
            body = exc.read().decode("utf-8", "ignore")[:420]
        return exc.code, body
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def main() -> int:
    key = load_key()
    winner = None
    for label, url, payload in CANDIDATES:
        status, body = post(url, key, payload)
        mark = "+" if status == 200 else "x"
        print(f"  {mark} {label}")
        print(f"      HTTP {status}  {body[:180]}")
        if status == 200 and winner is None:
            winner = (label, url)
    print()
    if winner:
        print(f"可用端点：{winner[0]}")
        print(f"  {winner[1]}")
        return 0
    print("全部不可用 —— 建议先关闭 TTS，让流水线不因音频失败而中断。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
