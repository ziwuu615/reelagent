"""对比三个档位的真实产出：格式、分辨率、码率、时长、成本。

只报实测值，不写"画质更好"这类无法量化的判断。
可以量化的是：编码格式、像素、码率、颜色深度、成本、耗时。
无法量化的是"看起来好不好看" —— 那需要人眼判断，我不做这个结论。
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = str(ROOT / ".venv/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe")


def probe(path: Path) -> dict[str, str]:
    out = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=120
    )
    info: dict[str, str] = {}
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", out.stderr)
    if m:
        info["dur"] = f"{int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)):.2f}s"
    for line in out.stderr.splitlines():
        if "Video:" in line:
            res = re.search(r"(\d{2,5})x(\d{2,5})", line)
            if res:
                info["res"] = f"{res.group(1)}x{res.group(2)}"
            br = re.search(r"(\d+)\s*kb/s", line)
            if br:
                info["bitrate"] = f"{br.group(1)} kb/s"
            codec = re.search(r"Video:\s*([a-z0-9]+)", line)
            if codec:
                info["codec"] = codec.group(1)
            fps = re.search(r"(\d+)\s*fps", line)
            if fps:
                info["fps"] = fps.group(1)
        if "Audio:" in line:
            ac = re.search(r"Audio:\s*([a-z0-9]+)", line)
            info["audio"] = ac.group(1) if ac else "有"
    return info


def main() -> None:
    con = sqlite3.connect(ROOT / "workspace" / "cloud.db")
    con.row_factory = sqlite3.Row
    storage = ROOT / "workspace" / "storage"

    # 每个档位挑一个最新项目
    picks: dict[str, sqlite3.Row] = {}
    for r in con.execute(
        "SELECT id, total_shots, provider_cost_usd, created_at FROM projects "
        "WHERE status='ready' ORDER BY created_at DESC"
    ):
        tier_row = con.execute(
            "SELECT tier FROM shots WHERE project_id=? AND tier != '' LIMIT 1", (r["id"],)
        ).fetchone()
        tier = tier_row["tier"] if tier_row else None
        if tier is None:
            # draft 档的 tier 列也是 draft；若无 tier 则看片段后缀推断
            clip = con.execute(
                "SELECT storage_key FROM artifacts WHERE project_id=? AND kind='video' "
                "AND storage_key NOT LIKE '%final%' LIMIT 1",
                (r["id"],),
            ).fetchone()
            if clip and clip["storage_key"].endswith(".gif"):
                tier = "draft"
        if tier and tier not in picks:
            picks[tier] = r

    print("=" * 92)
    print("三个档位的实测产出对比")
    print("=" * 92)

    for tier in ("draft", "standard", "premium"):
        r = picks.get(tier)
        if r is None:
            print(f"\n【{tier}】没有可用样本")
            continue
        print(f"\n【{tier}】项目 {r['id']}   {r['total_shots']} 镜   ${r['provider_cost_usd'] or 0:.4f}")
        for kind in ("keyframe", "video"):
            rows = con.execute(
                "SELECT storage_key FROM artifacts WHERE project_id=? AND kind=? ",
                (r["id"], kind),
            ).fetchall()
            for row in rows:
                key = row["storage_key"].replace("\\", "/")
                path = storage / Path(key)
                if not path.exists():
                    cands = [p for p in storage.rglob(Path(key).name) if r["id"] in str(p)]
                    if not cands:
                        continue
                    path = cands[0]
                info = probe(path)
                size_kb = path.stat().st_size // 1024
                label = "成片" if "final" in path.name else kind
                bits = "  ".join(f"{k}={v}" for k, v in info.items())
                print(f"    {label:<10} {path.name:<18} {size_kb:>6} KB   {bits}")

    con.close()


if __name__ == "__main__":
    main()
