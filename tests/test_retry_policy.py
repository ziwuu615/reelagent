"""重试策略的测试。

重点是**限流感知退避**。这不是优化，是修 bug：

    早期实现对 429 使用和普通失败一样的退避（0.6 秒起步、上限 8 秒）。
    但 429 的语义是「你请求太快了」，不是「这次调用失败了」。
    结果三次尝试在不到两秒内全部撞回 429，系统据此判定
    「视频供应商不可用」并降级成本地合成 —— 而被降级的那个模型
    其实等几十秒就能用。

    真实数据佐证：智谱免费视频模型（CogVideoX-Flash）返回 429 时，
    原实现的三次重试间隔实测只有 120ms / 208ms / 152ms。
"""

from __future__ import annotations

import pytest

from reelagent.harness.executor import (
    RetryPolicy,
    is_quota_exhausted,
    is_rate_limited,
    run_with_retry,
)
from reelagent.providers.base import ProviderError

#: 真实抓到的智谱 429 报文（模型过载）
ZHIPU_429 = ProviderError(
    "智谱 HTTP 429：模型当前访问量过大（code 1305），属瞬时过载，稍后重试即可。"
    '原始返回：{"error":{"code":"1305","message":"该模型当前访问量过大，请您稍后再试"}}'
)

#: 真实抓到的**账户级**配额限制报文。
#: 同样返回 429，但错误码是 1302 而不是 1305 —— 恢复时间是分钟级。
ZHIPU_QUOTA = ProviderError(
    "智谱 HTTP 429：模型当前访问量过大（code 1305），属瞬时过载，稍后重试即可。"
    '原始返回：{"error":{"code":"1302","message":"您的账户已达到速率限制，请您控制请求频率"}}'
)


class TestQuotaIsDistinctFromOverload:
    """账户配额与模型过载必须分开对待 —— 恢复时间差一个量级。

    真实事故：一次云端任务里某镜头的关键帧连续两次撞上**账户级**配额限制
    （1302），而当时的退避只有约 20 秒（按模型过载处理），
    两次尝试耗尽后该镜头被判永久失败，最终只剩 1/2 个镜头可用。
    """

    def test_account_quota_is_detected(self) -> None:
        assert is_quota_exhausted(ZHIPU_QUOTA) is True

    def test_model_overload_is_not_quota(self) -> None:
        """1305 只在几十秒内恢复，不应被当成分钟级的配额问题。"""
        assert is_quota_exhausted(ZHIPU_429) is False
        assert is_rate_limited(ZHIPU_429) is True

    def test_ordinary_failure_is_neither(self) -> None:
        plain = ProviderError("HTTP 500 内部错误")
        assert is_quota_exhausted(plain) is False
        assert is_rate_limited(plain) is False

    def test_quota_backoff_exceeds_overload_backoff(self) -> None:
        policy = RetryPolicy()
        assert policy.delay_for(1, ZHIPU_QUOTA) > policy.delay_for(1, ZHIPU_429)
        assert policy.delay_for(3, ZHIPU_QUOTA) > policy.delay_for(3, ZHIPU_429)

    def test_quota_total_wait_can_outlast_a_minute_window(self) -> None:
        """三次重试的总退避应达到数分钟量级。

        判据是「能否等过一个按分钟计的配额窗口」——
        旧实现总退避约 60 秒，实测不足以恢复。
        """
        policy = RetryPolicy()
        total = sum(policy.delay_for(i, ZHIPU_QUOTA) for i in (1, 2, 3))
        assert total >= 180.0, f"账户配额场景总退避只有 {total:.0f}s，等不到窗口重置"

    def test_backoff_is_capped_for_quota(self) -> None:
        policy = RetryPolicy(quota_max_delay=120.0)
        for attempt in (1, 5, 20):
            assert policy.delay_for(attempt, ZHIPU_QUOTA) <= 120.0 * 1.25  # 含抖动


class TestRateLimitDetection:
    @pytest.mark.parametrize(
        "message",
        [
            "HTTP 429 Too Many Requests",
            "rate limit exceeded",
            "429",
            "模型当前访问量过大",
            "服务过载 overloaded",
            '{"code":"1305"}',
        ],
    )
    def test_recognizes_rate_limit(self, message: str) -> None:
        assert is_rate_limited(ProviderError(message)) is True

    @pytest.mark.parametrize(
        "message",
        [
            "HTTP 500 内部错误",
            "模型不存在",
            "Key 无效",
            "连接超时",
        ],
    )
    def test_does_not_flag_ordinary_failures(self, message: str) -> None:
        assert is_rate_limited(ProviderError(message)) is False

    def test_none_is_not_rate_limited(self) -> None:
        assert is_rate_limited(None) is False

    def test_honours_status_code_attribute(self) -> None:
        class WithStatus(Exception):
            status_code = 429

        assert is_rate_limited(WithStatus("anything")) is True


class TestBackoffDurations:
    def test_rate_limit_waits_much_longer(self) -> None:
        policy = RetryPolicy()
        ordinary = policy.delay_for(1, ProviderError("HTTP 500"))
        limited = policy.delay_for(1, ZHIPU_429)
        assert limited > ordinary * 5, "限流退避必须显著长于普通退避"

    def test_rate_limit_backoff_is_meaningful(self) -> None:
        """首次限流退避至少 15 秒 —— 毫秒级重试对限流没有意义。"""
        policy = RetryPolicy()
        assert policy.delay_for(1, ZHIPU_429) >= 15.0

    def test_backoff_is_capped(self) -> None:
        policy = RetryPolicy(rate_limit_max_delay=60.0)
        assert policy.delay_for(10, ZHIPU_429) <= 60.0 * 1.25  # 含抖动

    def test_total_wait_before_giving_up_is_substantial(self) -> None:
        """三次尝试在放弃前的总等待应达到数十秒量级。

        这是「等得到」与「白等」的分界线：总等待只有一两秒时，
        降级不是因为供应商不可用，而是因为我们没给它机会。
        """
        policy = RetryPolicy()
        total = sum(policy.delay_for(i, ZHIPU_429) for i in (1, 2))
        assert total >= 40.0, f"限流场景下总退避只有 {total:.1f}s，不足以等到恢复"


class TestRetryExecution:
    def test_retries_then_succeeds(self) -> None:
        calls = {"n": 0}

        def flaky() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise ProviderError("HTTP 500 临时故障")
            return "ok"

        # 用极短退避，避免测试真的等几秒
        result = run_with_retry(flaky, policy=RetryPolicy(attempts=3, base_delay=0.001))
        assert result == "ok"
        assert calls["n"] == 3

    def test_gives_up_after_attempts(self) -> None:
        """用尽尝试次数后必须抛出，且错误信息里带每次的失败原因。

        注意 `run_with_retry` 抛的是 RuntimeError（保留最后一次异常为 cause），
        而不是原始异常类型 —— 它的职责是「重试编排」，
        需要把多次尝试的完整历史呈现给调用方。
        """

        def always_fails() -> str:
            raise ProviderError("HTTP 500")

        with pytest.raises(RuntimeError, match="2 次尝试后失败") as info:
            run_with_retry(
                always_fails,
                policy=RetryPolicy(attempts=2, base_delay=0.001, max_delay=0.002),
            )

        assert isinstance(info.value.__cause__, ProviderError), "应保留最后一次异常作为 cause"
        assert "#1" in str(info.value) and "#2" in str(info.value), "错误信息应包含每次尝试"
