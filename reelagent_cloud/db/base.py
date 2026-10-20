"""SQLAlchemy 基类与通用列类型。

可移植性策略：同一套模型同时支持 **PostgreSQL（生产）** 与 **SQLite（本地开发/测试）**。
- JSON 在 PG 上用 JSONB（可索引），在 SQLite 上退化为 TEXT
- 枚举不用 PG 原生 ENUM（类型变更好管理），而是 VARCHAR + CHECK
- 时间统一用带时区的 Python 侧默认值，避免 SQLite 丢时区
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

#: PG 用 JSONB，其它方言用 JSON
JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """始终以「带 UTC 时区」的 datetime 读写。

    为什么需要它：SQLite 不存储时区信息，读回来是 naive datetime，
    而业务代码里到处是 `datetime.now(timezone.utc)`（aware）。
    aware 与 naive 一旦参与比较或相减就会抛 TypeError，
    且报错点往往离根因很远，排查成本极高。

    这个装饰器保证：
      - 写入：naive 一律按 UTC 解释，统一转成 UTC aware
      - 读取：一律补上 UTC tzinfo
    这样上层代码永远只面对 aware UTC，类型混乱从源头消除。
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def make_enum(enum_cls: type, name: str) -> SAEnum:
    """构造可移植的枚举列：存枚举的 value，带长度与 CHECK。"""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


class Base(DeclarativeBase):
    #: SQLAlchemy 的类型推断映射。声明为 ClassVar 以表明这是类级配置而非实例状态。
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSONType,
        list[str]: JSONType,
        list[dict[str, Any]]: JSONType,
        datetime: UTCDateTime,
    }

    def __repr__(self) -> str:  # pragma: no cover
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"

    def to_dict(self, *, exclude: set[str] | None = None) -> dict[str, Any]:
        exclude = exclude or set()
        out: dict[str, Any] = {}
        for column in self.__table__.columns:
            if column.name in exclude:
                continue
            value = getattr(self, column.name)
            if isinstance(value, datetime):
                value = value.isoformat()
            out[column.name] = value
        return out


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)
