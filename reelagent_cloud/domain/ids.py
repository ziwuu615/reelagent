"""ID 生成。

用 ULID（Crockford Base32，26 字符）而非 UUID4：
  - **单调递增**：按时间有序，做主键时 B-tree 插入局部性好，避免 UUID 的随机写放大
  - **可读**：带业务前缀（`proj_01H...`），日志与 URL 里一眼能看出类型
  - **无中心依赖**：不需要数据库自增或 Snowflake 服务
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def ulid(timestamp_ms: int | None = None) -> str:
    """生成 26 字符 ULID。"""
    ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    randomness = int.from_bytes(os.urandom(10), "big")
    return _encode(ts, 10) + _encode(randomness, 16)


def new_id(prefix: str) -> str:
    """带业务前缀的 ID，例如 `proj_01JBQ...`。"""
    return f"{prefix}_{ulid()}"


def parse_ulid_timestamp(value: str) -> datetime:
    """从 ID 中解出创建时间（去掉前缀后前 10 位）。"""
    raw = value.split("_", 1)[1] if "_" in value else value
    ts = 0
    for ch in raw[:10]:
        ts = ts * 32 + _CROCKFORD.index(ch)
    return datetime.fromtimestamp(ts / 1000, tz=UTC)


#: 各实体前缀
PREFIX = {
    "user": "usr",
    "org": "org",
    "membership": "mem",
    "api_key": "key",
    "project": "proj",
    "job": "job",
    "shot": "shot",
    "artifact": "art",
    "ledger": "led",
    "subscription": "sub",
    "usage": "use",
    "audit": "aud",
    "webhook": "wh",
    "session": "ses",
    "invite": "inv",
}
