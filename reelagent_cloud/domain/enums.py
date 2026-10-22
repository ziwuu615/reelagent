"""领域枚举与状态机。

把状态定义集中在一处，避免字符串散落各处导致拼写漂移。
每个枚举都显式声明「终态」与「可转换目标」，让状态机可被静态推理与测试。
"""

from __future__ import annotations

from enum import StrEnum


# --------------------------------------------------------------------------- #
# 账号与租户
# --------------------------------------------------------------------------- #
class MembershipRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"

    @property
    def rank(self) -> int:
        return {"owner": 3, "admin": 2, "member": 1}[self.value]

    def can_manage_billing(self) -> bool:
        return self.rank >= MembershipRole.ADMIN.rank

    def can_manage_members(self) -> bool:
        return self.rank >= MembershipRole.ADMIN.rank

    def can_write(self) -> bool:
        return True


class PlanTier(StrEnum):
    FREE = "free"
    STARTER = "starter"
    PRO = "pro"
    STUDIO = "studio"


# --------------------------------------------------------------------------- #
# 项目
# --------------------------------------------------------------------------- #
class ProjectStatus(StrEnum):
    DRAFT = "draft"  # 已创建，尚未入队
    QUEUED = "queued"  # 已入队等待执行
    RUNNING = "running"  # 正在生成
    READY = "ready"  # 成片就绪
    FAILED = "failed"  # 失败
    ARCHIVED = "archived"  # 用户归档

    @property
    def is_terminal(self) -> bool:
        return self in {ProjectStatus.READY, ProjectStatus.FAILED, ProjectStatus.ARCHIVED}

    @property
    def is_active(self) -> bool:
        return self in {ProjectStatus.QUEUED, ProjectStatus.RUNNING}


# --------------------------------------------------------------------------- #
# 任务
# --------------------------------------------------------------------------- #
class JobKind(StrEnum):
    GENERATE_PROJECT = "generate_project"
    REGENERATE_SHOT = "regenerate_shot"
    EVALUATE_PROJECT = "evaluate_project"
    RENDER_PROJECT = "render_project"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"  # 可重试
    DEAD = "dead"  # 超过重试上限，需人工介入
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.DEAD, JobStatus.CANCELLED}

    @property
    def is_claimable(self) -> bool:
        return self in {JobStatus.PENDING, JobStatus.FAILED}


class JobStage(StrEnum):
    """流水线阶段，用于向用户展示细粒度进度。"""

    QUEUED = "queued"
    PLANNING = "planning"
    ASSETS = "assets"
    GENERATING = "generating"
    EVALUATING = "evaluating"
    RENDERING = "rendering"
    DONE = "done"

    @property
    def progress(self) -> float:
        return {
            JobStage.QUEUED: 0.0,
            JobStage.PLANNING: 0.08,
            JobStage.ASSETS: 0.18,
            JobStage.GENERATING: 0.55,
            JobStage.EVALUATING: 0.82,
            JobStage.RENDERING: 0.93,
            JobStage.DONE: 1.0,
        }[self]


# --------------------------------------------------------------------------- #
# 产物
# --------------------------------------------------------------------------- #
class ArtifactKind(StrEnum):
    """产物类型。

    `FINAL_VIDEO` 与 `VIDEO` 必须分开，不能都叫 video：

    镜头片段（`VIDEO`）和整条成片（`FINAL_VIDEO`）都是 mp4，早期两者共用
    `VIDEO`，取成片只能靠「按创建时间倒序取第一条」猜。而一条流水线会在
    同一批里写入 3~10 个 mp4，谁的时间戳更晚取决于数据库插入顺序 ——
    一旦顺序变了，`find_final_artifact` 就会把某个**镜头片段**当成成片返回，
    用户点「下载成片」拿到的是一小段。这种错误不会报错，只会静默给错东西。
    """

    VIDEO = "video"  # 镜头片段
    FINAL_VIDEO = "final_video"  # 整条成片
    GIF = "gif"
    AUDIO = "audio"
    KEYFRAME = "keyframe"
    BASE_IMAGE = "base_image"
    STORYBOARD = "storyboard"
    MANIFEST = "manifest"
    REPORT = "report"


# --------------------------------------------------------------------------- #
# 额度账本
# --------------------------------------------------------------------------- #
class LedgerKind(StrEnum):
    SIGNUP_GRANT = "signup_grant"  # 注册赠送
    PLAN_GRANT = "plan_grant"  # 订阅周期发放
    PURCHASE = "purchase"  # 单独购买点数包
    CONSUME = "consume"  # 生成消耗
    REFUND = "refund"  # 失败退还
    ADJUSTMENT = "adjustment"  # 人工调整
    EXPIRE = "expire"  # 过期作废

    @property
    def sign(self) -> int:
        return -1 if self is LedgerKind.CONSUME else 1


class SubscriptionStatus(StrEnum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"
    UNPAID = "unpaid"


class WebhookStatus(StrEnum):
    RECEIVED = "received"
    PROCESSED = "processed"
    FAILED = "failed"
    IGNORED = "ignored"


class AuditAction(StrEnum):
    USER_SIGNUP = "user.signup"
    USER_LOGIN = "user.login"
    USER_LOGIN_FAILED = "user.login_failed"
    USER_LOGOUT = "user.logout"
    USER_PASSWORD_CHANGED = "user.password_changed"  # noqa: S105 - 审计事件名，非口令
    API_KEY_CREATED = "api_key.created"
    API_KEY_REVOKED = "api_key.revoked"
    PROJECT_CREATED = "project.created"
    PROJECT_DELETED = "project.deleted"
    SHOT_REGENERATED = "shot.regenerated"
    JOB_CANCELLED = "job.cancelled"
    CREDITS_GRANTED = "credits.granted"
    SUBSCRIPTION_CHANGED = "subscription.changed"
    MEMBER_INVITED = "member.invited"
    MEMBER_REMOVED = "member.removed"
    ORG_UPDATED = "org.updated"
