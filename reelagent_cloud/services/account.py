"""账号注销。

## 为什么「删掉一行 users」不算注销

用户行使删除权时，要求的是「我的数据不再被保留」，而不是「我的登录入口没了」。
如果只软删用户行，下面这些全都还在：
  - 项目与剧本（含用户输入的原始文案）
  - 产物文件（成片、关键帧、音轨——最大的存储块）
  - API Key（仍可鉴权）
  - 正在跑的任务（会继续生成并产生费用）
  - 额度账本（财务上要留，属于法定义务，见下）

所以真正的注销是**一串动作**，且顺序有讲究。

## 顺序与理由

    1. 校验再认证        —— 注销是不可逆操作，必须要求密码，防 CSRF/误触
    2. 取消活跃任务      —— 不取消的话，删完数据任务还在跑，会把产物又写回来
    3. 清除产物对象      —— 先删文件再删记录；反过来会出现「记录没了文件还在」的黑洞
    4. 软删项目          —— 保留行以便审计追溯，但业务上不可见
    5. 吊销 API Key      —— 否则注销后旧 Key 仍能调接口
    6. 匿名化用户        —— 邮箱换成占位值、口令置随机、会话代际 +1
    7. 停用组织          —— is_suspended，阻断一切入口
    8. 写审计            —— 最后写，确保前面每一步都留下了痕迹

## 为什么账本不删

《电子商务法》与会计档案要求交易记录保留一定年限。所以账本、用量记录**保留**，
但其中的用户身份已被匿名化（邮箱改写），无法再关联到自然人。
这个取舍必须写进隐私政策，否则就是「声称删除但实际保留」。

## 为什么匿名化邮箱而不是删用户行

邮箱上有唯一约束。直接删行会让用户无法用同一邮箱重新注册（历史记录残留冲突），
而改写成一个不可路由的占位地址既释放了唯一约束，又不破坏外键关系。
"""

from __future__ import annotations

import secrets
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import utcnow
from ..db.models import ApiKey, Job, Project, User
from ..domain.enums import AuditAction, JobStatus, ProjectStatus
from ..domain.errors import Forbidden, NotFound, ValidationFailed
from ..observability import get_logger
from ..security import passwords as password_svc
from . import audit
from . import retention as retention_svc

log = get_logger(__name__)

#: 匿名化后邮箱的域。用 `.invalid` 是 RFC 2606 保留的不可路由 TLD，
#: 保证这个地址永远不会被真的投递出去。
_ANON_DOMAIN = "deleted.invalid"


async def delete_account(
    session: AsyncSession,
    settings: Any,
    *,
    user: User,
    org_id: str,
    password: str,
) -> dict[str, Any]:
    """注销账号：清数据、禁入口、留审计。

    返回一份「删了什么」的回执 —— 注销类操作必须给用户可核对的回执，
    否则他无法确认自己的数据到底处理掉了没有。
    """
    if not password_svc.verify_password(password, user.password_hash):
        # 用 403 而不是「密码错误」的 401：这是一种权限确认失败，
        # 语义上不同于登录失败，也不该触发登录失败计数与锁定。
        raise Forbidden("密码不正确，无法注销账号")

    from ..db.models import Organization

    org = await session.get(Organization, org_id)
    if org is None:
        raise NotFound("组织不存在")

    now = utcnow()

    # 2. 取消活跃任务（不取消会把产物写回来）
    active = (
        await session.scalars(
            select(Job).where(Job.org_id == org_id, Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)))
        )
    ).all()
    for job in active:
        job.status = JobStatus.CANCELLED
        job.finished_at = now

    # 3+4. 清产物、软删项目
    projects = (
        await session.scalars(select(Project).where(Project.org_id == org_id, Project.deleted_at.is_(None)))
    ).all()
    purged = 0
    freed = 0
    for project in projects:
        result = await retention_svc.purge_project_artifacts(
            session, settings, project_id=project.id, now=now
        )
        purged += result["deleted"]
        freed += result["freed_bytes"]
        project.deleted_at = now
        project.status = ProjectStatus.ARCHIVED

    # 5. 吊销 API Key
    await session.execute(
        update(ApiKey).where(ApiKey.org_id == org_id, ApiKey.revoked_at.is_(None)).values(revoked_at=now)
    )

    # 6. 匿名化用户
    original_email = user.email
    user.email = f"deleted-{user.id}@{_ANON_DOMAIN}"
    user.password_hash = password_svc.hash_password(secrets.token_urlsafe(32))
    user.display_name = "已注销用户"
    user.is_active = False
    user.session_version += 1
    user.meta = {**(user.meta or {}), "deleted_at": now.isoformat(), "deleted_from": "self_service"}

    # 7. 停用组织
    org.is_suspended = True
    org.suspended_reason = "账号已注销"
    org.meta = {**(org.meta or {}), "deleted_at": now.isoformat(), "deleted_by_user": user.id}

    # 8. 审计（复用 ORG_UPDATED + meta 标记，避免为一次注销新增 PG 枚举值）
    await audit.record(
        session,
        action=AuditAction.ORG_UPDATED,
        org_id=org_id,
        actor_user_id=user.id,
        target_type="org",
        target_id=org_id,
        meta={
            "account_deleted": True,
            "cancelled_jobs": len(active),
            "projects_archived": len(projects),
            "artifacts_purged": purged,
            "bytes_freed": freed,
            "email_anonymized": True,
        },
    )

    log.info(
        "account_deleted",
        org_id=org_id,
        user_id=user.id,
        cancelled_jobs=len(active),
        artifacts_purged=purged,
        freed_bytes=freed,
        had_email=bool(original_email),
    )
    return {
        "org_id": org_id,
        "cancelled_jobs": len(active),
        "projects_archived": len(projects),
        "artifacts_purged": purged,
        "bytes_freed": freed,
    }


async def assert_can_delete(session: AsyncSession, *, user: User, org_id: str) -> None:
    """注销前置检查：组织里还有别的成员时不能自助注销。

    一个组织被多人共用时，「一个人的注销」不该摧毁其他人的数据 ——
    这种情况必须走转移所有权或人工处理。
    """
    from ..db.models import Membership

    others = await session.scalar(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id != user.id).limit(1)
    )
    if others is not None:
        raise ValidationFailed("组织中还有其他成员，请先移除成员或转移数据后再注销（可联系客服人工处理）")
