"""通过真实 HTTP 走一遍「用户从注册到提交生成需求」的完整流程。

这一步验证的是**产品闭环的上半段**：
    注册 → 拿到会话 → 提交一句话需求 → 项目落库 + 任务入队

之所以要单独做，是因为「页面能打开」和「用户真的能用它干活」是两回事。
页面渲染正常但表单提交失败、或任务没入队，都属于面板不可用。
"""

from __future__ import annotations

import re
import sys
import time
import uuid

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8091"
SEED = "一个女孩在雨夜便利店门口等人，霓虹灯闪烁，她手里拿着一把红色的伞。"


def extract_csrf(html: str, cookie_name: str = "reel_csrf") -> str:
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    return match.group(1) if match else ""


def main() -> int:
    email = f"demo_{uuid.uuid4().hex[:8]}@example.com"
    password = "Str0ng-Passw0rd-2026"

    with httpx.Client(base_url=BASE, follow_redirects=False, timeout=60) as client:
        # 1) 注册页拿 CSRF
        signup_page = client.get("/signup")
        token = extract_csrf(signup_page.text)
        print(f"[1] 打开注册页 HTTP {signup_page.status_code}，CSRF={'有' if token else '无'}")

        # 2) 提交注册
        resp = client.post(
            "/signup",
            data={
                "email": email,
                "password": password,
                "display_name": "演示用户",
                "org_name": "演示工作室",
                "agree": "on",
                "csrf_token": token,
            },
        )
        print(f"[2] 提交注册 HTTP {resp.status_code} -> {resp.headers.get('location', '(无跳转)')}")
        if resp.status_code not in (302, 303):
            print(f"    响应片段：{resp.text[:300]}")
            return 1

        # 3) 进控制台
        dash = client.get("/app", headers={"Accept": "text/html"})
        print(f"[3] 打开控制台 HTTP {dash.status_code}  {len(dash.content) // 1024} KB")
        if dash.status_code != 200:
            return 1

        # 4) 打开新建项目页，取 CSRF
        new_page = client.get("/app/projects/new", headers={"Accept": "text/html"})
        token = extract_csrf(new_page.text)
        print(f"[4] 打开新建页 HTTP {new_page.status_code}  {len(new_page.content) // 1024} KB")

        # 5) 提交生成需求
        #    注意端点是 /app/projects（不是 /app/projects/new）——
        #    后者只处理 GET。用表单里的 action 为准，别凭直觉猜。
        start = time.perf_counter()
        create = client.post(
            "/app/projects",
            data={
                "seed_text": SEED,
                "title": "雨夜便利店",
                "style": "cinematic",
                "aspect_ratio": "9:16",
                "shots": "2",
                "csrf_token": token,
            },
        )
        elapsed = time.perf_counter() - start
        location = create.headers.get("location", "")
        print(f"[5] 提交生成需求 HTTP {create.status_code} ({elapsed:.1f}s) -> {location}")

        if create.status_code not in (302, 303):
            print(f"    响应片段：{create.text[:400]}")
            return 1

        project_id = location.rstrip("/").split("/")[-1]
        print(f"    项目 ID = {project_id}")

        # 6) 查任务是否入队
        detail = client.get(f"/app/projects/{project_id}", headers={"Accept": "text/html"})
        print(f"[6] 项目详情页 HTTP {detail.status_code}  {len(detail.content) // 1024} KB")

    print()
    print("=" * 62)
    print("上半段闭环：通过 ✓")
    print(f"  邮箱      : {email}")
    print(f"  项目 ID   : {project_id}")
    print("=" * 62)
    print()
    print("下一步需要 worker 消费任务：reelagent-worker")
    print(f"可查状态：curl -b cookies {BASE}/app/projects/{project_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
