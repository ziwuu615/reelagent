"""限流。

两种实现，按是否配置 Redis 自动选择：
  - **Redis**：固定窗口计数器，多实例共享配额（生产推荐）
  - **进程内**：滑动窗口 + 惰性清理，单实例部署够用（自托管友好）

限流失败时的策略是**放行（fail-open）**，而不是拒绝：
限流器本身挂了不应该让整个站点不可用。这个取舍在注释里写明，
因为它是个有争议的决定。
"""

from __future__ import annotations

import contextlib
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from ..config import Settings
from ..domain.errors import RateLimited
from ..observability import get_logger, rate_limit_hits_total

log = get_logger(__name__)


@dataclass(frozen=True)
class Limit:
    scope: str
    limit: int
    window_seconds: int


class RateLimiter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._redis = None
        if settings.rate_limit.enabled and settings.redis.enabled:
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(
                    settings.redis.url,
                    socket_timeout=settings.redis.socket_timeout,
                    max_connections=settings.redis.max_connections,
                    decode_responses=True,
                )
            except Exception as exc:  # pragma: no cover
                log.warning("redis_unavailable_falling_back", error=str(exc))
                self._redis = None

        self._windows: dict[str, deque[float]] = defaultdict(deque)
        self._last_cleanup = time.monotonic()

    @property
    def backend(self) -> str:
        return "redis" if self._redis is not None else "memory"

    # ------------------------------------------------------------------ #
    async def check(self, limit: Limit, identity: str) -> None:
        """超出配额抛 RateLimited；内部故障放行。"""
        if not self.settings.rate_limit.enabled or limit.limit <= 0:
            return
        key = f"rl:{limit.scope}:{identity}"

        try:
            count = await self._incr(key, limit.window_seconds)
        except Exception as exc:  # fail-open：限流器故障不能拖垮站点
            log.warning("rate_limit_backend_error", scope=limit.scope, error=str(exc))
            return

        if count > limit.limit:
            rate_limit_hits_total.labels(scope=limit.scope).inc()
            retry_after = limit.window_seconds
            log.info(
                "rate_limited", scope=limit.scope, identity=identity[:48], count=count, limit=limit.limit
            )
            raise RateLimited(
                f"操作过于频繁，请 {retry_after} 秒后再试",
                details={"scope": limit.scope, "retry_after_seconds": retry_after},
            )

    # ------------------------------------------------------------------ #
    async def _incr(self, key: str, window: int) -> int:
        if self._redis is not None:
            pipe = self._redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, window, nx=True)
            results = await pipe.execute()
            return int(results[0])
        return self._incr_memory(key, window)

    def _incr_memory(self, key: str, window: int) -> int:
        now = time.monotonic()
        bucket = self._windows[key]
        cutoff = now - window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        bucket.append(now)

        # 惰性清理：每 5 分钟扫一次，避免键无限增长
        if now - self._last_cleanup > 300:
            self._last_cleanup = now
            stale = [k for k, v in self._windows.items() if not v or v[-1] < cutoff]
            for k in stale:
                self._windows.pop(k, None)
        return len(bucket)

    async def close(self) -> None:
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
            self._redis = None


# --------------------------------------------------------------------------- #
# 配额定义
# --------------------------------------------------------------------------- #
def limits_from(settings: Settings) -> dict[str, Limit]:
    """构造各场景的配额。

    登录/注册的阈值取自 `security` 而不是 `rate_limit`：
    它们本质是**防暴力破解**策略（与口令策略、锁定策略同源），
    放在安全配置里才能与 `accounts.authenticate` 的锁定阈值保持一致。
    """
    rl = settings.rate_limit
    sec = settings.security
    return {
        "anonymous": Limit("anonymous", rl.anonymous_per_minute, 60),
        "user": Limit("user", rl.user_per_minute, 60),
        "api_key": Limit("api_key", rl.api_key_per_minute, 60),
        "login": Limit("login", sec.login_max_attempts, sec.login_window_seconds),
        "signup": Limit("signup", 5, 3600),
        "project_create": Limit("project_create", rl.project_creates_per_hour, 3600),
        "upload": Limit("upload", 30, 3600),
    }


_limiter: RateLimiter | None = None


def get_limiter(settings: Settings) -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(settings)
    return _limiter


async def reset_limiter() -> None:
    global _limiter
    if _limiter is not None:
        await _limiter.close()
    _limiter = None


def reset_limiter_sync() -> None:
    """同步重置（测试 fixture 用）。

    只丢弃内存计数；若后端是 Redis，连接会由 GC 回收或进程退出时释放，
    测试场景下可以接受。
    """
    global _limiter
    _limiter = None
