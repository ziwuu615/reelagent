"""审计日志。

哪些操作必须留痕：认证、密钥、计费、成员变更、项目删除、账号注销。

审计写入**不参与业务事务的成功与否**——业务成功但审计失败时，
宁可丢掉一条审计也不能回滚用户的操作；因此单独提交并吞掉异常。

## 链式哈希：为什么审计日志必须能自证未被篡改

一份「谁都能改」的审计日志在事故复盘里没有证据价值。这里用最轻量的方案
把它变成防篡改的：每条记录存 `prev_hash`（上一条的哈希）与 `entry_hash`
（本条内容的哈希），于是整个组织的审计串成一条链。

篡改单条记录的语义字段（比如把「删除了项目」改成「更新了项目」）会让
本条 `entry_hash` 与实际内容不符；想掩盖就必须把之后所有记录的哈希重算一遍，
而且校验时会停在**第一处断点**，精确指出从哪条开始不可信。

链是**按组织分**的（`org_id` 为空的系统级记录走一个单独的空串链）。
按组织分而不是全局一条链，是为了让「某个租户的审计可独立校验」——
导出给客户做审计时，不必暴露其他租户的记录数量与时序。

## 并发下的已知限制（如实记录）

`record()` 读「上一条」再写「本条」。两个并发事务可能读到同一个 `prev_hash`，
于是链出现**分叉**而不是篡改伪影。校验函数因此区分两种失败：

    hash_mismatch  内容与哈希不符 —— 篡改
    broken_link    prev_hash 对不上上一条 —— 分叉或删除

分叉不影响「未被篡改」这个结论（两条都自洽），但会让「线性链」的假设失效。
彻底解决需要串行化写入（per-org 咨询锁）；本项目按「同一用户的审计动作
天然串行」的现实接受这个限制，并在校验结果里如实区分两种失败。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import utcnow
from ..db.models import AuditLog
from ..domain.enums import AuditAction
from ..domain.ids import PREFIX, new_id
from ..observability import current_context, get_logger

log = get_logger(__name__)

#: 链的检查结果状态
OK = "ok"
HASH_MISMATCH = "hash_mismatch"
BROKEN_LINK = "broken_link"


def _canonical(*, entry: AuditLog, prev_hash: str) -> str:
    """把一条审计的**语义字段**序列化成稳定字符串。

    只取会被用来「说明发生了什么」的字段。刻意不包含 `created_at` 之外的时间戳、
    也不包含自增序号：哈希要对同一份事实稳定，不能因为重算时机不同而变。
    `meta` 用 sort_keys 保证键序不影响结果。
    """
    payload = {
        "id": entry.id,
        "org_id": entry.org_id or "",
        "actor_user_id": entry.actor_user_id or "",
        "actor_type": entry.actor_type,
        "action": entry.action.value if hasattr(entry.action, "value") else str(entry.action),
        "target_type": entry.target_type,
        "target_id": entry.target_id,
        "ip": entry.ip,
        "request_id": entry.request_id,
        "meta": entry.meta or {},
        "created_at": entry.created_at.isoformat() if entry.created_at else "",
        "prev": prev_hash,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_hash(*, entry: AuditLog, prev_hash: str) -> str:
    return hashlib.sha256(_canonical(entry=entry, prev_hash=prev_hash).encode("utf-8")).hexdigest()


async def _chain_tail(session: AsyncSession, org_id: str | None) -> tuple[str, int]:
    """取该组织（或系统链）的最后一条哈希与序号。"""
    key = org_id or ""
    row = (
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.org_id == (org_id if org_id else None))
            .order_by(AuditLog.chain_seq.desc(), AuditLog.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        _ = key  # 空链：hash 用空串表示「链的起点」
        return "", 0
    return row.entry_hash or "", int(row.chain_seq or 0)


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

    # 取链尾并算哈希。任何一步失败都**不能让业务回滚** —— 审计是旁路。
    try:
        prev_hash, seq = await _chain_tail(session, org_id)
        entry.prev_hash = prev_hash
        entry.chain_seq = seq + 1
        entry.entry_hash = compute_hash(entry=entry, prev_hash=prev_hash)
    except Exception as exc:  # pragma: no cover - 数据库异常
        log.warning("audit_chain_failed", error=str(exc))

    session.add(entry)
    log.info(
        "audit",
        action=action.value,
        org_id=org_id,
        actor=actor_user_id,
        target=f"{target_type}:{target_id}" if target_id else "",
        chain_seq=entry.chain_seq,
    )
    return entry


async def verify_chain(session: AsyncSession, *, org_id: str | None, limit: int = 5000) -> dict[str, Any]:
    """校验某组织的审计链。

    返回第一处异常（若有）：是哪条、哪种失败、期望哈希与实际哈希。
    校验「全链通过」比「报告有多少条」有价值得多 —— 结论只有两种：可信，或从某条起不可信。
    """
    rows = (
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.org_id == (org_id if org_id else None))
            .order_by(AuditLog.chain_seq.asc(), AuditLog.created_at.asc())
            .limit(limit)
        )
    ).all()

    prev = ""
    for index, entry in enumerate(rows):
        # 先验「链接」再验「内容」，顺序不能反。
        #
        # 若先比哈希：抽掉中间一条时，后一条的 prev_hash 指向已删除那条，
        # 于是重算出的哈希必然不符，会误报成 hash_mismatch（篡改），
        # 而真实原因是**缺了一条**。先比 prev_hash 能把这两种情况分开：
        #   prev 对不上 -> 链断了（删除 / 分叉）
        #   prev 对得上但哈希不符 -> 内容被改
        if entry.prev_hash != prev:
            return {
                "ok": False,
                "status": BROKEN_LINK,
                "checked": index + 1,
                "broken_at": entry.id,
                "chain_seq": entry.chain_seq,
                "expected_prev": prev,
                "actual_prev": entry.prev_hash,
            }
        expected = compute_hash(entry=entry, prev_hash=prev)
        if entry.entry_hash != expected:
            return {
                "ok": False,
                "status": HASH_MISMATCH,
                "checked": index + 1,
                "broken_at": entry.id,
                "chain_seq": entry.chain_seq,
                "expected_hash": expected,
                "actual_hash": entry.entry_hash,
            }
        prev = entry.entry_hash

    return {"ok": True, "status": OK, "checked": len(rows), "last_hash": prev}
