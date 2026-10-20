"""智谱（BigModel）连通性分级探测。

设计原则与 probe_ark.py 一致：**先验证鉴权，再逐级花钱**。
但这里多做一件关键的事 —— 不只测原始 HTTP，而是**直接驱动项目自己的适配器**
（ZhipuImageProvider / ZhipuVideoProvider）。原因是：

    只测原始 HTTP 只能证明「接口通」，不能证明「我的代码对」。
    真正要交付的是适配器，所以让适配器本身接受检验，
    一旦失败就能定位到是适配器的哪一步错了。

用法：
    python scripts/probe_zhipu.py              # 鉴权 + 文本 + 图像
    python scripts/probe_zhipu.py --video      # 额外验证视频（CogVideoX-Flash 免费）
    python scripts/probe_zhipu.py --models     # 只列出可用模型
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = "https://open.bigmodel.cn/api/paas/v4"

#: 候选模型名。智谱不同代次的命名风格有差异（注意是**点号**：glm-5.3-flash），
#: 逐个试比猜更快。
LLM_CANDIDATES = ["glm-5.3-flash", "glm-5.3", "glm-4.5-air", "glm-4-flash"]
IMAGE_CANDIDATES = ["glm-image", "cogview-4", "cogview-3-flash"]

#: 免费视频模型。探测默认只碰这些。
FREE_VIDEO_CANDIDATES = ["cogvideox-flash"]

#: 付费视频模型。**必须显式加 --paid 才会尝试。**
#: 早期版本把免费与付费混在一个列表里逐个试，结果免费模型 429 时
#: 会自动降级到付费模型并成功扣费 —— 用户的钱在无感知的情况下被花掉了。
#: 这种「静默升级到付费」的行为绝不该出现在任何工具里。
PAID_VIDEO_CANDIDATES = ["cogvideox-3"]


def mask(key: str) -> str:
    return f"{key[:12]}...{key[-6:]}" if len(key) > 22 else "***"


def load_key() -> str:
    env = ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("REEL_PROVIDERS__LLM__API_KEY="):
            value = line.split("=", 1)[1].strip()
            if value:
                return value
    raise SystemExit(".env 里没有 REEL_PROVIDERS__LLM__API_KEY（或为空）")


def explain(model: str, resp: httpx.Response) -> None:
    """把智谱的错误码翻译成可执行建议。"""
    code = message = ""
    try:
        body = resp.json()
        err = body.get("error") or body
        code = str(err.get("code") or "")
        message = str(err.get("message") or "")
    except Exception:  # pragma: no cover
        message = resp.text[:200]

    if resp.status_code == 401 or "auth" in code.lower():
        print(f"  ✗ {model:<22} Key 无效或已过期")
        return
    if "balance" in message.lower() or "欠费" in message or resp.status_code == 402:
        print(f"  ✗ {model:<22} 余额不足（免费模型不应出现此项）")
        return
    if resp.status_code == 404 or "model" in code.lower():
        print(f"  ✗ {model:<22} 模型名不存在或当前账号不可用")
        return
    print(f"  ✗ {model:<22} HTTP {resp.status_code} {code}")
    if message:
        print(f"      {message[:170]}")


# --------------------------------------------------------------------------- #
def probe_llm(client: httpx.Client, key: str) -> str | None:
    print("\n[1/4] 鉴权 + 文本模型")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for model in LLM_CANDIDATES:
        started = time.perf_counter()
        try:
            resp = client.post(
                f"{BASE}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "回复两个字：可用"}],
                    "max_tokens": 16,
                },
                timeout=60,
            )
        except Exception as exc:
            print(f"  ✗ {model:<22} 网络错误 {type(exc).__name__}")
            continue

        if resp.status_code == 200:
            body = resp.json()
            text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
            usage = body.get("usage") or {}
            print(
                f"  ✓ {model:<22} {(time.perf_counter() - started) * 1000:.0f}ms  "
                f"「{text.strip()[:20]}」 tokens={usage.get('total_tokens', '?')}"
            )
            return model
        explain(model, resp)

    print("  → Key 可能无效，或账号下没有可用文本模型。")
    return None


def probe_image_adapter(key: str, model: str) -> bool:
    """用项目自己的适配器生成一张图——验证的是代码，不是接口文档。"""
    print(f"\n[2/4] 图像 —— 通过 ZhipuImageProvider（model={model}）")
    from reelagent.providers.zhipu import ZhipuImageProvider

    out = ROOT / "workspace" / "probe" / "zhipu_probe.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        provider = ZhipuImageProvider(api_key=key, base_url=BASE, model=model, poll_timeout=180)
    except Exception as exc:
        print(f"  ✗ 构造失败：{exc}")
        return False

    started = time.perf_counter()
    try:
        result = provider.generate(
            prompt="一个深蓝色调的雨夜街角，电影感，简洁构图",
            out_path=out,
            size=(1024, 1024),
        )
    except Exception as exc:
        print(f"  ✗ 调用失败：{type(exc).__name__}: {exc}")
        return False

    elapsed = (time.perf_counter() - started) * 1000
    if out.exists() and out.stat().st_size > 1024:
        print(f"  ✓ 成功 {elapsed:.0f}ms  {out.stat().st_size // 1024} KB -> {out.relative_to(ROOT)}")
        print(f"     供应商自报成本：${result.cost_usd:.4f}")
        return True

    print("  ✗ 适配器返回成功但文件不存在或过小")
    return False


def probe_video_adapter(key: str, model: str, limit: float) -> bool:
    """用项目自己的视频适配器生成一段——CogVideoX-Flash 官方免费。"""
    print(f"\n[3/4] 视频 —— 通过 ZhipuVideoProvider（model={model}）")
    from reelagent.providers.zhipu import ZhipuVideoProvider

    out = ROOT / "workspace" / "probe" / "zhipu_probe.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        provider = ZhipuVideoProvider(api_key=key, base_url=BASE, model=model, poll_timeout=limit)
    except Exception as exc:
        print(f"  ✗ 构造失败：{exc}")
        return False

    started = time.perf_counter()
    try:
        result = provider.generate(
            prompt="镜头缓慢推近，雨滴落在窗玻璃上，霓虹倒影，电影感",
            # 传一个明确不存在的路径 = 纯文生视频。
            # 注意不能用 Path()：它求值为 `.`（当前目录），会命中「本地关键帧」分支。
            keyframe=Path("/nonexistent/no-keyframe.png"),
            out_path=out,
            duration_s=5,
            size=(1280, 720),
        )
    except Exception as exc:
        print(f"  ✗ 调用失败：{type(exc).__name__}: {exc}")
        return False

    elapsed = time.perf_counter() - started
    if out.exists() and out.stat().st_size > 10240:
        print(f"  ✓ 成功 {elapsed:.1f}s  {out.stat().st_size // 1024} KB -> {out.relative_to(ROOT)}")
        print(f"     供应商自报成本：${result.cost_usd:.4f}（免费模型应为 0）")
        return True

    print("  ✗ 适配器返回成功但文件不存在或过小")
    return False


def list_models(client: httpx.Client, key: str) -> None:
    print("\n[4/4] 账号可用模型")
    try:
        resp = client.get(f"{BASE}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    except Exception as exc:
        print(f"  ✗ {type(exc).__name__}: {exc}")
        return

    if resp.status_code != 200:
        print(f"  ⚠ /models 返回 {resp.status_code}（智谱可能不提供该端点）")
        return

    try:
        items = resp.json().get("data") or []
    except Exception:
        print("  ⚠ 返回体无法解析")
        return

    for item in items[:40]:
        mid = item.get("id") or item.get("model")
        print(f"  - {mid}")
    if not items:
        print("  （空列表）")


def main() -> int:
    parser = argparse.ArgumentParser(description="智谱连通性分级探测")
    parser.add_argument("--video", action="store_true", help="验证视频（默认只用免费模型）")
    parser.add_argument(
        "--paid",
        action="store_true",
        help="允许尝试付费视频模型（会产生真实费用，需与 --video 同用）",
    )
    parser.add_argument("--skip-image", action="store_true", help="跳过图像")
    parser.add_argument("--models", action="store_true", help="只列出模型")
    parser.add_argument("--timeout", type=float, default=600.0, help="视频轮询上限（秒）")
    args = parser.parse_args()

    key = load_key()
    print(f"智谱 Key：{mask(key)}")
    print(f"Base URL：{BASE}")

    with httpx.Client() as client:
        if args.models:
            list_models(client, key)
            return 0

        llm_model = probe_llm(client, key)
        if llm_model is None:
            print("\n鉴权/文本未通过，后续探测跳过（先解决 Key 问题）。")
            list_models(client, key)
            return 1

        image_ok = False
        if not args.skip_image:
            # 用文本探测成功的账号，逐个试图像候选
            for model in IMAGE_CANDIDATES:
                if probe_image_adapter(key, model):
                    image_ok = True
                    break

        video_ok = False
        if args.video:
            candidates = list(FREE_VIDEO_CANDIDATES)
            if args.paid:
                print("\n⚠ --paid 已启用：将尝试付费视频模型，会产生真实费用。")
                candidates += PAID_VIDEO_CANDIDATES
            else:
                print("\n（仅测试免费视频模型；如需尝试付费模型请显式加 --paid）")
            for model in candidates:
                if probe_video_adapter(key, model, args.timeout):
                    video_ok = True
                    break

        list_models(client, key)

    print("\n" + "=" * 64)
    print("探测结果")
    print("=" * 64)
    print(f"  {'✓' if llm_model else '✗'} 文本   {llm_model or '不可用'}")
    if not args.skip_image:
        print(f"  {'✓' if image_ok else '✗'} 图像   见上方成功的模型名")
    if args.video:
        print(f"  {'✓' if video_ok else '✗'} 视频   见上方成功的模型名")
    print("\n把探测成功的模型名填进 .env 的 MODEL 字段。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
