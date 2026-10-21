"""FastAPI 应用工厂。

启动顺序（顺序错了会踩坑）：
  1. 配置与日志——**最先**，否则启动期的报错看不到结构化日志
  2. 数据库引擎——后续所有依赖它的初始化都要在此之后
  3. 迁移检查——代码比库新是生产事故的头号来源，宁可起不来
  4. 中间件与路由

关闭顺序相反，且必须显式释放连接池与 Redis，
否则滚动更新时会出现「老进程握着连接不放」导致的连接数打满。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text

from ..config import Settings, get_settings
from ..db.session import create_all, dispose_engine, get_engine, init_engine
from ..observability import configure_logging, get_logger
from ..web import STATIC_DIR
from .errors import register_error_handlers
from .middleware import install_middleware
from .ratelimit import get_limiter, reset_limiter
from .routes import api as api_routes
from .routes import auth as auth_routes
from .routes import billing as billing_routes
from .routes import console as console_routes
from .routes import files as files_routes
from .routes import pages as pages_routes
from .routes import system as system_routes

log = get_logger(__name__)

DESCRIPTION = """
**ReelAgent** 是面向短视频创作的 AI 生产平台。

给它一段素材，它会自主完成：
剧本拆解 → 角色与场景设定 → 逐镜生成 → 配音 → 质量评测 → 合成成片，
并保证跨镜头角色与场景一致。

### 鉴权
浏览器会话（Cookie）或 API Key：

```
Authorization: Bearer reel_xxxxxxxx_yyyyyyyy
```

### 幂等
创建生成的接口支持 `Idempotency-Key` 请求头。网络超时后重试请复用同一个 Key，
服务端不会重复扣费。

### 错误
所有错误统一返回：

```json
{"error": {"code": "insufficient_credits", "message": "...", "details": {}},
 "request_id": "..."}
```

排障时请提供 `request_id`。
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    started = time.perf_counter()

    configure_logging(
        level=settings.observability.log_level,
        fmt=settings.observability.log_format,
    )
    log.info(
        "app_starting",
        env=settings.env.value,
        base_url=settings.base_url,
        config=settings.safe_dump(),
    )

    settings.ensure_dirs()
    init_engine(settings.database)
    await _ensure_schema(settings)

    limiter = get_limiter(settings)
    log.info("rate_limiter_ready", backend=limiter.backend)

    if settings.observability.sentry_dsn.get_secret_value():
        _init_sentry(settings)

    log.info("app_ready", boot_ms=round((time.perf_counter() - started) * 1000, 1))

    try:
        yield
    finally:
        log.info("app_stopping")
        await reset_limiter()
        await dispose_engine()
        log.info("app_stopped")


async def _ensure_schema(settings: Settings) -> None:
    """确认数据库结构就绪。

    生产环境**只检查不修改**：结构变更必须走 Alembic，
    否则一次误部署就可能悄悄改掉线上表结构。
    开发环境（SQLite）允许自动建表，降低上手门槛。
    """
    engine = get_engine()
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))

    required = {"users", "organizations", "projects", "jobs", "ledger_entries"}
    missing = required - tables

    if not missing:
        return

    if settings.is_production:
        raise RuntimeError(f"数据库缺少必要表：{sorted(missing)}。请先执行 `alembic upgrade head`。")

    log.warning("auto_creating_schema", missing=sorted(missing), reason="非生产环境")
    await create_all()
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


def _init_sentry(settings: Settings) -> None:
    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover
        log.warning("sentry_not_installed")
        return
    sentry_sdk.init(
        dsn=settings.observability.sentry_dsn.get_secret_value(),
        environment=settings.env.value,
        traces_sample_rate=0.1,
        send_default_pii=False,  # 不把用户邮箱等 PII 发给第三方
    )
    log.info("sentry_initialized")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title=f"{settings.product_name} API",
        version="1.0.0",
        description=DESCRIPTION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        contact={"name": settings.product_name, "email": settings.support_email},
        license_info={"name": "Apache-2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0"},
        servers=[{"url": settings.base_url.rstrip("/"), "description": settings.env.value}],
    )
    app.state.settings = settings

    # 模板层是进程级单例，必须显式告诉它「这次用哪份配置」。
    # 不这么做的话，create_app(settings) 的参数对模板无效 ——
    # 页面会一直用进程启动时的全局配置渲染（表现为改了 product_name /
    # icp_beian 却不生效，且被 lru_cache 永久冻结）。
    from .templating import set_template_settings

    set_template_settings(settings)

    register_error_handlers(app)
    install_middleware(app, settings)

    # 静态资源
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # 路由：系统探针放最前，不受业务路由影响
    app.include_router(system_routes.router)
    app.include_router(billing_routes.router)  # Webhook 必须在最靠前，且无鉴权
    app.include_router(auth_routes.router)
    app.include_router(api_routes.router)
    app.include_router(files_routes.router)
    app.include_router(console_routes.router)
    app.include_router(pages_routes.router)  # 通配的落地页放最后

    _install_fallbacks(app)
    return app


def _install_fallbacks(app: FastAPI) -> None:
    @app.exception_handler(404)
    async def not_found(request: Request, exc: Exception) -> Response:
        from ..observability import current_context

        # API 路径返回 JSON，页面路径返回 HTML —— 让调用方拿到符合预期的格式
        if request.url.path.startswith(("/api/", "/webhooks/", "/files/")):
            return JSONResponse(
                {
                    "error": {"code": "not_found", "message": "接口不存在"},
                    "request_id": current_context().get("request_id", ""),
                },
                status_code=404,
            )
        from .templating import get_templates

        return get_templates().TemplateResponse(
            request, "pages/404.html", {"principal": None}, status_code=404
        )


def app_factory() -> FastAPI:
    """给 ASGI 服务器使用的无参工厂（uvicorn --factory）。"""
    return create_app()


def __getattr__(name: str):
    """支持 `uvicorn reelagent_cloud.api.app:app`。

    注意：**不能**在模块里写 `app = None`——那会让属性查找先命中 None，
    本函数永远不会被调用。这里用惰性构造，让 CLI 与测试都能按需建 app。
    """
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
