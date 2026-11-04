"""熔断器状态机。

熔断是「省时间」而不是「省调用」的机制，所以测试重点是**状态迁移的边界**：
冷却刚好到期、半开只放一个探针、探针成功/失败后的去向。
这些地方写错了不会报错，只会表现为「该熔断时没熔断」或「永远不恢复」。
"""

from __future__ import annotations

import unittest

from reelagent.harness.breaker import (
    CLOSED,
    HALF_OPEN,
    OPEN,
    BreakerTracker,
    CircuitBreaker,
)


class TestStateMachine(unittest.TestCase):
    def test_closed_allows(self) -> None:
        cb = CircuitBreaker(threshold=3, reset_seconds=10)
        self.assertTrue(cb.allow("a"))
        self.assertEqual(cb.snapshot()["a"]["state"], CLOSED)

    def test_opens_after_threshold(self) -> None:
        cb = CircuitBreaker(threshold=3, reset_seconds=10)
        self.assertFalse(cb.record_failure("a", now=1))
        self.assertFalse(cb.record_failure("a", now=2))
        self.assertTrue(cb.record_failure("a", now=3), "第 3 次失败应当打开断路器")
        self.assertEqual(cb.snapshot()["a"]["state"], OPEN)

    def test_open_denies_until_cooldown(self) -> None:
        cb = CircuitBreaker(threshold=1, reset_seconds=10)
        cb.record_failure("a", now=100)
        self.assertFalse(cb.allow("a", now=105))
        self.assertTrue(cb.allow("a", now=110), "冷却到期后应放行一个探针")
        self.assertEqual(cb.snapshot()["a"]["state"], HALF_OPEN)

    def test_half_open_allows_only_one_probe(self) -> None:
        cb = CircuitBreaker(threshold=1, reset_seconds=10)
        cb.record_failure("a", now=0)
        self.assertTrue(cb.allow("a", now=10))
        self.assertFalse(cb.allow("a", now=10), "半开状态只能放一个探针，否则等于没熔断")
        self.assertFalse(cb.allow("a", now=11))

    def test_probe_success_closes(self) -> None:
        cb = CircuitBreaker(threshold=1, reset_seconds=10)
        cb.record_failure("a", now=0)
        self.assertTrue(cb.allow("a", now=10))
        cb.record_success("a")
        self.assertEqual(cb.snapshot()["a"]["state"], CLOSED)
        self.assertTrue(cb.allow("a", now=11))

    def test_probe_failure_reopens(self) -> None:
        cb = CircuitBreaker(threshold=1, reset_seconds=10)
        cb.record_failure("a", now=0)
        cb.allow("a", now=10)
        self.assertTrue(cb.record_failure("a", now=11), "探针失败要立刻重新打开")
        self.assertFalse(cb.allow("a", now=12))

    def test_success_resets_consecutive_count(self) -> None:
        cb = CircuitBreaker(threshold=3, reset_seconds=10)
        cb.record_failure("a")
        cb.record_failure("a")
        cb.record_success("a")
        # 计数被清零后，再失败两次不应打开
        cb.record_failure("a")
        self.assertFalse(cb.record_failure("a"))
        self.assertEqual(cb.snapshot()["a"]["state"], CLOSED)

    def test_keys_are_independent(self) -> None:
        """视频熔断不能影响图像。"""
        cb = CircuitBreaker(threshold=1, reset_seconds=10)
        cb.record_failure("video:clip", now=0)
        self.assertFalse(cb.allow("video:clip", now=1))
        self.assertTrue(cb.allow("image:keyframe", now=1))

    def test_reset_clears_all(self) -> None:
        cb = CircuitBreaker(threshold=1, reset_seconds=10)
        cb.record_failure("a", now=0)
        cb.reset()
        self.assertTrue(cb.allow("a", now=1))


class TestTracker(unittest.TestCase):
    def test_counts_short_circuits(self) -> None:
        tracker = BreakerTracker(breaker=CircuitBreaker(threshold=1, reset_seconds=10))
        self.assertTrue(tracker.guard("v"))
        # 不注入 now：这里走真实的 monotonic，才能验证 guard() 自己的判定
        tracker.breaker.record_failure("v")
        self.assertFalse(tracker.guard("v"))
        self.assertFalse(tracker.guard("v"))
        self.assertEqual(tracker.short_circuited, 2, "被短路的次数是熔断的收益，必须可统计")


if __name__ == "__main__":
    unittest.main()
