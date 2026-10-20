"""端到端验证档位选择器：表单值是否真的落到任务 payload。

不需要真实生成（那样要花钱）：只要确认 `force_tier` 落到了 jobs.payload，
就证明「表单 → 路由 → 服务 → 队列」这条链路是通的。
引擎是否遵守档位由 tests/test_routing_tiers.py 覆盖。

排查过程中踩过两个坑，都记在这里避免重蹈：

  1. **解析重定向地址不能只做字符串切割**。
     被拒时 location 是 `/app/projects/new?error=...`，
     `split('/app/projects/')[-1]` 会得到非空字符串，把「失败」伪装成
     「成功但值为空」—— 比直接报错更难查。必须校验 ID 的真实格式。

  2. **每个场景要用独立的 HTTP 客户端**。
     复用同一个 client 时，第一次注册成功即建立会话，
     之后再访问 /signup 会被判为「已登录」，服务端返回注册关闭页
     （没有 CSRF 隐藏字段），后续 POST 全部 403。
     看起来像注册功能坏了，实际是测试方法错了。
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8091"
SEED = "雨夜便利店门口的追逐戏，霓虹灯闪烁，一个女孩撑着红伞回头看了一眼。"

#: 项目 ID 的真实形态：proj_ + 26 位 Crockford Base32（ULID）
PROJECT_ID_RE = re.compile(r"^proj_[0-9A-HJKMNP-TV-Z]{26}$")


def extract_project_id(location: str) -> str | None:
    """从重定向地址里取出**合法**的项目 ID；不是合法 ID 就返回 None。"""
    match = re.search(r"/app/projects/([^/?#]+)", location)
    if not match:
        return None
    candidate = match.group(1)
    return candidate if PROJECT_ID_RE.match(candidate) else None


def error_of(location: str) -> str:
    match = re.search(r"[?&]error=([^&]*)", location)
    return match.group(1) if match else ""


def new_client() -> httpx.Client:
    """每次注册都要用全新客户端，原因见模块 docstring 第 2 条。"""
    return httpx.Client(base_url=BASE, follow_redirects=False, timeout=60)


def signup_and_submit(tier: str) -> tuple[bool, str | None, str]:
    """注册一个新账号并提交指定档位，返回 (是否创建成功, 项目 ID, 原因)。"""
    with new_client() as client:
        page = client.get("/signup")
        token = re.search(r'name="csrf_token"\s+value="([^"]+)"', page.text)
        token = token.group(1) if token else ""
        reg = client.post(
            "/signup",
            data={
                "email": f"tier_{uuid.uuid4().hex[:8]}@example.com",
                "password": "Str0ng-Passw0rd-2026",
                "display_name": "档位验证",
                "org_name": f"档位工作室{uuid.uuid4().hex[:6]}",
                "agree": "on",
                "csrf_token": token,
            },
        )
        if reg.status_code not in (302, 303):
            return False, None, f"注册失败 HTTP {reg.status_code}"

        form = client.get("/app/projects/new", headers={"Accept": "text/html"})
        token = re.search(r'name="csrf_token"\s+value="([^"]+)"', form.text)
        token = token.group(1) if token else ""

        resp = client.post(
            "/app/projects",
            data={
                "seed_text": SEED,
                "title": f"档位测试{tier}",
                "style": "cinematic",
                "aspect_ratio": "9:16",
                "shots": "3",
                "quality_tier": tier,
                "csrf_token": token,
            },
        )
        location = resp.headers.get("location", "")
        project_id = extract_project_id(location)
        if project_id:
            return True, project_id, ""
        return False, None, error_of(location) or f"HTTP {resp.status_code}"


def payload_for(project_id: str) -> dict:
    con = sqlite3.connect(ROOT / "workspace" / "cloud.db")
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT payload FROM jobs WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    con.close()
    return json.loads(row["payload"]) if row else {}


def main() -> int:
    failures = 0

    print("=" * 70)
    print("1. 各档位是否真地落到任务 payload")
    print("=" * 70)
    for tier, expected in (("auto", None), ("draft", "draft"), ("standard", "standard")):
        created, project_id, why = signup_and_submit(tier)
        if not created:
            print(f"  x {tier:<10} 提交被拒：{why[:66]}")
            failures += 1
            continue
        got = payload_for(project_id).get("force_tier")
        good = got == expected
        failures += not good
        print(f"  {'+' if good else 'x'} {tier:<10} payload.force_tier = {got!r}（期望 {expected!r}）")

    print()
    print("=" * 70)
    print("2. 非法档位必须被拒（防注入）")
    print("=" * 70)
    for bad in ("ultra", "premium2", "../../etc", "draft;rm -rf"):
        created, _, why = signup_and_submit(bad)
        ok = not created
        failures += not ok
        print(f"  {'+' if ok else 'x'} {bad!r:<18} 被拒={ok}  {why[:50]}")

    print()
    print("=" * 70)
    print("3. 免费套餐不得使用 premium（绕过界面直接提交）")
    print("=" * 70)
    created, project_id, why = signup_and_submit("premium")
    if not created:
        print(f"  + 被拒：{why[:66]}")
    else:
        got = payload_for(project_id).get("force_tier")
        blocked = got != "premium"
        failures += not blocked
        print(f"  {'+' if blocked else 'x'} 竟然创建成功，force_tier={got!r}")

    print()
    print("=" * 70)
    print("全部通过" if not failures else f"失败 {failures} 项")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
