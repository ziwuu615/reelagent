"""验证「现在能不能直接用」：走一遍真实用户能看到的全部页面。

不创建项目（那会真实扣费），只验证：
  注册 -> 登录态 -> 控制台各页面 -> 静态资源 -> 健康端点
"""

from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8091"


def csrf(text: str) -> str:
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', text)
    return m.group(1) if m else ""


def main() -> int:
    fails = 0

    print("=" * 70)
    print("健康端点")
    print("=" * 70)
    for ep in ("/health", "/readyz", "/metrics"):
        try:
            r = httpx.get(f"{BASE}{ep}", timeout=10)
            print(f"  {ep:<10} HTTP {r.status_code}  {len(r.content)} B")
            fails += r.status_code != 200
        except Exception as exc:
            print(f"  {ep:<10} 失败: {exc}")
            fails += 1

    with httpx.Client(base_url=BASE, follow_redirects=False, timeout=30) as c:
        print()
        print("=" * 70)
        print("公开页面（未登录）")
        print("=" * 70)
        for ep in ("/", "/pricing", "/login", "/signup", "/docs"):
            try:
                r = c.get(ep)
                ok = r.status_code in (200, 307, 308)
                print(f"  {ep:<12} HTTP {r.status_code}  {len(r.content) // 1024} KB")
                fails += not ok
            except Exception as exc:
                print(f"  {ep:<12} 失败: {exc}")
                fails += 1

        print()
        print("=" * 70)
        print("注册新账号")
        print("=" * 70)
        tok = csrf(c.get("/signup").text)
        email = f"usable_{uuid.uuid4().hex[:8]}@example.com"
        r = c.post(
            "/signup",
            data={
                "email": email,
                "password": "Str0ng-Passw0rd-2026",
                "display_name": "可用性检查",
                "org_name": f"可用性工作台{uuid.uuid4().hex[:6]}",
                "agree": "on",
                "csrf_token": tok,
            },
        )
        print(f"  POST /signup  HTTP {r.status_code}  -> {r.headers.get('location', '')[:40]}")
        fails += r.status_code not in (302, 303)

        print()
        print("=" * 70)
        print("登录后的控制台页面")
        print("=" * 70)
        for ep in (
            "/app",
            "/app/projects",
            "/app/projects/new",
            "/app/usage",
            "/app/billing",
            "/app/settings",
        ):
            try:
                r = c.get(ep, headers={"Accept": "text/html"})
                ok = r.status_code == 200
                print(f"  {ep:<22} HTTP {r.status_code}  {len(r.content) // 1024} KB")
                fails += not ok
            except Exception as exc:
                print(f"  {ep:<22} 失败: {exc}")
                fails += 1

        print()
        print("=" * 70)
        print("静态资源")
        print("=" * 70)
        for ep in ("/static/css/app.css", "/static/js/app.js"):
            try:
                r = c.get(ep)
                print(f"  {ep:<26} HTTP {r.status_code}  {len(r.content)} B")
                fails += r.status_code != 200
            except Exception as exc:
                print(f"  {ep:<26} 失败: {exc}")
                fails += 1

    print()
    print("=" * 70)
    print(f"结果：{'全部可用' if fails == 0 else f'{fails} 项异常'}")
    print(f"登录邮箱：{email}")
    print("=" * 70)
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
