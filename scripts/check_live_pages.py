"""对**正在运行的** Web 服务做页面级验证。

与 scripts/smoke_http.py 的区别：那个用 FastAPI 的 TestClient 在进程内跑，
验证的是代码路径；这个走真实 HTTP，验证的是**实际部署起来的服务**能否访问，
包括静态资源、模板渲染、以及关键的跳转行为。
"""

from __future__ import annotations

import re
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8090"

PAGES = [
    ("/", "落地页", ["ReelAgent"]),
    ("/pricing", "定价页", []),
    ("/signup", "注册页", []),
    ("/login", "登录页", []),
    ("/terms", "服务条款", []),
    ("/privacy", "隐私政策", []),
    ("/status", "状态页", []),
    ("/docs", "API 文档", []),
    ("/static/css/app.css", "样式表", []),
    ("/static/js/app.js", "前端脚本", []),
    ("/static/favicon.svg", "站点图标", []),
]


def main() -> int:
    ok = 0
    fail = 0
    with httpx.Client(base_url=BASE, follow_redirects=False, timeout=30) as client:
        print(f"目标服务：{BASE}\n")
        for path, label, must_contain in PAGES:
            try:
                resp = client.get(path)
            except Exception as exc:
                print(f"  ✗ {label:<12} {path:<20} 连接失败 {type(exc).__name__}")
                fail += 1
                continue

            size = len(resp.content)
            good = resp.status_code == 200
            missing = [m for m in must_contain if m.encode() not in resp.content]
            if missing:
                good = False

            mark = "✓" if good else "✗"
            note = f"HTTP {resp.status_code}  {size // 1024} KB"
            if missing:
                note += f"  缺少内容 {missing}"
            print(f"  {mark} {label:<12} {path:<20} {note}")
            ok += good
            fail += not good

        # 未登录访问控制台应被**重定向到登录页**，而不是 500 或一坨 401 JSON。
        # 浏览器导航会带 Accept: text/html；这里显式模拟浏览器行为。
        print("\n未登录访问控制台（模拟浏览器导航，应 303 跳转登录）：")
        browser = {"Accept": "text/html,application/xhtml+xml"}
        for path in ("/app", "/app/projects", "/app/projects/new"):
            resp = client.get(path, headers=browser)
            location = resp.headers.get("location", "")
            good = resp.status_code == 303 and "/login" in location
            print(f"  {'✓' if good else '✗'} {path:<20} HTTP {resp.status_code} -> {location or '(无跳转)'}")
            ok += good
            fail += not good

        # API 路径必须保持 401 JSON：调用方是程序，重定向会破坏客户端逻辑
        print("\nAPI 路径未登录（应保持 401 JSON，不重定向）：")
        for path in ("/api/v1/projects", "/api/v1/me"):
            resp = client.get(path, headers=browser)
            is_json = resp.headers.get("content-type", "").startswith("application/json")
            good = resp.status_code == 401 and is_json
            print(f"  {'✓' if good else '✗'} {path:<20} HTTP {resp.status_code} json={is_json}")
            ok += good
            fail += not good

        # 页面标题，用于人工确认渲染没串版
        print("\n页面标题抽样：")
        for path in ("/", "/pricing", "/signup"):
            resp = client.get(path)
            match = re.search(rb"<title[^>]*>(.*?)</title>", resp.content, re.S)
            title = match.group(1).decode("utf-8", "ignore").strip() if match else "(无 title)"
            print(f"  {path:<12} {title[:70]}")

    print(f"\n{'=' * 60}")
    print(f"通过 {ok} 项，失败 {fail} 项")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
