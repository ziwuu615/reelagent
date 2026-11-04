"""audit hash chain + reconciliation diffs

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-22 10:10:00.000000

变更说明：
    - 为什么：对账原本只有「检测 + 告警」，没有地方记录「这条漂移从什么时候开始、
      处理到哪一步、谁抹平的」。加一张 reconciliation_diffs 表把它变成可追踪工单。
    - 是否可逆：可逆（新建表，downgrade drop）。
    - 大表变更是否锁表：新表 + 索引，不涉及既有表。
    - 是否需要数据回填：不需要。历史漂移没有记录本身就是事实，
      补造历史工单等于伪造处理流水。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from reelagent_cloud.db.base import UTCDateTime

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_diffs",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(length=40),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("balance", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("ledger_sum", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("drift", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("detected_at", UTCDateTime(), nullable=False),
        sa.Column("last_seen_at", UTCDateTime(), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("resolved_at", UTCDateTime(), nullable=True),
        sa.Column("resolution", sa.String(length=64), nullable=False, server_default=""),
    )
    op.create_index("ix_recon_diff_org_open", "reconciliation_diffs", ["org_id", "resolved_at"])
    op.create_index("ix_recon_diff_detected", "reconciliation_diffs", ["detected_at"])


def downgrade() -> None:
    op.drop_index("ix_recon_diff_detected", table_name="reconciliation_diffs")
    op.drop_index("ix_recon_diff_org_open", table_name="reconciliation_diffs")
    op.drop_table("reconciliation_diffs")
