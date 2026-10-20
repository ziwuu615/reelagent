"""审计日志。

哪些操作必须留痕：认证、密钥、计费、成员变更、项目删除。
审计写入**不参与业务事务的成功与否**——业务成功但审计失败时，
宁可丢掉一条审计也不能回滚用户的操作；因此单独提交并吞掉异常。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import utcnow
from ..db.models import AuditLog
from ..domain.enums import AuditAction
from ..domain.ids import PREFIX, new_id
from ..observability import current_context, get_logger

log = get_logger(__name__)


async def record(
    session: AsyncSession,
    *,
    action: AuditAction,
    org_id: str | None = None,
    actor_user_id: str | None = None,
    actor_type: str = "user",
    target_type: str = "",
    target_id: str = "",
    ip: str = "",
    user_agent: str = "",
    meta: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        id=new_id(PREFIX["audit"]),
        org_id=org_id,
        actor_user_id=actor_user_id,
        actor_type=actor_type,
        action=action,
        target_type=target_type,
        target_id=target_id[:64],
        ip=ip[:64],
        user_agent=user_agent[:255],
        request_id=current_context().get("request_id", ""),
        meta=meta or {},
        created_at=utcnow(),
    )
    session.add(entry)
    log.info(
        "audit",
        action=action.value,
        org_id=org_id,
        actor=actor_user_id,
        target=f"{target_type}:{target_id}" if target_id else "",
    )
    return entry
