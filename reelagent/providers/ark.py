"""火山方舟（Volcengine Ark）供应商实现。

覆盖 Seedream（图生图/文生图）与 Seedance（图生视频/文生视频）。
只用标准库 urllib，不依赖官方 SDK。

注意：live 路径需要真实 API Key，本仓库默认在 mock 模式下运行，
因此这部分代码未经端到端联网验证——接口按公开文档实现，接 Key 后可直接使用。
"""

from __future__ import annotations

import base64
import contextlib
import json
import mimetypes
import time
import urllib.error
import urllib.request
from pathlib import Path

from .base import (
    ImageProvider,
    MediaResult,
    ProviderError,
    TTSProvider,
    VideoProvider,
    estimate_image_cost,
    estimate_tts_cost,
    estimate_video_cost,
)


# --------------------------------------------------------------------------- #
# HTTP 工具
# --------------------------------------------------------------------------- #
def _post_json(url: str, payload: dict, api_key: str, *, timeout: float = 180.0) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    return _read_json(req, timeout)


def _get_json(url: str, api_key: str, *, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    return _read_json(req, timeout)


def _read_json(req: urllib.request.Request, timeout: float) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):
            detail = exc.read().decode("utf-8", "ignore")[:500]
        raise ProviderError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except Exception as exc:
        raise ProviderError(f"{type(exc).__name__}: {exc}") from exc


def _download(url: str, out_path: Path, *, timeout: float = 300.0) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "reelagent/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out_path.write_bytes(resp.read())
    return out_path


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


# --------------------------------------------------------------------------- #
# 图像：Seedream
# --------------------------------------------------------------------------- #
class ArkImageProvider(ImageProvider):
    name = "ark"

    #: 方舟接受 base64 内联参考图，因此关键帧可参考线索基准图，一致性成立
    accepts_local_ref_images = True
    model = "doubao-seedream-4-0-250828"

    def __init__(self, *, api_key: str, base_url: str, model: str = "") -> None:
        if not api_key:
            raise ProviderError("Ark 图像供应商缺少 API Key")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model or self.model

    def generate(
        self,
        *,
        prompt: str,
        out_path: Path,
        size: tuple[int, int],
        ref_images: list[Path] | None = None,
        seed: int = 0,
        #: 是否使用优速版模型。当前仅 DashScope 支持（express 档），
        #: 其他供应商忽略此参数 —— 但**必须显式接受**，否则调用方传了
        #: 就会 TypeError，片段生成失败、镜头判为未完成，导致 generate
        #: 阶段在每次 resume 时重跑。抽象基类与所有实现签名必须一致。
        use_fast: bool = False,
    ) -> MediaResult:
        started = time.perf_counter()
        payload: dict[str, object] = {
            "model": self.model,
            "prompt": prompt,
            "size": f"{size[0]}x{size[1]}",
            "response_format": "url",
            "watermark": False,
        }
        if seed:
            payload["seed"] = seed
        # 参考图输入：跨镜头一致性的关键
        if ref_images:
            refs = [r for r in ref_images if r.exists()]
            if refs:
                payload["image"] = _data_uri(refs[0]) if len(refs) == 1 else [_data_uri(r) for r in refs]

        body = _post_json(f"{self.base_url}/images/generations", payload, self.api_key)
        items = body.get("data") or []
        if not items:
            raise ProviderError(f"Ark 图像返回为空：{str(body)[:300]}")

        item = items[0]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if item.get("b64_json"):
            out_path.write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            _download(item["url"], out_path)
        else:
            raise ProviderError(f"Ark 图像返回无法识别：{str(item)[:300]}")

        return MediaResult(
            path=out_path,
            provider=self.name,
            model=self.model,
            cost_usd=estimate_image_cost(1),
            latency_ms=(time.perf_counter() - started) * 1000,
            meta={"size": size, "refs": len(ref_images or [])},
        )


# --------------------------------------------------------------------------- #
# 视频：Seedance（异步任务 + 轮询）
# --------------------------------------------------------------------------- #
class ArkVideoProvider(VideoProvider):
    #: 真实视频模型返回 MP4 容器
    output_suffix = "mp4"

    name = "ark"

    #: 方舟支持 base64 内联参考图，因此图生视频可用，跨镜头一致性成立
    accepts_local_keyframe = True
    model = "doubao-seedance-1-0-pro-250528"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str = "",
        poll_interval: float = 5.0,
        poll_timeout: float = 900.0,
    ) -> None:
        if not api_key:
            raise ProviderError("Ark 视频供应商缺少 API Key")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model or self.model
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    def generate(
        self,
        *,
        prompt: str,
        out_path: Path,
        duration_s: float,
        size: tuple[int, int],
        keyframe: Path | None = None,
        seed: int = 0,
        #: 优速版开关：仅 DashScope 支持，其余供应商忽略但必须接受，
        #: 否则调用方传参会 TypeError，导致片段生成失败。
        use_fast: bool = False,
    ) -> MediaResult:
        started = time.perf_counter()
        content: list[dict] = [{"type": "text", "text": prompt}]
        # 用 is_file() 而不是 exists()：`Path()` 求值为 `.`（当前目录），
        # exists() 对它返回 True，会把「没有关键帧」误判成「传了本地关键帧」。
        if keyframe is not None and keyframe.is_file():
            content.append({"type": "image_url", "image_url": {"url": _data_uri(keyframe)}})

        payload: dict[str, object] = {
            "model": self.model,
            "content": content,
            "ratio": _ratio_of(size),
            "duration": int(max(2, min(12, round(duration_s)))),
            "watermark": False,
        }
        if seed:
            payload["seed"] = seed

        created = _post_json(f"{self.base_url}/contents/generations/tasks", payload, self.api_key)
        task_id = created.get("id") or created.get("task_id")
        if not task_id:
            raise ProviderError(f"Ark 视频任务创建失败：{str(created)[:300]}")

        deadline = time.time() + self.poll_timeout
        video_url = ""
        while time.time() < deadline:
            time.sleep(self.poll_interval)
            state = _get_json(f"{self.base_url}/contents/generations/tasks/{task_id}", self.api_key)
            status = (state.get("status") or "").lower()
            if status in {"succeeded", "success", "completed"}:
                content_obj = state.get("content") or {}
                video_url = content_obj.get("video_url") or state.get("video_url") or ""
                break
            if status in {"failed", "error", "cancelled"}:
                raise ProviderError(f"Ark 视频任务失败：{str(state)[:300]}")

        if not video_url:
            raise ProviderError(f"Ark 视频任务超时（{self.poll_timeout}s，task={task_id}）")

        _download(video_url, out_path)
        return MediaResult(
            path=out_path,
            provider=self.name,
            model=self.model,
            cost_usd=estimate_video_cost(duration_s),
            latency_ms=(time.perf_counter() - started) * 1000,
            meta={"task_id": task_id, "duration_s": duration_s, "size": size},
        )


def _ratio_of(size: tuple[int, int]) -> str:
    w, h = size
    from math import gcd

    g = gcd(w, h) or 1
    return f"{w // g}:{h // g}"


# --------------------------------------------------------------------------- #
# 通用 OpenAI 兼容的图像 / TTS
# --------------------------------------------------------------------------- #
class OpenAICompatImageProvider(ImageProvider):
    name = "openai"
    model = "gpt-image-1"

    def __init__(self, *, api_key: str, base_url: str, model: str = "") -> None:
        if not api_key:
            raise ProviderError("OpenAI 兼容图像供应商缺少 API Key")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model or self.model

    def generate(
        self,
        *,
        prompt: str,
        out_path: Path,
        size: tuple[int, int],
        ref_images: list[Path] | None = None,
        seed: int = 0,
    ) -> MediaResult:
        started = time.perf_counter()
        payload = {
            "model": self.model,
            "prompt": prompt,
            "size": f"{size[0]}x{size[1]}",
            "n": 1,
            "response_format": "b64_json",
        }
        body = _post_json(f"{self.base_url}/images/generations", payload, self.api_key)
        items = body.get("data") or []
        if not items or not items[0].get("b64_json"):
            raise ProviderError(f"图像返回异常：{str(body)[:300]}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(items[0]["b64_json"]))
        return MediaResult(
            path=out_path,
            provider=self.name,
            model=self.model,
            cost_usd=estimate_image_cost(1),
            latency_ms=(time.perf_counter() - started) * 1000,
        )


class OpenAICompatTTSProvider(TTSProvider):
    name = "openai"
    model = "tts-1"

    def __init__(self, *, api_key: str, base_url: str, model: str = "", voice: str = "alloy") -> None:
        if not api_key:
            raise ProviderError("TTS 供应商缺少 API Key")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model or self.model
        self.voice = voice

    def generate(self, *, text: str, out_path: Path, voice: str = "") -> MediaResult:
        started = time.perf_counter()
        payload = {"model": self.model, "input": text, "voice": voice or self.voice, "response_format": "wav"}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/audio/speech",
            data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                out_path.write_bytes(resp.read())
        except urllib.error.HTTPError as exc:
            raise ProviderError(f"TTS HTTP {exc.code}: {exc.reason}") from exc
        return MediaResult(
            path=out_path,
            provider=self.name,
            model=self.model,
            cost_usd=estimate_tts_cost(text),
            latency_ms=(time.perf_counter() - started) * 1000,
        )
