"""产品配置。

单一事实来源：所有可调参数都在这里，通过环境变量覆盖。
命名约定：
    REEL_ENV=prod
    REEL_DATABASE__URL=postgresql+asyncpg://...
    REEL_VIDEO_API_KEY=sk-xxx          # 供应商保持扁平命名，便于运维辨识

设计要点：
  1. **启动即校验**：生产环境缺失关键配置直接拒绝启动，而不是运行到一半才炸。
  2. **密钥集中**：所有 Secret 都在 `SecretStr` 里，`repr` 自动打码，避免日志泄漏。
  3. **可降级**：`enabled` 开关让某项能力（如 Redis、S3）在缺失时自动退化，
     而不是把整个服务拖死。
"""

from __future__ import annotations

import secrets
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Environment(StrEnum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"

    @property
    def is_production(self) -> bool:
        return self is Environment.PROD


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #
class DatabaseSettings(BaseModel):
    url: str = "sqlite+aiosqlite:///./workspace/reelagent.db"
    echo: bool = False
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout: int = 30
    pool_recycle: int = 1800
    pool_pre_ping: bool = True
    statement_timeout_ms: int = 30_000

    @property
    def is_sqlite(self) -> bool:
        return self.url.startswith("sqlite")


class RedisSettings(BaseModel):
    """Redis 是**可选**的：用于限流与缓存。缺失时限流退化为进程内实现。"""

    url: str = "redis://localhost:6379/0"
    enabled: bool = False
    socket_timeout: float = 2.0
    max_connections: int = 32


class StorageSettings(BaseModel):
    backend: Literal["local", "s3"] = "local"
    local_dir: Path = PROJECT_ROOT / "workspace" / "storage"
    # S3 / OSS / R2 / MinIO 通用
    s3_bucket: str = ""
    s3_region: str = ""
    s3_endpoint_url: str = ""
    s3_access_key_id: SecretStr = SecretStr("")
    s3_secret_access_key: SecretStr = SecretStr("")
    s3_public_base_url: str = ""
    presign_ttl_seconds: int = 3600
    max_upload_mb: int = 32

    @model_validator(mode="after")
    def _check_s3(self) -> StorageSettings:
        if self.backend == "s3" and not self.s3_bucket:
            raise ValueError("storage.backend=s3 时必须配置 s3_bucket")
        return self


class SecuritySettings(BaseModel):
    #: 会话签名与 CSRF 密钥。
    #: **生产环境必须由运维显式配置**——否则多副本各自生成随机密钥，
    #: 用户在负载均衡后会被随机踢下线，且每次滚动发布全站登出。
    #: 是否自动生成由父级 Settings 依据 env 决定（见 Settings._validate_environment）。
    secret_key: SecretStr = SecretStr("")
    #: API Key 的额外 pepper（与 secret_key 分离，便于独立轮换）
    api_key_pepper: SecretStr = SecretStr("")
    session_cookie: str = "reelagent_session"
    session_max_age_seconds: int = 60 * 60 * 24 * 14
    csrf_cookie: str = "reelagent_csrf"
    csrf_header: str = "x-csrf-token"
    https_only: bool = False
    allowed_hosts: list[str] = Field(default_factory=lambda: ["*"])
    #: 登录失败限流
    login_max_attempts: int = 10
    login_window_seconds: int = 300
    #: 密码策略
    min_password_length: int = 10
    #: 是否允许公开注册（私有部署可关掉）
    allow_signup: bool = True
    #: 邀请制注册所需的邀请码（为空则不校验）
    signup_invite_code: SecretStr = SecretStr("")


class RateLimitSettings(BaseModel):
    enabled: bool = True
    #: 匿名（按 IP）
    anonymous_per_minute: int = 30
    #: 登录用户（按 user）
    user_per_minute: int = 240
    #: 建项目（按 org），防止刷额度
    project_creates_per_hour: int = 60
    #: 单个 API Key
    api_key_per_minute: int = 300


class WorkerSettings(BaseModel):
    #: 同时执行的 Job 数
    concurrency: int = 2
    #: 单个 Job 内并发生成的镜头数
    shot_concurrency: int = 3
    #: 领取任务的轮询间隔（无任务时）
    poll_interval_seconds: float = 2.0
    #: 租约时长：超过未续约则视为 worker 崩溃，任务可被重新领取
    lease_seconds: int = 900
    #: 心跳间隔
    heartbeat_seconds: int = 20
    #: 任务最长执行时间
    job_timeout_seconds: int = 3600
    #: 最大重试次数
    max_attempts: int = 3
    #: 优雅停机等待时间
    shutdown_grace_seconds: int = 60


class EmailSettings(BaseModel):
    """邮件发送。未配置 SMTP 时，邮件写入 outbox 表并由日志记录（开发友好）。"""

    enabled: bool = False
    host: str = ""
    port: int = 587
    username: str = ""
    password: SecretStr = SecretStr("")
    use_tls: bool = True
    from_address: str = "ReelAgent <no-reply@example.com>"


class ObservabilitySettings(BaseModel):
    log_level: str = "INFO"
    #: json 适合生产采集，console 适合本地阅读
    log_format: Literal["json", "console"] = "json"
    metrics_enabled: bool = True
    metrics_path: str = "/metrics"
    #: 慢请求阈值（用于打点告警）
    slow_request_ms: int = 3000
    sentry_dsn: SecretStr = SecretStr("")


class BillingSettings(BaseModel):
    """Stripe 计费。未配置时产品降级为「额度模式」：注册即赠额度，不可充值。"""

    enabled: bool = False
    secret_key: SecretStr = SecretStr("")
    webhook_secret: SecretStr = SecretStr("")
    publishable_key: str = ""
    currency: str = "cny"
    #: Stripe Price ID -> 套餐
    price_starter: str = ""
    price_pro: str = ""
    price_studio: str = ""
    #: 供应商成本 -> 用户扣费的加价倍率
    markup_multiplier: float = 3.0
    #: 100 额度 = 1 美元（对外展示为「点数」）
    credits_per_usd: int = 100

    @model_validator(mode="after")
    def _check(self) -> BillingSettings:
        if self.enabled and not self.secret_key.get_secret_value():
            raise ValueError("billing.enabled=true 时必须配置 stripe secret_key")
        return self


# --------------------------------------------------------------------------- #
# 供应商（扁平命名，兼容引擎层既有 .env）
# --------------------------------------------------------------------------- #
class ProviderSettings(BaseModel):
    enabled: bool = False
    provider: str = ""
    model: str = ""
    api_key: SecretStr = SecretStr("")
    base_url: str = ""
    timeout_seconds: float = 180.0
    #: 单供应商并发上限，避免触发厂商限流
    max_concurrency: int = 4
    #: 失败重试
    max_retries: int = 3
    #: 熔断：连续失败 N 次后打开断路器
    circuit_breaker_threshold: int = 5
    circuit_breaker_reset_seconds: int = 60

    @property
    def configured(self) -> bool:
        return bool(self.api_key.get_secret_value())


class ProvidersSettings(BaseModel):
    llm: ProviderSettings = ProviderSettings(
        provider="deepseek", model="deepseek-chat", base_url="https://api.deepseek.com/v1"
    )
    image: ProviderSettings = ProviderSettings(
        provider="ark",
        model="doubao-seedream-4-0-250828",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
    )
    video: ProviderSettings = ProviderSettings(
        provider="ark",
        model="doubao-seedance-1-0-pro-250528",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
    )
    tts: ProviderSettings = ProviderSettings(
        provider="dashscope",
        model="qwen3-tts-flash",
        base_url="https://dashscope.aliyuncs.com/api/v1",
    )

    #: 允许用户在控制台填入自己的 Key（BYOK），平台 Key 仅作兜底
    allow_byok: bool = True

    def any_video_configured(self) -> bool:
        return self.video.configured


# --------------------------------------------------------------------------- #
# 根配置
# --------------------------------------------------------------------------- #
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REEL_",
        env_nested_delimiter="__",
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 产品标识 ----
    env: Environment = Environment.DEV
    product_name: str = "ReelAgent"
    #: 对外访问地址。未申请域名时用 localhost，绑定域名后改这里即可
    base_url: str = "http://localhost:8000"
    #: 必须是一个语法合法的邮箱——OpenAPI 会校验它
    support_email: str = "support@example.com"
    docs_url: str = "/docs"

    # ---- 基础设施 ----
    database: DatabaseSettings = DatabaseSettings()
    redis: RedisSettings = RedisSettings()
    storage: StorageSettings = StorageSettings()
    security: SecuritySettings = SecuritySettings()
    rate_limit: RateLimitSettings = RateLimitSettings()
    worker: WorkerSettings = WorkerSettings()
    email: EmailSettings = EmailSettings()
    observability: ObservabilitySettings = ObservabilitySettings()
    billing: BillingSettings = BillingSettings()
    providers: ProvidersSettings = ProvidersSettings()

    # ---- 引擎参数 ----
    style: str = "cinematic"
    aspect_ratio: str = "9:16"
    max_shots_per_project: int = 12
    max_base_images: int = 6
    job_budget_usd: float = 2.0

    #: 本地开发的数据根目录
    workspace_dir: Path = PROJECT_ROOT / "workspace"

    # ------------------------------------------------------------------ #
    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.env.is_production

    @property
    def engine_workspace(self) -> Path:
        return self.workspace_dir / "engine"

    # ------------------------------------------------------------------ #
    @model_validator(mode="after")
    def _validate_environment(self) -> Settings:
        """生产环境必须在启动时就满足硬性要求——宁可起不来，也不要带病上线。

        注意：密钥的**自动生成只能发生在这里**，不能放在 SecuritySettings 的
        校验器里。子模型校验器先于父模型执行，那样会在父级检查之前就把
        secret_key 填上，使「生产必须配置 SECRET_KEY」变成永远为假的死代码——
        结果是多副本各自随机、用户随机掉登录。
        """
        problems: list[str] = []

        if self.env.is_production:
            if not self.security.secret_key.get_secret_value():
                problems.append(
                    "REEL_SECURITY__SECRET_KEY 未配置（多副本部署会导致会话互不通用，"
                    '请用 `python -c "import secrets;print(secrets.token_urlsafe(48))"` 生成）'
                )
            if not self.security.api_key_pepper.get_secret_value():
                problems.append(
                    "REEL_SECURITY__API_KEY_PEPPER 未配置（用于 API Key 哈希加盐，"
                    "轮换该值会使所有已签发 Key 失效，请提前规划）"
                )
            if self.database.is_sqlite:
                problems.append("生产环境不应使用 SQLite，请配置 REEL_DATABASE__URL")
            if not self.security.https_only:
                problems.append("生产环境必须开启 REEL_SECURITY__HTTPS_ONLY=true")
            if "*" in self.security.allowed_hosts:
                problems.append("生产环境必须显式配置 REEL_SECURITY__ALLOWED_HOSTS")
            if self.base_url.startswith("http://"):
                problems.append("生产环境 REEL_BASE_URL 必须是 https")
            if not self.providers.video.configured:
                problems.append("未配置视频供应商 Key（REEL_PROVIDERS__VIDEO__API_KEY），产品将无法产出成片")
        else:
            # 开发环境：允许自动生成，保证 clone 即跑
            if not self.security.secret_key.get_secret_value():
                self.security.secret_key = SecretStr(secrets.token_urlsafe(48))
            if not self.security.api_key_pepper.get_secret_value():
                self.security.api_key_pepper = SecretStr(
                    self.security.secret_key.get_secret_value() + ":apikey"
                )

        if problems:
            raise ValueError(
                "配置校验未通过（环境={}）：\n  - {}".format(self.env.value, "\n  - ".join(problems))
            )
        return self

    # ------------------------------------------------------------------ #
    def ensure_dirs(self) -> None:
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.engine_workspace.mkdir(parents=True, exist_ok=True)
        if self.storage.backend == "local":
            self.storage.local_dir.mkdir(parents=True, exist_ok=True)

    def public_url(self, path: str = "") -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

    def safe_dump(self) -> dict[str, object]:
        """可安全写入日志/状态页的配置快照（所有密钥已打码）。"""
        return {
            "env": self.env.value,
            "base_url": self.base_url,
            "database": "sqlite" if self.database.is_sqlite else "postgresql",
            "redis": self.redis.enabled,
            "storage": self.storage.backend,
            "billing": self.billing.enabled,
            "rate_limit": self.rate_limit.enabled,
            "providers": {
                "llm": {"provider": self.providers.llm.provider, "configured": self.providers.llm.configured},
                "image": {
                    "provider": self.providers.image.provider,
                    "configured": self.providers.image.configured,
                },
                "video": {
                    "provider": self.providers.video.provider,
                    "configured": self.providers.video.configured,
                },
                "tts": {"provider": self.providers.tts.provider, "configured": self.providers.tts.configured},
            },
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例。测试可用 `get_settings.cache_clear()` 重置。"""
    settings = Settings()
    settings.ensure_dirs()
    return settings
