"""Agent 层：四个职责单一、边界清晰的智能体。

    Planner      种子文本 -> 结构化剧本（场景/分镜/线索）
    ArtDirector  线索 -> 稳定外观描述 + 基准图
    Director     逐镜生成（并发 + 路由 + 重试 + 断点恢复）
    Evaluator    确定性指标 + LLM-as-Judge + CI 门禁

它们之间不直接通信，只通过 Project 状态与 Store 交换数据——
这正是"长任务可断点恢复"的前提。
"""

from .artdirector import ArtDirector
from .base import Agent, clue_id
from .director import Director
from .evaluator import Evaluator
from .planner import Planner

__all__ = ["Agent", "ArtDirector", "Director", "Evaluator", "Planner", "clue_id"]
