"""上下文窗口管理：组装 + 压缩 + Token 预算。

对应 JD：
  - "Context 组装与压缩策略"（腾讯 Agent Harness）
  - "在模型窗口和 token efficiency 约束下引导模型聚焦关键信息"（腾讯 WorkBuddy Context Engineering 方向）
  - "优化 LLM 请求组织、上下文复用、Prompt Cache、Token 使用效率"（字节即梦 Agent Harness）

策略：
  1. **固定区（pinned）**：系统提示、关键事实（如已确定的线索外观），永不压缩。
  2. **滚动区（rolling）**：历史消息，超出预算时从最旧的一段开始折叠成摘要。
  3. 折叠优先保留"产物路径 / 失败原因 / 被否决方案"，丢弃重复试探。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..llm import prompts
from ..llm.base import LLMClient, Message, estimate_tokens


@dataclass
class ContextStats:
    compressions: int = 0
    tokens_before_compress: int = 0
    tokens_after_compress: int = 0
    peak_tokens: int = 0

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_before_compress - self.tokens_after_compress)

    def as_dict(self) -> dict[str, Any]:
        return {
            "compressions": self.compressions,
            "tokens_before_compress": self.tokens_before_compress,
            "tokens_after_compress": self.tokens_after_compress,
            "tokens_saved": self.tokens_saved,
            "peak_tokens": self.peak_tokens,
            "compression_ratio": (
                round(self.tokens_saved / self.tokens_before_compress, 3)
                if self.tokens_before_compress
                else 0.0
            ),
        }


@dataclass
class ContextWindow:
    """一个受 token 预算约束的消息窗口。"""

    system: str
    budget: int = 24000
    llm: LLMClient | None = None
    keep_recent: int = 4  # 最近 N 条永不压缩
    stats: ContextStats = field(default_factory=ContextStats)

    _rolling: list[Message] = field(default_factory=list)
    _pinned: list[Message] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    def pin(self, content: str, *, role: str = "system") -> None:
        """写入永不压缩的关键事实。"""
        self._pinned.append({"role": role, "content": content})

    def add(self, role: str, content: str) -> None:
        self._rolling.append({"role": role, "content": content})

    def add_tool_result(self, tool: str, content: str) -> None:
        self._rolling.append({"role": "user", "content": f"[工具 {tool} 返回]\n{content}"})

    # ------------------------------------------------------------------ #
    @property
    def tokens(self) -> int:
        total = estimate_tokens(self.system)
        for m in self._pinned:
            total += estimate_tokens(m["content"])
        for m in self._rolling:
            total += estimate_tokens(m["content"])
        return total

    def messages(self) -> list[Message]:
        return [
            {"role": "system", "content": self.system},
            *self._pinned,
            *self._rolling,
        ]

    # ------------------------------------------------------------------ #
    def maybe_compress(self, *, force: bool = False) -> bool:
        """超预算时折叠最旧的历史。返回是否发生了压缩。"""
        if self.llm is None:
            return False
        if not force and self.tokens <= self.budget:
            return False

        compressible = self._rolling[: max(0, len(self._rolling) - self.keep_recent)]
        if not compressible:
            return False

        before = self.tokens
        history = "\n".join(f"{m['role']}: {m['content']}" for m in compressible)
        try:
            resp = self.llm.chat(
                [
                    {"role": "system", "content": prompts.COMPRESS_SYSTEM},
                    {"role": "user", "content": prompts.compress_user(history)},
                ],
                temperature=0.2,
                max_tokens=512,
                task="compress",
            )
            summary = resp.text.strip() or "(压缩失败)"
        except Exception as exc:  # 压缩失败要降级而不是中断
            summary = f"(压缩失败：{type(exc).__name__}；原始 {len(compressible)} 条已按字符截断)"

        tail = self._rolling[len(compressible) :]
        self._rolling = [
            {"role": "user", "content": f"[历史摘要]\n{summary}"},
            *tail,
        ]
        self.stats.compressions += 1
        self.stats.tokens_before_compress += before
        self.stats.tokens_after_compress += self.tokens
        return True

    def enforce_budget(self) -> bool:
        """硬约束：压缩仍超预算时，暴力截断最旧消息。"""
        changed = False
        while self.tokens > self.budget and len(self._rolling) > self.keep_recent:
            dropped = self._rolling.pop(0)
            changed = True
            _ = dropped
        return changed

    def snapshot(self) -> dict[str, Any]:
        return {
            "tokens": self.tokens,
            "budget": self.budget,
            "rolling": len(self._rolling),
            "pinned": len(self._pinned),
            **self.stats.as_dict(),
        }
