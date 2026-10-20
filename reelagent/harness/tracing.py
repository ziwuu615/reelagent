"""Span 级链路追踪。

设计要点："设计并实现 Agent 执行全链路的可观测系统，对 LLM 调用、工具调用、
重试与降级、Token 与成本消耗等关键环节做 Span 级建模与结构化埋点。"

两种用法：
    # 1) 包裹一段执行（推荐）
    with tracer.span("director.shot", kind="agent", shot_id="s1") as sp:
        ...
        sp.set(prompt_tokens=120)
        sp.fail("...")        # 可选

    # 2) 即时记录一个已经发生的事实（不需要 with）
    tracer.record("art_director.base_image.failed", status="failed", error=str(exc))

注意：Span 只有在 `with` 退出或被显式 `close()` 时才会写入存储。
若需要"记完就走"，请用 `record()`——早期版本在这里踩过坑：
用 `span(...).set(...)` 的写法不会落库，导致失败被静默吞掉。
"""

from __future__ import annotations

import contextlib
import threading
import time
import uuid
from typing import Any, Literal


class Span:
    def __init__(
        self,
        tracer: Tracer,
        span_id: str,
        name: str,
        kind: str,
        attrs: dict[str, Any],
        parent_id: str | None,
    ) -> None:
        self.tracer = tracer
        self.span_id = span_id
        self.name = name
        self.kind = kind
        self.attrs: dict[str, Any] = dict(attrs)
        self.parent_id = parent_id
        self.started_at = time.time()
        self.status = "ok"
        self.error: str | None = None
        self._closed = False

    # ------------------------------------------------------------------ #
    def set(self, **attrs: Any) -> Span:
        self.attrs.update(attrs)
        return self

    def fail(self, error: str) -> Span:
        self.status = "failed"
        self.error = error[:1000]
        return self

    # ------------------------------------------------------------------ #
    def __enter__(self) -> Span:
        self.tracer._push(self)
        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
        if exc is not None:
            self.status = "failed"
            self.error = f"{exc_type.__name__ if exc_type else 'Error'}: {exc}"[:1000]
        self.close()
        return False  # 不吞异常

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.tracer._close(self)

    # 忘记 close 时的兜底：仍然落库，避免"静默丢失"
    def __del__(self) -> None:  # pragma: no cover
        with contextlib.suppress(Exception):
            self.close()


class _NullSpan:
    """无 store 时的空实现。

    **必须实现与 Span 完全相同的接口**，否则调用方一旦直接调 `close()`，
    在没有 store 的环境（如离线模式）就会 AttributeError ——
    空对象的价值就在于「行为退化为无操作」，而不是「行为退化为崩溃」。
    """

    def __init__(self) -> None:
        self.attrs: dict[str, Any] = {}
        self.started_at = time.time()
        self.status = "ok"
        self.error: str | None = None

    def set(self, **attrs: Any) -> _NullSpan:
        self.attrs.update(attrs)
        return self

    def fail(self, error: str) -> _NullSpan:
        self.status = "failed"
        self.error = error
        return self

    def close(self) -> None:
        """空实现：没有 store 就无处可写。"""
        return None

    def __enter__(self) -> _NullSpan:
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        return False


class Tracer:
    """轻量 tracer。线程局部维护 parent 栈，支持并发子 Agent。"""

    def __init__(self, store: Any | None, project_id: str) -> None:
        self.store = store
        self.project_id = project_id
        self._local = threading.local()

    # ------------------------------------------------------------------ #
    @property
    def _stack(self) -> list[str]:
        if not hasattr(self._local, "stack"):
            self._local.stack = []
        return self._local.stack

    @property
    def current_parent(self) -> str | None:
        return self._stack[-1] if self._stack else None

    # ------------------------------------------------------------------ #
    def span(
        self,
        name: str,
        *,
        kind: str = "internal",
        attrs: dict[str, Any] | None = None,
        **extra: Any,
    ) -> Span | _NullSpan:
        """创建一个 span。

        `attrs` 用于传入键名可能与形参冲突的字典（例如本身含 "kind" 键），
        `**extra` 用于普通附加字段。
        """
        if self.store is None:
            return _NullSpan()
        merged: dict[str, Any] = {}
        if attrs:
            merged.update(attrs)
        merged.update(extra)
        return Span(self, uuid.uuid4().hex[:16], name, kind, merged, self.current_parent)

    # ------------------------------------------------------------------ #
    def record(
        self,
        name: str,
        *,
        kind: str = "internal",
        status: str = "ok",
        error: str | None = None,
        duration_ms: float = 0.0,
        attrs: dict[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        """即时写入一个 span，无需 with。用于记录已经发生的事实。"""
        if self.store is None:
            return
        merged: dict[str, Any] = {}
        if attrs:
            merged.update(attrs)
        merged.update(extra)
        now = time.time()
        with contextlib.suppress(Exception):
            self.store.record_span(
                span_id=uuid.uuid4().hex[:16],
                project_id=self.project_id,
                parent_id=self.current_parent,
                name=name,
                kind=kind,
                status=status,
                started_at=now - duration_ms / 1000.0,
                ended_at=now,
                attrs=merged,
                error=error[:1000] if error else None,
            )

    # ------------------------------------------------------------------ #
    def _push(self, sp: Span) -> None:
        self._stack.append(sp.span_id)

    def _close(self, sp: Span) -> None:
        if self._stack and self._stack[-1] == sp.span_id:
            self._stack.pop()
        if self.store is None:  # 构造时已判过，这里只是让类型检查器放心
            return
        with contextlib.suppress(Exception):
            self.store.record_span(
                span_id=sp.span_id,
                project_id=self.project_id,
                parent_id=sp.parent_id,
                name=sp.name,
                kind=sp.kind,
                status=sp.status,
                started_at=sp.started_at,
                ended_at=time.time(),
                attrs=sp.attrs,
                error=sp.error,
            )
