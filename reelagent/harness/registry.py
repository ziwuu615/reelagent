"""工具注册表（Tool / Skill 抽象与注册）。

对应 JD：
  - "Tool/Skill 抽象与注册机制"（腾讯 Agent Harness）
  - "标准协议接入内外部开发的 Agent"（字节 Agent 软件架构师）

`specs()` / `call()` 采用 MCP 的 tools/list 与 tools/call 数据结构，
因此这层注册表可以直接被一个 MCP Server 暴露出去，无需改写。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class ToolError(RuntimeError):
    pass


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., Any]
    tags: list[str] = field(default_factory=list)

    def spec(self) -> dict[str, Any]:
        """MCP tools/list 条目格式。"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._calls: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    def register(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        fn: Callable[..., Any],
        *,
        tags: list[str] | None = None,
        overwrite: bool = False,
    ) -> Tool:
        if name in self._tools and not overwrite:
            raise ToolError(f"工具已注册：{name}")
        tool = Tool(name, description, input_schema, fn, tags or [])
        self._tools[name] = tool
        return tool

    def tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        *,
        tags: list[str] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """装饰器形式注册。"""

        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.register(name, description, input_schema, fn, tags=tags)
            return fn

        return deco

    # ------------------------------------------------------------------ #
    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolError(f"未注册的工具：{name}（可用：{', '.join(sorted(self._tools))}）")
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        return [t.spec() for t in self._tools.values()]

    # ------------------------------------------------------------------ #
    def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """MCP tools/call 语义：抛出 ToolError，调用记录留痕。"""
        tool = self.get(name)
        args = arguments or {}
        started = time.perf_counter()
        record: dict[str, Any] = {"tool": name, "args": args, "ts": started}
        try:
            result = tool.fn(**args)
            record["ok"] = True
            return result
        except Exception as exc:
            record["ok"] = False
            record["error"] = f"{type(exc).__name__}: {exc}"
            raise ToolError(f"工具 {name} 执行失败：{exc}") from exc
        finally:
            record["ms"] = (time.perf_counter() - started) * 1000
            self._calls.append(record)

    @property
    def call_log(self) -> list[dict[str, Any]]:
        return list(self._calls)

    def stats(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for c in self._calls:
            b = out.setdefault(c["tool"], {"calls": 0, "total_ms": 0.0, "failed": 0})
            b["calls"] += 1
            b["total_ms"] += c.get("ms", 0.0)
            if not c.get("ok"):
                b["failed"] += 1
        return out


# --------------------------------------------------------------------------- #
# JSON Schema 小工具
# --------------------------------------------------------------------------- #
def obj_schema(**props: dict[str, Any]) -> dict[str, Any]:
    """把 name=schema 的 kwargs 变成 object schema。"""
    return {
        "type": "object",
        "properties": props,
        "required": [k for k, v in props.items() if v.pop("_required", False)],
        "additionalProperties": False,
    }


def str_prop(desc: str, *, required: bool = False, enum: list[str] | None = None) -> dict[str, Any]:
    p: dict[str, Any] = {"type": "string", "description": desc}
    if enum:
        p["enum"] = enum
    if required:
        p["_required"] = True
    return p


def num_prop(desc: str, *, required: bool = False, default: float | None = None) -> dict[str, Any]:
    p: dict[str, Any] = {"type": "number", "description": desc}
    if default is not None:
        p["default"] = default
    if required:
        p["_required"] = True
    return p


def arr_prop(desc: str, items: str = "string", *, required: bool = False) -> dict[str, Any]:
    p: dict[str, Any] = {"type": "array", "description": desc, "items": {"type": items}}
    if required:
        p["_required"] = True
    return p
