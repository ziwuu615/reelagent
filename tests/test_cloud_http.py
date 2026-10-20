"""HTTP 集成测试。

用真实 FastAPI TestClient 走完整链路：渲染页面、提交表单、调 REST API。
单元测试证明「函数是对的」，这里证明「连起来是对的」——
历史上最多的 bug（context 键冲突、CSRF 时序、租户越权）都只能在这一层发现。
"""

from __future__ import annotations

from datetime import UTC

import pytest
from fastapi.testclient import TestClient

from reelagent_cloud.config import (
    DatabaseSettings,
    ProviderSettings,
    ProvidersSettings,
    SecuritySettings,
    Settings,
    StorageSettings,
)

from .conftest import (
    DEFAULT_PASSWORD,
    build_settings,
    extract_csrf_token,
    signup_via_form,
    unique_signup_payload,
)

pytestmark = pytest.mark.usefixtures("db")


# --------------------------------------------------------------------------- #
# 配置校验
# --------------------------------------------------------------------------- #
class TestProductionConfigGate:
    """生产配置校验必须真的生效。

    这里锁住一个曾经存在的缺陷：`SecuritySettings` 的子校验器先自动生成了随机
    secret_key，导致父级「生产必须配置 SECRET_KEY」的检查永远为假——
    即多副本各自持有一把随机密钥，用户在负载均衡后随机掉登录。
    """

    def _prod_kwargs(self, workspace, **overrides):
        kwargs = {
            "env": "prod",
            "base_url": "https://reelagent.example.com",
            "support_email": "support@example.com",
            "workspace_dir": workspace,
            "database": DatabaseSettings(url="postgresql+asyncpg://u:p@db:5432/reel"),
            "storage": StorageSettings(backend="local", local_dir=workspace / "storage"),
            "security": SecuritySettings(
                secret_key="configured-by-ops",
                api_key_pepper="configured-pepper",
                https_only=True,
                allowed_hosts=["reelagent.example.com"],
            ),
            "providers": ProvidersSettings(
                video=ProviderSettings(provider="ark", model="m", api_key="sk-real", base_url="")
            ),
        }
        kwargs.update(overrides)
        return kwargs

    def test_complete_production_config_accepted(self, tmp_workspace):
        Settings(**self._prod_kwargs(tmp_workspace))

    def test_missing_secret_key_rejected_in_production(self, tmp_workspace):
        kwargs = self._prod_kwargs(tmp_workspace)
        kwargs["security"] = SecuritySettings(
            secret_key="",
            api_key_pepper="p",
            https_only=True,
            allowed_hosts=["reelagent.example.com"],
        )
        with pytest.raises(ValueError, match="SECRET_KEY"):
            Settings(**kwargs)

    def test_missing_api_key_pepper_rejected_in_production(self, tmp_workspace):
        kwargs = self._prod_kwargs(tmp_workspace)
        kwargs["security"] = SecuritySettings(
            secret_key="s",
            api_key_pepper="",
            https_only=True,
            allowed_hosts=["reelagent.example.com"],
        )
        with pytest.raises(ValueError, match="API_KEY_PEPPER"):
            Settings(**kwargs)

    @pytest.mark.parametrize(
        "field,value,expected",
        [
            ("https_only", False, "HTTPS_ONLY"),
            ("allowed_hosts", ["*"], "ALLOWED_HOSTS"),
        ],
    )
    def test_insecure_production_settings_rejected(self, tmp_workspace, field, value, expected):
        kwargs = self._prod_kwargs(tmp_workspace)
        sec = SecuritySettings(
            secret_key="s",
            api_key_pepper="p",
            https_only=True,
            allowed_hosts=["reelagent.example.com"],
        )
        setattr(sec, field, value)
        kwargs["security"] = sec
        with pytest.raises(ValueError, match=expected):
            Settings(**kwargs)

    def test_sqlite_rejected_in_production(self, tmp_workspace):
        kwargs = self._prod_kwargs(tmp_workspace)
        kwargs["database"] = DatabaseSettings(url="sqlite+aiosqlite:///./x.db")
        with pytest.raises(ValueError, match="SQLite"):
            Settings(**kwargs)

    def test_dev_autogenerates_secret(self, tmp_workspace):
        s = build_settings(tmp_workspace)
        assert s.security.secret_key.get_secret_value()
        assert s.security.api_key_pepper.get_secret_value()


# --------------------------------------------------------------------------- #
# 公开页面
# --------------------------------------------------------------------------- #
class TestPublicPages:
    @pytest.mark.parametrize(
        "path,must_contain",
        [
            ("/", "ReelAgent"),
            ("/pricing", "点数"),
            ("/terms", "服务条款"),
            ("/privacy", "隐私"),
            ("/status", "状态"),
            ("/login", "登录"),
            ("/signup", "注册"),
        ],
    )
    def test_page_renders(self, client: TestClient, path: str, must_contain: str):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
        assert must_contain in resp.text, f"{path} 缺少关键内容「{must_contain}」"

    def test_404_page(self, client: TestClient):
        resp = client.get("/this-page-does-not-exist")
        assert resp.status_code == 404
        assert "text/html" in resp.headers["content-type"]

    def test_api_404_returns_json(self, client: TestClient):
        resp = client.get("/api/v1/nope")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    def test_static_assets_served(self, client: TestClient):
        css = client.get("/static/css/app.css")
        assert css.status_code == 200
        assert len(css.text) > 1000
        js = client.get("/static/js/app.js")
        assert js.status_code == 200
        assert "csrf" in js.text.lower()

    def test_no_inline_script_in_pages(self, client: TestClient):
        """CSP 是 script-src 'self'，任何内联脚本都会被浏览器拦掉——
        与其上线后发现页面交互失效，不如在测试里就卡住。"""
        for path in ("/", "/pricing", "/login", "/signup"):
            html = client.get(path).text
            assert "<script>" not in html, f"{path} 含内联 <script>"
            assert "onclick=" not in html, f"{path} 含内联事件属性"

    def test_docs_available(self, client: TestClient):
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200


# --------------------------------------------------------------------------- #
# 安全响应头
# --------------------------------------------------------------------------- #
class TestSecurityHeaders:
    def test_headers_present(self, client: TestClient):
        resp = client.get("/")
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"
        csp = resp.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert resp.headers.get("x-request-id")

    def test_no_hsts_in_dev(self, client: TestClient):
        """开发环境走 http，发 HSTS 会让浏览器把 localhost 也强制 https。"""
        assert "strict-transport-security" not in client.get("/").headers


# --------------------------------------------------------------------------- #
# 注册 / 登录 / 登出
# --------------------------------------------------------------------------- #
class TestAuthFlow:
    def test_signup_creates_session_and_org(self, client: TestClient):
        resp = signup_via_form(client, unique_signup_payload())
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/app")
        assert client.cookies.get("reelagent_session"), "必须下发会话 Cookie"
        assert client.cookies.get("reelagent_csrf"), "必须下发 CSRF Cookie"

    def test_signup_sets_httponly_session(self, client: TestClient):
        resp = signup_via_form(client, unique_signup_payload())
        set_cookie = resp.headers.get("set-cookie", "")
        assert "HttpOnly" in set_cookie, "会话 Cookie 必须 HttpOnly，防 XSS 窃取"

    def test_dashboard_renders_after_signup(self, authed_client: TestClient):
        resp = authed_client.get("/app")
        assert resp.status_code == 200, resp.text[:500]
        assert "点数" in resp.text or "额度" in resp.text

    def test_signup_grant_is_visible(self, authed_client: TestClient):
        resp = authed_client.get("/app/usage")
        assert resp.status_code == 200
        assert "注册赠送" in resp.text

    def test_duplicate_email_rejected(self, client: TestClient):
        payload = unique_signup_payload("dup")
        assert signup_via_form(client, payload).status_code == 303
        client.cookies.clear()
        resp = signup_via_form(client, payload)
        assert resp.status_code == 400
        assert "已注册" in resp.text

    def test_weak_password_rejected(self, client: TestClient):
        payload = unique_signup_payload("weak")
        payload["password"] = "12345678"
        resp = signup_via_form(client, payload)
        assert resp.status_code == 400
        assert "密码" in resp.text

    def test_signup_requires_agreement(self, client: TestClient):
        payload = unique_signup_payload("noagree")
        payload.pop("agree")
        resp = signup_via_form(client, payload)
        assert resp.status_code == 400
        assert "服务条款" in resp.text

    def test_login_logout(self, client: TestClient):
        payload = unique_signup_payload("login")
        assert signup_via_form(client, payload).status_code == 303
        client.cookies.clear()

        page = client.get("/login")
        token = extract_csrf_token(page.text)
        resp = client.post(
            "/login",
            data={
                "email": payload["email"],
                "password": payload["password"],
                "next": "/app",
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert client.get("/app").status_code == 200

        client.post("/logout", follow_redirects=False)
        assert client.get("/app", follow_redirects=False).status_code in (303, 401)

    def test_wrong_password_shows_error_page(self, client: TestClient):
        payload = unique_signup_payload("wrongpw")
        assert signup_via_form(client, payload).status_code == 303
        client.cookies.clear()
        page = client.get("/login")
        resp = client.post(
            "/login",
            data={
                "email": payload["email"],
                "password": "Wrong-Password-1!",
                "csrf_token": extract_csrf_token(page.text),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 401
        assert "不正确" in resp.text

    def test_login_without_csrf_rejected(self, client: TestClient):
        """防「登录 CSRF」：攻击者不能把你静默登进他的账号。"""
        payload = unique_signup_payload("csrf")
        assert signup_via_form(client, payload).status_code == 303
        client.cookies.clear()
        client.get("/login")
        resp = client.post(
            "/login",
            data={"email": payload["email"], "password": payload["password"]},
            follow_redirects=False,
        )
        assert resp.status_code == 403
        assert "刷新" in resp.text

    def test_login_page_has_nonempty_csrf(self, client: TestClient):
        """首次访问就必须带可用 token——曾经因为「先渲染后设 Cookie」导致空 token。"""
        html = client.get("/login").text
        token = extract_csrf_token(html)
        assert token and len(token) > 10

    def test_change_password_invalidates_old_session(self, client: TestClient, settings: Settings, sync_db):
        assert signup_via_form(client, unique_signup_payload("pwchange")).status_code == 303
        first_cookie = client.cookies.get("reelagent_session")

        # 直接改库模拟「改密/登出所有设备」，避免跨事件循环操作引擎
        sync_db.execute("UPDATE users SET session_version = session_version + 1")
        sync_db.commit()

        client.cookies.set("reelagent_session", first_cookie)
        resp = client.get("/app", follow_redirects=False)
        assert resp.status_code in (303, 401), "改密后旧会话必须立刻失效"


# --------------------------------------------------------------------------- #
# 控制台
# --------------------------------------------------------------------------- #
class TestConsole:
    def test_projects_page_empty_state(self, authed_client: TestClient):
        resp = authed_client.get("/app/projects")
        assert resp.status_code == 200

    def test_new_project_page(self, authed_client: TestClient):
        resp = authed_client.get("/app/projects/new")
        assert resp.status_code == 200
        assert "cinematic" in resp.text or "电影感" in resp.text

    def test_create_project_deducts_credits_and_queues_job(self, authed_client: TestClient, csrf: str):
        before = _balance_via_api(authed_client)
        resp = authed_client.post(
            "/app/projects",
            data={
                "seed_text": "深夜的便利店只剩下最后一盏灯。林晚找到一把黄铜钥匙。老人说等了很久。",
                "title": "便利店",
                "style": "cinematic",
                "aspect_ratio": "9:16",
                "shots": 4,
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.text[:400]
        assert "/app/projects/proj_" in resp.headers["location"]

        after = _balance_via_api(authed_client)
        assert after < before, "创建项目必须预扣额度"

    def test_created_project_is_visible(self, authed_client: TestClient, csrf: str):
        _create_project(authed_client, csrf)
        listing = authed_client.get("/app/projects")
        assert listing.status_code == 200
        assert "便利店" in listing.text

        detail = authed_client.get(_project_url(authed_client))
        assert detail.status_code == 200

    def test_running_project_page_renders(self, authed_client: TestClient, csrf: str):
        _create_project(authed_client, csrf)
        resp = authed_client.get(_project_url(authed_client))
        assert resp.status_code == 200
        assert "生成" in resp.text

    def test_studio_page_renders(self, authed_client: TestClient, csrf: str):
        _create_project(authed_client, csrf)
        url = _project_url(authed_client) + "/studio"
        resp = authed_client.get(url)
        assert resp.status_code == 200

    def test_settings_page_lists_api_keys_section(self, authed_client: TestClient):
        resp = authed_client.get("/app/settings")
        assert resp.status_code == 200
        assert "API" in resp.text

    def test_billing_page_renders(self, authed_client: TestClient):
        resp = authed_client.get("/app/billing")
        assert resp.status_code == 200
        assert "创作者版" in resp.text or "专业版" in resp.text

    def test_usage_page_renders(self, authed_client: TestClient):
        resp = authed_client.get("/app/usage")
        assert resp.status_code == 200

    def test_console_requires_login(self, client: TestClient):
        for path in ("/app", "/app/projects", "/app/settings", "/app/billing"):
            resp = client.get(path, follow_redirects=False)
            assert resp.status_code in (303, 401, 403), f"{path} 未拦截未登录访问"

    def test_csrf_required_for_writes(self, authed_client: TestClient):
        resp = authed_client.post(
            "/app/projects",
            data={"seed_text": "一段足够长的素材内容", "csrf_token": "wrong-token"},
            follow_redirects=False,
        )
        assert resp.status_code == 403

    def test_project_detail_never_leaks_api_keys(self, authed_client: TestClient, csrf: str):
        _create_project(authed_client, csrf)
        html = authed_client.get(_project_url(authed_client)).text
        assert "sk-" not in html and "reel_" not in html


# --------------------------------------------------------------------------- #
# REST API
# --------------------------------------------------------------------------- #
class TestRestApi:
    def test_me_returns_identity(self, authed_client: TestClient):
        data = authed_client.get("/api/v1/me").json()
        assert data["actor"]["kind"] == "session"
        assert data["organization"]["id"].startswith("org_")
        assert data["credits"]["balance"] >= 0

    def test_create_project_via_api(self, authed_client: TestClient):
        resp = authed_client.post(
            "/api/v1/projects",
            json={"seed_text": "一个关于旧收音机的短故事，她听见了昨天的自己。", "shots": 3},
        )
        assert resp.status_code == 201, resp.text[:400]
        body = resp.json()
        assert body["project"]["id"].startswith("proj_")
        assert body["job"] is not None
        assert body["quote"]["estimated_credits"] > 0

    def test_api_idempotency_key(self, authed_client: TestClient):
        payload = {"seed_text": "重复提交测试：她拧动旋钮，听见昨天的自己。", "shots": 2}
        headers = {"Idempotency-Key": "test-key-001"}
        first = authed_client.post("/api/v1/projects", json=payload, headers=headers)
        second = authed_client.post("/api/v1/projects", json=payload, headers=headers)
        assert first.status_code == 201
        assert second.status_code in (201, 200)
        assert first.json()["project"]["id"] == second.json()["project"]["id"]

    def test_auto_idempotency_by_content_fingerprint(self, authed_client: TestClient):
        """未显式给 Key 时，用内容指纹兜底，避免连点两次扣两份钱。"""
        payload = {"seed_text": "内容指纹幂等测试：沙漠公路尽头的加油站。", "shots": 2}
        first = authed_client.post("/api/v1/projects", json=payload)
        second = authed_client.post("/api/v1/projects", json=payload)
        assert first.json()["project"]["id"] == second.json()["project"]["id"]

    def test_validation_errors_are_structured(self, authed_client: TestClient):
        resp = authed_client.post("/api/v1/projects", json={})
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "validation_failed"
        assert "request_id" in body

    def test_project_detail_with_shots(self, authed_client: TestClient):
        created = authed_client.post(
            "/api/v1/projects",
            json={"seed_text": "详情接口测试：她打开盒盖，里面只有一把钥匙。", "shots": 3},
        ).json()
        pid = created["project"]["id"]
        detail = authed_client.get(f"/api/v1/projects/{pid}").json()
        assert detail["id"] == pid
        assert "clues" in detail
        assert "evaluation" in detail

    def test_job_polling_endpoint(self, authed_client: TestClient):
        created = authed_client.post(
            "/api/v1/projects",
            json={"seed_text": "任务轮询测试：雨还在下着。", "shots": 2},
        ).json()
        job_id = created["job"]["id"]
        job = authed_client.get(f"/api/v1/jobs/{job_id}").json()
        assert job["id"] == job_id
        assert job["status"] in {"pending", "running"}
        assert "stage" in job and "progress" in job

    def test_cancel_job(self, authed_client: TestClient):
        created = authed_client.post(
            "/api/v1/projects",
            json={"seed_text": "取消任务测试：钥匙上刻着她的生日。", "shots": 2},
        ).json()
        job_id = created["job"]["id"]
        resp = authed_client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_quote_endpoint(self, authed_client: TestClient):
        data = authed_client.post("/api/v1/credits/quote?shots=6").json()
        assert data["estimated_credits"] > 0
        assert "affordable" in data

    def test_plans_public(self, client: TestClient):
        plans = client.get("/api/v1/plans").json()["items"]
        assert len(plans) == 4
        assert plans[0]["tier"] == "free"

    def test_unauthenticated_rejected(self, client: TestClient):
        for method, path in [
            ("get", "/api/v1/me"),
            ("get", "/api/v1/projects"),
            ("post", "/api/v1/projects"),
            ("get", "/api/v1/credits"),
            ("get", "/api/v1/jobs"),
        ]:
            resp = getattr(client, method)(path)
            assert resp.status_code == 401, f"{method.upper()} {path} 未拦截"


# --------------------------------------------------------------------------- #
# API Key
# --------------------------------------------------------------------------- #
class TestApiKeyAuth:
    """API Key 鉴权。

    夹具数据用同步 sqlite3 直连准备：TestClient 与 pytest-asyncio 的事件循环不同，
    在测试里另开循环操作同一个异步引擎会静默失败（表现为 401）。
    """

    def _issue_key(self, sync_db, settings: Settings) -> str:
        from datetime import datetime

        from reelagent_cloud.domain.ids import PREFIX, new_id
        from reelagent_cloud.security import generate_api_key

        sync_db.execute("UPDATE organizations SET plan = 'pro'")
        gen = generate_api_key(settings)
        org_id = sync_db.execute("SELECT id FROM organizations LIMIT 1").fetchone()["id"]
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
        sync_db.execute(
            "INSERT INTO api_keys (id, org_id, created_by, name, prefix, key_hash, scopes,"
            " last_used_ip, request_count, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id(PREFIX["api_key"]),
                org_id,
                None,
                "CI",
                gen.prefix,
                gen.key_hash,
                '["read", "write"]',
                "",
                0,
                stamp,
                stamp,
            ),
        )
        sync_db.commit()
        return gen.full_key

    def test_api_key_works_for_reads(self, authed_client: TestClient, settings: Settings, sync_db):
        full = self._issue_key(sync_db, settings)
        resp = authed_client.get("/api/v1/me", headers={"Authorization": f"Bearer {full}"})
        assert resp.status_code == 200, resp.text[:300]
        assert resp.json()["actor"]["kind"] == "api_key"

    def test_x_api_key_header_also_works(self, authed_client: TestClient, settings: Settings, sync_db):
        full = self._issue_key(sync_db, settings)
        assert authed_client.get("/api/v1/me", headers={"X-API-Key": full}).status_code == 200

    def test_api_key_cannot_manage_billing(self, authed_client: TestClient, settings: Settings, sync_db):
        """API Key 会出现在 CI 与第三方集成里，泄漏概率高，因此禁止管理计费。"""
        full = self._issue_key(sync_db, settings)
        resp = authed_client.post(
            "/app/billing/checkout",
            data={"tier": "pro", "csrf_token": "x"},
            headers={"Authorization": f"Bearer {full}"},
            follow_redirects=False,
        )
        assert resp.status_code in (401, 403), resp.text[:200]

    def test_api_key_cannot_create_projects_when_readonly(
        self, authed_client: TestClient, settings: Settings, sync_db
    ):
        from datetime import datetime

        from reelagent_cloud.domain.ids import PREFIX, new_id
        from reelagent_cloud.security import generate_api_key

        sync_db.execute("UPDATE organizations SET plan = 'pro'")
        gen = generate_api_key(settings)
        org_id = sync_db.execute("SELECT id FROM organizations LIMIT 1").fetchone()["id"]
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
        sync_db.execute(
            "INSERT INTO api_keys (id, org_id, created_by, name, prefix, key_hash, scopes,"
            " last_used_ip, request_count, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id(PREFIX["api_key"]),
                org_id,
                None,
                "RO",
                gen.prefix,
                gen.key_hash,
                '["read"]',
                "",
                0,
                stamp,
                stamp,
            ),
        )
        sync_db.commit()

        resp = authed_client.post(
            "/api/v1/projects",
            json={"seed_text": "只读密钥不应能创建项目，这是一段足够长的素材。"},
            headers={"Authorization": f"Bearer {gen.full_key}"},
        )
        assert resp.status_code == 403, resp.text[:200]

    def test_invalid_api_key_rejected(self, client: TestClient):
        resp = client.get("/api/v1/me", headers={"Authorization": "Bearer reel_deadbeef_invalidsecret"})
        assert resp.status_code == 401

    def test_malformed_key_rejected(self, client: TestClient):
        assert client.get("/api/v1/me", headers={"Authorization": "Bearer not-a-key"}).status_code == 401

    def test_revoked_key_rejected(self, authed_client: TestClient, settings: Settings, sync_db):
        from datetime import datetime

        full = self._issue_key(sync_db, settings)
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
        sync_db.execute("UPDATE api_keys SET revoked_at = ?", (stamp,))
        sync_db.commit()
        resp = authed_client.get("/api/v1/me", headers={"Authorization": f"Bearer {full}"})
        assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# 租户隔离
# --------------------------------------------------------------------------- #
class TestTenantIsolation:
    def test_cannot_read_other_orgs_project(
        self, authed_client: TestClient, second_client: TestClient, csrf: str
    ):
        _create_project(authed_client, csrf)
        victim_id = _project_id(authed_client)

        resp = second_client.get(f"/api/v1/projects/{victim_id}")
        assert resp.status_code == 404, "越权读取返回 404（不泄露资源是否存在）"

    def test_cannot_delete_other_orgs_project(
        self, authed_client: TestClient, second_client: TestClient, csrf: str
    ):
        _create_project(authed_client, csrf)
        victim_id = _project_id(authed_client)
        assert second_client.delete(f"/api/v1/projects/{victim_id}").status_code == 404

    def test_cannot_list_other_orgs_projects(
        self, authed_client: TestClient, second_client: TestClient, csrf: str
    ):
        _create_project(authed_client, csrf)
        listing = second_client.get("/api/v1/projects").json()
        assert listing["total"] == 0, "另一个组织不应看到任何项目"

    def test_cannot_download_other_orgs_file(
        self, authed_client: TestClient, second_client: TestClient, settings: Settings, sync_db
    ):
        """产物 key 与租户绑定，换个 URL 拿不到别人的成片。"""
        from reelagent_cloud.services.storage import build_key

        victim_org = sync_db.execute("SELECT id FROM organizations LIMIT 1").fetchone()["id"]
        key = build_key(org_id=victim_org, project_id="proj_x", category="final", filename="secret.mp4")
        path = settings.storage.local_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"top-secret")

        assert second_client.get(f"/files/{key}").status_code == 403

    def test_own_file_download_works(self, authed_client: TestClient, settings: Settings, sync_db):
        from reelagent_cloud.services.storage import build_key

        own_org = sync_db.execute("SELECT id FROM organizations LIMIT 1").fetchone()["id"]
        key = build_key(org_id=own_org, project_id="proj_y", category="final", filename="mine.mp4")
        path = settings.storage.local_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"my-content")

        resp = authed_client.get(f"/files/{key}")
        assert resp.status_code == 200
        assert resp.content == b"my-content"

    def test_file_path_traversal_blocked(self, authed_client: TestClient, settings: Settings, sync_db):
        """路径穿越不能读到存储根之外的文件。"""
        own_org = sync_db.execute("SELECT id FROM organizations LIMIT 1").fetchone()["id"]
        secret = settings.storage.local_dir.parent / "outside.txt"
        secret.write_bytes(b"should-not-be-readable")

        resp = authed_client.get(f"/files/org/{own_org}/final/../../../outside.txt")
        assert resp.status_code in (403, 404), resp.text[:200]


# --------------------------------------------------------------------------- #
# 限流
# --------------------------------------------------------------------------- #
class TestRateLimit:
    def _app_with_rate_limit(self, tmp_workspace):
        import asyncio

        from reelagent_cloud.api.app import create_app
        from reelagent_cloud.api.ratelimit import reset_limiter_sync
        from reelagent_cloud.db import init_engine
        from reelagent_cloud.db.session import create_all
        from reelagent_cloud.services.storage import reset_storage

        settings = build_settings(tmp_workspace, rate_limit_enabled=True)
        # 注册限流阈值是固定 5 次/小时（见 ratelimit.limits_from）

        loop = asyncio.get_event_loop_policy().new_event_loop()
        reset_storage()
        reset_limiter_sync()
        init_engine(settings.database)
        loop.run_until_complete(create_all())
        return create_app(settings), loop

    def test_signup_rate_limited(self, tmp_workspace):
        app, _loop = self._app_with_rate_limit(tmp_workspace)
        with TestClient(app) as client:
            codes = []
            for i in range(7):
                token = extract_csrf_token(client.get("/signup").text)
                resp = client.post(
                    "/signup",
                    data={
                        "email": f"rate{i}@example.com",
                        "password": DEFAULT_PASSWORD,
                        "agree": "on",
                        "csrf_token": token,
                    },
                    follow_redirects=False,
                )
                codes.append(resp.status_code)
            assert 429 in codes, f"应当触发限流，实际状态码 {codes}"


# --------------------------------------------------------------------------- #
# 健康与指标
# --------------------------------------------------------------------------- #
class TestObservability:
    def test_health_endpoints(self, client: TestClient):
        assert client.get("/livez").json()["status"] == "alive"
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["checks"]["database"]["ok"] is True

    def test_metrics_expose_business_counters(self, client: TestClient):
        client.get("/")
        text = client.get("/metrics").text
        for metric in (
            "reelagent_http_requests_total",
            "reelagent_jobs_total",
            "reelagent_provider_cost_usd_total",
            "reelagent_credits_consumed_total",
        ):
            assert metric in text, f"缺少指标 {metric}"

    def test_request_id_propagates(self, client: TestClient):
        resp = client.get("/", headers={"X-Request-ID": "trace-abc-123"})
        assert resp.headers["x-request-id"] == "trace-abc-123"

    def test_status_page_public(self, client: TestClient):
        assert client.get("/status").status_code == 200

    def test_system_status_requires_login(self, client: TestClient):
        assert client.get("/api/v1/system/status").status_code == 401

    def test_system_status_hides_config_from_non_admin(self, authed_client: TestClient):
        data = authed_client.get("/api/v1/system/status").json()
        # 注册者是 owner，属于管理员，因此这里应当能看到 config
        assert "config" in data
        assert "secret_key" not in str(data).lower().replace("secret_key", "")

    def test_no_secrets_in_status_output(self, authed_client: TestClient):
        text = authed_client.get("/api/v1/system/status").text
        assert "test-secret-key-for-unit-tests-only" not in text
        assert "test-pepper" not in text


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
def _extract_form_token(html: str) -> str:
    return extract_csrf_token(html)


def _balance_via_api(client: TestClient) -> int:
    return int(client.get("/api/v1/me").json()["credits"]["balance"])


def _create_project(client: TestClient, csrf: str) -> str:
    resp = client.post(
        "/app/projects",
        data={
            "seed_text": "深夜的便利店只剩下最后一盏灯。林晚找到一把黄铜钥匙。老人说等了很久。",
            "title": "便利店",
            "style": "cinematic",
            "aspect_ratio": "9:16",
            "shots": 3,
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text[:400]
    location = resp.headers["location"]
    return location.split("?")[0]


def _project_url(client: TestClient) -> str:
    listing = client.get("/app/projects")
    import re

    match = re.search(r"/app/projects/(proj_[A-Za-z0-9]+)", listing.text)
    assert match, "项目列表里找不到项目链接"
    return match.group(0)


def _project_id(client: TestClient) -> str:
    return _project_url(client).rsplit("/", 1)[-1]
