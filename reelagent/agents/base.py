"""Agent 基类与共享工具。"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..runtime import Runtime


def clue_id(name: str) -> str:
    """由线索名称生成稳定 ID——同一名称永远映射到同一 ID，断点恢复才不会错位。"""
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "_", name).strip("_")[:16] or "item"
    digest = hashlib.md5(name.encode("utf-8"), usedforsecurity=False).hexdigest()[:4]
    return f"clue_{slug}_{digest}"


class Agent:
    """所有 Agent 的基类。Agent 只依赖 Runtime，不感知具体模型/供应商。"""

    name = "agent"
    role = ""

    def __init__(self, rt: Runtime) -> None:
        self.rt = rt

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} name={self.name}>"
