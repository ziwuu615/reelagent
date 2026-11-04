"""熔断器。

## 为什么有重试和降级，还需要熔断

   重试    解决「偶发失败」——同一请求再试一次可能就成功
   降级    解决「这条路不通，换一条」——比如视频模型挂了改本地合成
   熔断    解决「这条路整体不通，别再试了」

没有熔断时，供应商整体故障的表现是：**每个镜头都要先重试 3 次**，
每个请求还要等超时，才走到降级。3 个镜头 × 3 次 × 90 秒超时 ≈ 13 分钟
全是白等，期间还持续打对方的限流接口。熔断把这段等待从「分钟级」压到「毫秒级」。

## 状态机

    CLOSED   --连续失败 N 次-->  OPEN
    OPEN     --冷却 T 秒-->     HALF_OPEN
    HALF_OPEN --探针成功-->      CLOSED
    HALF_OPEN --探针失败-->      OPEN（并重置冷却）

HALF_OPEN 只允许**一个**探针通过：让所有请求同时涌进去等于没熔断。

## 为什么按「供应商 + 能力」而不是全局

视频挂了不代表图像挂了。按 `provider:capability` 分开计数，
才能做到「视频熔断、图像继续」，而不是一个供应商抖动把整站停掉。

## 线程安全

Director 用线程池并发跑镜头，所以熔断状态必须加锁。这里用最简单的
`threading.Lock`：临界区只有几次字典读写，锁竞争可以忽略。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


@dataclass
class BreakerState:
    state: str = CLOSED
    failures: int = 0
    opened_at: float = 0.0
    #: HALF_OPEN 状态下是否已有探针在飞
    probe_in_flight: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "failures": self.failures,
            "opened_at": self.opened_at,
        }


class CircuitBreaker:
    """按 key 独立计数的熔断器。

    `threshold` 连续失败次数达到即打开；`reset_seconds` 冷却时间。
    成功一次立刻清零失败计数 —— 用「连续失败」而不是「失败率」，
    是为了让实现足够简单可验证：滑动窗口的失败率需要样本量，
    而单个镜头的重试次数通常只有 3，样本量根本不够算率。
    """

    def __init__(self, *, threshold: int = 5, reset_seconds: float = 60.0) -> None:
        self.threshold = max(1, int(threshold))
        self.reset_seconds = max(0.0, float(reset_seconds))
        self._states: dict[str, BreakerState] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def _get(self, key: str) -> BreakerState:
        st = self._states.get(key)
        if st is None:
            st = BreakerState()
            self._states[key] = st
        return st

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """当前是否允许放行一次调用。

        OPEN 且冷却未到 -> False
        OPEN 且已过冷却 -> 转 HALF_OPEN，只放行一个探针
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            st = self._get(key)
            if st.state == CLOSED:
                return True
            if st.state == OPEN:
                if now - st.opened_at < self.reset_seconds:
                    return False
                st.state = HALF_OPEN
                st.probe_in_flight = True
                return True
            # HALF_OPEN：只允许一个探针
            if st.probe_in_flight:
                return False
            st.probe_in_flight = True
            return True

    def record_success(self, key: str) -> None:
        with self._lock:
            st = self._get(key)
            st.state = CLOSED
            st.failures = 0
            st.opened_at = 0.0
            st.probe_in_flight = False

    def record_failure(self, key: str, *, now: float | None = None) -> bool:
        """记录一次失败，返回「是否因此打开了断路器」。

        返回值给调用方用来写一条显式的 span/告警 —— 「断路器刚打开」
        是必须被看见的事件，不能只存在于内存状态里。
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            st = self._get(key)
            if st.state == HALF_OPEN:
                # 探针失败：立刻重新打开并重置冷却
                st.state = OPEN
                st.opened_at = now
                st.probe_in_flight = False
                return True
            st.failures += 1
            st.probe_in_flight = False
            if st.state == CLOSED and st.failures >= self.threshold:
                st.state = OPEN
                st.opened_at = now
                return True
            return False

    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict[str, dict[str, Any]]:
        """当前各 key 的状态，供指标与排障使用。"""
        with self._lock:
            return {k: v.as_dict() for k, v in self._states.items()}

    def reset(self) -> None:
        with self._lock:
            self._states.clear()


@dataclass
class BreakerTracker:
    """把熔断器与「被短路了多少次」的统计绑在一起。

    单纯一个 CircuitBreaker 无法回答「省了多少次调用」，而那正是熔断的收益。
    """

    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    short_circuited: int = 0

    def guard(self, key: str) -> bool:
        allowed = self.breaker.allow(key)
        if not allowed:
            self.short_circuited += 1
        return allowed
