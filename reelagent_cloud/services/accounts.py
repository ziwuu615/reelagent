"""账号与组织。

注册流程在一个事务内完成四件事：
  1. 建用户（口令 Argon2 哈希）
  2. 建组织（slug 唯一化）
  3. 建 owner 成员关系
  4. 发放注册赠送额度 + 建免费订阅记录

**必须同事务**：任何一步失败都整体回滚，否则会出现「有用户没组织」
这种需要人工修数据的脏状态。

登录侧做三件事：
  - 口令校验（固定耗时的比对，避免用户名枚举）
  - 失败计数与临时锁定（抗暴力破解）
  - 登录成功时按需 rehash（Argon2 参数升级后自动迁移）
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.base import utcnow
from ..db.models import ApiKey, Membership, Organization, Subscription, User
from ..domain.enums import (
    AuditAction,
    LedgerKind,
    MembershipRole,
    PlanTier,
    SubscriptionStatus,
)
from ..domain.errors import (
    AccountLocked,
    AlreadyExists,
    Forbidden,
    NotFound,
    Unauthorized,
    ValidationFailed,
)
from ..domain.ids import PREFIX, new_id
from ..domain.plans import SIGNUP_GRANT_CREDITS, Plan, get_plan
from ..observability import auth_failures_total, get_logger
from ..security.passwords import (
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from ..security.tokens import (
    generate_api_key,
    mask_api_key,
    normalize_email,
    normalize_slug,
)
from . import audit, credits

log = get_logger(__name__)

#: 连续失败多少次后锁定
LOCK_THRESHOLD = 8
LOCK_MINUTES = 15


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    return (
        await session.execute(select(User).where(User.email == normalize_email(email)))
    ).scalar_one_or_none()


async def get_user(session: AsyncSession, user_id: str) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("用户不存在")
    return user


async def organizations_for_user(
    session: AsyncSession, user_id: str
) -> list[tuple[Organization, Membership]]:
    rows = await session.execute(
        select(Organization, Membership)
        .join(Membership, Membership.org_id == Organization.id)
        .where(Membership.user_id == user_id)
        .order_by(Membership.created_at)
    )
    return [(org, mem) for org, mem in rows.all()]


async def unique_slug(session: AsyncSession, base: str) -> str:
    """生成唯一 slug。用带随机后缀而不是自增，避免枚举出组织总量。"""
    root = normalize_slug(base) or "org"
    for _ in range(6):
        candidate = root if _ == 0 else f"{root}-{secrets.token_hex(2)}"
        exists = await session.scalar(
            select(func.count(Organization.id)).where(Organization.slug == candidate)
        )
        if not exists:
            return candidate
    return f"{root}-{secrets.token_hex(4)}"


# --------------------------------------------------------------------------- #
# 注册
# --------------------------------------------------------------------------- #
async def signup(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    password: str,
    display_name: str = "",
    org_name: str = "",
    invite_code: str = "",
    ip: str = "",
    user_agent: str = "",
) -> tuple[User, Organization, Membership]:
    if not settings.security.allow_signup:
        raise Forbidden("本站已关闭注册")

    expected_invite = settings.security.signup_invite_code.get_secret_value()
    if expected_invite:
        import hmac

        if not hmac.compare_digest(invite_code or "", expected_invite):
            raise Forbidden("邀请码不正确")

    normalized_email = normalize_email(email)
    if not normalized_email or "@" not in normalized_email:
        raise ValidationFailed("邮箱格式不正确")
    validate_password_strength(
        password, min_length=settings.security.min_password_length, email=normalized_email
    )

    if await get_user_by_email(session, normalized_email) is not None:
        raise AlreadyExists("该邮箱已注册")

    user = User(
        id=new_id(PREFIX["user"]),
        email=normalized_email,
        password_hash=hash_password(password),
        display_name=(display_name or normalized_email.split("@")[0])[:80],
        is_active=True,
        is_verified=False,
    )

    org = Organization(
        id=new_id(PREFIX["org"]),
        name=(org_name or f"{user.display_name} 的空间")[:120],
        slug=await unique_slug(session, org_name or normalized_email.split("@")[0]),
        plan=PlanTier.FREE,
        credits_balance=0,
        default_style=settings.style,
        watermark=True,
    )

    membership = Membership(
        id=new_id(PREFIX["membership"]),
        user_id=user.id,
        org_id=org.id,
        role=MembershipRole.OWNER,
        last_active_at=utcnow(),
    )

    subscription = Subscription(
        id=new_id(PREFIX["subscription"]),
        org_id=org.id,
        plan=PlanTier.FREE,
        status=SubscriptionStatus.ACTIVE,
        current_period_start=utcnow(),
        current_period_end=utcnow() + timedelta(days=30),
    )

    # ⚠️ 必须**分两步** flush，不能一次性 add 四个对象再 flush。
    #
    # 原因：SQLAlchemy 的 unit of work 在模型之间**没有声明 `relationship()`** 时，
    # 无法从 ORM 层推断插入顺序，退化为按**表名字母序**排序：
    #
    #     memberships < organizations < subscriptions < users
    #
    # `memberships` 字母序最靠前，于是先于 `users` 被 INSERT ——
    # 而它带着指向 `users` 的外键，Postgres 直接报
    #     ForeignKeyViolationError: memberships_user_id_fkey
    #     Key (user_id)=(usr_...) is not present in table "users"
    #
    # 这个 bug 在 SQLite 上永远复现不了：SQLite 默认**不启用**外键约束
    # （需要显式 `PRAGMA foreign_keys=ON`），所以本地跑全绿、一上 Postgres 就 500。
    # 是真实部署才暴露出来的，不是测试能兜住的（测试库也没开 PRAGMA）。
    session.add(user)
    session.add(org)
    await session.flush()  # users / organizations 先落库（账本写入也需要 org 行存在）

    session.add(membership)
    session.add(subscription)
    await session.flush()

    await credits.grant(
        session,
        org_id=org.id,
        amount=SIGNUP_GRANT_CREDITS,
        kind=LedgerKind.SIGNUP_GRANT,
        description=f"注册赠送 {SIGNUP_GRANT_CREDITS} 点",
        idempotency_key=f"signup:{user.id}",
    )

    await audit.record(
        session,
        action=AuditAction.USER_SIGNUP,
        org_id=org.id,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        ip=ip,
        user_agent=user_agent,
        meta={"plan": PlanTier.FREE.value},
    )

    log.info("user_signed_up", user_id=user.id, org_id=org.id, email=normalized_email)
    return user, org, membership


# --------------------------------------------------------------------------- #
# 登录
# --------------------------------------------------------------------------- #
async def authenticate(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    password: str,
    ip: str = "",
    user_agent: str = "",
) -> User:
    normalized_email = normalize_email(email)
    user = await get_user_by_email(session, normalized_email)

    # 用户不存在时也走一次哈希校验，避免通过响应时间枚举已注册邮箱
    if user is None:
        verify_password(password, hash_password("dummy-password-for-timing"))
        auth_failures_total.labels(reason="unknown_user").inc()
        raise Unauthorized("邮箱或密码不正确")

    now = datetime.now(UTC)
    if user.locked_until and user.locked_until > now:
        remaining = int((user.locked_until - now).total_seconds() // 60) + 1
        raise AccountLocked(f"登录失败次数过多，请 {remaining} 分钟后重试")

    if not user.is_active:
        auth_failures_total.labels(reason="inactive").inc()
        raise Unauthorized("账号已停用，请联系支持")

    if not verify_password(password, user.password_hash):
        user.failed_login_count = int(user.failed_login_count) + 1
        if user.failed_login_count >= LOCK_THRESHOLD:
            user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            log.warning("account_locked", user_id=user.id, failures=user.failed_login_count)
        auth_failures_total.labels(reason="bad_password").inc()
        await audit.record(
            session,
            action=AuditAction.USER_LOGIN_FAILED,
            actor_user_id=user.id,
            target_type="user",
            target_id=user.id,
            ip=ip,
            user_agent=user_agent,
            meta={"failures": user.failed_login_count},
        )
        raise Unauthorized("邮箱或密码不正确")

    # 成功：清零失败计数，按需升级哈希参数
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        log.info("password_rehashed", user_id=user.id)

    await audit.record(
        session,
        action=AuditAction.USER_LOGIN,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        ip=ip,
        user_agent=user_agent,
    )
    log.info("user_logged_in", user_id=user.id)
    return user


async def change_password(
    session: AsyncSession,
    *,
    user: User,
    current_password: str,
    new_password: str,
    settings: Settings,
    ip: str = "",
) -> None:
    if not verify_password(current_password, user.password_hash):
        raise Unauthorized("当前密码不正确")
    validate_password_strength(
        new_password, min_length=settings.security.min_password_length, email=user.email
    )
    user.password_hash = hash_password(new_password)
    # 会话代际 +1：所有已登录设备立即失效，必须重新登录
    user.session_version = int(user.session_version) + 1
    await audit.record(
        session,
        action=AuditAction.USER_PASSWORD_CHANGED,
        actor_user_id=user.id,
        target_type="user",
        target_id=user.id,
        ip=ip,
    )
    log.info("password_changed", user_id=user.id)


async def logout_all_devices(session: AsyncSession, *, user: User) -> None:
    user.session_version = int(user.session_version) + 1
    log.info("logout_all_devices", user_id=user.id)


# --------------------------------------------------------------------------- #
# API Key
# --------------------------------------------------------------------------- #
async def create_api_key(
    session: AsyncSession,
    settings: Settings,
    *,
    org_id: str,
    user_id: str,
    name: str,
    scopes: list[str] | None = None,
    expires_in_days: int | None = None,
) -> tuple[ApiKey, str]:
    """创建 API Key。明文只在返回值里出现这一次，之后无法再取回。"""
    plan = await _plan_for_org(session, org_id)
    if plan.is_free:
        raise Forbidden("免费版不支持 API Key，请升级到专业版")

    active = await session.scalar(
        select(func.count(ApiKey.id)).where(ApiKey.org_id == org_id, ApiKey.revoked_at.is_(None))
    )
    if int(active or 0) >= 20:
        raise Forbidden("API Key 数量已达上限（20），请先撤销不再使用的 Key")

    generated = generate_api_key(settings)
    key = ApiKey(
        id=new_id(PREFIX["api_key"]),
        org_id=org_id,
        created_by=user_id,
        name=(name or "未命名密钥")[:80],
        prefix=generated.prefix,
        key_hash=generated.key_hash,
        scopes=scopes or ["read", "write"],
        expires_at=utcnow() + timedelta(days=expires_in_days) if expires_in_days else None,
    )
    session.add(key)
    await audit.record(
        session,
        action=AuditAction.API_KEY_CREATED,
        org_id=org_id,
        actor_user_id=user_id,
        target_type="api_key",
        target_id=key.id,
        meta={"prefix": mask_api_key(generated.prefix)},
    )
    log.info("api_key_created", org_id=org_id, prefix=generated.prefix)
    return key, generated.full_key


async def revoke_api_key(session: AsyncSession, *, org_id: str, key_id: str, user_id: str) -> None:
    key = (
        await session.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.org_id == org_id))
    ).scalar_one_or_none()
    if key is None:
        raise NotFound("密钥不存在")
    key.revoked_at = utcnow()
    await audit.record(
        session,
        action=AuditAction.API_KEY_REVOKED,
        org_id=org_id,
        actor_user_id=user_id,
        target_type="api_key",
        target_id=key_id,
    )


async def list_api_keys(session: AsyncSession, org_id: str) -> list[ApiKey]:
    rows = await session.execute(
        select(ApiKey).where(ApiKey.org_id == org_id).order_by(ApiKey.created_at.desc())
    )
    return list(rows.scalars())


# --------------------------------------------------------------------------- #
# 成员
# --------------------------------------------------------------------------- #
async def memberships_for_org(session: AsyncSession, org_id: str) -> list[tuple[Membership, User]]:
    rows = await session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.org_id == org_id)
        .order_by(Membership.created_at)
    )
    return [(mem, user) for mem, user in rows.all()]


async def add_member(
    session: AsyncSession,
    *,
    org_id: str,
    email: str,
    role: MembershipRole,
    invited_by: str,
) -> Membership:
    plan = await _plan_for_org(session, org_id)
    count = await session.scalar(select(func.count(Membership.id)).where(Membership.org_id == org_id))
    if plan.max_org_members > 0 and int(count or 0) >= plan.max_org_members:
        raise Forbidden(f"{plan.name}最多支持 {plan.max_org_members} 名成员，请升级套餐")

    user = await get_user_by_email(session, email)
    if user is None:
        raise NotFound("该邮箱尚未注册，请先让对方注册账号")

    existing = await session.scalar(
        select(func.count(Membership.id)).where(Membership.org_id == org_id, Membership.user_id == user.id)
    )
    if existing:
        raise AlreadyExists("该用户已是团队成员")

    membership = Membership(
        id=new_id(PREFIX["membership"]),
        user_id=user.id,
        org_id=org_id,
        role=role,
        invited_by=invited_by,
    )
    session.add(membership)
    await audit.record(
        session,
        action=AuditAction.MEMBER_INVITED,
        org_id=org_id,
        actor_user_id=invited_by,
        target_type="user",
        target_id=user.id,
        meta={"role": role.value},
    )
    return membership


async def remove_member(session: AsyncSession, *, org_id: str, user_id: str, actor_id: str) -> None:
    membership = (
        await session.execute(
            select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
        )
    ).scalar_one_or_none()
    if membership is None:
        raise NotFound("成员不存在")
    if membership.role is MembershipRole.OWNER:
        raise Forbidden("不能移除组织所有者，请先转移所有权")
    await session.delete(membership)
    await audit.record(
        session,
        action=AuditAction.MEMBER_REMOVED,
        org_id=org_id,
        actor_user_id=actor_id,
        target_type="user",
        target_id=user_id,
    )


# --------------------------------------------------------------------------- #
# 内部
# --------------------------------------------------------------------------- #
async def _plan_for_org(session: AsyncSession, org_id: str) -> Plan:
    org = await session.get(Organization, org_id)
    if org is None:
        raise NotFound("组织不存在")
    return get_plan(org.plan)
