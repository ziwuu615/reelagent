"""对比「带 size」与「不带 size」的实际输出尺寸，确认宽高比能否指定。

上一层探测只能说明参数**被接受**（都是 200），但无法说明它**被采纳** ——
接口对不认识的参数常常静默忽略。所以必须轮询任务、把成片拉下来量尺寸。
这是唯一能区分「接口支持」与「接口假装支持」的办法。

成本：2 个任务 × 2 秒 ≈ $0.10。
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://dashscope.aliyuncs.com/api/v1"
SUBMIT = f"{BASE}/services/aigc/video-generation/video-synthesis"
PROMPT = "雨夜街道，镜头缓慢推进，霓虹灯倒映在积水里"

CASES = [
    ("现状：只发 resolution=480P", {"resolution": "480P", "duration": 2}),
    ("修法：resolution=480P + size=540*960", {"resolution": "480P", "duration": 2, "size": "540*960"}),
]


def load_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("REEL_PROVIDERS__VIDEO__API_KEY=") and line.split("=", 1)[1].strip():
            return line.split("=", 1)[1].strip()
    raise SystemExit("找不到百炼 Key")


def request(url: str, key: str, payload: dict | None, *, method: str = "POST") -> tuple[int, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if method == "POST":
        headers["X-DashScope-Async"] = "enable"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status, resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):
            detail = exc.read().decode("utf-8", "ignore")[:400]
        return exc.code, detail
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def probe_dims(path: Path) -> str:
    import subprocess

    ff = ROOT / ".venv/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe"
    out = subprocess.run(
        [str(ff), "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=60
    )
    import re

    for line in out.stderr.splitlines():
        if "Video:" in line:
            m = re.search(r"(\d{2,5})x(\d{2,5})", line)
            if m:
                w, h = int(m.group(1)), int(m.group(2))
                return f"{w}x{h}  (宽高比 {w / h:.4f})"
    return "未知"


def main() -> int:
    key = load_key()
    outdir = ROOT / "workspace" / "probe"
    outdir.mkdir(parents=True, exist_ok=True)

    tasks: list[tuple[str, str]] = []
    for label, params in CASES:
        status, body = request(
            SUBMIT,
            key,
            {"model": "wan3.0-video", "input": {"prompt": PROMPT}, "parameters": params},
        )
        if status != 200:
            print(f"  {label}: 提交失败 {status} {body[:140]}")
            continue
        tid = json.loads(body).get("output", {}).get("task_id")
        print(f"  {label}: 已提交 {tid}")
        tasks.append((label, str(tid)))

    print()
    print("  轮询中（2 秒视频通常 1-3 分钟）...")
    for label, tid in tasks:
        video_url = None
        for _ in range(60):
            time.sleep(10)
            st, body = request(f"{BASE}/tasks/{tid}", key, None, method="GET")
            if st != 200:
                continue
            out = json.loads(body).get("output") or {}
            state = str(out.get("task_status") or "").upper()
            if state in ("SUCCEEDED", "SUCCESS"):
                video_url = out.get("video_url")
                break
            if state in ("FAILED", "CANCELED"):
                print(f"  {label}: 失败 {str(out)[:160]}")
                break
        if not video_url:
            print(f"  {label}: 未拿到结果")
            continue
        dst = outdir / f"size_{abs(hash(label)) % 10000}.mp4"
        with urllib.request.urlopen(video_url, timeout=180) as resp, dst.open("wb") as fh:
            fh.write(resp.read())
        print(f"  {label}")
        print(f"      实际输出: {probe_dims(dst)}")
    print()
    print("  参考: 9:16 竖屏应为宽高比 0.5625；854x480 是 1.779（16:9 横屏）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
