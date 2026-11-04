"""审计链式哈希：防篡改。

审计日志如果谁都能改，它在事故复盘里就没有证据价值。这里验证三件事：

  1. 正常写入的链能校验通过（否则这个机制一上线就是坏的）
  2. 改动任意一条的**语义字段**能被发现，并指出是哪一条
  3. 抽掉中间一条能被发现（这是「掩盖」最常用的手法）

注意 tamper 之后必须绕过 ORM 直接 UPDATE：走 ORM 会把 entry_hash 一起改掉，
那测的就不是篡改了。
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select, text

from reelagent_cloud.db.session import session_scope
from reelagent_cloud.domain.enums import AuditAction
from reelagent_cloud.domain.ids import PREFIX, new_id
from reelagent_cloud.services import audit

pytestmark = pytest.mark.usefixtures("db")


async def _write_three(org_id: str) -> list[str]:
    ids: list[str] = []
    for i in range(3):
        async with session_scope() as session:
            entry = await audit.record(
                session,
                action=AuditAction.PROJECT_CREATED,
                org_id=org_id,
                actor_user_id="usr_test",
                target_type="project",
                target_id=f"proj_{i}",
                meta={"index": i},
            )
            ids.append(entry.id)
    return ids


async def test_chain_verifies_after_normal_writes() -> None:
    org_id = new_id(PREFIX["org"])
    await _write_three(org_id)

    async with session_scope() as session:
        result = await audit.verify_chain(session, org_id=org_id)

    assert result["ok"] is True
    assert result["status"] == audit.OK
    assert result["checked"] == 3


async def test_tampering_a_field_is_detected() -> None:
    org_id = new_id(PREFIX["org"])
    ids = await _write_three(org_id)

    # 绕过 ORM 直接改语义字段（把「创建」改成「删除」）
    async with session_scope() as session:
        await session.execute(
            text("UPDATE audit_logs SET action = :a WHERE id = :i").bindparams(
                a=AuditAction.PROJECT_DELETED.value, i=ids[1]
            )
        )

    async with session_scope() as session:
        result = await audit.verify_chain(session, org_id=org_id)

    assert result["ok"] is False
    assert result["status"] == audit.HASH_MISMATCH
    assert result["broken_at"] == ids[1]
    assert result["expected_hash"] != result["actual_hash"]


async def test_removing_a_middle_entry_breaks_the_link() -> None:
    org_id = new_id(PREFIX["org"])
    ids = await _write_three(org_id)

    async with session_scope() as session:
        await session.execute(delete(audit.AuditLog).where(audit.AuditLog.id == ids[1]))

    async with session_scope() as session:
        result = await audit.verify_chain(session, org_id=org_id)

    assert result["ok"] is False
    assert result["status"] == audit.BROKEN_LINK
    assert result["broken_at"] == ids[2], "断点应停在被删记录之后的第一个"


async def test_chains_are_isolated_per_org() -> None:
    """一个租户的审计链独立校验，不暴露其他租户的记录量。"""
    org_a = new_id(PREFIX["org"])
    org_b = new_id(PREFIX["org"])
    await _write_three(org_a)
    async with session_scope() as session:
        await audit.record(session, action=AuditAction.USER_LOGIN, org_id=org_b)

    async with session_scope() as session:
        a = await audit.verify_chain(session, org_id=org_a)
        b = await audit.verify_chain(session, org_id=org_b)

    assert a["checked"] == 3
    assert b["checked"] == 1


async def test_chain_seq_is_monotonic() -> None:
    org_id = new_id(PREFIX["org"])
    ids = await _write_three(org_id)

    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(audit.AuditLog)
                .where(audit.AuditLog.org_id == org_id)
                .order_by(audit.AuditLog.chain_seq)
            )
        ).all()

    assert [r.chain_seq for r in rows] == [1, 2, 3]
    assert [r.id for r in rows] == ids
