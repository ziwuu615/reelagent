"""认证路由：注册、登录、登出。

表单提交的错误处理刻意与 JSON API 不同：
**重新渲染页面并带上错误消息**，而不是返回 JSON —— 用户看到的是表单上的红色提示，
而不是一坨 JSON。这是产品体验的基本要求，但很多 FastAPI 项目会漏掉。
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from ...config import Settings
from ...db.session import session_scope
from ...deps import AppSettings, DbSession, client_ip, user_agent
from ...domain.enums import AuditAction
from ...domain.errors import AppError, Forbidden, Unauthorized
from ...domain.plans import SIGNUP_GRANT_CREDITS
from ...observability import auth_failures_total, get_logger
from ...security import (
    OptionalPrincipal,
    issue_session,
    new_csrf_token,
    normalize_email,
)
from ...services import accounts, audit
from ...services import email as email_svc
from ..ratelimit import get_limiter, limits_from
from ..templating import get_templates

log = get_logger(__name__)
router = APIRouter(tags=["auth"])


def _set_session_cookies(response: Response, settings: Settings, token: str) -> None:
    """写入会话 Cookie 与 CSRF Cookie。

    - 会话 Cookie：HttpOnly，JS 读不到，防 XSS 窃取
    - CSRF Cookie：非 HttpOnly，前端要读出来放进请求头
    - SameSite=Lax：阻止跨站 POST，同时保留从外部链接进来的可用性
    """
    max_age = settings.security.session_max_age_seconds
    response.set_cookie(
        settings.security.session_cookie,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.security.https_only,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.security.csrf_cookie,
        new_csrf_token(),
        max_age=max_age,
        httponly=False,
        secure=settings.security.https_only,
        samesite="lax",
        path="/",
    )


def _clear_session_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.security.session_cookie, path="/")
    response.delete_cookie(settings.security.csrf_cookie, path="/")


def _verify_anonymous_csrf(request: Request, submitted: str, settings: Settings) -> None:
    cookie = request.cookies.get(settings.security.csrf_cookie, "")
    if not cookie or not submitted or not hmac.compare_digest(cookie, submitted):
        auth_failures_total.labels(reason="csrf_mismatch").inc()
        raise Forbidden("页面已过期，请刷新后重试")


def _render(
    request: Request,
    template: str,
    context: dict,
    settings: Settings,
    *,
    status_code: int = 200,
) -> HTMLResponse:
    """渲染公开页面，并保证匿名访客也拿到可用的 CSRF token。

    **必须先决定 token 再渲染**：早期写法是先渲染、再往响应上补 Cookie，
    结果首次访问时隐藏字段里是空串，表单必然校验失败。
    """
    token = request.cookies.get(settings.security.csrf_cookie, "")
    needs_cookie = not token
    if needs_cookie:
        token = new_csrf_token()

    response = get_templates().TemplateResponse(
        request, template, {**context, "csrf_token": token}, status_code=status_code
    )
    if needs_cookie:
        response.set_cookie(
            settings.security.csrf_cookie,
            token,
            max_age=settings.security.session_max_age_seconds,
            httponly=False,
            secure=settings.security.https_only,
            samesite="lax",
            path="/",
        )
    return response


def _safe_next(value: str) -> str:
    """防开放重定向：只允许站内相对路径。"""
    candidate = (value or "").strip()
    if candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    return "/app"


# --------------------------------------------------------------------------- #
# 登录
# --------------------------------------------------------------------------- #
@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(
    request: Request,
    settings: AppSettings,
    principal: OptionalPrincipal,
    next: str = "/app",
):
    if principal is not None:
        return RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    return _render(
        request,
        "auth/login.html",
        {"next": _safe_next(next), "error": "", "email": ""},
        settings,
    )


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_submit(
    request: Request,
    settings: AppSettings,
    session: DbSession,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form("/app"),
    csrf_token: str = Form(""),
):
    limiter = get_limiter(settings)
    await limiter.check(limits_from(settings)["login"], client_ip(request))

    try:
        _verify_anonymous_csrf(request, csrf_token, settings)
    except Forbidden as exc:
        return _render(
            request,
            "auth/login.html",
            {"next": _safe_next(next), "error": exc.message, "email": email},
            settings,
            status_code=status.HTTP_403_FORBIDDEN,
        )

    try:
        user = await accounts.authenticate(
            session,
            settings,
            email=email,
            password=password,
            ip=client_ip(request),
            user_agent=user_agent(request),
        )
    except AppError as exc:
        log.info("login_rejected", email=normalize_email(email)[:80], reason=exc.code)
        return _render(
            request,
            "auth/login.html",
            {"next": _safe_next(next), "error": exc.message, "email": email},
            settings,
            status_code=status.HTTP_401_UNAUTHORIZED
            if isinstance(exc, Unauthorized)
            else status.HTTP_403_FORBIDDEN,
        )

    response = RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookies(
        response, settings, issue_session(settings, user_id=user.id, session_version=user.session_version)
    )
    log.info("login_ok", user_id=user.id)
    return response


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #
@router.get("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup_page(
    request: Request,
    settings: AppSettings,
    principal: OptionalPrincipal,
):
    if principal is not None:
        return RedirectResponse("/app", status_code=status.HTTP_303_SEE_OTHER)
    if not settings.security.allow_signup:
        return _render(
            request,
            "auth/signup_closed.html",
            {},
            settings,
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return _render(
        request,
        "auth/signup.html",
        {"error": "", "email": "", "display_name": "", "org_name": ""},
        settings,
    )


@router.post("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup_submit(
    request: Request,
    settings: AppSettings,
    email: str = Form(""),
    password: str = Form(""),
    display_name: str = Form(""),
    org_name: str = Form(""),
    invite_code: str = Form(""),
    agree: str = Form(""),
    csrf_token: str = Form(""),
):
    limiter = get_limiter(settings)
    await limiter.check(limits_from(settings)["signup"], client_ip(request))

    form_state = {
        "error": "",
        "email": email,
        "display_name": display_name,
        "org_name": org_name,
    }

    try:
        _verify_anonymous_csrf(request, csrf_token, settings)
    except Forbidden as exc:
        form_state["error"] = exc.message
        return _render(
            request,
            "auth/signup.html",
            form_state,
            settings,
            status_code=status.HTTP_403_FORBIDDEN,
        )

    if not agree:
        form_state["error"] = "请先阅读并同意服务条款与隐私政策"
        return _render(
            request,
            "auth/signup.html",
            form_state,
            settings,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        # 独立事务：注册成功后再发欢迎邮件，邮件失败不影响注册结果
        async with session_scope() as session:
            user, _org, _membership = await accounts.signup(
                session,
                settings,
                email=email,
                password=password,
                display_name=display_name,
                org_name=org_name,
                invite_code=invite_code,
                ip=client_ip(request),
                user_agent=user_agent(request),
            )
            user_id, session_version = user.id, user.session_version
            subject, text, html = email_svc.welcome_email(
                settings, display_name=user.display_name or "创作者", credits=SIGNUP_GRANT_CREDITS
            )
            await email_svc.enqueue(
                session, to_address=user.email, subject=subject, body_text=text, body_html=html
            )
    except AppError as exc:
        log.info("signup_rejected", email=normalize_email(email)[:80], reason=exc.code)
        form_state["error"] = exc.message
        return _render(
            request,
            "auth/signup.html",
            form_state,
            settings,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    response = RedirectResponse("/app?welcome=1", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookies(
        response, settings, issue_session(settings, user_id=user_id, session_version=session_version)
    )
    log.info("signup_ok", user_id=user_id)
    return response


# --------------------------------------------------------------------------- #
# 登出
# --------------------------------------------------------------------------- #
@router.post("/logout", include_in_schema=False)
async def logout(request: Request, settings: AppSettings, session: DbSession):
    principal = None
    try:
        from ...security import get_optional_principal

        principal = await get_optional_principal(request, session, settings)
    except Exception:  # pragma: no cover
        principal = None

    if principal is not None and principal.user is not None:
        await audit.record(
            session,
            action=AuditAction.USER_LOGOUT,
            org_id=principal.org_id,
            actor_user_id=principal.user_id,
            ip=client_ip(request),
        )

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    _clear_session_cookies(response, settings)
    return response


@router.post("/logout-all", include_in_schema=False)
async def logout_all(request: Request, settings: AppSettings, session: DbSession):
    """登出所有设备：会话代际 +1，所有已签发 Cookie 立即失效。"""
    from ...security import get_optional_principal

    principal = await get_optional_principal(request, session, settings)
    if principal is not None and principal.user is not None:
        await accounts.logout_all_devices(session, user=principal.user)
        await audit.record(
            session,
            action=AuditAction.USER_LOGOUT,
            org_id=principal.org_id,
            actor_user_id=principal.user_id,
            ip=client_ip(request),
            meta={"scope": "all_devices"},
        )

    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    _clear_session_cookies(response, settings)
    return response
