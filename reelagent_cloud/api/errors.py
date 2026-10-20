"""错误映射：把领域异常翻译成统一的 HTTP 响应。

统一响应体（前端与 API 调用方只需处理一种结构）：

    {"error": {"code": "insufficient_credits", "message": "...", "details": {...}},
     "request_id": "..."}

关键原则：**内部异常绝不外泄细节**。未预期异常统一返回 500 + 通用文案，
真实堆栈只进日志，并在响应里带上 request_id 供排障关联。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..domain.errors import AppError
from ..observability import current_context, get_logger

log = get_logger(__name__)


def error_response(
    code: str,
    message: str,
    *,
    status_code: int,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload: dict[str, Any] = {
        "error": {"code": code, "message": message},
        "request_id": current_context().get("request_id", ""),
    }
    if details:
        payload["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


#: 控制台（浏览器页面）路径前缀。这些路径下未登录要跳转登录页，
#: 而不是返回 401 JSON。
CONSOLE_PREFIX = "/app"


def _wants_html(request: Request) -> bool:
    """判断这个请求是不是浏览器在导航页面。

    依据是路径前缀 + Accept 头：真实的浏览器导航会带 `text/html`，
    而 fetch/XHR 一般带 `application/json`。两者都看，避免把
    控制台内部的 XHR 也重定向（那样前端拿到 303 会解析失败）。
    """
    if not request.url.path.startswith(CONSOLE_PREFIX):
        return False
    accept = request.headers.get("accept", "")
    return "text/html" in accept


async def handle_app_error(request: Request, exc: AppError) -> Response:
    level = log.warning if exc.http_status < 500 else log.error
    level(
        "app_error",
        code=exc.code,
        status=exc.http_status,
        path=request.url.path,
        detail=exc.message[:300],
    )

    # 浏览器访问控制台但未登录 → 跳转登录页。
    #
    # 为什么必须区分：
    #   /api/v1/*  调用方是程序，401 是正确语义，便于客户端分支处理
    #   /app/*     调用方是浏览器。返回 401 JSON 会让用户看到一坨错误文本、
    #              且**没有任何入口去登录**，整个控制台等于不可用。
    if exc.http_status == 401 and _wants_html(request):
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(
            f"/login?next={quote(target, safe='/')}", status_code=status.HTTP_303_SEE_OTHER
        )

    headers: dict[str, str] = {}
    retry_after = exc.details.get("retry_after_seconds")
    if retry_after:
        headers["Retry-After"] = str(int(retry_after))
    return error_response(
        exc.code, exc.message, status_code=exc.http_status, details=exc.details or None, headers=headers
    )


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """把 Pydantic 的校验错误压成用户能看懂的形式。"""
    fields: list[dict[str, str]] = []
    for err in exc.errors()[:10]:
        location = ".".join(str(p) for p in err.get("loc", []) if p not in ("body", "query", "path"))
        fields.append({"field": location or "body", "message": err.get("msg", "校验失败")})
    log.info("validation_failed", path=request.url.path, fields=fields)
    return error_response(
        "validation_failed",
        "请求参数不合法",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        details={"fields": fields},
    )


async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    mapping = {
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        429: "rate_limited",
    }
    code = mapping.get(exc.status_code, f"http_{exc.status_code}")
    message = str(exc.detail) if exc.detail else "请求无法处理"
    return error_response(code, message, status_code=exc.status_code)


async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    """兜底：未预期异常。

    对外只给通用文案 + request_id；完整堆栈进日志。
    """
    log.exception(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        error_type=type(exc).__name__,
    )
    return error_response(
        "internal_error",
        "服务开小差了，请稍后重试。若持续出现请联系支持并提供 request_id",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, handle_unexpected)
