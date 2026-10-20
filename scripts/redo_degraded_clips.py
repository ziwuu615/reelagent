"""把某个项目里「已降级为本地合成」的镜头标记为需要重做。

使用场景：视频模型因限流/故障被降级成本地动态合成后，等模型恢复再把真正
的视频补上。手工改状态库容易出错，所以固化成脚本：
先备份 → 只清 clip_path 与 status → 删除旧片段文件 → 交由 `resume` 重跑。

为什么只清 clip_path：
    keyframe / audio 仍然有效且已经付过费，重做它们纯属浪费。
    只把「片段」这一步退回，让 Director 的断点恢复逻辑重新生成它。

表结构备忘（踩过一次坑）：
    shots(project_id, shot_id, status, data, updated_at)
    —— `project_id` 是**独立列**，不在 data JSON 里；
       按 JSON 里的 project_id 过滤会永远匹配不到任何行。
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

#: 退回后的状态：Director 据此只重做「片段」这一步，跳过关键帧与配音
RESUME_FROM = "keyframe_done"


def main() -> int:
    parser = argparse.ArgumentParser(description="标记降级镜头以便重做")
    parser.add_argument("project_id", help="项目 ID，如 proj_69af656caf")
    parser.add_argument("--all", action="store_true", help="重做全部镜头（不止降级的）")
    parser.add_argument("--yes", action="store_true", help="跳过确认")
    args = parser.parse_args()

    if not STATE_DB.exists():
        raise SystemExit(f"找不到状态库：{STATE_DB}")

    # 1) 备份 —— 改状态库不可逆，先留退路
    backup = STATE_DB.with_suffix(f".backup_{time.strftime('%Y%m%d_%H%M%S')}.db")
    shutil.copy2(STATE_DB, backup)
    print(f"已备份状态库 -> {backup.name}")

    con = sqlite3.connect(STATE_DB)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        "SELECT shot_id, status, data FROM shots WHERE project_id = ?", (args.project_id,)
    ).fetchall()

    if not rows:
        con.close()
        raise SystemExit(f"状态库里没有项目 {args.project_id} 的镜头")

    # 判定「哪些片段是降级产物」的**权威依据是链路追踪**，不是 Shot 字段。
    #
    # 走过两条弯路，记下来避免重蹈：
    #   1. 用 shot["project_id"] 过滤 —— 该字段在独立列里，JSON 里没有，永远匹配不到
    #   2. 用 shot["provider_used"] 判断 —— 它记录的是**关键帧**的供应商；
    #      即使片段是本地合成的，这里仍显示 "zhipu"，会把降级片段误判成真实片段
    # 降级是执行期事件，它的权威记录本就该在追踪里（director.clip.degraded_to_local）。
    degraded: set[str] = set()
    for span in con.execute(
        "SELECT attrs FROM spans WHERE project_id = ? AND name = 'director.clip.degraded_to_local'",
        (args.project_id,),
    ):
        try:
            shot_id = (json.loads(span["attrs"] or "{}") or {}).get("shot_id")
        except (ValueError, TypeError):
            continue
        if shot_id:
            degraded.add(str(shot_id))

    targets: list[str] = []
    for row in rows:
        shot = json.loads(row["data"] or "{}")
        if not shot.get("clip_path"):
            continue
        if args.all or row["shot_id"] in degraded:
            targets.append(row["shot_id"])

    if not targets:
        print(
            f"项目 {args.project_id} 无需重做的片段"
            f"（{len(rows)} 个镜头，追踪里有 {len(degraded)} 条降级记录）"
        )
        con.close()
        return 0

    print(f"\n将重做 {len(targets)} 个镜头" + ("（--all）" if args.all else "（降级产物）") + "：")
    for shot_id in targets:
        row = next(r for r in rows if r["shot_id"] == shot_id)
        shot = json.loads(row["data"] or "{}")
        mark = "降级" if shot_id in degraded else "正常"
        print(f"  {shot_id}  [{mark}]  clip={shot.get('clip_path')}  status={row['status']}")

    if not args.yes:
        answer = input("\n确认？会删除旧片段文件并退回 status [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("已取消")
            con.close()
            return 1

    removed = 0
    for shot_id in targets:
        row = next(r for r in rows if r["shot_id"] == shot_id)
        shot = json.loads(row["data"] or "{}")
        clip = shot.get("clip_path")
        if clip:
            path = ROOT / "workspace" / clip
            if path.exists():
                path.unlink()
                removed += 1
        shot["clip_path"] = None
        shot["status"] = RESUME_FROM
        shot["error"] = None
        con.execute(
            "UPDATE shots SET data = ?, status = ?, updated_at = ? WHERE project_id = ? AND shot_id = ?",
            (
                json.dumps(shot, ensure_ascii=False),
                RESUME_FROM,
                time.time(),
                args.project_id,
                shot_id,
            ),
        )
    con.commit()
    con.close()

    print(f"\n已清理 {len(targets)} 个镜头，删除 {removed} 个旧片段文件")
    print(f"下一步：python -m reelagent resume {args.project_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
