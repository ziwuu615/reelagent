"""领域错误。

统一错误类型的价值：业务层只抛语义化异常，HTTP 状态码与错误响应的映射
集中在 `api/errors.py` 一处完成。这样业务代码不依赖 Web 框架，
换传输层（gRPC / 消息队列）时不用改业务逻辑。

每个错误都有稳定的 `code`，前端与 API 调用方据此分支，而不是匹配中文消息。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """所有可预期错误的基类。"""

    code: str = "internal_error"
    http_status: int = 500
    #: 是否可安全地把 message 直接展示给终端用户
    user_facing: bool = True

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message or self.__class__.__doc__ or self.code)
        self.message = message or self.__class__.__doc__ or self.code
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


# --------------------------------------------------------------------------- #
# 400 / 422
# --------------------------------------------------------------------------- #
class ValidationFailed(AppError):
    """请求参数不合法"""

    code = "validation_failed"
    http_status = 422


class InvalidState(AppError):
    """当前状态不允许该操作"""

    code = "invalid_state"
    http_status = 409


class UnsupportedOperation(AppError):
    """该功能在当前部署下不可用"""

    code = "unsupported_operation"
    http_status = 400


# --------------------------------------------------------------------------- #
# 401 / 403
# --------------------------------------------------------------------------- #
class Unauthorized(AppError):
    """未登录或凭证无效"""

    code = "unauthorized"
    http_status = 401


class Forbidden(AppError):
    """没有权限执行该操作"""

    code = "forbidden"
    http_status = 403


class AccountLocked(AppError):
    """账号已被临时锁定"""

    code = "account_locked"
    http_status = 403


class AccountSuspended(AppError):
    """账号已被停用"""

    code = "account_suspended"
    http_status = 403


# --------------------------------------------------------------------------- #
# 402 / 429
# --------------------------------------------------------------------------- #
class PaymentRequired(AppError):
    """额度不足"""

    code = "insufficient_credits"
    http_status = 402

    def __init__(self, message: str = "", *, required: int = 0, available: int = 0) -> None:
        super().__init__(
            message or f"额度不足：本次需要 {required} 点，当前可用 {available} 点",
            details={"required": required, "available": available},
        )


class QuotaExceeded(AppError):
    """超出套餐限制"""

    code = "quota_exceeded"
    http_status = 402


class RateLimited(AppError):
    """请求过于频繁"""

    code = "rate_limited"
    http_status = 429


# --------------------------------------------------------------------------- #
# 404 / 409
# --------------------------------------------------------------------------- #
class NotFound(AppError):
    """资源不存在"""

    code = "not_found"
    http_status = 404


class AlreadyExists(AppError):
    """资源已存在"""

    code = "already_exists"
    http_status = 409


# --------------------------------------------------------------------------- #
# 502 / 503
# --------------------------------------------------------------------------- #
class ProviderFailure(AppError):
    """上游供应商调用失败"""

    code = "provider_failure"
    http_status = 502
    user_facing = False

    def __init__(self, message: str = "", *, provider: str = "", retryable: bool = True) -> None:
        super().__init__(
            message or "上游生成服务暂时不可用", details={"provider": provider, "retryable": retryable}
        )
        self.provider = provider
        self.retryable = retryable


class ProviderCircuitOpen(AppError):
    """供应商熔断中"""

    code = "provider_circuit_open"
    http_status = 503

    def __init__(self, message: str = "", *, provider: str = "", retry_after: int = 60) -> None:
        super().__init__(
            message or f"生成服务「{provider}」暂时不可用，请稍后重试",
            details={"provider": provider, "retry_after_seconds": retry_after},
        )


class ServiceUnavailable(AppError):
    """服务暂时不可用"""

    code = "service_unavailable"
    http_status = 503


# --------------------------------------------------------------------------- #
# 任务
# --------------------------------------------------------------------------- #
class JobFailed(AppError):
    """任务执行失败"""

    code = "job_failed"
    http_status = 500


class GenerationFailed(JobFailed):
    """生成流程失败，且**不适合自动重试**。

    与 JobFailed 的唯一区别是 retryable：
    worker 的 `_finish_failure` 用 `getattr(exc, "retryable", True)` 决定
    队列是否重新投递，而 AppError 基类没有这个属性 —— 于是默认 True。

    但生成失败的真实原因通常是**账户余额不足、API Key 失效、供应商服务
    不可用**这类需要人介入的问题。自动重试只会：

      1. 把注定失败的请求再打一遍，浪费配额与时间
      2. 无意义地耗尽 job 的 attempts
      3. 在界面上把「欠费」表现成「偶发失败，正在重试」

    真实事故：百炼账户欠费（HTTP 400 / code=Arrearage），plan 阶段直接
    被拒，但因为默认可重试，队列又投递了两次，每次都立刻失败。
    """

    code = "generation_failed"
    retryable = False


class JobCancelled(AppError):
    """任务已取消"""

    code = "job_cancelled"
    http_status = 409


class BudgetExceeded(AppError):
    """超出预算上限"""

    code = "budget_exceeded"
    http_status = 402
