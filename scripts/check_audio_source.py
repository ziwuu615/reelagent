"""验证成片实际用的是哪条音轨：片段的原生音频，还是我的 TTS。

方法：对同一个项目，分别测
  - final.mp4 的音轨平均音量
  - 该项目 audio.wav（TTS 拼接结果）的平均音量
  - 该项目片段 mp4 的音轨平均音量
如果 final 与片段接近而与 TTS 差异大，说明 TTS 被丢弃了。

这不是"听一下"能解决的问题 —— 必须用可量化的指标。
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = ROOT / ".venv/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe"


def mean_volume_db(path: Path) -> tuple[float | None, float | None]:
    """返回 (平均音量 dB, 时长秒)。用 volumedetect 滤波器测。"""
    out = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    mean = None
    m = re.search(r"mean_volume:\s*(-?[\d.]+)", out.stderr)
    if m:
        mean = float(m.group(1))
    dur = None
    d = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", out.stderr)
    if d:
        dur = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))
    return mean, dur


def has_audio(path: Path) -> bool:
    out = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=60
    )
    return "Audio:" in out.stderr


def main() -> None:
    con = sqlite3.connect(ROOT / "workspace" / "cloud.db")
    con.row_factory = sqlite3.Row
    # 找同时有 final 和 audio 产物的项目（说明是完整跑过的）
    rows = con.execute(
        """SELECT project_id, storage_key, kind FROM artifacts
           WHERE kind IN ('audio', 'video')
           ORDER BY project_id""",
    ).fetchall()
    con.close()

    by_project: dict[str, dict[str, list[str]]] = {}
    for r in rows:
        by_project.setdefault(r["project_id"], {}).setdefault(r["kind"], []).append(r["storage_key"])

    storage = ROOT / "workspace" / "storage"
    print("=" * 88)
    print("成片音轨来源判定")
    print("=" * 88)

    shown = 0
    for pid, kinds in by_project.items():
        finals = [k for k in kinds.get("video", []) if k.endswith("final.mp4")]
        audios = [k for k in kinds.get("audio", []) if k.endswith("audio.wav")]
        clips = [k for k in kinds.get("video", []) if "sc0" in k and k.endswith(".mp4")]
        if not finals or not audios:
            continue

        final = storage / finals[0].replace("\\", "/")
        tts = storage / audios[0].replace("\\", "/")
        if not final.exists() or not tts.exists():
            continue

        f_mean, f_dur = mean_volume_db(final)
        t_mean, t_dur = mean_volume_db(tts)
        clip_info = ""
        if clips:
            clip = storage / clips[0].replace("\\", "/")
            if clip.exists() and has_audio(clip):
                c_mean, c_dur = mean_volume_db(clip)
                clip_info = f"片段音轨 mean={c_mean}dB dur={c_dur:.2f}s"

        print(f"  {pid}  tier={_tier_of(pid)}")
        print(
            f"    final.mp4   音轨 mean={f_mean}dB  dur={f_dur:.2f}s" if f_dur else "    final.mp4 无音频数据"
        )
        print(f"    audio.wav   mean={t_mean}dB  dur={t_dur:.2f}s" if t_dur else "    audio.wav 无音频数据")
        if clip_info:
            print(f"    {clip_info}")
        shown += 1
        if shown >= 6:
            break

    print()
    print("  判定依据：final 的 mean 若与 audio.wav 接近（±2dB）且时长一致，说明用了 TTS；")
    print("           若与片段音轨接近而与 TTS 差异大，说明 TTS 被 ffmpeg 默认流选择丢弃。")


def _tier_of(pid: str) -> str:
    con = sqlite3.connect(ROOT / "workspace" / "cloud.db")
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT tier FROM shots WHERE project_id=? AND tier != '' LIMIT 1", (pid,)).fetchone()
    con.close()
    return row["tier"] if row else "?"


if __name__ == "__main__":
    main()
