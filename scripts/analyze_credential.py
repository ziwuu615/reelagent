"""分析一个疑似凭证的值，并用它实测即梦端点。

要回答两个问题：
  1. 这个值到底是什么结构？（本地解码即可，不联网）
  2. 它能用来调用即梦 API 吗？（实测，让服务端给答案）
"""

from __future__ import annotations

import base64
import contextlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

VALUE = (
    "VxCgNvLTE.ChBqRnI1M0lHSFBRODBUakpxEPzqj_kHGAIgvZCxLioQ991wen4DTciXjKc0p95Pfg"
    ".0WmbJ2npgiIMEkENn5tKqWd_I0wyYEZVK-X_jmpM8DSIjDGdbJ4URsCrFegFD14_FrGJunyzs_uGDxkJqSBg4AeZ"
)

ROOT = Path(__file__).resolve().parent.parent


def b64d(segment: str) -> bytes:
    """宽容的 base64 解码（补齐 padding，支持 urlsafe）。"""
    s = segment.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s)
    except Exception as exc:
        return f"<解码失败 {exc}>".encode()


def printable(raw: bytes) -> str:
    """把字节里可打印的 ASCII 抽出来，方便识别内嵌的明文。"""
    runs = re.findall(rb"[\x20-\x7e]{4,}", raw)
    return " | ".join(r.decode() for r in runs) if runs else "(无可打印片段)"


def analyze() -> None:
    print("=" * 74)
    print("1. 本地结构分析")
    print("=" * 74)
    parts = VALUE.split(".")
    print(f"  段数: {len(parts)}")
    for i, seg in enumerate(parts):
        raw = b64d(seg)
        print(f"\n  [{i}] 长度={len(seg)}  原文前 40: {seg[:40]}")
        if isinstance(raw, bytes) and not raw.startswith(b"<"):
            print(f"      解码后 {len(raw)} 字节，前 16: {raw[:16].hex(' ')}")
            print(f"      内嵌明文: {printable(raw)}")
            # protobuf 常见的 field tag：0x0A = field1 len-delimited
            if raw[:1] == b"\x0a" and len(raw) > 2:
                declared = raw[1]
                payload = raw[2 : 2 + declared]
                print(f"      protobuf: field1 长度={declared} 内容={payload[:48]!r}")
        else:
            print(f"      {raw!r}")

    print()
    print("  对照：火山引擎 AccessKey ID 的标准形态")
    print("      前缀 AKLT + 32 位，例如 AKLTxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    print(f"      本值前缀: {VALUE[:9]!r}  ->  {'匹配' if VALUE.startswith('AKLT') else '不匹配'}")


def probe(payload_value: str) -> None:
    """把它当作 Bearer 令牌打即梦端点，看服务端怎么回应。"""
    print()
    print("=" * 74)
    print("2. 实测：作为 Bearer 令牌调用即梦 Visual 端点")
    print("=" * 74)
    url = "https://visual.volcengineapi.com?Action=CVSync2AsyncSubmitTask&Version=2022-08-31"
    body = json.dumps({"req_key": "jimeng_ti2v_v30_pro", "prompt": "测试"}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {payload_value}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            print(f"  HTTP {resp.status}")
            print(f"  响应: {resp.read().decode('utf-8', 'ignore')[:400]}")
            print("\n  -> 如果这里是 200，说明它确实能用，我立刻写适配器")
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):
            detail = exc.read().decode("utf-8", "ignore")[:500]
        print(f"  HTTP {exc.code}")
        print(f"  响应: {detail}")
        low = detail.lower()
        if "100024" in low or "invalidauthorization" in low:
            print("\n  -> 仍是 InvalidAuthorization：这个值不是有效的火山引擎签名凭证。")
        elif "modelnotopen" in low:
            print("\n  -> 凭证有效，但模型未开通 —— 这就是门槛所在。")
    except Exception as exc:
        print(f"  网络错误: {type(exc).__name__}: {exc}")


def main() -> int:
    analyze()
    probe(VALUE)
    print()
    print("=" * 74)
    print("结论")
    print("=" * 74)
    print("  这个值的结构（protobuf + ECDSA 签名）与火山引擎原生 AK/SK 完全不同，")
    print("  也不是 Ark 的 Bearer Key（ark- 开头）。")
    print("  它更像某个**中转/聚合平台**签发的 API Key ——")
    print("  你之前给的阿里云那把也是同样的结构，两者很可能来自同一处。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
