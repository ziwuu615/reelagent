"""OpenAI 兼容协议的 LLM 客户端。

只用标准库 urllib，因此不需要 httpx/openai SDK 也能跑真实模型。
支持：chat/completions、JSON 模式、自动重试。
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.error
import urllib.request

from ..schema import Usage
from .base import LLMClient, LLMError, LLMResponse, Message, compute_cost


class OpenAICompatClient(LLMClient):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 180.0,
        max_retries: int = 3,
    ) -> None:
        if not api_key:
            raise LLMError("缺少 LLM API Key（REEL_LLM_API_KEY）")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    # ------------------------------------------------------------------ #
    def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_mode: bool = False,
        task: str = "",
    ) -> LLMResponse:
        target = model or self.model
        payload: dict[str, object] = {
            "model": target,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            started = time.perf_counter()
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                elapsed = (time.perf_counter() - started) * 1000

                choice = (body.get("choices") or [{}])[0]
                text = (choice.get("message") or {}).get("content") or ""
                raw_usage = body.get("usage") or {}
                usage = Usage(
                    prompt_tokens=int(raw_usage.get("prompt_tokens") or 0),
                    completion_tokens=int(raw_usage.get("completion_tokens") or 0),
                    cached_tokens=int(
                        (raw_usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
                    ),
                )
                cost = compute_cost(target, usage)
                self.total_usage = self.total_usage + usage
                self.total_cost_usd += cost
                return LLMResponse(
                    text=text,
                    model=body.get("model", target),
                    usage=usage,
                    latency_ms=elapsed,
                    cost_usd=cost,
                    raw=body,
                )
            except urllib.error.HTTPError as exc:  # 4xx/5xx
                detail = ""
                with contextlib.suppress(Exception):
                    detail = exc.read().decode("utf-8", "ignore")[:400]
                last_err = LLMError(f"HTTP {exc.code}: {detail or exc.reason}")
                # 4xx 里除了 429 之外重试无意义
                if 400 <= exc.code < 500 and exc.code != 429:
                    raise last_err from exc
            except Exception as exc:  # 网络类错误
                last_err = LLMError(f"{type(exc).__name__}: {exc}")
            if attempt < self.max_retries:
                time.sleep(min(2**attempt * 0.5, 6.0))

        raise LLMError(f"LLM 调用失败（重试 {self.max_retries} 次）：{last_err}")
