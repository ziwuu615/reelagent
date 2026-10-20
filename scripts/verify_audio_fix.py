"""验证音频映射修复在真实运行里是否生效。

判定标准（可量化，不靠"听"）：
  片段原生音轨 + TTS 混音后的结果，其平均音量应当**既不等于**原生音轨，
  也**不等于** TTS —— 因为它是两路按不同权重叠加的结果。

如果 final 的音量仍与原生音轨分毫不差，说明修复没生效（还是隐式选择）。
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = str(ROOT / ".venv/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe")


def stats(path: Path) -> tuple[float | None, float | None]:
    out = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    mean = re.search(r"mean_volume:\s*(-?[\d.]+)", out.stderr)
    dur = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", out.stderr)
    seconds = int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3)) if dur else None
    return (float(mean.group(1)) if mean else None), seconds


def main() -> None:
    con = sqlite3.connect(ROOT / "workspace" / "cloud.db")
    con.row_factory = sqlite3.Row
    proj = con.execute(
        "SELECT id, provider_cost_usd, finished_shots, total_shots "
        "FROM projects ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    pid = proj["id"]
    arts = con.execute("SELECT kind, storage_key FROM artifacts WHERE project_id=?", (pid,)).fetchall()
    con.close()

    storage = ROOT / "workspace" / "storage"

    def resolve(key: str) -> Path | None:
        p = storage / Path(key.replace("\\", "/"))
        if p.exists():
            return p
        found = [q for q in storage.rglob(Path(key).name) if pid in str(q)]
        return found[0] if found else None

    final = next((resolve(a["storage_key"]) for a in arts if a["storage_key"].endswith("final.mp4")), None)
    tts = next((resolve(a["storage_key"]) for a in arts if a["storage_key"].endswith("audio.wav")), None)
    clip = next(
        (
            resolve(a["storage_key"])
            for a in arts
            if a["storage_key"].endswith(".mp4") and "sc0" in a["storage_key"]
        ),
        None,
    )

    print("=" * 84)
    print(
        f"项目 {pid}   {proj['finished_shots']}/{proj['total_shots']} 镜   ${proj['provider_cost_usd']:.4f}"
    )
    print("=" * 84)

    rows = []
    for label, path in (("片段（原生音轨）", clip), ("audio.wav（TTS）", tts), ("final.mp4", final)):
        if path is None:
            print(f"  {label:<20} 未找到")
            continue
        mean, dur = stats(path)
        rows.append((label, mean, dur))
        print(f"  {label:<20} mean={mean}dB   dur={dur:.3f}s" if dur else f"  {label:<20} 无数据")

    print()
    print("  判定：")
    native = next((m for lb, m, _ in rows if "片段" in lb), None)
    voice = next((m for lb, m, _ in rows if "TTS" in lb), None)
    mixed = next((m for lb, m, _ in rows if "final" in lb), None)

    if mixed is None or native is None:
        print("    数据不足，无法判定")
        return

    same_as_native = abs(mixed - native) < 0.5
    differs_from_tts = voice is None or abs(mixed - voice) > 0.5

    print(f"    final 与原生音轨差 {abs(mixed - native):.2f}dB")
    if voice is not None:
        print(f"    final 与 TTS     差 {abs(mixed - voice):.2f}dB")
    print()
    if same_as_native:
        print("    => 仍与原生音轨一致：修复**未生效**或混音权重极低")
    else:
        print("    => 与原生音轨已不同：说明 TTS 参与了混音，修复**生效**")
        if differs_from_tts:
            print("       且与纯 TTS 也不同 —— 符合「两路叠加」的预期特征")


if __name__ == "__main__":
    main()
