"""ORM 模型。

设计要点：
  1. **多租户**：除 `users` 外所有业务表都带 `org_id`，查询一律以 org 为前缀过滤，
     避免越权读取（这是 SaaS 最常见的安全漏洞来源）。
  2. **额度账本**：`organizations.credits_balance` 是快照，`ledger_entries` 是只追加的事实。
     两者在同一事务内更新，`balance_after` 落库以便对账与追责。
  3. **任务租约**：Job 用 `locked_by` + `lease_expires_at` 表达「被谁占着」，
     worker 崩溃后租约自然过期，任务可被重新领取——不需要额外的故障检测服务。
  4. **Webhook 幂等**：`webhook_events(provider, event_id)` 唯一，
     同一个 Stripe 事件重复投递只处理一次。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..domain.enums import (
    ArtifactKind,
    AuditAction,
    JobKind,
    JobStage,
    JobStatus,
    LedgerKind,
    MembershipRole,
    PlanTier,
    ProjectStatus,
    SubscriptionStatus,
    WebhookStatus,
)
from .base import Base, JSONType, TimestampMixin, UTCDateTime, make_enum, utcnow


# --------------------------------------------------------------------------- #
# 账号与租户
# --------------------------------------------------------------------------- #
class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    #: 会话代际。改密码 / 「登出所有设备」时 +1，使所有已签发 Cookie 立即失效。
    #: 比维护一张服务端 session 表更轻，同时保留了「批量吊销」能力。
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: 界面语言 / 时区偏好
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    # 邮箱在应用层统一 lower() 归一化，因此不需要函数索引。


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    plan: Mapped[PlanTier] = mapped_column(
        make_enum(PlanTier, "plan_tier"), nullable=False, default=PlanTier.FREE, index=True
    )
    #: 额度快照。真值以 ledger_entries 为准，这里是为了避免每次请求都做聚合
    credits_balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: 本周期已发放额度，用于防重复发放
    credits_granted_this_period: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: 结算周期锚点
    period_started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    #: 品牌与默认风格
    default_style: Mapped[str] = mapped_column(String(32), nullable=False, default="cinematic")
    watermark: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_suspended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suspended_reason: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    #: BYOK：组织自定义供应商密钥（加密存储）
    byok: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)

    __table_args__ = (CheckConstraint("credits_balance >= 0", name="ck_org_credits_non_negative"),)


class Membership(Base, TimestampMixin):
    __tablename__ = "memberships"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[MembershipRole] = mapped_column(
        make_enum(MembershipRole, "membership_role"), nullable=False, default=MembershipRole.MEMBER
    )
    invited_by: Mapped[str | None] = mapped_column(String(40))
    last_active_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (UniqueConstraint("user_id", "org_id", name="uq_membership_user_org"),)


class ApiKey(Base, TimestampMixin):
    """程序化访问凭证。

    只存**哈希**，明文仅在创建时返回一次——与 Stripe / GitHub 的做法一致。
    `prefix` 保留明文片段，便于用户在列表里辨认是哪一把。
    """

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(40), ForeignKey("users.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=lambda: ["read", "write"])
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_used_ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    request_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    @property
    def is_active(self) -> bool:
        if self.revoked_at is not None:
            return False
        return not (self.expires_at and self.expires_at < utcnow())


# --------------------------------------------------------------------------- #
# 项目与镜头
# --------------------------------------------------------------------------- #
class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(40), ForeignKey("users.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    seed_text: Mapped[str] = mapped_column(Text, nullable=False)
    style: Mapped[str] = mapped_column(String(32), nullable=False, default="cinematic")
    aspect_ratio: Mapped[str] = mapped_column(String(8), nullable=False, default="9:16")
    status: Mapped[ProjectStatus] = mapped_column(
        make_enum(ProjectStatus, "project_status"), nullable=False, default=ProjectStatus.DRAFT, index=True
    )
    #: 引擎产出的完整剧本（场景 + 镜头 + 线索）
    script: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    clues: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, nullable=False, default=list)
    evaluation: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    cost_summary: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    #: 汇总指标，便于列表页直接展示而不用解 JSON
    total_shots: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finished_shots: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    credits_spent: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    provider_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    engine_project_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    __table_args__ = (
        Index("ix_projects_org_created", "org_id", "created_at"),
        Index("ix_projects_org_status", "org_id", "status"),
    )


class Shot(Base, TimestampMixin):
    """镜头。独立成表是为了支持「单镜重跑」这种局部操作，
    而不必把整个项目的 JSON 读出来改写再写回（那样会有并发覆盖问题）。"""

    __tablename__ = "shots"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    shot_key: Mapped[str] = mapped_column(String(64), nullable=False)  # sc01_sh01
    scene_key: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    shot_size: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    camera: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    duration_s: Mapped[float] = mapped_column(Float, nullable=False, default=3.0)
    action: Mapped[str] = mapped_column(Text, nullable=False, default="")
    narration: Mapped[str] = mapped_column(Text, nullable=False, default="")
    dialogue: Mapped[str] = mapped_column(Text, nullable=False, default="")
    image_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    video_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    clue_ids: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    tier: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    keyframe_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    clip_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    audio_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("project_id", "shot_key", name="uq_shot_project_key"),
        Index("ix_shots_project_order", "project_id", "order_index"),
    )


# --------------------------------------------------------------------------- #
# 任务队列
# --------------------------------------------------------------------------- #
class Job(Base, TimestampMixin):
    """Postgres `FOR UPDATE SKIP LOCKED` 驱动的任务队列。

    为什么不用 Celery/RQ：
      - 少一个 Redis 依赖（队列与业务数据同库同事务，不会出现「任务入队了但事务回滚」）
      - SKIP LOCKED 天然支持多 worker 无锁竞争，吞吐足够本项目量级
    代价是：任务吞吐受数据库限制。若将来需要每秒上千任务，再换专用 broker。
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(40), ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[JobKind] = mapped_column(make_enum(JobKind, "job_kind"), nullable=False, index=True)
    status: Mapped[JobStatus] = mapped_column(
        make_enum(JobStatus, "job_status"), nullable=False, default=JobStatus.PENDING, index=True
    )
    stage: Mapped[JobStage] = mapped_column(
        make_enum(JobStage, "job_stage"), nullable=False, default=JobStage.QUEUED
    )
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_code: Mapped[str] = mapped_column(String(48), nullable=False, default="")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    # ---- 调度与租约 ----
    run_after: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, index=True)
    locked_by: Mapped[str | None] = mapped_column(String(64), index=True)
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ---- 成本与幂等 ----
    credits_reserved: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    credits_charged: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    provider_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    idempotency_key: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (
        UniqueConstraint("org_id", "idempotency_key", name="uq_job_org_idempotency"),
        # 领取任务的核心索引：按状态 + 优先级 + 可执行时间排序
        Index("ix_jobs_claim", "status", "priority", "run_after"),
        Index("ix_jobs_lease", "status", "lease_expires_at"),
        Index("ix_jobs_org_created", "org_id", "created_at"),
    )

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal


# --------------------------------------------------------------------------- #
# 产物
# --------------------------------------------------------------------------- #
class Artifact(Base, TimestampMixin):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[str | None] = mapped_column(String(40), ForeignKey("jobs.id", ondelete="SET NULL"))
    shot_key: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    kind: Mapped[ArtifactKind] = mapped_column(
        make_enum(ArtifactKind, "artifact_kind"), nullable=False, index=True
    )
    #: 存储层 key（本地相对路径或 S3 object key）
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    content_type: Mapped[str] = mapped_column(String(128), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: 是否可被公开 CDN 访问（成片分享用）
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    share_token: Mapped[str | None] = mapped_column(String(48), unique=True)
    download_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)

    __table_args__ = (Index("ix_artifacts_project_kind", "project_id", "kind"),)


# --------------------------------------------------------------------------- #
# 额度账本
# --------------------------------------------------------------------------- #
class LedgerEntry(Base):
    """只追加的额度账本。

    任何额度变动都必须写一条，且带 `balance_after`。
    对账方式：`ledger_entries` 求和应当等于 `organizations.credits_balance`；
    不等就是有 bug，这个等式正是这套设计存在的意义。
    """

    __tablename__ = "ledger_entries"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[LedgerKind] = mapped_column(make_enum(LedgerKind, "ledger_kind"), nullable=False, index=True)
    #: 正数入账、负数出账
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    project_id: Mapped[str | None] = mapped_column(String(40), index=True)
    job_id: Mapped[str | None] = mapped_column(String(40), index=True)
    #: 外部单号（Stripe PaymentIntent / Invoice）
    external_ref: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    #: 幂等键：同一外部单号只入账一次
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[str | None] = mapped_column(String(40))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("org_id", "idempotency_key", name="uq_ledger_org_idempotency"),
        Index("ix_ledger_org_created", "org_id", "created_at"),
    )


class UsageRecord(Base):
    """每次供应商调用的原始用量，用于成本分析与对账。"""

    __tablename__ = "usage_records"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(40), index=True)
    job_id: Mapped[str | None] = mapped_column(String(40), index=True)
    shot_key: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    capability: Mapped[str] = mapped_column(String(16), nullable=False)  # image/video/tts/llm
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    unit: Mapped[str] = mapped_column(String(16), nullable=False, default="call")
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    provider_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    credits_charged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_code: Mapped[str] = mapped_column(String(48), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)

    __table_args__ = (Index("ix_usage_org_created", "org_id", "created_at"),)


# --------------------------------------------------------------------------- #
# 订阅与支付
# --------------------------------------------------------------------------- #
class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    plan: Mapped[PlanTier] = mapped_column(
        make_enum(PlanTier, "sub_plan"), nullable=False, default=PlanTier.FREE
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        make_enum(SubscriptionStatus, "sub_status"), nullable=False, default=SubscriptionStatus.ACTIVE
    )
    stripe_customer_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    #: 必须可空且默认 NULL。若用 `default=""` + unique，
    #: 所有未订阅组织的这一列都是空串，第二个用户注册就会撞唯一约束直接失败。
    #: SQL 的唯一约束不限制多个 NULL，因此 NULL 才是「尚未订阅」的正确表示。
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), unique=True, default=None)
    stripe_price_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    current_period_start: Mapped[datetime | None] = mapped_column(UTCDateTime)
    current_period_end: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    canceled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    trial_end: Mapped[datetime | None] = mapped_column(UTCDateTime)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)

    @property
    def is_active(self) -> bool:
        return self.status in {SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING}


class WebhookEvent(Base):
    """外部 Webhook 去重表。Stripe 会重试投递，必须幂等。"""

    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[WebhookStatus] = mapped_column(
        make_enum(WebhookStatus, "webhook_status"), nullable=False, default=WebhookStatus.RECEIVED
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)

    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_webhook_provider_event"),)


# --------------------------------------------------------------------------- #
# 审计与通知
# --------------------------------------------------------------------------- #
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(String(40), index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(40), index=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False, default="user")  # user|api_key|system
    action: Mapped[AuditAction] = mapped_column(
        make_enum(AuditAction, "audit_action"), nullable=False, index=True
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    target_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)

    __table_args__ = (Index("ix_audit_org_created", "org_id", "created_at"),)


class EmailOutbox(Base):
    """邮件发件箱。

    业务代码只写这张表，由 worker 负责实际投递并重试。
    好处：请求路径不被 SMTP 阻塞；投递失败可重试且可观测。
    """

    __tablename__ = "email_outbox"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    to_address: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)
