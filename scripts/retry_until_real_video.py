"""反复重做降级片段，直到真的拿到模型产出的视频。

为什么需要循环：免费视频模型（CogVideoX-Flash）是**间歇性**可用的 ——
可能这一分钟接受任务、下一分钟就 429。单次「清状态 + 重跑」大概率撞上限流，
人工反复操作既烦又容易放弃，最后默认接受本地合成的结果。

判定「成功了」的依据是**成本表里 video 类目的 provider 不再是 mock**：
这是唯一能区分「真的调用了模型」与「降级成本地合成」的硬证据 ——
两者的成本都是 0（免费模型记 0），只看金额会得出错误结论。

用法：
    python scripts/retry_until_real_video.py proj_69af656caf --rounds 6
"""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DB = ROOT / "workspace" / "reelagent.db"
PY = sys.executable


def video_providers(project_id: str) -> list[str]:
    """该项目 video 类目用过的 provider（去重）。"""
    if not STATE_DB.exists():
        return []
    con = sqlite3.connect(STATE_DB)
    try:
        rows = con.execute(
            "SELECT DISTINCT provider FROM costs WHERE project_id = ? AND category = 'video'",
            (project_id,),
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def run(cmd: list[str], label: str) -> bool:
    print(f"\n{'─' * 66}\n{label}\n{'─' * 66}")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = (proc.stdout or "").strip().splitlines()
    for line in tail[-14:]:
        print("  " + line)
    if proc.returncode != 0:
        print(f"  [命令失败 exit={proc.returncode}]")
        if proc.stderr:
            print("  stderr:", proc.stderr.strip().splitlines()[-1][:200])
    return proc.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="反复重做降级片段直到成功")
    parser.add_argument("project_id")
    parser.add_argument("--rounds", type=int, default=6, help="最多尝试轮数")
    parser.add_argument("--wait", type=float, default=60.0, help="每轮之间额外等待秒数（留出限流恢复时间）")
    args = parser.parse_args()

    print(f"目标：让项目 {args.project_id} 的片段由**真实视频模型**产出")
    print(f"初始 video provider: {video_providers(args.project_id)}")
    print("（mock = 本地合成，说明当前是降级产物）\n")

    for round_no in range(1, args.rounds + 1):
        print(f"\n{'=' * 66}\n第 {round_no}/{args.rounds} 轮\n{'=' * 66}")

        # 1) 把降级片段标记为待重做（resume 靠产物存在性判断，必须先清）
        ok = run(
            [PY, str(ROOT / "scripts" / "redo_degraded_clips.py"), args.project_id, "--yes"],
            "步骤 1：标记降级片段待重做",
        )
        if not ok:
            print("标记失败，退出")
            return 1

        # 2) 重跑 generate（限流感知退避已生效，会等数十秒再放弃）
        run([PY, "-m", "reelagent.cli", "resume", args.project_id], "步骤 2：重跑 generate")

        # 3) 检查是否真的用上了模型
        providers = video_providers(args.project_id)
        if any(p != "mock" for p in providers):
            print(f"\n✓ 成功：video provider = {providers}")
            print("  片段已由真实视频模型产出。")
            return 0

        print(f"\n✗ 本轮仍全部降级（providers={providers}）")
        if round_no < args.rounds:
            print(f"  等待 {args.wait:g} 秒后重试…")
            time.sleep(args.wait)

    print(f"\n✗ {args.rounds} 轮后仍未成功。免费模型持续过载。")
    print("  选择：加大 --rounds / --wait 继续等，或改用付费模型 cogvideox-3。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
