"""数据库方言差异的补偿锁。

某些并发语义在不同数据库上并不等价，最典型的是行锁：

    PostgreSQL  支持 `SELECT ... FOR UPDATE [SKIP LOCKED]`，多实例部署下正确
    SQLite      不支持 FOR UPDATE，**SQLAlchemy 会静默忽略该子句**

静默忽略最危险的地方在于：代码看起来是对的，测试在 Postgres 上也过，
但本地 SQLite 会把钱算错、把同一个任务派给两个 worker。

因此在 SQLite 下补一层进程内锁。这不会牺牲什么——
SQLite 本身也只能被单进程可靠使用，所以进程内锁即是完整解。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

#: 每个 (事件循环, 名称) 一把锁。Lock 不能在多个 loop 之间共享，
#: 而测试会为每个用例新建 loop，因此必须按 loop 隔离。
_locks: dict[tuple[int, str], asyncio.Lock] = {}
_guard = threading.Lock()


def loop_lock(name: str) -> asyncio.Lock:
    """取得当前事件循环下、指定名称的锁。"""
    loop = asyncio.get_running_loop()
    key = (id(loop), name)
    with _guard:
        lock = _locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _locks[key] = lock
        return lock


def is_sqlite(session: AsyncSession) -> bool:
    """判断当前会话是否跑在 SQLite 上。

    探测失败时返回 True（按最严格方式处理）：宁可多加一层锁，
    也不要因为探测异常而悄悄退化成不安全的并发行为。
    """
    try:
        return session.get_bind().dialect.name == "sqlite"
    except Exception:  # pragma: no cover
        return True


@asynccontextmanager
async def serialized_on_sqlite(session: AsyncSession, name: str) -> AsyncIterator[None]:
    """SQLite 下串行化，其它数据库直接放行。"""
    if is_sqlite(session):
        async with loop_lock(name):
            yield
    else:
        yield
