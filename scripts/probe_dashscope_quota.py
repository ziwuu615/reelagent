"""探测百炼视频模型的额度状态。

误差来源提醒：上一次跑的引擎工作区已被清理，若诊断脚本随手取一个目录，
读到的会是**旧运行**的 span，从而把历史错误当成当前错误。
所以这里直接打接口，让服务端给出当下的答案。
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


def load_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("REEL_PROVIDERS__VIDEO__API_KEY=") and line.split("=", 1)[1].strip():
            return line.split("=", 1)[1].strip()
    raise SystemExit("找不到百炼 Key")


def post(url: str, key: str, payload: dict, *, async_mode: bool = True) -> tuple[int, str]:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if async_mode:
        headers["X-DashScope-Async"] = "enable"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status, resp.read().decode("utf-8", "ignore")[:500]
    except urllib.error.HTTPError as exc:
        body = ""
        # 读取错误体只为给出更清晰的判定，失败也不该掩盖状态码
        with contextlib.suppress(Exception):
            body = exc.read().decode("utf-8", "ignore")[:500]
        return exc.code, body
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def main() -> int:
    key = load_key()
    print(f"  百炼 Key: {key[:14]}…{key[-6:]}")
    print()

    print("=== 视频模型（wan3.0-video）===")
    status, body = post(
        f"{BASE}/services/aigc/video-generation/video-synthesis",
        key,
        {
            "model": "wan3.0-video",
            "input": {"prompt": "雨夜街道，镜头缓慢推进"},
            "parameters": {"resolution": "480P", "duration": 5},
        },
    )
    print(f"  HTTP {status}")
    print(f"  {body[:400]}")
    if "FreeTierOnly" in body or "Free quota" in body:
        print()
        print("  >>> 免费额度已用尽，且账号开启了「免费额度用完即停」。")
        print("      控制台里这个开关**不叫** 'use free tier only'（那是英文 API 原文），")
        print("      它叫「免费额度用完即停」，且**不在「费用与成本」里**，位置是：")
        print("      百炼控制台 -> 免费额度")
        print("      https://bailian.console.aliyun.com/cn-beijing/costing-balance/free-quota")
        print("      在「全部模型」表格里找到该模型，关闭该开关（或用批量操作）。")
        print("      注意：官方文档说明此开关只能在免费额度**完全消耗后**关闭；")
        print("      另外账号需绑定有效支付方式，否则操作会失败。")
    elif status == 200:
        print()
        print("  >>> 视频模型可用（额度充足）")
        try:
            tid = json.loads(body).get("output", {}).get("task_id")
            print(f"  task_id={tid}（这是一次真实扣费调用）")
        except Exception:
            pass

    print()
    print("=== 对照：图像模型（wan2.5-t2i-preview）===")
    status2, body2 = post(
        f"{BASE}/services/aigc/text2image/image-synthesis",
        key,
        {
            "model": "wan2.5-t2i-preview",
            "input": {"prompt": "雨夜霓虹灯"},
            "parameters": {"size": "768*768", "n": 1},
        },
    )
    print(f"  HTTP {status2}  {body2[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
