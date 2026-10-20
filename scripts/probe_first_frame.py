"""验证 wan3.0-video 的正确首帧传法，以及 ratio=adaptive 是否生效。

背景：适配器一直用旧写法的 `input.img_url` 传关键帧，而文档要求
`input.media: [{"type": "first_frame", "url": ...}]`。参数名不对时接口
**返回 200 但静默忽略** —— 于是：
  1. 关键帧没被当首帧用（跨镜头一致性其实是空的）
  2. 缺 `ratio` 导致输出宽高比不受控（480P 出 854x480 横屏）

这个探测要同时确认两件事：base64 能否用于 media[].url，以及输出是否跟随首帧比例。
"""

from __future__ import annotations

import base64
import contextlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://dashscope.aliyuncs.com/api/v1"
SUBMIT = f"{BASE}/services/aigc/video-generation/video-synthesis"
FFMPEG = ROOT / ".venv/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe"


def load_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("REEL_PROVIDERS__VIDEO__API_KEY=") and line.split("=", 1)[1].strip():
            return line.split("=", 1)[1].strip()
    raise SystemExit("找不到百炼 Key")


def find_portrait_keyframe() -> Path | None:
    """找一个 9:16 的关键帧作为首帧。"""
    from PIL import Image

    storage = ROOT / "workspace" / "storage"
    for p in sorted(storage.rglob("sc01_sh01.png"), key=lambda x: x.stat().st_mtime, reverse=True):
        with contextlib.suppress(Exception), Image.open(p) as im:
            if im.size[1] > im.size[0]:  # 竖屏
                return p
    return None


def call(url: str, key: str, payload: dict | None, *, method: str = "POST") -> tuple[int, str]:
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
        # 读取错误体只为给出更清晰的判定，失败也不该掩盖状态码
        with contextlib.suppress(Exception):
            detail = exc.read().decode("utf-8", "ignore")[:400]
        return exc.code, detail
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def dims(path: Path) -> str:
    out = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=60
    )
    for line in out.stderr.splitlines():
        if "Video:" in line:
            m = re.search(r"(\d{2,5})x(\d{2,5})", line)
            if m:
                w, h = int(m.group(1)), int(m.group(2))
                return f"{w}x{h} (比例 {w / h:.4f})"
    return "未知"


def main() -> int:
    key = load_key()
    kf = find_portrait_keyframe()
    if kf is None:
        print("  找不到竖屏关键帧，跳过")
        return 0
    from PIL import Image

    with Image.open(kf) as im:
        src = im.size
    print(f"  首帧: {kf.name}  {src[0]}x{src[1]} (比例 {src[0] / src[1]:.4f})")

    b64 = base64.b64encode(kf.read_bytes()).decode()
    uri = f"data:image/png;base64,{b64}"

    payload = {
        "model": "wan3.0-video",
        "input": {
            "prompt": "女孩撑着红伞在雨夜街道回头，霓虹灯倒映在积水里，镜头缓慢推进",
            "media": [{"type": "first_frame", "url": uri}],  # 文档要求的正确结构
        },
        "parameters": {
            "resolution": "480P",
            "ratio": "adaptive",  # 让模型跟随首帧宽高比
            "duration": 2,
        },
    }
    print("  提交（media[].first_frame + ratio=adaptive, base64）...")
    status, body = call(SUBMIT, key, payload)
    if status != 200:
        print(f"  提交失败 HTTP {status}: {body[:300]}")
        return 1
    tid = json.loads(body).get("output", {}).get("task_id")
    print(f"  task_id={tid}")

    video_url = None
    for _ in range(60):
        time.sleep(10)
        st, b = call(f"{BASE}/tasks/{tid}", key, None, method="GET")
        if st != 200:
            continue
        out = json.loads(b).get("output") or {}
        state = str(out.get("task_status") or "").upper()
        if state in ("SUCCEEDED", "SUCCESS"):
            video_url = out.get("video_url")
            break
        if state in ("FAILED", "CANCELED"):
            print(f"  任务失败: {str(out)[:300]}")
            return 1
    if not video_url:
        print("  未拿到结果")
        return 1

    dst = ROOT / "workspace" / "probe" / "first_frame_adaptive.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(video_url, timeout=180) as resp, dst.open("wb") as fh:
        fh.write(resp.read())

    print()
    print(f"  输入首帧: {src[0]}x{src[1]}  比例 {src[0] / src[1]:.4f}")
    print(f"  输出视频: {dims(dst)}")
    print()
    print("  判定：输出比例与首帧一致 => media/ratio 写法正确且生效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
