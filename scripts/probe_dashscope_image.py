"""验证现有的百炼 Key 能否生成图像（万相 t2i）。

目的：确认「同一把 Key 覆盖图像」这个前提成立，再动手写适配器。
盲写 300 行适配器结果发现 Key 没权限，是最浪费时间的做法。
"""

from __future__ import annotations

import contextlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://dashscope.aliyuncs.com/api/v1"

#: 万相图像生成的候选模型，从新到旧
CANDIDATES = [
    "wan2.5-t2i-preview",
    "wan2.2-t2i-flash",
    "wan2.2-t2i-plus",
    "wanx2.1-t2i-turbo",
]


def load_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("REEL_PROVIDERS__VIDEO__API_KEY=") and line.split("=", 1)[1].strip():
            return line.split("=", 1)[1].strip()
    raise SystemExit("找不到百炼 Key（REEL_PROVIDERS__VIDEO__API_KEY）")


def post(url: str, key: str, payload: dict, *, async_mode: bool) -> tuple[int, str]:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if async_mode:
        headers["X-DashScope-Async"] = "enable"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status, resp.read().decode("utf-8", "ignore")[:500]
    except urllib.error.HTTPError as exc:
        body = ""
        with contextlib.suppress(Exception):
            body = exc.read().decode("utf-8", "ignore")[:500]
        return exc.code, body
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def main() -> int:
    key = load_key()
    print(f"百炼 Key: {key[:14]}…{key[-6:]}")
    print()

    print("=" * 72)
    print("万相图像生成（同步端点 /services/aigc/text2image/image-synthesis）")
    print("=" * 72)
    url = f"{BASE}/services/aigc/text2image/image-synthesis"
    for model in CANDIDATES:
        status, body = post(
            url,
            key,
            {
                "model": model,
                "input": {"prompt": "雨夜便利店，霓虹灯，电影感"},
                "parameters": {"size": "1024*1024", "n": 1},
            },
            async_mode=True,
        )
        if status == 200:
            try:
                task = json.loads(body).get("output", {}).get("task_id", "?")
            except Exception:
                task = "?"
            print(f"  + {model:<22} HTTP 200  任务已建 {task}")
            print("\n  -> 这个 Key 可以做图像，我立刻写适配器")
            return 0
        # 提炼错误要点
        short = body[:150].replace("\n", " ")
        print(f"  x {model:<22} HTTP {status}  {short}")

    print()
    print("=" * 72)
    print("全部失败。可能原因：")
    print("  - 该 Key 未开通图像模型（需在百炼控制台「模型广场」开通）")
    print("  - 或模型名不对（控制台里能看到确切名称）")
    print("=" * 72)
    return 1


if __name__ == "__main__":
    sys.exit(main())
