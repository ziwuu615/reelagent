"""一次性数据修正：把「扩展名说谎」的片段改名并同步状态库。

背景：早期版本的调用处把片段输出名固定成 `{shot_id}.gif`，
而真实视频模型返回的是 MP4。文件内容完全有效，只是名字错了。
后果是合成阶段用 Pillow 读它必然失败，而成片会**静默停留在旧版本**。

修正方式选择了**改名**而不是重新生成：
    片段内容是好的，只是命名错了。重新生成要再花约 3 元且耗时数分钟，
    换不来任何新信息。改名是对既有数据的纠正，不是绕过问题。

修正后代码已改为「扩展名由供应商声明」，因此这是一次性操作。

用法：
    python scripts/fix_clip_extensions.py --dry-run
    python scripts/fix_clip_extensions.py --apply
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DB = ROOT / "workspace" / "reelagent.db"

#: 通过文件头判断真实容器
MAGIC = {b"\x89PNG": "png", b"GIF8": "gif", b"\xff\xd8\xff": "jpg"}


def sniff(path: Path) -> str:
    head = path.read_bytes()[:16]
    for marker, ext in MAGIC.items():
        if head.startswith(marker):
            return ext
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "mp4"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="修正片段扩展名与状态库")
    parser.add_argument("--apply", action="store_true", help="实际执行（默认只预览）")
    args = parser.parse_args()

    if not STATE_DB.exists():
        raise SystemExit(f"找不到状态库：{STATE_DB}")

    con = sqlite3.connect(STATE_DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT project_id, shot_id, data FROM shots").fetchall()

    plans: list[tuple[str, str, Path, Path, dict]] = []
    for row in rows:
        shot = json.loads(row["data"] or "{}")
        clip = shot.get("clip_path")
        if not clip:
            continue
        path = ROOT / "workspace" / clip
        if not path.exists():
            continue
        real = sniff(path)
        if real and real != path.suffix.lstrip(".").lower():
            target = path.with_suffix(f".{real}")
            plans.append((row["project_id"], row["shot_id"], path, target, shot))

    if not plans:
        print("没有需要修正的片段（扩展名与内容一致）")
        con.close()
        return 0

    print(f"发现 {len(plans)} 个扩展名与内容不符的片段：")
    for project_id, shot_id, src, dst, _ in plans:
        print(f"  [{project_id}] {shot_id}: {src.name} -> {dst.name}")

    if not args.apply:
        print("\n（预览模式，加 --apply 执行）")
        con.close()
        return 0

    backup = STATE_DB.with_suffix(f".backup_{time.strftime('%Y%m%d_%H%M%S')}.db")
    shutil.copy2(STATE_DB, backup)
    print(f"\n已备份状态库 -> {backup.name}")

    for project_id, shot_id, src, dst, shot in plans:
        if dst.exists():
            print(f"  跳过 {shot_id}：目标已存在 {dst.name}")
            continue
        src.rename(dst)
        # 同步状态库里的路径（相对 workspace 的 posix 路径）
        shot["clip_path"] = dst.relative_to(ROOT / "workspace").as_posix()
        con.execute(
            "UPDATE shots SET data = ?, updated_at = ? WHERE project_id = ? AND shot_id = ?",
            (json.dumps(shot, ensure_ascii=False), time.time(), project_id, shot_id),
        )
        print(f"  已修正 {shot_id} -> {shot['clip_path']}")

    con.commit()
    con.close()
    print("\n完成。现在可以重跑 render 生成正确的成片：")
    print("  python -m reelagent resume <project_id> --from-stage render")
    return 0


if __name__ == "__main__":
    sys.exit(main())
