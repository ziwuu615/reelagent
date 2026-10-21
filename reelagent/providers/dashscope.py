"""阿里云百炼（DashScope）供应商实现。

选它的理由（相对火山方舟与智谱）：
  - **真正按量付费**，没有火山方舟那种「模型开通与余额门槛绑定」的问题
  - **接受 base64 内联图片**，因此关键帧可直接作为图生视频的首帧，
    跨镜头一致性完整可用，不需要额外配置对象存储与公网地址
    （智谱要求公网 URL，这一条把一致性能力砍掉了一半）
  - 一把 Key 覆盖文本 / 图像 / 视频 / TTS

协议要点（与方舟、智谱都不同）：
  - 鉴权：`Authorization: Bearer sk-xxx`
  - 视频生成必须带 `X-DashScope-Async: enable`，返回 `output.task_id`
  - 轮询：GET `/api/v1/tasks/{task_id}`，成功时 `output.video_url`
  - 分辨率用档位字符串（480P/720P/1080P），不是宽高像素

实测确认（2026-10-16）：
  `/services/aigc/video-generation/video-synthesis` 对
  `img_url` 为 base64 data URI 的请求返回 200 并给出 task_id，
  即 base64 内联图片被接受。
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

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"

#: 首选的视频模型：官方推荐的 Wan 系列全能模型，
#: 同时覆盖文生视频、首帧生视频、全能参考（角色一致性）
DEFAULT_VIDEO_MODEL = "wan3.0-video"

#: 首选图像模型。实测该账号可用（返回 200 并给出 task_id）。
DEFAULT_IMAGE_MODEL = "wan2.5-t2i-preview"

#: 语音合成模型与音色。
#: 百炼**没有**实现 OpenAI 的 `/audio/speech` 协议（该路径返回 404），
#: 必须走原生多模态生成端点。实测：
#:   POST /services/aigc/multimodal-generation/generation
#:   {"model": "qwen3-tts-flash", "input": {"text": "...", "voice": "Cherry"}}
#:   -> {"output": {"audio": {"url": "..."}}}
#: 而且是**同步**返回下载地址，不需要轮询（与图像/视频不同）。
DEFAULT_TTS_MODEL = "qwen3-tts-flash"
DEFAULT_TTS_VOICE = "Cherry"

#: 万相文生图的**像素总量**约束（实测报错原文）：
#:   Size total pixels must be between 589824 (768*768) and 2073600 (1440*1440)
#:
#: 注意它与视频接口的约束方式完全不同：视频按 480P/720P/1080P 分档，
#: 图像则卡「总像素」区间。因此引擎的默认尺寸 540x960（=518400）
#: 会被直接拒绝 —— 这是按 9:16 比例能推出的最小值 576x1024（=589824）。
#:
#: 每个供应商都有一套自己的尺寸规矩，靠猜必然挨个踩坑；
#: 正确做法是在**发请求前**按该供应商的规则归一化（就是下面这个函数）。
MIN_IMAGE_PIXELS = 768 * 768  # 589_824
MAX_IMAGE_PIXELS = 1440 * 1440  # 2_073_600


def normalize_image_size(size: tuple[int, int]) -> tuple[int, int]:
    """把尺寸调整到万相允许的像素区间，同时尽量保持宽高比。

    做法：按目标像素总量等比缩放，然后做少量整数修正以保证严格落在区间内。
    整数取整会让乘积略微偏离目标，所以修正不能省 —— 差一个像素就够被拒。
    """
    width, height = max(1, int(size[0])), max(1, int(size[1]))
    pixels = width * height
    if MIN_IMAGE_PIXELS <= pixels <= MAX_IMAGE_PIXELS:
        return width, height

    target = MIN_IMAGE_PIXELS if pixels < MIN_IMAGE_PIXELS else MAX_IMAGE_PIXELS
    scale = (target / pixels) ** 0.5
    width = max(1, int(width * scale))
    height = max(1, int(height * scale))

    # 等比缩放后的整数取整可能让总量掉出区间，向安全侧逐像素修正。
    # 每步改变 (w+h+1) 个像素，因此最多几十次迭代即可收敛。
    guard = 0
    while width * height < MIN_IMAGE_PIXELS and guard < 10_000:
        width += 1
        height += 1
        guard += 1
    while width * height > MAX_IMAGE_PIXELS and guard < 20_000:
        width = max(1, width - 1)
        height = max(1, height - 1)
        guard += 1
    return width, height


#: 分辨率档位。DashScope 不接受任意宽高，只接受这三档。
#:
#: 判定用**短边**而不是高度。竖屏 9:16 的三个档位分别是
#: 480x854 / 720x1280 / 1080x1920 —— 决定档位的是宽度。
#: 若按高度判断，480P 竖屏（高 854）会被误判成 720P，
#: 在用户毫无察觉的情况下把成本抬高一档。
RESOLUTION_TIERS: tuple[tuple[int, str], ...] = (
    (1000, "1080P"),
    (600, "720P"),
    (0, "480P"),
)


def resolution_tier(size: tuple[int, int]) -> str:
    """把目标像素尺寸映射到 DashScope 的分辨率档位。

    取短边比对：横屏 1280x720 与竖屏 720x1280 都归入 720P，
    与「480P/720P/1080P 指短边」的行业惯例一致。
    """
    shorter = min(int(size[0]), int(size[1]))
    for threshold, tier in RESOLUTION_TIERS:
        if shorter >= threshold:
            return tier
    return "480P"  # pragma: no cover - 最后一项阈值为 0，不会走到


def _headers(api_key: str, *, async_mode: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if async_mode:
        # 视频/图像生成是长耗时任务，必须显式声明异步，否则网关会超时
        headers["X-DashScope-Async"] = "enable"
    return headers


def _request_json(
    url: str,
    *,
    api_key: str,
    payload: dict | None = None,
    method: str = "POST",
    async_mode: bool = False,
    timeout: float = 120.0,
) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers=_headers(api_key, async_mode=async_mode), method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = ""
        # 读取错误体只为给出更清晰的提示，失败也不该掩盖原始状态码
        with contextlib.suppress(Exception):
            raw = exc.read().decode("utf-8", "ignore")[:400]

        code = ""
        message = raw
        # 尽力解析结构化错误；解析不了就用原始文本兜底
        with contextlib.suppress(Exception):
            parsed = json.loads(raw)
            code = str(parsed.get("code") or (parsed.get("error") or {}).get("code") or "")
            message = str(parsed.get("message") or (parsed.get("error") or {}).get("message") or raw)

        if exc.code == 429 or code == "Throttling.RateQuota":
            # 限流是瞬时状态。消息里带上 429 字样，让上层的重试策略
            # 能识别并采用更长的退避（见 harness/executor.is_rate_limited）。
            raise ProviderError(f"百炼 HTTP 429 限流（{code}）：{message}。属瞬时状态，稍后重试。") from exc
        raise ProviderError(f"百炼 HTTP {exc.code}（{code}）：{message}") from exc
    except Exception as exc:
        raise ProviderError(f"{type(exc).__name__}: {exc}") from exc


def _download(url: str, out_path: Path, *, timeout: float = 300.0) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "reelagent/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out_path.write_bytes(resp.read())
    return out_path


def _data_uri(path: Path) -> str:
    """把本地图片转成 data URI。

    实测万相接受这种形式（智谱不接受，要求公网 URL），
    因此关键帧可以直接内联传入，无需对象存储。
    """
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


class DashScopeTTSProvider(TTSProvider):
    """百炼语音合成（qwen3-tts-flash）。

    与图像/视频最大的不同：**同步返回**。提交后响应里直接带音频下载地址，
    没有 task_id 也不需要轮询 —— 因此不能用同一套异步骨架去套。
    """

    name = "dashscope"
    model = DEFAULT_TTS_MODEL

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = "",
        voice: str = "",
        timeout: float = 120.0,
    ) -> None:
        if not api_key:
            raise ProviderError("百炼语音合成供应商缺少 API Key")
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_TTS_MODEL
        self.voice = voice or DEFAULT_TTS_VOICE
        self.timeout = timeout

    def generate(self, *, text: str, out_path: Path, voice: str = "") -> MediaResult:
        if not text.strip():
            raise ProviderError("语音合成文本为空")

        started = time.perf_counter()
        body = _request_json(
            f"{self.base_url}/services/aigc/multimodal-generation/generation",
            api_key=self.api_key,
            payload={
                "model": self.model,
                "input": {"text": text, "voice": voice or self.voice},
            },
            timeout=self.timeout,
        )

        output = body.get("output") or {}
        audio = output.get("audio") if isinstance(output.get("audio"), dict) else {}
        url = (audio or {}).get("url")
        if not url:
            raise ProviderError(f"百炼语音合成未返回音频地址：{str(body)[:300]}")

        _download(str(url), out_path)
        return MediaResult(
            path=out_path,
            provider=self.name,
            model=self.model,
            # 必须按字符计费，不能写死 0.0。
            # 真实事故（proj_e2092083e7）：3 次 TTS 调用共 40 字符全部记为 $0，
            # 成本对账里 tts 一栏恒为 0 —— 单条短片看不出来，量大后就是
            # 账本与真实支出对不上的系统性缺口。ark.py 的 TTS 早就用了
            # estimate_tts_cost，这里与它对齐。
            cost_usd=estimate_tts_cost(text),
            latency_ms=(time.perf_counter() - started) * 1000,
            meta={"voice": voice or self.voice, "chars": len(text)},
        )


#: 尺寸 -> 宽高比字符串。文生视频时用它显式指定；
#: 图生视频时改用 `adaptive`（让模型跟随首帧实际比例，比从 size 反推更准）。
_RATIO_BY_SHAPE = {
    (9, 16): "9:16",
    (16, 9): "16:9",
    (1, 1): "1:1",
    (4, 3): "4:3",
    (3, 4): "3:4",
    (21, 9): "21:9",
}


def ratio_for(size: tuple[int, int]) -> str:
    """把宽高换算成供应商认识的比例串；认不出来就交给它自适应。"""
    width, height = int(size[0]), int(size[1])
    if height == 0:
        return "adaptive"
    ratio = width / height
    for (w, h), label in _RATIO_BY_SHAPE.items():
        if abs(ratio - w / h) < 0.02:
            return label
    return "adaptive"


class DashScopeImageProvider(ImageProvider):
    """万相文生图。

    协议要点（与视频接口同源但有差异）：
      - 端点 /services/aigc/text2image/image-synthesis，同样需要 X-DashScope-Async
      - 尺寸格式是 1024*1024（**星号**，不是 x）—— 写错会被 400 拒绝
      - 轮询结果在 output.results[].url，而视频在 output.video_url，
        两者字段名不同，不能共用解析逻辑

    分辨率：万相按 size 字符串接收任意宽高，但要求**总像素与边长在合理范围**。
    这里复用视频那套短边分档，转换成对应的方图/竖图尺寸。
    """

    name = "dashscope"
    model = DEFAULT_IMAGE_MODEL

    #: 万相文生图接口不接受本地参考图。
    #:
    #: 与智谱不同，这里不是「要求公网 URL」而是**接口本身没有参考图入参** ——
    #: 图生图要走另一个模型（i2i 系列）。因此流水线会退化为逐镜头独立生成，
    #: 跨镜头一致性会减弱，这一降级由流水线记录在追踪里。
    accepts_local_ref_images = False

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = "",
        poll_interval: float = 3.0,
        poll_timeout: float = 300.0,
    ) -> None:
        if not api_key:
            raise ProviderError("百炼图像供应商缺少 API Key")
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_IMAGE_MODEL
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

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

        if ref_images:
            # 不静默忽略：丢掉参考图会让「跨镜头一致性」看起来生效、实际没有
            raise ProviderError(
                "万相文生图接口不支持参考图；如需参考图引导，请使用 i2i 系列模型，"
                "或改用支持 base64 参考图的供应商（火山方舟）。"
            )

        # 发请求前先归一化：万相卡的是**像素总量**区间，
        # 引擎默认的 540x960（=518400）会被直接拒绝。
        # 按 9:16 比例能推出的最小值恰好是 576x1024（=589824），宽高比无损。
        actual = normalize_image_size(size)

        params: dict[str, object] = {
            # 注意是星号分隔，不是 'x'
            "size": f"{actual[0]}*{actual[1]}",
            "n": 1,
        }
        if seed:
            params["seed"] = int(seed)

        body = _request_json(
            f"{self.base_url}/services/aigc/text2image/image-synthesis",
            api_key=self.api_key,
            payload={"model": self.model, "input": {"prompt": prompt}, "parameters": params},
            async_mode=True,
            timeout=90.0,
        )
        output = body.get("output") or {}
        task_id = output.get("task_id") or body.get("task_id")
        if not task_id:
            raise ProviderError(f"百炼未返回 task_id：{str(body)[:300]}")

        url = self._await_image(str(task_id))
        _download(url, out_path)

        return MediaResult(
            path=out_path,
            provider=self.name,
            model=self.model,
            cost_usd=estimate_image_cost(1),
            latency_ms=(time.perf_counter() - started) * 1000,
            meta={
                "task_id": task_id,
                # 记录实际尺寸：归一化后可能与请求值不同，写进 meta 让下游可感知
                "requested_size": (int(size[0]), int(size[1])),
                "size": actual,
                "size_adjusted": actual != (int(size[0]), int(size[1])),
                "mode": "async",
            },
        )

    def _await_image(self, task_id: str) -> str:
        """轮询图像任务。结果在 output.results[].url（与视频的字段不同）。"""
        deadline = time.time() + self.poll_timeout
        last = "UNKNOWN"
        while time.time() < deadline:
            time.sleep(self.poll_interval)
            body = _request_json(
                f"{self.base_url}/tasks/{task_id}",
                api_key=self.api_key,
                payload=None,
                method="GET",
                timeout=60.0,
            )
            output = body.get("output") or {}
            last = str(output.get("task_status") or "").upper()
            if last in {"SUCCEEDED", "SUCCESS", "COMPLETED"}:
                results = output.get("results") or []
                if results and results[0].get("url"):
                    return str(results[0]["url"])
                raise ProviderError(f"图像任务成功但无结果 URL：{str(output)[:300]}")
            if last in {"FAILED", "CANCELED", "CANCELLED", "UNKNOWN"}:
                detail = output.get("message") or output.get("code") or ""
                raise ProviderError(f"百炼图像任务失败（{last}）：{str(detail)[:300]}")
        raise ProviderError(f"百炼图像任务轮询超时（{self.poll_timeout}s，最后状态 {last}）")


class DashScopeVideoProvider(VideoProvider):
    #: 真实视频模型返回 MP4 容器
    output_suffix = "mp4"

    name = "dashscope"
    model = DEFAULT_VIDEO_MODEL

    #: 实测支持 base64 内联首帧，因此图生视频可用，跨镜头一致性成立
    accepts_local_keyframe = True

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = "",
        fast_model: str = "",
        poll_interval: float = 5.0,
        poll_timeout: float = 900.0,
    ) -> None:
        if not api_key:
            raise ProviderError("百炼视频供应商缺少 API Key")
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or DEFAULT_VIDEO_MODEL
        #: 优速版模型名。未显式配置时按约定推导（`X` -> `X-prime`）。
        #: 实测优速版比基础版快 63%、贵 54%，画质同档 —— 是「速度」而非「质量」的差异。
        self.fast_model = fast_model or f"{self.model}-prime"
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    # ------------------------------------------------------------------ #
    def _submit(self, payload: dict) -> str:
        body = _request_json(
            f"{self.base_url}/services/aigc/video-generation/video-synthesis",
            api_key=self.api_key,
            payload=payload,
            async_mode=True,
            timeout=120.0,
        )
        output = body.get("output") or {}
        task_id = output.get("task_id") or body.get("task_id")
        if not task_id:
            raise ProviderError(f"百炼未返回 task_id：{str(body)[:300]}")
        return str(task_id)

    def _await(self, task_id: str) -> dict:
        deadline = time.time() + self.poll_timeout
        last_status = "UNKNOWN"
        while time.time() < deadline:
            time.sleep(self.poll_interval)
            body = _request_json(
                f"{self.base_url}/tasks/{task_id}",
                api_key=self.api_key,
                payload=None,
                method="GET",
                timeout=60.0,
            )
            output = body.get("output") or {}
            last_status = str(output.get("task_status") or body.get("task_status") or "").upper()

            if last_status in {"SUCCEEDED", "SUCCESS", "COMPLETED"}:
                return output
            if last_status in {"FAILED", "CANCELED", "CANCELLED", "UNKNOWN"}:
                detail = output.get("message") or output.get("code") or body.get("message") or ""
                raise ProviderError(f"百炼任务失败（{last_status}）：{str(detail)[:300]}")
        raise ProviderError(f"百炼任务轮询超时（{self.poll_timeout}s，最后状态 {last_status}）")

    # ------------------------------------------------------------------ #
    def generate(
        self,
        *,
        prompt: str,
        out_path: Path,
        duration_s: float,
        size: tuple[int, int],
        keyframe: Path | None = None,
        seed: int = 0,
        use_fast: bool = False,
    ) -> MediaResult:
        started = time.perf_counter()

        tier = resolution_tier(size)
        # DashScope 的 duration 单位为秒，整数；wan3.0-video 支持 2-30 秒
        duration = max(2, min(30, round(duration_s)))
        # express 档走优速版：同分辨率、同画质，只是更快（实测快 63%）
        model = self.fast_model if use_fast else self.model

        input_payload: dict[str, object] = {"prompt": prompt}
        consistency = "text_only"
        has_keyframe = keyframe is not None and keyframe.is_file()
        if has_keyframe:
            # ⚠️ 首帧必须走 `input.media` 数组，字段名是 `first_frame`。
            #
            # 早期实现用的是 `input.img_url`（旧接口写法）。服务端对
            # **不认识的参数返回 200 并静默忽略** —— 于是：
            #   1. 关键帧根本没被当作首帧，成片实际是纯文生视频，
            #      跨镜头一致性形同虚设（而 meta 里还写着 image_to_video）；
            #   2. 缺 `ratio` 时输出比例不受控，480P 出的是 854x480 横屏，
            #      一个 9:16 的竖屏项目因此产出横屏视频。
            # 这类「接口接受但不用」的失败最难发现：HTTP 200、日志正常、产物齐全。
            assert keyframe is not None
            input_payload["media"] = [{"type": "first_frame", "url": _data_uri(keyframe)}]
            consistency = "image_to_video"

        payload: dict[str, object] = {
            "model": model,
            "input": input_payload,
            "parameters": {
                "resolution": tier,
                # 有首帧就让模型跟随它的比例（文档推荐）；否则显式指定，
                # 不能省 —— 省掉就会拿到供应商的默认比例。
                "ratio": "adaptive" if has_keyframe else ratio_for(size),
                "duration": duration,
            },
        }
        if seed:
            payload["parameters"]["seed"] = int(seed)  # type: ignore[index]

        task_id = self._submit(payload)
        output = self._await(task_id)

        video_url = output.get("video_url") or output.get("url")
        if not video_url:
            raise ProviderError(f"百炼任务成功但未返回 video_url：{str(output)[:300]}")
        _download(str(video_url), out_path)

        return MediaResult(
            path=out_path,
            provider=self.name,
            model=model,
            cost_usd=estimate_video_cost(duration, tier, fast=use_fast),
            latency_ms=(time.perf_counter() - started) * 1000,
            meta={
                "task_id": task_id,
                "duration_s": duration,
                "resolution_tier": tier,
                "consistency": consistency,
                "fast_model": use_fast,
                "usage": output.get("usage") or {},
            },
        )
