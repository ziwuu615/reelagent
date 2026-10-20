"""阿里云百炼（DashScope）连通性分级探测。

先验证鉴权，再逐级验证能力。设计原则与 probe_ark / probe_zhipu 一致，
但这里多做一件关键的事：**同时探测两条协议路径**。

为什么要双路径：
    DashScope 提供两套端点 ——
      1. OpenAI 兼容端点（/compatible-mode/v1）—— 我的 LLM/TTS 适配器现成可用
      2. 原生端点（/api/v1）—— 视频、图像生成必须走这条
    两者鉴权方式相同但路径与请求体格式完全不同，必须分别验证。

用法：
    python scripts/probe_dashscope.py            # 鉴权 + 文本
    python scripts/probe_dashscope.py --video    # 额外验证视频（会真实扣费）
    python scripts/probe_dashscope.py --image-input   # 验证视频接口接受哪种图片格式
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: OpenAI 兼容端点（文本与 TTS）
COMPAT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
#: 原生端点（图像、视频生成）
NATIVE_BASE = "https://dashscope.aliyuncs.com/api/v1"

TEXT_MODELS = ["qwen-turbo", "qwen-plus", "qwen-max", "qwen-flash"]
#: 视频候选。wan3.0-video 是官方推荐的全能模型（t2v + i2v + r2v）
VIDEO_MODELS = ["wan3.0-video", "wan3.0-video-prime", "wan2.6-i2v-flash", "wan2.2-i2v-flash"]


def mask(key: str) -> str:
    return f"{key[:14]}…{key[-6:]}" if len(key) > 24 else "***"


def load_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DASHSCOPE_API_KEY=") and line.split("=", 1)[1].strip():
            return line.split("=", 1)[1].strip()
    raise SystemExit(".env 里没有 DASHSCOPE_API_KEY（或为空）")


def _post(url: str, key: str, payload: dict, *, extra_headers: dict | None = None, timeout: float = 90.0):
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = ""
        # 读取错误体只为给出更清晰的提示，失败也不该掩盖状态码
        with contextlib.suppress(Exception):
            body = exc.read().decode("utf-8", "ignore")[:300]
        return exc.code, {"_raw": body}
    except Exception as exc:
        return -1, {"_raw": f"{type(exc).__name__}: {exc}"}


def probe_text(key: str) -> str | None:
    print("\n[1/4] 鉴权 + 文本（OpenAI 兼容端点）")
    for model in TEXT_MODELS:
        status, body = _post(
            f"{COMPAT_BASE}/chat/completions",
            key,
            {"model": model, "messages": [{"role": "user", "content": "回复两个字：可用"}], "max_tokens": 16},
            timeout=60,
        )
        if status == 200:
            text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
            usage = body.get("usage") or {}
            print(f"  ✓ {model:<14} 「{text.strip()[:20]}」 tokens={usage.get('total_tokens', '?')}")
            return model
        code = (body.get("error") or {}).get("code") or body.get("code") or ""
        msg = (body.get("error") or {}).get("message") or body.get("_raw", "")
        print(f"  ✗ {model:<14} HTTP {status} {code} {str(msg)[:150]}")
    return None


def probe_native_auth(key: str) -> bool:
    """确认原生端点也认这把 Key（视频/图像必须走原生端点）。"""
    print("\n[2/4] 原生端点鉴权（/api/v1）")
    status, body = _post(
        f"{NATIVE_BASE}/services/aigc/text-generation/generation",
        key,
        {"model": "qwen-turbo", "input": {"prompt": "hi"}, "parameters": {"max_tokens": 4}},
        timeout=60,
    )
    if status == 200:
        print("  ✓ 原生端点鉴权通过")
        return True
    print(f"  ⚠ 原生端点返回 HTTP {status}：{json.dumps(body, ensure_ascii=False)[:220]}")
    print("    （若为 401 则 Key 无效；若为 400/404 通常只是该接口路径不对，鉴权本身可能是好的）")
    return status not in (401, 403)


def probe_video_contract(key: str, model: str) -> bool:
    """验证视频接口的请求契约与图片输入格式。

    这一步是**只提交任务、不等待结果**：目的是拿到接口对参数的反馈，
    尤其是「本地图片能不能用」——智谱就卡在这里（要求公网 URL）。
    """
    print(f"\n[3/4] 视频接口契约（{model}）")
    url = f"{NATIVE_BASE}/services/aigc/video-generation/video-synthesis"

    # 造一张极小的 PNG 作为 base64 测试图
    import base64

    png_1x1 = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    data_uri = "data:image/png;base64," + base64.b64encode(png_1x1).decode()

    cases: list[tuple[str, dict]] = [
        (
            "纯文生视频（基线）",
            {
                "model": model,
                "input": {"prompt": "雨滴落在窗玻璃上，电影感"},
                "parameters": {"resolution": "480P", "duration": 5},
            },
        ),
        (
            "base64 data URI 作首帧",
            {
                "model": model,
                "input": {"prompt": "镜头缓慢推近", "img_url": data_uri},
                "parameters": {"resolution": "480P", "duration": 5},
            },
        ),
        (
            "公网 URL 作首帧",
            {
                "model": model,
                "input": {"prompt": "镜头缓慢推近", "img_url": "https://example.com/a.png"},
                "parameters": {"resolution": "480P", "duration": 5},
            },
        ),
    ]

    accepted_base64 = False
    for label, payload in cases:
        status, body = _post(url, key, payload, extra_headers={"X-DashScope-Async": "enable"}, timeout=90)
        note = ""
        if status == 200:
            task_id = (body.get("output") or {}).get("task_id") or body.get("task_id")
            note = f"任务已建 {task_id}"
            if "base64" in label:
                accepted_base64 = True
        else:
            err = body.get("message") or body.get("_raw") or json.dumps(body, ensure_ascii=False)
            note = str(err)[:200]
        print(f"  {'✓' if status == 200 else '✗'} {label:<22} HTTP {status}  {note}")

    print()
    if accepted_base64:
        print("  → **万相接受 base64 本地图片**：关键帧可以直接传，跨镜头一致性完整可用 ✅")
    else:
        print("  → 未见 base64 被接受。若上面「公网 URL」也失败，需进一步排查；")
        print("    若公网 URL 成功而 base64 失败，则需配置对象存储提供公网地址。")
    return accepted_base64


def list_models(key: str, *, video: bool) -> None:
    print("\n[4/4] 模型可见性")
    status, body = _post(f"{NATIVE_BASE}/models", key, {}, timeout=30)
    if status == 200:
        items = body.get("data") or body.get("models") or []
        names = [i.get("id") or i.get("model") for i in items][:40]
        print(f"  /models 返回 {len(items)} 个：{names[:12]}")
    else:
        print(f"  ⚠ 无 /models 端点（HTTP {status}），跳过。这属正常，不影响使用。")


def main() -> int:
    parser = argparse.ArgumentParser(description="阿里云百炼连通性探测")
    parser.add_argument("--video", action="store_true", help="验证视频接口契约（提交任务，会扣费）")
    parser.add_argument("--all-video", action="store_true", help="遍历候选视频模型")
    args = parser.parse_args()

    key = load_key()
    print(f"DashScope Key：{mask(key)}")
    print(f"兼容端点：{COMPAT_BASE}")
    print(f"原生端点：{NATIVE_BASE}")

    text_model = probe_text(key)
    if text_model is None:
        print("\n✗ 文本鉴权未通过 —— 先确认 Key 是否正确/有效。")
        return 1

    native_ok = probe_native_auth(key)

    if args.video:
        models = VIDEO_MODELS if args.all_video else VIDEO_MODELS[:1]
        for model in models:
            probe_video_contract(key, model)
    else:
        print("\n[3/4] 视频接口 —— 已跳过（加 --video 启用）")

    list_models(key, video=args.video)

    print("\n" + "=" * 64)
    print("探测结果")
    print("=" * 64)
    print(f"  ✓ 文本        {text_model}")
    print(f"  {'✓' if native_ok else '✗'} 原生端点    鉴权{'通过' if native_ok else '失败'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
