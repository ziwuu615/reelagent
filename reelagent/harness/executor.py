"""带重试、Tracing 与降级记录的执行包装。

设计要点："重试与降级"、"失败样本最小复现"——每次失败都留下结构化痕迹，
而不是被吞掉。
"""

from __future__ import annotations

import contextlib
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from .tracing import Tracer

T = TypeVar("T")


#: 限流类错误的识别标记（大小写不敏感）。
#: 之所以要单独识别：429 的语义是「你请求太快了」，而不是「这次调用失败了」。
#: 用普通退避（0.6 秒起步）重试它，等于必然再撞一次 429 —— 三次尝试会在
#: 一秒钟内全部耗尽，然后错误地判定「供应商不可用」并降级。
_RATE_LIMIT_MARKERS = (
    "429",
    "too many requests",
    "rate limit",
    "ratelimit",
    "overloaded",
    "当前访问量过大",
    "请稍后再试",
    '"code":"1305"',
)

#: **账户级**配额限制的标记，优先级高于上面的模型过载。
#:
#: 为什么必须与模型过载分开：两者虽然都返回 429，但恢复时间差一个量级。
#:   模型过载（智谱 1305「模型当前访问量过大」）—— 几秒到几十秒即可恢复
#:   账户配额（智谱 1302「您的账户已达到速率限制」）—— 按**配额窗口**生效，
#:                                                     通常以分钟计
#: 实测教训：一次云端任务里 sc01_sh01 的关键帧连续两次撞 1302，
#: 20 秒退避完全不够，两次尝试耗尽后整条镜头被判失败，
#: 最终只剩 1/2 个镜头、门禁如实报出「基准图注入覆盖率 0.50 < 0.90」。
_QUOTA_MARKERS = (
    "1302",
    "速率限制",
    "已达到速率",
    "quota",
    "配额",
    "insufficient_quota",
)


def is_quota_exhausted(exc: BaseException | None) -> bool:
    """判断是否为**账户级**配额限制（需要分钟级退避，而非秒级）。"""
    if exc is None:
        return False
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _QUOTA_MARKERS)


#: **不可重试**的失败标记：重试再多次也不会成功，只会白白消耗时间与配额。
#:
#: 最典型的是「余额不足」。正确的处置是立刻把「去充值」这个动作告诉用户，
#: 而不是按限流逻辑再等几分钟重试三次。
#: 实测教训：智谱返回 1113「余额不足或无可用资源包,请充值」时，
#: 由于适配器把它伪装成了「429 模型过载」，重试策略足足试了 4 次
#: （含数分钟退避）才放弃，最后给出的原因还是错的。
_FATAL_MARKERS = (
    "1113",
    "余额不足",
    "请充值",
    "无可用资源包",
    "insufficient",
    "quota_exceeded",
    "欠费",
    "invalid api key",
    "authenticationerror",
    "token 非法",
    "token非法",
    "未开通",
    "not activated",
)


def is_fatal(exc: BaseException | None) -> bool:
    """判断失败是否**不值得重试**（重试必然再次失败）。

    包括：余额不足、密钥无效/过期、模型未开通、权限不足。
    这些都不会因为"再等一会儿"而变好 —— 需要的是人去充值或改配置，
    继续重试只会浪费时间与配额，还会掩盖真正该执行的动作。
    """
    if exc is None:
        return False
    # 401/403 是「身份或权限不对」，重试没有意义
    if getattr(exc, "status_code", None) in (401, 403):
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _FATAL_MARKERS)


def is_rate_limited(exc: BaseException | None) -> bool:
    """判断异常是否是限流/过载（瞬时状态，值得用更长退避重试）。"""
    if exc is None:
        return False
    if is_fatal(exc):
        # 不可重试的失败经常也带 429 状态码，必须先排除，
        # 否则「余额不足」会被当成「稍后重试即可」。
        return False
    if getattr(exc, "status_code", None) == 429:
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


@dataclass
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 0.6
    max_delay: float = 8.0
    jitter: float = 0.25
    retry_on: tuple[type[BaseException], ...] = (Exception,)

    #: 命中限流时的退避基数与上限，明显长于普通退避。
    #: 踩坑记录：智谱免费视频模型返回 429 时，原实现的重试间隔只有 120~370ms，
    #: 三次尝试全部撞回 429，于是被判定为「不可用」而降级成本地合成。
    #: 而 429 是「稍后再试」——等几十秒往往就能成功。
    rate_limit_base_delay: float = 20.0
    rate_limit_max_delay: float = 120.0

    #: 命中**账户级配额**时的退避，比模型过载再长一档 —— 它按分钟级窗口恢复。
    quota_base_delay: float = 60.0
    quota_max_delay: float = 300.0

    def delay_for(self, attempt: int, exc: BaseException | None = None) -> float:
        # 三档退避，按恢复时间从长到短判断：
        #   账户配额（分钟级） > 模型过载（秒级） > 普通失败（立即）
        if is_quota_exhausted(exc):
            ceiling, base = self.quota_max_delay, self.quota_base_delay
        elif is_rate_limited(exc):
            ceiling, base = self.rate_limit_max_delay, self.rate_limit_base_delay
        else:
            ceiling, base = self.max_delay, self.base_delay
        raw = min(base * (2 ** (attempt - 1)), ceiling)
        return raw * (1.0 + random.uniform(-self.jitter, self.jitter))


@dataclass
class Attempt:
    index: int
    ok: bool
    error: str = ""
    ms: float = 0.0


def run_with_retry(
    fn: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    tracer: Tracer | None = None,
    name: str = "op",
    attrs: dict[str, Any] | None = None,
    on_fail: Callable[[int, BaseException], None] | None = None,
) -> T:
    """执行 fn，失败按策略重试；全过程写入 span。"""
    policy = policy or RetryPolicy()
    history: list[Attempt] = []
    last_exc: BaseException | None = None

    # 注意：attrs 必须走 `attrs=` 关键字传参。
    # 早期版本用 `**attrs` 展开，当 attrs 里含 "kind" 键时会与 span(kind=...) 形参
    # 冲突抛 TypeError，并被外层 except 静默吞掉。
    span_cm = tracer.span(name, kind="retry", attrs=attrs, attempts=policy.attempts) if tracer else None
    span = span_cm.__enter__() if span_cm else None
    try:
        for i in range(1, policy.attempts + 1):
            started = time.perf_counter()
            try:
                result = fn()
                history.append(Attempt(i, True, ms=(time.perf_counter() - started) * 1000))
                if span is not None:
                    span.set(
                        attempts_used=i,
                        attempt_history=[a.__dict__ for a in history],
                    )
                return result
            except policy.retry_on as exc:
                last_exc = exc
                history.append(
                    Attempt(
                        i,
                        False,
                        error=f"{type(exc).__name__}: {exc}",
                        ms=(time.perf_counter() - started) * 1000,
                    )
                )
                if on_fail:
                    # 回调失败不应影响重试主流程
                    with contextlib.suppress(Exception):
                        on_fail(i, exc)

                if is_fatal(exc):
                    # 不可重试的失败（余额不足、密钥无效、模型未开通…）：
                    # 立刻停止，不要按限流逻辑再等几分钟。
                    # 重试不可能让它变好，只会浪费时间和配额，还会把
                    # 「去充值」这个真正该做的动作掩盖成「稍后重试」。
                    detail = f"{type(exc).__name__}: {exc}"
                    if span is not None:
                        span.set(
                            attempts_used=i,
                            fatal=True,
                            attempt_history=[a.__dict__ for a in history],
                        )
                        span.fail(detail)
                        span.close()
                    raise RuntimeError(f"{name} 遇到不可重试的失败（已跳过剩余尝试）：{detail}") from exc

                if i < policy.attempts:
                    # 把异常传进去，让策略区分「普通失败」「模型过载」与「账户配额」——
                    # 三者的恢复时间差一个量级，用同一套退避必然误判。
                    delay = policy.delay_for(i, exc)
                    if span is not None:
                        if is_quota_exhausted(exc):
                            span.set(quota_exhausted=True, backoff_s=round(delay, 1))
                        elif is_rate_limited(exc):
                            span.set(rate_limited=True, backoff_s=round(delay, 1))
                    time.sleep(delay)

        detail = " | ".join(f"#{a.index} {a.error}" for a in history if not a.ok)
        if span is not None:
            span.fail(detail)
            span.set(attempt_history=[a.__dict__ for a in history])
        raise RuntimeError(f"{name} 在 {policy.attempts} 次尝试后失败：{detail}") from last_exc
    finally:
        if span_cm is not None:
            span_cm.__exit__(None, None, None)
