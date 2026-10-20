"""查清智谱 glm-5.3-flash 返回空输出的原因。

现象：探测时调用"成功"（有 token 计费），但 text 为空字符串。
两种可能：
  A. max_tokens 太小，被推理过程吃光（推理模型的常见表现）
  B. 适配器只读 content 字段，而模型把内容放在 reasoning_content 里

如果是 B，那是**适配器缺陷** —— 真实流水线里也会拿到空内容，
只是可能被上层重试掩盖了。这类问题必须查清，不能"看起来能跑"就算过。
"""

from __future__ import annotations

import contextlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT = "用一句话描述雨夜的便利店，不超过 20 字。"


def env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _, value = stripped.partition("=")
            values[key.strip()] = value.strip()
    return values


def call(api_key: str, base_url: str, model: str, max_tokens: int) -> dict:
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        with contextlib.suppress(Exception):
            body = exc.read().decode("utf-8", "ignore")[:300]
        return {"__error__": f"HTTP {exc.code} {body}"}
    except Exception as exc:
        return {"__error__": f"{type(exc).__name__}: {exc}"}


def inspect(body: dict, label: str) -> None:
    if "__error__" in body:
        print(f"  {label}: {body['__error__']}")
        return
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content")
    reasoning = message.get("reasoning_content")
    usage = body.get("usage") or {}
    finish = choice.get("finish_reason")
    print(f"  {label}")
    print(f"    finish_reason     = {finish}")
    print(f"    content 长度      = {len(content or '')}   内容={str(content)[:40]!r}")
    print(f"    reasoning_content = {'有，长度 ' + str(len(reasoning)) if reasoning else '无'}")
    print(f"    usage             = {usage}")


def main() -> int:
    values = env()
    key = values.get("REEL_PROVIDERS__LLM__API_KEY", "")
    base = values.get("REEL_PROVIDERS__LLM__BASE_URL", "")
    model = values.get("REEL_PROVIDERS__LLM__MODEL", "glm-5.3-flash")
    if not key:
        print("  .env 里没有 LLM Key")
        return 0

    print("=" * 74)
    print(f"智谱 {model} —— 不同 max_tokens 下的响应结构")
    print("=" * 74)
    for budget in (60, 300, 800):
        inspect(call(key, base, model, budget), f"max_tokens={budget}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
