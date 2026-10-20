"""pytest 公共 fixture。

原则：**每个测试用独立的临时工作区与独立的 SQLite 库**，
不共享状态，不依赖执行顺序，可以并行跑。

为什么用 SQLite 而不是 Postgres：
  单元与集成测试要能在任何机器上零依赖跑起来。
  Postgres 特有的能力（SKIP LOCKED、JSONB）由 CI 里的 pg 服务单独覆盖。
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

# 测试必须在导入应用前把环境定为 dev，避免生产校验直接拒绝启动
os.environ["REEL_ENV"] = "dev"
os.environ.setdefault("REEL_OBSERVABILITY__LOG_LEVEL", "WARNING")

from reelagent_cloud.api.app import create_app
from reelagent_cloud.config import (
    DatabaseSettings,
    ProviderSettings,
    ProvidersSettings,
    SecuritySettings,
    Settings,
    StorageSettings,
)
from reelagent_cloud.db import dispose_engine, init_engine
from reelagent_cloud.db.session import create_all, session_scope
from reelagent_cloud.services import storage as storage_svc

_TMP_ROOT = Path(__file__).resolve().parent / "_tmp"


# --------------------------------------------------------------------------- #
# 基础
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session", autouse=True)
def _cleanup_root() -> Iterator[None]:
    yield
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


@pytest.fixture
def tmp_workspace() -> Iterator[Path]:
    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = _TMP_ROOT / f"ws_{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def build_settings(workspace: Path, **overrides) -> Settings:
    """构造一份用于测试的配置：SQLite + 本地存储 + 默认关闭限流 + 离线供应商。"""
    rate_limit_enabled = bool(overrides.pop("rate_limit_enabled", False))
    db_path = (workspace / "test.db").as_posix()
    base = {
        "env": "dev",
        "base_url": "http://testserver",
        "support_email": "support@example.com",
        "workspace_dir": workspace,
        "database": DatabaseSettings(url=f"sqlite+aiosqlite:///{db_path}"),
        "storage": StorageSettings(backend="local", local_dir=workspace / "storage"),
        "security": SecuritySettings(
            secret_key="test-secret-key-for-unit-tests-only",
            api_key_pepper="test-pepper",
            allow_signup=True,
            https_only=False,
        ),
        "providers": ProvidersSettings(
            llm=ProviderSettings(provider="mock", model="mock", api_key="", base_url=""),
            image=ProviderSettings(provider="mock", model="mock", api_key="", base_url=""),
            video=ProviderSettings(provider="mock", model="mock", api_key="", base_url=""),
            tts=ProviderSettings(provider="mock", model="mock", api_key="", base_url=""),
        ),
        "max_shots_per_project": 8,
    }
    base.update(overrides)
    settings = Settings(**base)  # type: ignore[arg-type]
    settings.rate_limit.enabled = rate_limit_enabled
    settings.ensure_dirs()
    return settings


@pytest.fixture
def settings(tmp_workspace: Path) -> Settings:
    return build_settings(tmp_workspace)


@pytest.fixture
async def db(settings: Settings) -> None:
    """初始化数据库引擎并建表。"""
    storage_svc.reset_storage()
    init_engine(settings.database)
    await create_all()
    try:
        yield  # type: ignore[misc]
    finally:
        await dispose_engine()
        storage_svc.reset_storage()


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
@pytest.fixture
def app(settings: Settings, db: None):
    from reelagent_cloud.api.ratelimit import reset_limiter_sync

    reset_limiter_sync()
    application = create_app(settings)
    yield application
    reset_limiter_sync()


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- #
# 账号
# --------------------------------------------------------------------------- #
DEFAULT_PASSWORD = "Str0ng-Passw0rd!23"


@pytest.fixture
def signup_payload() -> dict[str, str]:
    return {
        "email": f"user_{uuid.uuid4().hex[:8]}@example.com",
        "password": DEFAULT_PASSWORD,
        "display_name": "测试用户",
        "org_name": "测试工作室",
        "agree": "on",
    }


@pytest.fixture
def authed_client(client: TestClient, signup_payload: dict[str, str]) -> TestClient:
    """已完成注册并带上会话 Cookie 的客户端。

    注册表单启用了匿名 CSRF 校验，因此必须先 GET 一次拿到 Cookie 与隐藏 token，
    否则会被 403 拦掉。
    """
    page = client.get("/signup")
    token = extract_csrf_token(page.text)
    assert token, "注册页没有下发 CSRF token"

    resp = client.post("/signup", data={**signup_payload, "csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 303, f"注册失败：{resp.status_code} {resp.text[:300]}"
    return client


def extract_csrf_token(html: str) -> str:
    """从 HTML 里抠出 CSRF 隐藏字段的值。"""
    marker = 'name="csrf_token"'
    idx = html.find(marker)
    if idx < 0:
        return ""
    value_idx = html.find('value="', idx)
    if value_idx < 0:
        return ""
    end = html.find('"', value_idx + len('value="'))
    return html[value_idx + len('value="') : end]


def signup_via_form(client: TestClient, payload: dict[str, str]):
    """走完整注册表单流程：先 GET 取 CSRF token，再 POST。

    注册表单启用了匿名 CSRF 校验，直接 POST 会被 403。
    """
    token = extract_csrf_token(client.get("/signup").text)
    assert token, "注册页没有下发 CSRF token"
    return client.post("/signup", data={**payload, "csrf_token": token}, follow_redirects=False)


def unique_signup_payload(prefix: str = "user") -> dict[str, str]:
    return {
        "email": f"{prefix}_{uuid.uuid4().hex[:8]}@example.com",
        "password": DEFAULT_PASSWORD,
        "display_name": "测试用户",
        "org_name": "测试工作室",
        "agree": "on",
    }


@pytest.fixture
def second_client(app) -> Iterator[TestClient]:
    """另一个组织的已登录客户端，用于验证租户隔离。"""
    with TestClient(app) as other:
        resp = signup_via_form(other, unique_signup_payload("other"))
        assert resp.status_code == 303, f"第二个用户注册失败：{resp.status_code}"
        yield other


@pytest.fixture
def sync_db(settings: Settings) -> Iterator[Any]:
    """对同一个 SQLite 文件做**同步**读写的直连。

    为什么需要它：TestClient 与 pytest-asyncio 各用各的事件循环，
    而 aiosqlite 的连接绑定在创建它的循环上。在测试里新开一个循环去操作
    同一个引擎，会拿到 401/空结果这类莫名其妙的失败。
    直接开一个 sqlite3 连接做夹具数据准备，绕开整个循环问题。
    """
    import sqlite3

    path = settings.database.url.replace("sqlite+aiosqlite:///", "")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def csrf(authed_client: TestClient) -> str:
    return authed_client.cookies.get("reelagent_csrf", "")


@pytest.fixture
async def org_id(authed_client: TestClient, settings: Settings) -> str:
    """取当前登录用户所属组织 ID。"""
    from sqlalchemy import select

    from reelagent_cloud.db.models import Organization

    async with session_scope() as session:
        org = (await session.execute(select(Organization))).scalars().first()
        assert org is not None
        return org.id
