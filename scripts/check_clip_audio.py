"""检查视频模型产出的片段是否自带音轨。

文档写明 wan3.0-video「有声视频默认开启」（可通过 audio=false 关闭）。
如果片段自带人声/BGM，而流水线又用 TTS 单独配音，成片会有两层声音冲突。
这必须用文件实际探测，不能靠读文档推断。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = ROOT / ".venv/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe"


def probe(path: Path) -> str:
    out = subprocess.run(
        [str(FFMPEG), "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=60
    )
    streams = [line.strip() for line in out.stderr.splitlines() if "Stream #" in line]
    if not streams:
        return "无流信息"
    video = [s for s in streams if "Video:" in s]
    audio = [s for s in streams if "Audio:" in s]
    parts = []
    for s in video:
        m = re.search(r"(\d{2,5})x(\d{2,5})", s)
        parts.append(f"视频 {m.group(1)}x{m.group(2)}" if m else "视频")
    if audio:
        m = re.search(r"Audio:\s*([a-z0-9]+)", audio[0])
        parts.append(f"**音轨 {m.group(1) if m else '有'}**")
    else:
        parts.append("无音轨")
    return "  ".join(parts)


def main() -> None:
    storage = ROOT / "workspace" / "storage"
    print("=" * 86)
    print("视频片段音轨检查（判断是否与 TTS 叠加）")
    print("=" * 86)

    clips = sorted(storage.rglob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    shown = 0
    for p in clips:
        name = p.name
        if name.startswith("final") or "sc0" in name:
            kind = "成片" if name.startswith("final") else "片段"
            print(f"  {kind:<4} {name:<22} {probe(p)}")
            shown += 1
            if shown >= 10:
                break

    if shown == 0:
        print("  没有找到 mp4 产物")
        return

    print()
    print("  参考：若片段自带音轨，说明模型原生有声；成片里的音频会被混入。")


if __name__ == "__main__":
    main()
