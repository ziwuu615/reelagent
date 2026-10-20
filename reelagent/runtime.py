"""运行时（Runtime）：把 Harness / LLM / 供应商 / 工具注册表装配在一起。

所有 Agent 都只依赖 Runtime，不直接感知具体模型或供应商——
这样换模型、换供应商、换存储都不需要改 Agent 代码。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .config import Settings
from .harness import Store, ToolRegistry, Tracer
from .llm import LLMClient, build_llm
from .providers import ProviderBundle, build_providers
from .schema import Usage

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict[str, Any]:
    """从 LLM 输出里稳健地抽出 JSON。

    依次尝试：直接解析 -> 去 markdown 代码块 -> 截取最外层花括号。
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("LLM 返回为空")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError(f"无法解析 JSON：{exc}；片段={candidate[:200]}") from exc
    raise ValueError(f"未找到 JSON：{text[:200]}")


class Runtime:
    def __init__(self, settings: Settings, project_id: str, *, store: Store | None = None) -> None:
        self.settings = settings
        self.project_id = project_id
        self.settings.ensure_workspace()

        self.store = store or Store(settings.workspace / "reelagent.db")
        self._owns_store = store is None
        self.tracer = Tracer(self.store, project_id)

        self.llm: LLMClient = build_llm(settings)
        self.judge_llm: LLMClient = build_llm(settings, judge=True)
        self.providers: ProviderBundle = build_providers(settings)
        self.tools: ToolRegistry = ToolRegistry()

        # 把供应商能力注册成工具（Tool/Skill 抽象）
        from .tools.media_tools import register_media_tools

        register_media_tools(self)

        self.usage = Usage()

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def __enter__(self) -> Runtime:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    def dir_for(self, project_id: str, kind: str) -> Path:
        return self.settings.artifacts_dir(project_id, kind)

    def rel(self, path: Path) -> str:
        return self.settings.rel(path)

    # ------------------------------------------------------------------ #
    def llm_json(
        self,
        *,
        system: str,
        user: str,
        task: str,
        span_name: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        judge: bool = False,
        max_repair: int = 2,
    ) -> dict[str, Any]:
        """调用 LLM 并解析 JSON，失败时自动要求模型修复格式。"""
        client = self.judge_llm if judge else self.llm
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_err: Exception | None = None
        for attempt in range(1, max_repair + 1):
            with self.tracer.span(
                span_name, kind="llm", task=task, attempt=attempt, model=client.model
            ) as sp:
                resp = client.chat(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=True,
                    task=task,
                )
                self.usage = self.usage + resp.usage
                self.store.record_cost(
                    project_id=self.project_id,
                    category="llm",
                    provider="llm",
                    model=resp.model,
                    unit="token",
                    quantity=resp.usage.total_tokens,
                    usd=resp.cost_usd,
                    meta={"task": task, "span": span_name},
                )
                sp.set(
                    prompt_tokens=resp.usage.prompt_tokens,
                    completion_tokens=resp.usage.completion_tokens,
                    latency_ms=round(resp.latency_ms, 1),
                    cost_usd=round(resp.cost_usd, 6),
                )
                try:
                    return extract_json(resp.text)
                except ValueError as exc:
                    last_err = exc
                    sp.fail(str(exc))
                    messages.append({"role": "assistant", "content": resp.text[:2000]})
                    messages.append(
                        {
                            "role": "user",
                            "content": "上面的输出不是合法 JSON。请只输出 JSON 对象本身，"
                            "不要 markdown 代码块，不要任何解释文字。",
                        }
                    )
        raise ValueError(f"{span_name} 连续 {max_repair} 次未能返回合法 JSON：{last_err}")

    # ------------------------------------------------------------------ #
    def record_media_cost(
        self,
        *,
        category: str,
        provider: str,
        model: str,
        unit: str,
        quantity: float,
        usd: float,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.store.record_cost(
            project_id=self.project_id,
            category=category,
            provider=provider,
            model=model,
            unit=unit,
            quantity=quantity,
            usd=usd,
            meta=meta,
        )

    def spent_usd(self) -> float:
        return float(self.store.cost_summary(self.project_id).get("total_usd", 0.0))

    def describe(self) -> dict[str, Any]:
        return {
            "settings": self.settings.describe(),
            "providers": self.providers.describe(),
            "provider_notes": self.providers.notes,
            "tools": self.tools.names(),
            "llm": self.llm.model,
        }


def timed() -> float:
    return time.perf_counter()
