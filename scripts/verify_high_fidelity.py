"""验证高保真档（premium）是否真的产出更高分辨率。

要证明的命题：**premium 不只是多收钱，它真的给 720P。**

背景：早期实现里 standard 与 premium 走的是完全相同的生成路径
（同模型、同分辨率），而计费侧却按 premium 多收额度。修复方式是用分辨率
把差异做实（`TIER_RESOLUTION_SCALE`: premium = 1.4，短边跨过 DashScope 的
720P 分档线），但这个机制**一次都没有实跑过** —— 单元测试只覆盖了尺寸换算，
没覆盖「供应商实际收到的尺寸」与「最终成片的分辨率」。

用法：
    python scripts/verify_high_fidelity.py premium 1     # 约 $0.47
    python scripts/verify_high_fidelity.py standard 1    # 约 $0.26

注意顺序陷阱：档位权限在 `create_project` 里校验，
**必须先升级套餐再提交**，否则 premium 会被直接挡掉。
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
# 补额度时要用 reelagent_cloud 的服务层函数（走正规账本接口），
# 因此必须把项目根目录加入搜索路径 —— 脚本直接运行时默认只有 scripts/ 在路径里。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8091"
SEED = "雨夜便利店的霓虹灯下，一个女孩撑着红伞回头，雨水打湿地面倒映着招牌的光。"
PROJECT_ID_RE = re.compile(r"^proj_[0-9A-HJKMNP-TV-Z]{26}$")
FFMPEG = ROOT / ".venv/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe"


def db() -> sqlite3.Connection:
    con = sqlite3.connect(ROOT / "workspace" / "cloud.db")
    con.row_factory = sqlite3.Row
    return con


def _csrf(text: str) -> str:
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', text)
    return m.group(1) if m else ""


def signup(client: httpx.Client) -> str:
    """注册新账号，返回组织 ID。"""
    tok = _csrf(client.get("/signup").text)
    client.post(
        "/signup",
        data={
            "email": f"hf_{uuid.uuid4().hex[:8]}@example.com",
            "password": "Str0ng-Passw0rd-2026",
            "display_name": "高保真验证",
            "org_name": f"高保真工作室{uuid.uuid4().hex[:6]}",
            "agree": "on",
            "csrf_token": tok,
        },
    )
    with db() as con:
        return con.execute("SELECT id FROM organizations ORDER BY created_at DESC LIMIT 1").fetchone()["id"]


def set_plan(org_id: str, plan: str) -> None:
    """升级套餐 —— 必须在提交项目**之前**。"""
    with db() as con:
        con.execute("UPDATE organizations SET plan=? WHERE id=?", (plan, org_id))
        con.commit()


def top_up(org_id: str, amount: int) -> int:
    """给测试组织补额度。

    走正规的 `credits.adjust` 接口而不是直接改余额字段：
    它会写一条 `adjustment` 类型的账本记录并带上原因，
    余额快照与账本保持可对账。直接改 `organizations.credits_balance`
    会让账本等式失衡，后续查账时无法解释。

    **这是测试专用操作**，原因字段里写清楚，不要伪装成真实付款。
    """
    import asyncio

    from reelagent_cloud.config import Settings as CloudSettings
    from reelagent_cloud.db import dispose_engine, init_engine, session_scope
    from reelagent_cloud.services import credits

    async def _run() -> int:
        init_engine(CloudSettings().database)
        try:
            async with session_scope() as session:
                before = await credits.get_balance(session, org_id)
                if before >= amount:
                    return before
                await credits.adjust(
                    session,
                    org_id=org_id,
                    amount=amount - before,
                    reason="端到端验证专用充值（非真实付款）",
                    created_by="verify_high_fidelity.py",
                )
                return await credits.get_balance(session, org_id)
        finally:
            await dispose_engine()

    return asyncio.run(_run())


def create_project(client: httpx.Client, tier: str, shots: int) -> str:
    tok = _csrf(client.get("/app/projects/new", headers={"Accept": "text/html"}).text)
    resp = client.post(
        "/app/projects",
        data={
            "seed_text": SEED,
            "title": f"高保真验证-{tier}",
            "style": "cinematic",
            "aspect_ratio": "9:16",
            "shots": str(shots),
            "quality_tier": tier,
            "csrf_token": tok,
        },
    )
    loc = resp.headers.get("location", "")
    m = re.search(r"/app/projects/([^/?#]+)", loc)
    pid = m.group(1) if m else ""
    if not PROJECT_ID_RE.match(pid):
        raise SystemExit(f"提交失败：{resp.status_code} {loc[:160]}")
    return pid


def wait_done(project_id: str, limit_s: int = 1800) -> str:
    last = ""
    for i in range(limit_s // 10):
        time.sleep(10)
        with db() as con:
            job = con.execute(
                "SELECT status, stage FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        line = f"  [{i * 10:>4}s] {job['status']:<10} stage={job['stage'] or '-'}"
        if line != last:
            print(line)
            last = line
        if job["status"] in ("succeeded", "failed", "canceled"):
            return job["status"]
    return "timeout"


def image_size(path: Path) -> str:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return f"{im.size[0]}x{im.size[1]}"
    except Exception as exc:
        return f"读取失败 {exc}"


def video_size(path: Path) -> str:
    try:
        out = subprocess.run(
            [str(FFMPEG), "-hide_banner", "-i", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        for line in out.stderr.splitlines():
            if "Video:" in line:
                m = re.search(r"(\d{2,5})x(\d{2,5})", line)
                if m:
                    return f"{m.group(1)}x{m.group(2)}"
    except Exception as exc:
        return f"探测失败 {exc}"
    return "未知"


def main() -> int:
    tier = sys.argv[1] if len(sys.argv) > 1 else "premium"
    shots = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    print(f"提交 {tier} 档 / {shots} 镜")
    with httpx.Client(base_url=BASE, follow_redirects=False, timeout=60) as c:
        org = signup(c)
        set_plan(org, "pro")  # 先升级，否则 premium 会被权限校验直接挡掉
        # 高镜数 + premium 的预扣额会超过注册赠送的 500 点，先补足
        balance = top_up(org, 3000)
        print(f"  组织 {org} 已升级 pro，额度充至 {balance} 点（测试用）")
        pid = create_project(c, tier, shots)
    print(f"  项目 {pid}")
    print(f"  组织 {org} 已升级到 pro")
    print()

    status = wait_done(pid)
    print()
    print("=" * 74)
    print(f"任务状态: {status}")
    print("=" * 74)

    with db() as con:
        job = con.execute(
            "SELECT payload, result, error FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
            (pid,),
        ).fetchone()
        proj = con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        srows = con.execute(
            "SELECT shot_key, tier, provider, model, cost_usd, status FROM shots WHERE project_id=?",
            (pid,),
        ).fetchall()

    payload = json.loads(job["payload"])
    print(f"  payload.force_tier = {payload.get('force_tier')!r}")
    if job["error"]:
        print(f"  错误: {job['error'][:280]}")
    print(f"  成本 ${proj['provider_cost_usd']:.4f}   镜头 {proj['finished_shots']}/{proj['total_shots']}")

    print()
    print("  云端 shots 表（tier 列是否终于有值）:")
    for s in srows:
        print(
            f"    {s['shot_key']:<12} tier={s['tier']!s:<10} {s['provider']!s:<10} "
            f"{s['model']!s:<18} ${s['cost_usd'] or 0:.4f} {s['status']}"
        )

    print()
    print("  产物分辨率:")
    #
    # 必须按**精确 storage_key** 定位文件。
    # 踩过的坑：先前用 `rglob(文件名)[0]` 查找，而 `sc01_sh01.png` 这种名字
    # 在多个历史项目里都存在，结果取到了旧项目的文件 —— 于是把已经生效的
    # 720P 误判为「没生效」。验证脚本自己出错，比被测代码出错更危险：
    # 它会让人去修一个本来就正常的地方。
    with db() as con:
        rows = con.execute(
            "SELECT kind, storage_key FROM artifacts WHERE project_id=? ORDER BY kind, storage_key",
            (pid,),
        ).fetchall()

    storage = ROOT / "workspace" / "storage"
    for a in rows:
        key = a["storage_key"].replace("\\", "/")
        f = storage / Path(key)
        if not f.exists():
            # 兼容另一种键布局：按文件名找，但必须限定在**本项目**目录下
            same_project = [p for p in storage.rglob(Path(key).name) if pid in str(p)]
            if not same_project:
                print(f"    {Path(key).name:<38} (文件未找到)")
                continue
            f = same_project[0]
        suf = f.suffix.lower()
        label = ""
        if suf in (".png", ".jpg", ".jpeg"):
            label = image_size(f)
        elif suf in (".mp4", ".gif", ".webm"):
            label = video_size(f)
        print(f"    {a['kind']:<11} {Path(key).name:<38} {label}")

    print()
    print("  预期: premium 关键帧/片段/final 为 756x1344（短边 756 -> 720P 档）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
