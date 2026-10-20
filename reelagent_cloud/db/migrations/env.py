"""Alembic 环境。

要点：
  1. **连接串来自应用配置**，不写在 alembic.ini 里——单一事实来源。
  2. 支持异步驱动（asyncpg）。Alembic 本身是同步的，因此用 `run_sync` 桥接。
  3. `render_as_batch=True`：SQLite 不支持 ALTER COLUMN，
     批量模式用「建新表 -> 拷数据 -> 换名」来模拟，保证本地与生产结构一致。
  4. `compare_type` / `compare_server_default` 打开，避免类型变更被漏检。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from reelagent_cloud.config import get_settings
from reelagent_cloud.db import models  # noqa: F401  —— 导入以注册全部表
from reelagent_cloud.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

#: 由应用配置注入真实连接串
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database.url)


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """过滤不需要迁移管理的对象。"""
    return not (type_ == "table" and name in {"spatial_ref_sys"})


def render_item(type_, obj, autogen_context):
    """自定义类型渲染。

    autogenerate 默认会把带方言变体的 JSON 渲染成
    `sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql')`，
    但**不会自动补 `Text` 的 import**，生成的迁移一跑就 NameError。

    同理，自定义 TypeDecorator（UTCDateTime）会被渲染成全限定名
    `reelagent_cloud.db.base.UTCDateTime(...)` 却不导入对应模块。
    这里统一改成从 `db.base` 直接 import 的短名，杜绝这类「生成即坏」的迁移。
    """
    from sqlalchemy import JSON as SAJSON

    from reelagent_cloud.db.base import UTCDateTime

    if type_ == "type" and isinstance(obj, SAJSON):
        autogen_context.imports.add("from reelagent_cloud.db.base import JSONType")
        return "JSONType"
    if type_ == "type" and isinstance(obj, UTCDateTime):
        autogen_context.imports.add("from reelagent_cloud.db.base import UTCDateTime")
        return "UTCDateTime"
    return False


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库。用于交付给 DBA 审核。"""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
        render_item=render_item,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
        render_item=render_item,
        render_as_batch=connection.dialect.name == "sqlite",
        # 迁移事务内禁止 DDL 自动提交，保证失败可整体回滚
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
