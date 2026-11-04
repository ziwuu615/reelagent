"""audit log hash chain

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-22 09:30:00.000000

变更说明：
    - 为什么：审计日志要能「自证未被篡改」。只有一张谁都能 UPDATE 的表，
      在事故复盘里没有证据价值。加 prev_hash / entry_hash / chain_seq
      把同一组织的审计串成一条哈希链。
    - 是否可逆：可逆（三列都有默认值，downgrade 直接 drop）。
    - 大表变更是否会锁表：`ADD COLUMN` 带常量默认值在 PostgreSQL 11+
      是元数据操作，不重写表、不长时间持锁。本表增长慢（一组织一天几十行），
      风险可忽略。
    - 是否需要数据回填：**需要**。历史审计没有哈希，它们会以
      `entry_hash=""` 存在。校验逻辑对空哈希的处理是「不通过」，
      因此历史记录需要单独回填或在校验时跳过。
      这里**选择不回填**：伪造历史哈希等于伪造证据，比承认「这段没有链」更糟。
      处理方式：校验从第一条 `entry_hash != ''` 的记录开始，
      文档里写明「链的起点」是哪条。
    - 应用侧灰度：写入侧对哈希计算做了 try/except，计算失败不影响业务。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_logs",
        sa.Column("prev_hash", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "audit_logs",
        sa.Column("entry_hash", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "audit_logs",
        sa.Column("chain_seq", sa.Integer(), nullable=False, server_default="0"),
    )
    # 校验链时按组织 + 序号顺序扫描，这个复合索引是它的支撑索引
    op.create_index(
        "ix_audit_org_chain",
        "audit_logs",
        ["org_id", "chain_seq"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_org_chain", table_name="audit_logs")
    op.drop_column("audit_logs", "chain_seq")
    op.drop_column("audit_logs", "entry_hash")
    op.drop_column("audit_logs", "prev_hash")
