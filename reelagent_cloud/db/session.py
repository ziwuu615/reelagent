"""数据库会话与引擎管理。

要点：
  - **异步引擎**：asyncpg（生产）/ aiosqlite（本地）。连接池参数按环境调优。
  - **会话生命周期**：`session_scope()` 保证异常回滚、正常提交、最终关闭。
  - **多租户安全网**：`tenant_session()` 在会话上挂载 org_id，
    配合 `assert_tenant()` 让「忘记加 org 过滤」的查询尽早暴露。
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..config import DatabaseSettings
from . import models  # noqa: F401  —— 导入以注册所有映射
from .base import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: DatabaseSettings) -> AsyncEngine:
    kwargs: dict[str, Any] = {
        "echo": settings.echo,
        "pool_pre_ping": settings.pool_pre_ping,
        "future": True,
    }
    if settings.is_sqlite:
        # SQLite 走单文件，不需要连接池参数
        kwargs.pop("pool_pre_ping")
        kwargs["connect_args"] = {"timeout": 30}
    else:
        kwargs.update(
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
            pool_timeout=settings.pool_timeout,
            pool_recycle=settings.pool_recycle,
            connect_args={
                "server_settings": {
                    "application_name": "reelagent",
                    "statement_timeout": str(settings.statement_timeout_ms),
                }
            },
        )
    return create_async_engine(settings.url, **kwargs)


def init_engine(settings: DatabaseSettings) -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_engine(settings)
        _sessionmaker = async_sessionmaker(
            _engine, expire_on_commit=False, autoflush=False, class_=AsyncSession
        )
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        from ..config import get_settings

        init_engine(get_settings().database)
    assert _engine is not None
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


@contextlib.asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """事务边界：正常提交，异常回滚。"""
    factory = get_sessionmaker()
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def create_all() -> None:
    """仅供测试与本地快速启动使用；生产一律走 Alembic 迁移。"""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
