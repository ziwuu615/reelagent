"""探测即梦（Jimeng）API 的准入门槛。

用你现有的火山引擎 Ark Key 去打几个候选端点，**让服务端告诉我们答案**，
而不是靠翻文档猜。要区分的情况：

    Key 无效        -> 401 AuthenticationError
    端点/模型不存在  -> 404 InvalidEndpointOrModel
    模型未开通       -> 404 ModelNotOpen（火山方舟那次的坑）
    需要签名认证     -> 401/403 且提示 signature/credential
    可以调用         -> 200 并返回 task_id

即梦在火山引擎上可能有三种接入形态，逐个试：
    1. Ark 兼容端点（和 Seedance 同一套，最省事）
    2. Visual/CV 服务端点（火山传统视觉服务，用 AK/SK 签名）
    3. 独立的即梦端点
"""

from __future__ import annotations

import contextlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 候选端点
CANDIDATES = [
    (
        "Ark 兼容端点 + 即梦模型 ID",
        "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks",
        {"model": "jimeng_ti2v_v30_pro", "content": [{"type": "text", "text": "测试"}]},
    ),
    (
        "Ark 兼容端点 + 即梦展示名",
        "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks",
        {"model": "jimeng-video-3.0-pro", "content": [{"type": "text", "text": "测试"}]},
    ),
    (
        "Visual 服务端点（火山传统视觉服务，用签名认证）",
        "https://visual.volcengineapi.com?Action=CVSync2AsyncSubmitTask&Version=2022-08-31",
        {"req_key": "jimeng_ti2v_v30_pro", "prompt": "测试"},
    ),
]

#: 知识性对照：同一个 Key 打已知可用的路径，确认 Key 本身有效
CONTROL = (
    "对照：Ark 模型清单（已知该 Key 有效）",
    "https://ark.cn-beijing.volces.com/api/v3/models",
    None,
)


def load_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("REEL_PROVIDERS__LLM__API_KEY=") and line.split("=", 1)[1].strip():
            value = line.split("=", 1)[1].strip()
            if value.startswith("ark-"):
                return value
    # .env 里可能已经换成别的供应商，退回到方舟那一段的注释里找
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "ark-cbf87402" in line:
            return line.split("=", 1)[-1].strip()
    raise SystemExit("在 .env 里找不到 Ark Key")


def call(url: str, key: str, payload: dict | None) -> tuple[int, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    method = "POST" if payload is not None else "GET"
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.status, resp.read().decode("utf-8", "ignore")[:400]
    except urllib.error.HTTPError as exc:
        body = ""
        # 读取错误体只为给出更清晰的判定，失败也不该掩盖状态码
        with contextlib.suppress(Exception):
            body = exc.read().decode("utf-8", "ignore")[:400]
        return exc.code, body
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def classify(status: int, body: str) -> str:
    low = body.lower()
    if status == 200:
        return "✓ 可以调用"
    if status == -1:
        return "✗ 网络层失败（可能是端点不存在或 DNS 不通）"
    if "authenticationerror" in low or status == 401:
        if "signature" in low or "credential" in low or "canonical" in low:
            return "✗ 需要火山引擎签名认证（AK/SK），不是 Bearer Key"
        return "✗ Key 无效或不被该端点接受"
    if "modelnotopen" in low:
        return "✗ 模型未开通 —— 需要到控制台开通（火山方舟那次的坑）"
    if "invalidendpointormodel" in low:
        return "✗ 端点/模型不存在 —— 即梦不在此路径下"
    if "signature" in low or "canonical" in low or "invalidauthorization" in low:
        return "✗ 需要火山引擎签名认证（AK/SK）"
    # 火山视觉服务的错误结构是 {"Error":{"CodeN":100024,"Code":"InvalidAuthorization"}}
    if "100024" in low:
        return "✗ 需要火山引擎签名认证（AK/SK）"
    if status == 403:
        return "✗ 无权限（可能未开通服务或需签名）"
    if status == 404:
        return "✗ 路径不存在"
    return f"? 未归类（HTTP {status}）"


def main() -> int:
    key = load_key()
    print(f"使用的 Key: {key[:16]}…{key[-6:]}")
    print()

    print("=" * 72)
    print("对照组：先确认这把 Key 本身仍然有效")
    print("=" * 72)
    label, url, payload = CONTROL
    status, body = call(url, key, payload)
    n = body.count('"id"')
    print(f"  {label}")
    print(f"    HTTP {status}  模型条目数≈{n}")
    print()

    print("=" * 72)
    print("即梦候选端点探测")
    print("=" * 72)
    results = []
    for label, url, payload in CANDIDATES:
        status, body = call(url, key, payload)
        verdict = classify(status, body)
        results.append((label, status, verdict, body))
        print(f"  {label}")
        print(f"    端点   : {url[:78]}")
        print(f"    HTTP   : {status}")
        print(f"    判定   : {verdict}")
        print(f"    响应   : {body[:220].replace(chr(10), ' ')}")
        print()

    print("=" * 72)
    print("结论")
    print("=" * 72)
    for label, _status, verdict, _body in results:
        print(f"  {verdict:<34} {label}")
    print()
    print("若全部失败且提示「需要签名认证」，说明即梦走的是火山引擎")
    print("传统视觉服务（AK/SK 签名），而不是 Ark 的 Bearer Key。")
    print("那种情况下需要在控制台创建 AccessKey/SecretKey，适配器也要改写签名。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
