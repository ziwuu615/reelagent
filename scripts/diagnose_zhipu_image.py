"""智谱图像接口原始响应诊断。

目的：搞清楚 `/images/generations` 到底是**同步返回图片**还是**返回任务 ID**。
适配器报的错误是「任务不存在」，这强烈暗示我误把同步响应里的
`request_id` 当成了异步任务 ID —— 但那只是猜测，必须看原始返回体确认。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://open.bigmodel.cn/api/paas/v4"


def load_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("REEL_PROVIDERS__LLM__API_KEY=") and line.split("=", 1)[1].strip():
            return line.split("=", 1)[1].strip()
    raise SystemExit("找不到 Key")


def show(label: str, resp: httpx.Response) -> None:
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    print(f"HTTP {resp.status_code}")
    try:
        body = resp.json()
    except Exception:
        print(resp.text[:500])
        return

    # 只展示结构骨架，避免把 base64 图片刷屏
    def skeleton(obj: object, depth: int = 0) -> object:
        if depth > 3:
            return "..."
        if isinstance(obj, dict):
            return {k: skeleton(v, depth + 1) for k, v in obj.items()}
        if isinstance(obj, list):
            return [skeleton(x, depth + 1) for x in obj[:2]] if obj else []
        if isinstance(obj, str) and len(obj) > 120:
            return f"<str len={len(obj)}>"
        return obj

    print(json.dumps(skeleton(body), ensure_ascii=False, indent=2)[:2000])

    # 关键判定
    print("\n--- 判定 ---")
    keys = set(body.keys())
    if "data" in keys and body["data"]:
        first = body["data"][0] if isinstance(body["data"], list) else {}
        print(f"  → 含 data 数组，字段：{sorted(first.keys()) if isinstance(first, dict) else type(first)}")
        print("  → **这是同步返回**，图片 URL 可直接取用，不应轮询")
    for k in ("id", "task_id", "request_id"):
        if k in body:
            print(f"  → 含 {k} = {body[k]}")
    if "task_status" in body:
        print(f"  → 含 task_status = {body['task_status']}")


def main() -> int:
    key = load_key()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    with httpx.Client(timeout=240) as client:
        # 1) cogview-4（上一轮报「任务不存在」的那个）
        show(
            "POST /images/generations  model=cogview-4",
            client.post(
                f"{BASE}/images/generations",
                headers=headers,
                json={"model": "cogview-4", "prompt": "测试图：一只简笔画的猫"},
            ),
        )

        # 2) glm-image（上一轮超时的那个）
        show(
            "POST /images/generations  model=glm-image",
            client.post(
                f"{BASE}/images/generations",
                headers=headers,
                json={"model": "glm-image", "prompt": "测试图：一只简笔画的猫", "size": "1024x1024"},
            ),
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
