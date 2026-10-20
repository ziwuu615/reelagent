"""探测 DashScope 视频接口是否接受显式尺寸 / 宽高比。

背景：现有适配器只发 `{"resolution": "480P"}`，把宽高比丢了，
导致 9:16 竖屏项目产出 854x480 横屏视频。要修就得知道接口支持什么参数。

注意成本：每次提交都是真实扣费。用 2 秒（最小）把成本压到约 $0.05/次。
"""

from __future__ import annotations

import contextlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"


def load_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("REEL_PROVIDERS__VIDEO__API_KEY=") and line.split("=", 1)[1].strip():
            return line.split("=", 1)[1].strip()
    raise SystemExit("找不到百炼 Key")


def submit(key: str, params: dict, *, label: str) -> tuple[int, str]:
    payload = {
        "model": "wan3.0-video",
        "input": {"prompt": "雨夜街道，镜头缓慢推进，霓虹灯倒映在积水里"},
        "parameters": params,
    }
    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = resp.read().decode("utf-8", "ignore")
            return resp.status, body
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):
            detail = exc.read().decode("utf-8", "ignore")[:400]
        return exc.code, detail
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def main() -> int:
    key = load_key()
    # 只探测「参数是否被接受」，不等结果 —— 结果尺寸由轮询才拿得到，
    # 但参数被拒会立刻返回 400，所以这一层就足够判断。
    cases = [
        ("仅 resolution（现状）", {"resolution": "480P", "duration": 2}),
        ("resolution + size 星号", {"resolution": "480P", "duration": 2, "size": "540*960"}),
        ("resolution + size 大写X", {"resolution": "480P", "duration": 2, "size": "540X960"}),
        ("仅 size（不带 resolution）", {"duration": 2, "size": "540*960"}),
    ]
    print("探测参数接受情况（参数被拒会返回 400）：")
    print()
    first_task = None
    for label, params in cases:
        status, body = submit(key, params, label=label)
        hint = ""
        if status == 200:
            with contextlib.suppress(Exception):
                first_task = json.loads(body).get("output", {}).get("task_id")
            hint = "已接受"
        else:
            try:
                err = json.loads(body)
                hint = str(err.get("message") or err.get("code") or body)[:110]
            except Exception:
                hint = body[:110]
        print(f"  {label:<28} HTTP {status}  {hint}")
    print()
    if first_task:
        print(f"  可用于轮询的 task_id: {first_task}")
        print("  （若要确认实际输出尺寸，需要再轮询该任务）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
