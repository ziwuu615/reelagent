"""验证面板上四个档位选项是否都渲染出来。

必须用**高级套餐**的账号测，否则 premium/express 会被权限过滤隐藏 ——
那会让人误以为选项缺失，实际是设计如此。
"""

from __future__ import annotations

import re
import sqlite3
import sys
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8091"


def db() -> sqlite3.Connection:
    con = sqlite3.connect(ROOT / "workspace" / "cloud.db")
    con.row_factory = sqlite3.Row
    return con


def csrf(text: str) -> str:
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', text)
    return m.group(1) if m else ""


def main() -> int:
    with httpx.Client(base_url=BASE, follow_redirects=False, timeout=60) as c:
        tok = csrf(c.get("/signup").text)
        c.post(
            "/signup",
            data={
                "email": f"tier_{uuid.uuid4().hex[:8]}@example.com",
                "password": "Str0ng-Passw0rd-2026",
                "display_name": "档位渲染检查",
                "org_name": f"档位工作台{uuid.uuid4().hex[:6]}",
                "agree": "on",
                "csrf_token": tok,
            },
        )
        # 升级到 pro，让 premium / express 通过权限过滤
        with db() as con:
            org = con.execute("SELECT id FROM organizations ORDER BY created_at DESC LIMIT 1").fetchone()[
                "id"
            ]
            con.execute("UPDATE organizations SET plan='pro' WHERE id=?", (org,))
            con.commit()

        for _plan, label in ((None, "pro（高级套餐）"),):
            page = c.get("/app/projects/new", headers={"Accept": "text/html"})
            options = re.findall(r'name="quality_tier" value="([^"]+)"', page.text)
            print(f"  套餐 {label}")
            print(f"    可见档位: {options}")
            print(f"    数量: {len(options)}")
            for name in ("自动", "省钱模式", "均衡", "最高质量", "极速出片"):
                print(f"    含「{name}」: {name in page.text}")
            print()

        # 再用全新免费账号看权限过滤是否生效
    with httpx.Client(base_url=BASE, follow_redirects=False, timeout=60) as c2:
        tok = csrf(c2.get("/signup").text)
        c2.post(
            "/signup",
            data={
                "email": f"free_{uuid.uuid4().hex[:8]}@example.com",
                "password": "Str0ng-Passw0rd-2026",
                "display_name": "免费档检查",
                "org_name": f"免费工作台{uuid.uuid4().hex[:6]}",
                "agree": "on",
                "csrf_token": tok,
            },
        )
        page = c2.get("/app/projects/new", headers={"Accept": "text/html"})
        free_options = re.findall(r'name="quality_tier" value="([^"]+)"', page.text)
        print("  套餐 free（免费）")
        print(f"    可见档位: {free_options}")
        print(f"    premium/express 已隐藏: {not any(o in ('premium', 'express') for o in free_options)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
