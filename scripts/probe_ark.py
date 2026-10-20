"""火山方舟连通性分级探测。

为什么分三级：视频生成是唯一真正烧钱的调用，不能在「Key 有没有权限」还没确认时
就去调它。顺序是 **鉴权 -> 文本 -> 图像 -> 视频**，每级通过才进下一级。

用法：
    python scripts/probe_ark.py            # 只跑前三级（不花钱或极少）
    python scripts/probe_ark.py --video    # 额外跑一次视频（约 0.1-0.5 元）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

#: 待探测的候选模型名。
#: 顺序有讲究：**优先试当前在售的型号**。方舟的 [Retiring] 旧型号对新账号
#: 往往返回 404（"does not exist or you do not have access to it"），
#: 容易被误判成「Key 没权限」或「模型名写错」，白白浪费排查时间。
LLM_CANDIDATES = [
    "doubao-seed-2-1-pro-260628",
    "doubao-seed-2-1-lite-260915",
    "doubao-seed-2-0-lite-260428",
    "doubao-seed-1-6-250615",
]
IMAGE_CANDIDATES = [
    "doubao-seedream-5-0-pro-260628",
    "doubao-seedream-5-0-flash-260915",
    "doubao-seedream-4-0-20260415",
    "doubao-seedream-4-0-250828",
]
VIDEO_CANDIDATES = [
    "doubao-seedance-2-5-260628",
    "doubao-seedance-2-0-260128",
    "doubao-seedance-2-0-fast-260128",
    "doubao-seedance-1-0-pro-fast-251015",
]


def mask(key: str) -> str:
    return f"{key[:14]}...{key[-4:]}" if len(key) > 20 else "***"


def load_key() -> str:
    env = ROOT / ".env"
    if not env.exists():
        raise SystemExit("找不到 .env")
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("REEL_PROVIDERS__LLM__API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(".env 里没有 REEL_PROVIDERS__LLM__API_KEY")


def probe_llm(client: httpx.Client, key: str) -> str | None:
    """第一级：文本模型。既验证鉴权是否有效，也顺便确认 Key 可用。"""
    print("\n[1/4] 文本模型（验证鉴权 + 权限）")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for model in LLM_CANDIDATES:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "回复两个字：可用"}],
            "max_tokens": 16,
        }
        started = time.perf_counter()
        try:
            resp = client.post(f"{BASE_URL}/chat/completions", headers=headers, json=payload, timeout=60)
        except Exception as exc:
            print(f"  ✗ {model:<32} 网络错误 {type(exc).__name__}")
            continue
        elapsed = (time.perf_counter() - started) * 1000

        if resp.status_code == 200:
            body = resp.json()
            text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
            usage = body.get("usage") or {}
            print(
                f"  ✓ {model:<32} {elapsed:.0f}ms  「{text.strip()[:20]}」 "
                f"tokens={usage.get('prompt_tokens', 0)}+{usage.get('completion_tokens', 0)}"
            )
            return model
        _explain(model, resp)

    print("  → 没有可用的文本模型。需要到方舟「模型广场」逐个点击『开通』。")
    return None


def probe_image(client: httpx.Client, key: str) -> str | None:
    """第二级：图像生成。约 0.03 美元/张，用来验证 Seedream 权限与我们的适配器。"""
    print("\n[2/4] 图像模型（Seedream，约 0.03 元/张）")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    out = ROOT / "workspace" / "probe"
    out.mkdir(parents=True, exist_ok=True)

    for model in IMAGE_CANDIDATES:
        payload = {
            "model": model,
            "prompt": "一个深蓝色调的雨夜街角，电影感，简洁构图",
            "size": "1024x1024",
            "response_format": "url",
            "watermark": False,
        }
        started = time.perf_counter()
        try:
            resp = client.post(f"{BASE_URL}/images/generations", headers=headers, json=payload, timeout=120)
        except Exception as exc:
            print(f"  ✗ {model:<32} 网络错误 {type(exc).__name__}")
            continue
        elapsed = (time.perf_counter() - started) * 1000

        if resp.status_code == 200:
            items = resp.json().get("data") or []
            if items and items[0].get("url"):
                target = out / f"probe_{model[:24]}.png"
                img = client.get(items[0]["url"], timeout=120)
                target.write_bytes(img.content)
                print(
                    f"  ✓ {model:<32} {elapsed:.0f}ms  {len(img.content) // 1024} KB -> {target.relative_to(ROOT)}"
                )
                return model
        _explain(model, resp)

    print("  → 没有可用的图像模型。需要到方舟「模型广场」开通 Seedream 系列。")
    return None


def probe_video(client: httpx.Client, key: str) -> str | None:
    """第三级：视频生成。真正花钱的一步，只在显式 --video 时执行。"""
    print("\n[3/4] 视频模型（Seedance，会真实扣费）")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    for model in VIDEO_CANDIDATES:
        payload = {
            "model": model,
            "content": [{"type": "text", "text": "镜头缓慢推近，雨滴落在窗玻璃上，电影感"}],
            "ratio": "16:9",
            "duration": 5,
            "watermark": False,
        }
        try:
            resp = client.post(
                f"{BASE_URL}/contents/generations/tasks", headers=headers, json=payload, timeout=120
            )
        except Exception as exc:
            print(f"  ✗ {model:<32} 网络错误 {type(exc).__name__}")
            continue

        if resp.status_code != 200:
            _explain(model, resp)
            continue

        task_id = resp.json().get("id")
        print(f"  ✓ {model:<32} 任务已创建 {task_id}")
        print("     轮询中（最长 5 分钟）…")

        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(5)
            state = client.get(
                f"{BASE_URL}/contents/generations/tasks/{task_id}", headers=headers, timeout=60
            ).json()
            status = str(state.get("status") or "").lower()
            if status in {"succeeded", "success", "completed"}:
                url = (state.get("content") or {}).get("video_url") or state.get("video_url")
                target = ROOT / "workspace" / "probe" / f"probe_{model[:24]}.mp4"
                target.write_bytes(client.get(url, timeout=300).content)
                print(f"  ✓ 生成完成 -> {target.relative_to(ROOT)} （{target.stat().st_size // 1024} KB）")
                return model
            if status in {"failed", "error", "cancelled"}:
                print(f"  ✗ 任务失败：{json.dumps(state, ensure_ascii=False)[:200]}")
                break
        else:
            print("  ✗ 轮询超时")

    print("  → 没有可用的视频模型。需要到方舟「模型广场」开通 Seedance 系列，")
    print("     并注意开通时选择正确的令牌分组（视频模型通常在 doubao-vip 分组下）。")
    return None


def _explain(model: str, resp: httpx.Response) -> None:
    """把方舟的错误码翻译成可执行的建议。

    方舟有**两类容易被混为一谈的 404**，处理方式完全不同：
      - `ModelNotOpen`                  → 模型存在但没开通，去控制台点「开通」
      - `InvalidEndpointOrModel.NotFound` → 模型名写错，或该型号已下线
    只看 HTTP 状态码会把这两种搞混，浪费大量排查时间，所以这里解析业务错误码。
    """
    code = ""
    message = ""
    try:
        body = resp.json()
        err = body.get("error") or {}
        code = str(err.get("code") or "")
        message = str(err.get("message") or "")
    except Exception:  # pragma: no cover
        message = resp.text[:200]

    if code == "ModelNotOpen":
        account = ""
        if "account " in message:
            account = message.split("account ", 1)[1].split(" ", 1)[0]
        print(f"  ✗ {model:<32} 未开通（账号 {account}）")
        print("      → 去方舟控制台「模型广场」搜索该模型并点击『开通』")
        return

    if code == "InvalidEndpointOrModel.NotFound":
        print(f"  ✗ {model:<32} 型号不存在或已下线（名称写错，或是 Retiring 旧型号）")
        return

    if code == "AuthenticationError" or resp.status_code == 401:
        print(f"  ✗ {model:<32} Key 无效")
        print("      → 回到方舟控制台重新复制 Key")
        return

    hint = {
        403: "无权限：模型未开通，或开通时选错了令牌分组",
        429: "触发限流，稍后重试",
        402: "余额/额度不足",
    }.get(resp.status_code, "")

    print(f"  ✗ {model:<32} HTTP {resp.status_code} {hint or code}")
    if message:
        print(f"      {message[:180]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="火山方舟连通性分级探测")
    parser.add_argument("--video", action="store_true", help="额外执行视频生成（会真实扣费）")
    parser.add_argument("--skip-image", action="store_true", help="跳过图像生成")
    args = parser.parse_args()

    key = load_key()
    print(f"方舟 Key：{mask(key)}")
    print(f"Base URL：{BASE_URL}")

    results: dict[str, str | None] = {}
    with httpx.Client() as client:
        results["llm"] = probe_llm(client, key)
        if not args.skip_image:
            results["image"] = probe_image(client, key)
        if args.video:
            results["video"] = probe_video(client, key)

    print("\n" + "=" * 62)
    print("探测结果")
    print("=" * 62)
    labels = {"llm": "文本", "image": "图像", "video": "视频"}
    for kind, model in results.items():
        mark = "✓" if model else "✗"
        print(f"  {mark} {labels[kind]:<4} {model or '不可用'}")
    print()
    print("把上面探测成功的模型名填进 .env 对应字段即可。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
