"""Harness：Agent 运行时底座。

模块职责：
  state.Store        —— Checkpoint 持久化（断点恢复的基础）
  tracing.Tracer     —— Span 级全链路可观测
  registry.ToolRegistry —— Tool/Skill 抽象、注册与调用（MCP 兼容结构）
  context.ContextWindow —— 上下文组装、压缩与 Token 预算
  executor.run_with_retry —— 带指数退避、Tracing 与降级记录的执行包装
"""

from .context import ContextStats, ContextWindow
from .executor import RetryPolicy, run_with_retry
from .registry import Tool, ToolError, ToolRegistry, arr_prop, num_prop, obj_schema, str_prop
from .state import STAGE_DONE, STAGE_FAILED, STAGE_PENDING, STAGE_RUNNING, StageRecord, Store
from .tracing import Span, Tracer

__all__ = [
    "STAGE_DONE",
    "STAGE_FAILED",
    "STAGE_PENDING",
    "STAGE_RUNNING",
    "ContextStats",
    "ContextWindow",
    "RetryPolicy",
    "Span",
    "StageRecord",
    "Store",
    "Tool",
    "ToolError",
    "ToolRegistry",
    "Tracer",
    "arr_prop",
    "num_prop",
    "obj_schema",
    "run_with_retry",
    "str_prop",
]
