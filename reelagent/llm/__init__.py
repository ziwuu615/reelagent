"""LLM 客户端工厂。"""

from __future__ import annotations

from ..config import Settings
from .base import LLMClient, LLMError, LLMResponse, Message, Usage, compute_cost, estimate_tokens
from .mock import MockLLM
from .openai_compat import OpenAICompatClient

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMResponse",
    "Message",
    "MockLLM",
    "OpenAICompatClient",
    "Usage",
    "build_llm",
    "compute_cost",
    "estimate_tokens",
]


def build_llm(settings: Settings, *, judge: bool = False) -> LLMClient:
    """按配置构建 LLM 客户端。

    - mock 模式：返回 MockLLM（无需任何 Key）
    - live 模式但未配置 Key：**自动降级为 MockLLM 并打印提示**，而不是直接崩掉。
      这让「没配 Key 也能先把流水线跑通」成为默认体验。
    """
    if settings.is_mock or not settings.llm.enabled:
        return MockLLM()
    model = (settings.llm_judge_model if judge else "") or settings.llm.model
    return OpenAICompatClient(
        base_url=settings.llm.base_url,
        api_key=settings.llm.api_key,
        model=model,
    )
