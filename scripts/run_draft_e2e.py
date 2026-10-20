"""提交一个省钱档（draft）任务并监控到完成。

这是完整链路的真实验证：
    面板表单 -> 任务 payload -> worker -> 引擎
      -> 百炼万相图像（新适配器）
      -> 草稿档本地合成（不调用视频 API）
      -> 渲染成片

省钱档一次约 $0.12，适合验证流程而不烧钱。
"""

from __future__ import annotations

import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8091"
SEED = "雨夜便利店的霓虹灯下，一个女孩撑着红伞回头，雨水打湿了地面，倒映着招牌的光。"
PROJECT_ID_RE = re.compile(r"^proj_[0-9A-HJKMNP-TV-Z]{26}$")


def db() -> sqlite3.Connection:
    con = sqlite3.connect(ROOT / "workspace" / "cloud.db")
    con.row_factory = sqlite3.Row
    return con


def submit(tier: str, shots: int) -> str:
    with httpx.Client(base_url=BASE, follow_redirects=False, timeout=60) as c:
        page = c.get("/signup")
        tok = re.search(r'name="csrf_token"\s+value="([^"]+)"', page.text)
        tok = tok.group(1) if tok else ""
        c.post(
            "/signup",
            data={
                "email": f"run_{uuid.uuid4().hex[:8]}@example.com",
                "password": "Str0ng-Passw0rd-2026",
                "display_name": "实跑验证",
                "org_name": f"实跑工作室{uuid.uuid4().hex[:6]}",
                "agree": "on",
                "csrf_token": tok,
            },
        )
        form = c.get("/app/projects/new", headers={"Accept": "text/html"})
        tok = re.search(r'name="csrf_token"\s+value="([^"]+)"', form.text)
        tok = tok.group(1) if tok else ""
        resp = c.post(
            "/app/projects",
            data={
                "seed_text": SEED,
                "title": f"省钱档实跑-{tier}",
                "style": "cinematic",
                "aspect_ratio": "9:16",
                "shots": str(shots),
                "quality_tier": tier,
                "csrf_token": tok,
            },
        )
        m = re.search(r"/app/projects/([^/?#]+)", resp.headers.get("location", ""))
        pid = m.group(1) if m else ""
        if not PROJECT_ID_RE.match(pid):
            raise SystemExit(f"提交失败：{resp.status_code} {resp.headers.get('location', '')[:120]}")
        return pid


def main() -> int:
    shots = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    pid = submit("draft", shots)
    print(f"项目已创建 {pid}（省钱档 / {shots} 镜）")
    print()

    last = ""
    for i in range(180):  # 最多等 30 分钟
        time.sleep(10)
        with db() as con:
            job = con.execute(
                "SELECT status, stage, error FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
                (pid,),
            ).fetchone()
            proj = con.execute(
                "SELECT status, finished_shots, total_shots, provider_cost_usd FROM projects WHERE id=?",
                (pid,),
            ).fetchone()
        line = (
            f"  [{i * 10:>4}s] job={job['status']:<10} stage={job['stage'] or '-':<10} "
            f"project={proj['status']:<10} shots={proj['finished_shots']}/{proj['total_shots']} "
            f"${proj['provider_cost_usd']:.4f}"
        )
        if line != last:
            print(line)
            last = line
        if job["status"] in ("succeeded", "failed", "canceled"):
            break

    print()
    print("=" * 70)
    with db() as con:
        job = con.execute(
            "SELECT status, error, result FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
            (pid,),
        ).fetchone()
        proj = con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        arts = con.execute(
            "SELECT kind, storage_key, size_bytes FROM artifacts WHERE project_id=?", (pid,)
        ).fetchall()

    print(f"任务状态: {job['status']}")
    if job["error"]:
        print(f"错误: {job['error'][:400]}")
    print(
        f"项目: shots={proj['finished_shots']}/{proj['total_shots']}  "
        f"credits={proj['credits_spent']}  ${proj['provider_cost_usd']:.4f}"
    )

    print(f"\n产物 {len(arts)} 个:")
    for a in arts:
        size = (a["size_bytes"] or 0) // 1024
        print(f"  {a['kind']:<10} {a['storage_key'].split('/')[-1]:<46} {size:>6} KB")

    has_final = any("final.mp4" in a["storage_key"] for a in arts)
    print()
    if job["status"] == "succeeded" and has_final and proj["finished_shots"] == proj["total_shots"]:
        print("✓ 完整链路验证通过：出片 + 全部镜头完成")
        return 0
    print("✗ 未达预期（缺 final.mp4 或有镜头未完成）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
