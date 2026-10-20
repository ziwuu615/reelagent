"""HTTP 层冒烟：构造 app、跑通探针与 JSON API。

用法：python scripts/smoke_http.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("REEL_ENV", "dev")

DB = Path(tempfile.gettempdir()) / "reel_http_smoke.db"
if DB.exists():
    DB.unlink()
os.environ["REEL_DATABASE__URL"] = f"sqlite+aiosqlite:///{DB.as_posix()}"

from fastapi.testclient import TestClient  # noqa: E402

from reelagent_cloud.api.app import create_app  # noqa: E402
from reelagent_cloud.config import get_settings  # noqa: E402
from reelagent_cloud.services.storage import reset_storage  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, extra: str = "") -> None:
    print(f"  {'✓' if ok else '✗'} {label}{(' — ' + extra) if extra else ''}")
    if not ok:
        failures.append(label)


def main() -> int:
    get_settings.cache_clear()
    settings = get_settings()
    reset_storage()
    app = create_app(settings)

    with TestClient(app) as client:
        print("== 探针 ==")
        r = client.get("/livez")
        check("GET /livez", r.status_code == 200 and r.json()["status"] == "alive", str(r.status_code))

        r = client.get("/readyz")
        body = r.json()
        check("GET /readyz", r.status_code == 200, f"checks={list(body.get('checks', {}))}")

        r = client.get("/health")
        check("GET /health", r.status_code == 200 and r.json()["status"] == "ok")

        print("== 可观测 ==")
        r = client.get("/metrics")
        check(
            "GET /metrics 返回 Prometheus 文本",
            r.status_code == 200 and "reelagent_" in r.text,
            f"{len(r.text)} bytes",
        )
        check("指标里含 HTTP 请求计数", "reelagent_http_requests_total" in r.text)
        r = client.get("/openapi.json")
        check(
            "OpenAPI 文档可生成",
            r.status_code == 200 and len(r.json().get("paths", {})) > 10,
            f"{len(r.json().get('paths', {}))} 个路径",
        )

        print("== 安全头 ==")
        r = client.get("/livez")
        csp = r.headers.get("content-security-policy", "")
        check("存在 CSP 头", bool(csp), csp[:60])
        check(
            "CSP 禁止内联脚本",
            "script-src 'self'" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0],
        )
        check("X-Content-Type-Options", r.headers.get("x-content-type-options") == "nosniff")
        check("X-Frame-Options", r.headers.get("x-frame-options") == "DENY")
        check("带回 request id", bool(r.headers.get("x-request-id")))

        print("== 鉴权边界 ==")
        r = client.get("/api/v1/me")
        check("未登录访问 /api/v1/me 返回 401", r.status_code == 401, str(r.status_code))
        check("401 响应体结构统一", r.json().get("error", {}).get("code") == "unauthorized")

        r = client.get("/api/v1/plans")
        check("公开套餐接口可访问", r.status_code == 200 and len(r.json()["items"]) == 4, str(r.status_code))

        r = client.get("/api/v1/projects")
        check("未登录访问项目列表返回 401", r.status_code == 401)

        r = client.get("/api/v1/system/status")
        check("状态接口需登录（含配置快照）", r.status_code == 401, str(r.status_code))

        print("== 限流 ==")
        # 公开接口不应触发限流异常；反复请求后仍应正常
        codes = [client.get("/api/v1/plans").status_code for _ in range(8)]
        check("连续请求不被误限流", all(c == 200 for c in codes), str(set(codes)))

        print("== 错误处理 ==")
        r = client.get("/api/v1/nonexistent")
        check("未知 API 路径返回 JSON 404", r.status_code == 404 and "error" in r.json())

        r = client.post("/api/v1/projects", json={"bad": "payload"})
        check("未鉴权的写请求先被 401 拦截", r.status_code == 401, str(r.status_code))

    print()
    if failures:
        print(f"❌ {len(failures)} 项失败：")
        for f in failures:
            print("   -", f)
        return 1
    print("✅ HTTP 层全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
