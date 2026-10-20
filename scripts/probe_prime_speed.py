"""实测 wan3.0-video 与 wan3.0-video-prime 的差异。

官方对 prime 的描述是「在保持高质量视频生成能力的同时**大幅提升生成速度**」。
「大幅」是多少？宣传语不算数，必须实测。

方法：同一提示词、同一分辨率、同一时长，提交给两个模型，
记录从提交到任务完成的**墙钟时间**，并比对输出规格。

成本：2 个任务 × 2 秒 ≈ $0.21（480P：$0.041256 + $0.0636 ≈ $0.21）
"""

from __future__ import annotations

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
FFMPEG = str(ROOT / ".venv/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe")
PROMPT = "雨夜便利店门口，霓虹灯闪烁，一个女孩撑着红伞缓缓回头，镜头缓慢推进"

MODELS = ["wan3.0-video", "wan3.0-video-prime"]
RESOLUTION = "480P"
DURATION = 2


def load_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        key, _, value = line.strip().partition("=")
        if key == "REEL_PROVIDERS__VIDEO__API_KEY" and value.strip():
            return value.strip()
    raise SystemExit("找不到百炼 Key")


def call(url: str, key: str, payload: dict | None, *, method: str = "POST") -> tuple[int, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if method == "POST":
        headers["X-DashScope-Async"] = "enable"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):
            detail = exc.read().decode("utf-8", "ignore")[:300]
        return exc.code, detail
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def spec(path: Path) -> str:
    out = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=120
    )
    res = bitrate = dur = ""
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", out.stderr)
    if m:
        dur = f"{int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)):.2f}s"
    for line in out.stderr.splitlines():
        if "Video:" in line:
            r = re.search(r"(\d{2,5})x(\d{2,5})", line)
            if r:
                res = f"{r.group(1)}x{r.group(2)}"
            b = re.search(r"(\d+)\s*kb/s", line)
            if b:
                bitrate = f"{b.group(1)} kb/s"
    size_kb = path.stat().st_size // 1024
    return f"{res}  {bitrate}  {dur}  {size_kb} KB"


def main() -> int:
    key = load_key()
    outdir = ROOT / "workspace" / "probe" / "prime"
    outdir.mkdir(parents=True, exist_ok=True)

    results: dict[str, dict] = {}
    print(f"  提示词: {PROMPT[:34]}…")
    print(f"  规格: {RESOLUTION} / {DURATION}s")
    print()

    for model in MODELS:
        print(f"  提交 {model} …")
        time.perf_counter()
        status, body = call(
            SUBMIT,
            key,
            {
                "model": model,
                "input": {"prompt": PROMPT},
                "parameters": {"resolution": RESOLUTION, "ratio": "9:16", "duration": DURATION},
            },
        )
        if status != 200:
            print(f"    提交失败 HTTP {status}: {body[:220]}")
            results[model] = {"error": body[:220]}
            continue
        task_id = json.loads(body).get("output", {}).get("task_id")
        # 存**时间戳**，不要存时长。
        # 上一版写成 `perf_counter() - started`（时长），再在下面用它做基准相减，
        # 结果报出「69073 秒」这种荒谬数字 —— 单位对不上，测了等于没测。
        results[model] = {"task_id": task_id, "started_at": time.perf_counter()}
        print(f"    task_id={task_id}")

    print()
    print("  并行轮询（交替检查，两个任务的耗时互不包含）…")
    # 串行轮询会让第二个任务的耗时把第一个的等待也算进去 ——
    # 那样比较出来的"速度差"是假的。必须交替检查。
    pending = {m: i for m, i in results.items() if "task_id" in i}
    deadline = time.time() + 900
    while pending and time.time() < deadline:
        time.sleep(5)
        for model in list(pending):
            info = pending[model]
            st, body = call(f"{BASE}/tasks/{info['task_id']}", key, None, method="GET")
            if st != 200:
                continue
            out = json.loads(body).get("output") or {}
            state = str(out.get("task_status") or "").upper()
            if state in ("SUCCEEDED", "SUCCESS"):
                info["elapsed_s"] = time.perf_counter() - info["started_at"]
                url = out.get("video_url")
                if url:
                    dst = outdir / f"{model.replace('.', '_')}.mp4"
                    with urllib.request.urlopen(url, timeout=300) as resp, dst.open("wb") as fh:
                        fh.write(resp.read())
                    info["path"] = dst
                pending.pop(model)
                print(f"    {model} 完成，耗时 {info['elapsed_s']:.1f} 秒")
            elif state in ("FAILED", "CANCELED"):
                info["error"] = str(out)[:220]
                pending.pop(model)
                print(f"    {model} 失败: {info['error'][:100]}")

    for model in MODELS:
        if model in pending:
            results[model]["error"] = "轮询超时"

    print()
    print("=" * 88)
    print("实测结果")
    print("=" * 88)
    for model in MODELS:
        info = results[model]
        print(f"\n  {model}")
        if "error" in info:
            print(f"    失败: {info['error']}")
            continue
        elapsed = info.get("elapsed_s")
        print(f"    生成耗时: {elapsed:.1f} 秒" if elapsed else "    耗时: 未完成")
        if "path" in info:
            print(f"    输出规格: {spec(info['path'])}")

    ok = [m for m in MODELS if "elapsed_s" in results[m]]
    if len(ok) == 2:
        base_t = results["wan3.0-video"]["elapsed_s"]
        prime_t = results["wan3.0-video-prime"]["elapsed_s"]
        ratio = base_t / prime_t if prime_t else 0
        print()
        print(f"  prime 相对基础版: {'快' if ratio > 1 else '慢'} {abs(ratio - 1) * 100:.0f}%")
        print(f"    （基础版 {base_t:.1f}s，prime {prime_t:.1f}s）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
