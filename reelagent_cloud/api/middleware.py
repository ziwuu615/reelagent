"""HTTP 中间件。

顺序很重要（FastAPI 中后添加的先执行）：
    1. RequestContext   生成 request_id、绑定日志上下文、记录耗时与指标
    2. SecurityHeaders  注入安全响应头
    3. 路由

安全头采用**保守但可用**的默认值：
CSP 默认拒绝内联脚本，因此模板里所有 JS 都必须走外部文件——
这条约束一开始就定死，避免后期为了"先跑起来"放宽 CSP 再也收不回来。
"""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from ..config import Settings
from ..observability import (
    bind,
    current_context,
    get_logger,
    http_requests_in_flight,
    observe_http,
)

log = get_logger(__name__)

#: 不记录访问日志的路径（探针与静态资源会淹没日志）
QUIET_PATHS = {"/health", "/healthz", "/readyz", "/livez", "/metrics", "/favicon.ico"}
QUIET_PREFIXES = ("/static/", "/assets/")


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next) -> Response:
        # 尊重上游代理传入的 request id，便于跨服务串联
        incoming = request.headers.get("x-request-id", "").strip()
        request_id = incoming[:64] if incoming else uuid.uuid4().hex[:16]

        http_requests_in_flight.inc()
        started = time.perf_counter()
        status_code = 500
        route = ""

        with bind(request_id=request_id):
            try:
                response = await call_next(request)
                status_code = response.status_code
            except Exception:
                # 异常交给上层错误处理器；这里只保证指标与日志被记录
                status_code = 500
                raise
            finally:
                elapsed = time.perf_counter() - started
                http_requests_in_flight.dec()

                route_obj = request.scope.get("route")
                # 用路由模板而不是真实路径，避免指标基数爆炸
                route = getattr(route_obj, "path", "") or request.url.path
                if _is_quiet(request.url.path):
                    route = "__quiet__" if route not in ("/health",) else route
                else:
                    observe_http(request.method, route, status_code, elapsed)

            response.headers["X-Request-ID"] = request_id
            response.headers["Server-Timing"] = f"app;dur={elapsed * 1000:.1f}"

            if not _is_quiet(request.url.path):
                level = log.warning if status_code >= 500 else log.info
                level(
                    "http_request",
                    method=request.method,
                    path=request.url.path,
                    route=route,
                    status=status_code,
                    duration_ms=round(elapsed * 1000, 1),
                    slow=elapsed * 1000 > self.settings.observability.slow_request_ms,
                )
            return response


def _is_quiet(path: str) -> bool:
    return path in QUIET_PATHS or path.startswith(QUIET_PREFIXES)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        settings = self.settings

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=(), interest-cohort=()"
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")

        # 上传的媒体资源允许跨源展示（成片要嵌在页面里），其余一律同源
        if request.url.path.startswith(("/files/", "/s/")):
            response.headers.setdefault("Cross-Origin-Resource-Policy", "cross-origin")
        else:
            response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")

        if settings.security.https_only:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

        if not response.headers.get("Content-Security-Policy"):
            response.headers["Content-Security-Policy"] = _csp(settings)
        return response


def _csp(settings: Settings) -> str:
    """内容安全策略。

    不使用 `unsafe-inline`：所有脚本与样式都在外部文件里。
    需要内联样式的场景（如进度条宽度）通过 `style-src 'unsafe-inline'` 单独放行，
    脚本则完全不放开。
    """
    connect = ["'self'"]
    if not settings.is_production:
        # 开发时允许直连本地 API 文档的 websocket 等
        connect += ["ws:", "wss:"]

    directives = [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "font-src 'self' data:",
        f"connect-src {' '.join(connect)}",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
    ]
    if settings.billing.enabled:
        # Stripe Checkout 需要跳转与 iframe
        directives[5] = f"connect-src {' '.join([*connect, 'https://api.stripe.com'])}"
        directives.append("frame-src https://js.stripe.com https://checkout.stripe.com")
        directives.append("script-src 'self' https://js.stripe.com")
    return "; ".join(directives)


def install_middleware(app, settings: Settings) -> None:
    """注册顺序：后添加者先执行。"""
    from starlette.middleware.gzip import GZipMiddleware
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(RequestContextMiddleware, settings=settings)
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    hosts = settings.security.allowed_hosts
    if hosts and "*" not in hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)


def request_id_of(request: Request) -> str:
    return current_context().get("request_id", "") or request.headers.get("x-request-id", "")
