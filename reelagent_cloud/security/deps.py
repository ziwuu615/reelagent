"""认证与授权依赖。

三种身份，能力递增且边界清晰：

  anonymous   匿名访问：只能看落地页/定价页/文档
  session     浏览器会话：完整产品能力，可管理计费与成员
  api_key     程序化凭证：受 scope 限制，**不允许**管理计费与成员

为什么 API Key 不能管计费：API Key 会出现在 CI、脚本、第三方集成里，
泄漏概率远高于浏览器会话。把它限制在「读写项目」范围内，
即使泄漏也不会导致账单被改或成员被踢。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import Depends, Request
from sqlalchemy import select, update

from ..db.models import ApiKey, Membership, Organization, User
from ..deps import AppSettings, DbSession, client_ip
from ..domain.enums import MembershipRole
from ..domain.errors import AccountSuspended, Forbidden, Unauthorized
from ..observability import auth_failures_total, get_logger
from .tokens import TokenError, csrf_matches, mask_api_key, parse_api_key, read_session

log = get_logger(__name__)

PrincipalKind = Literal["session", "api_key"]

#: last_used_at 的写入节流：避免每个 API 请求都产生一次 UPDATE
_LAST_USED_THROTTLE = timedelta(seconds=60)


@dataclass
class Principal:
    kind: PrincipalKind
    org: Organization
    user: User | None = None
    membership: Membership | None = None
    api_key: ApiKey | None = None

    # ------------------------------------------------------------------ #
    @property
    def org_id(self) -> str:
        return self.org.id

    @property
    def user_id(self) -> str | None:
        return self.user.id if self.user else None

    @property
    def role(self) -> MembershipRole:
        if self.kind == "api_key":
            return MembershipRole.MEMBER
        return self.membership.role if self.membership else MembershipRole.MEMBER

    @property
    def scopes(self) -> frozenset[str]:
        if self.kind == "api_key":
            return frozenset(self.api_key.scopes if self.api_key else [])
        return frozenset({"read", "write", "admin"})

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def actor_label(self) -> str:
        if self.kind == "api_key" and self.api_key:
            return f"api_key:{mask_api_key(self.api_key.prefix)}"
        return f"user:{self.user_id}"

    # ------------------------------------------------------------------ #
    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes or "admin" in self.scopes

    def require_scope(self, scope: str) -> None:
        if not self.has_scope(scope):
            raise Forbidden(f"当前凭证缺少 `{scope}` 权限")

    def require_role(self, minimum: MembershipRole) -> None:
        if self.kind != "session":
            raise Forbidden("该操作需要使用浏览器登录（API Key 不具备管理权限）")
        if self.role.rank < minimum.rank:
            raise Forbidden(f"需要 {minimum.value} 及以上权限")

    def require_billing(self) -> None:
        self.require_role(MembershipRole.ADMIN)


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    api_key = request.headers.get("x-api-key", "").strip()
    return api_key or None


async def _principal_from_api_key(session, settings, request: Request, raw_key: str) -> Principal | None:
    prefix = parse_api_key(raw_key)
    if not prefix:
        auth_failures_total.labels(reason="malformed_api_key").inc()
        return None

    from .tokens import hash_api_key

    key_hash = hash_api_key(settings, raw_key)
    api_key = (await session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))).scalar_one_or_none()

    if api_key is None or not api_key.is_active:
        auth_failures_total.labels(reason="invalid_api_key").inc()
        return None

    org = (
        await session.execute(select(Organization).where(Organization.id == api_key.org_id))
    ).scalar_one_or_none()
    if org is None or org.is_suspended:
        auth_failures_total.labels(reason="org_suspended").inc()
        raise AccountSuspended("该组织已被停用，请联系支持")

    # 节流更新使用信息：不是每个请求都写库
    now = datetime.now(UTC)
    if api_key.last_used_at is None or (now - api_key.last_used_at) > _LAST_USED_THROTTLE:
        await session.execute(
            update(ApiKey)
            .where(ApiKey.id == api_key.id)
            .values(
                last_used_at=now,
                last_used_ip=client_ip(request),
                request_count=ApiKey.request_count + 1,
            )
        )

    return Principal(kind="api_key", org=org, api_key=api_key)


async def _principal_from_session(session, settings, request: Request) -> Principal | None:
    token = request.cookies.get(settings.security.session_cookie, "")
    if not token:
        return None
    try:
        data = read_session(settings, token)
    except TokenError as exc:
        auth_failures_total.labels(reason="invalid_session").inc()
        log.info("session_rejected", reason=str(exc))
        return None

    user = (await session.execute(select(User).where(User.id == data["uid"]))).scalar_one_or_none()
    if user is None or not user.is_active:
        auth_failures_total.labels(reason="user_inactive").inc()
        return None

    # 会话代际校验：改密 / 登出所有设备后，旧 Cookie 立即失效
    if int(data.get("sv", 0)) != int(user.session_version):
        auth_failures_total.labels(reason="stale_session_version").inc()
        return None

    if user.locked_until and user.locked_until > datetime.now(UTC):
        raise AccountSuspended("账号已被临时锁定，请稍后再试")

    membership, org = await _resolve_org(session, user, request)
    if org is None:
        return None
    return Principal(kind="session", org=org, user=user, membership=membership)


async def _resolve_org(
    session, user: User, request: Request
) -> tuple[Membership | None, Organization | None]:
    """决定当前会话作用于哪个组织。

    支持 `X-Org-Id` 头切换组织（多组织用户），并**校验成员关系**——
    不做这一步就是最典型的越权漏洞。
    """
    rows = (
        await session.execute(
            select(Membership, Organization)
            .join(Organization, Organization.id == Membership.org_id)
            .where(Membership.user_id == user.id)
            .order_by(Membership.created_at)
        )
    ).all()
    if not rows:
        return None, None

    requested = request.headers.get("x-org-id", "").strip() if request else ""
    if requested:
        for membership, org in rows:
            if org.id == requested:
                return membership, org
        raise Forbidden("你不是该组织的成员")

    membership, org = rows[0]
    if org.is_suspended:
        raise AccountSuspended("该组织已被停用，请联系支持")
    return membership, org


# --------------------------------------------------------------------------- #
# 依赖
# --------------------------------------------------------------------------- #
async def get_optional_principal(
    request: Request, session: DbSession, settings: AppSettings
) -> Principal | None:
    """匿名可用的依赖：用于落地页等公开页面。"""
    try:
        raw_key = _extract_bearer(request)
        if raw_key:
            return await _principal_from_api_key(session, settings, request, raw_key)
        return await _principal_from_session(session, settings, request)
    except (AccountSuspended, Forbidden):
        raise
    except Exception as exc:  # pragma: no cover - 认证环节绝不因内部错误放行
        log.warning("auth_unexpected_error", error=str(exc))
        auth_failures_total.labels(reason="internal_error").inc()
        return None


async def get_principal(principal: Annotated[Principal | None, Depends(get_optional_principal)]) -> Principal:
    if principal is None:
        raise Unauthorized("请先登录")
    return principal


async def require_write(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    principal.require_scope("write")
    return principal


async def require_session(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    """需要浏览器会话（禁止 API Key），用于计费与成员管理。"""
    if principal.kind != "session":
        raise Forbidden("该操作需要使用浏览器登录")
    return principal


async def require_admin(principal: Annotated[Principal, Depends(require_session)]) -> Principal:
    principal.require_role(MembershipRole.ADMIN)
    return principal


async def require_owner(principal: Annotated[Principal, Depends(require_session)]) -> Principal:
    principal.require_role(MembershipRole.OWNER)
    return principal


async def verify_csrf(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    settings: AppSettings,
) -> None:
    """对「Cookie 认证 + 改变状态」的请求强制校验 CSRF。

    API Key 请求天然免疫 CSRF（浏览器不会自动携带），因此跳过。
    """
    if principal.kind != "session":
        return
    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return
    cookie_token = request.cookies.get(settings.security.csrf_cookie, "")
    header_token = request.headers.get(settings.security.csrf_header, "")
    if not csrf_matches(cookie_token, header_token):
        auth_failures_total.labels(reason="csrf_mismatch").inc()
        raise Forbidden("CSRF 校验失败，请刷新页面后重试")


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
OptionalPrincipal = Annotated[Principal | None, Depends(get_optional_principal)]
WritablePrincipal = Annotated[Principal, Depends(require_write)]
SessionPrincipal = Annotated[Principal, Depends(require_session)]
AdminPrincipal = Annotated[Principal, Depends(require_admin)]
OwnerPrincipal = Annotated[Principal, Depends(require_owner)]
CsrfProtected = Annotated[None, Depends(verify_csrf)]
