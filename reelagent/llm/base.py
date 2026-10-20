"""LLM 抽象层。

统一走 OpenAI 兼容协议，因此 DeepSeek / 通义千问 / 火山方舟 / OpenAI / 本地 vLLM
可以无缝互换。`task` 参数是给离线 mock 用的语义提示，真实客户端会忽略它。
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..schema import Usage

Message = dict[str, str]


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    text: str
    model: str = ""
    usage: Usage = field(default_factory=Usage)
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    raw: dict[str, Any] | None = None


def estimate_tokens(text: str) -> int:
    """轻量 token 估算：CJK 字符按 1 token，其余按 4 字符 1 token。

    不引入 tiktoken，保证零依赖；用于上下文预算管理已经足够。
    """
    if not text:
        return 0
    cjk = len(re.findall(r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]", text))
    other = len(text) - cjk
    return int(cjk + other / 4) + 1


class LLMClient(ABC):
    """所有 LLM 客户端的统一接口。"""

    model: str = ""

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_mode: bool = False,
        task: str = "",
    ) -> LLMResponse: ...

    # 便于统计
    total_usage: Usage = Usage()
    total_cost_usd: float = 0.0


# --------------------------------------------------------------------------- #
# 模型价格表（USD / 1M tokens），用于成本追踪与模型路由
# --------------------------------------------------------------------------- #
#: ⚠️ 只有标注了来源的条目经过核对，其余是估算值。
#: 核对状态直接决定成本上报是否可信 —— 别把估算当事实对外说。
MODEL_PRICES: dict[str, tuple[float, float]] = {
    # model: (prompt_per_1m, completion_per_1m)
    #
    # 已核对 —— 百炼 qwen-plus 官方定价页「华北2（北京）」输入≤128k：
    #   输入 $0.115/M、输出 $0.287/M
    #   https://www.alibabacloud.com/help/zh/model-studio/qwen-plus （抓取于 2026-10-16）
    #
    # 踩过的坑：这里原本填的是 (0.40, 1.20) —— 那是**新加坡地域**的价格，
    # 而我们用的是北京端点（dashscope.aliyuncs.com），成本因此高估约 3.5 倍。
    # 同一模型在不同地域价格不同，抄价格表时必须确认地域。
    "qwen-plus": (0.115, 0.287),
    #
    # 以下为**未核对**的估算值，只作成本追踪的量级参考，不保证与官方一致。
    # 用到哪个再核实哪个，不要批量当成真实价格。
    "deepseek-chat": (0.27, 1.10),  # 未核对
    "deepseek-reasoner": (0.55, 2.19),  # 未核对
    "qwen-max": (1.60, 6.40),  # 未核对
    "doubao-seed-1-6": (0.11, 0.28),  # 未核对
    "gpt-4o-mini": (0.15, 0.60),  # 未核对
    "gpt-4o": (2.50, 10.00),  # 未核对
}

#: 认不出模型时的保守兜底价。宁可高估（留余量），不要低估。
DEFAULT_PRICE = (0.50, 1.50)

#: 思考模式的**输出**价通常是非思考模式的数倍
#: （qwen-plus 北京：$1.147/M 对比 $0.287/M，约 4 倍）。
#: 当前适配器不开启思考模式，故 compute_cost 按非思考价计算；
#: 将来若启用 `enable_thinking`，必须同步调整，否则成本会低估约 4 倍。
THINKING_OUTPUT_MULTIPLIER = 4.0


def price_for(model: str) -> tuple[float, float]:
    if model in MODEL_PRICES:
        return MODEL_PRICES[model]
    for key, value in MODEL_PRICES.items():
        if model.startswith(key):
            return value
    return DEFAULT_PRICE


def compute_cost(model: str, usage: Usage) -> float:
    p_in, p_out = price_for(model)
    return (usage.prompt_tokens * p_in + usage.completion_tokens * p_out) / 1_000_000


class _TimedChat:
    """给 chat 调用计时的混入辅助。"""

    @staticmethod
    def _tick() -> float:
        return time.perf_counter()
