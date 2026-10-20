"""供应商工厂。

按 Settings 构建 (image, video, tts) 三元组。
**关键行为**：live 模式下若某个供应商缺少 API Key，自动逐项降级为 mock，
并在返回的降级说明里记录原因——保证流水线永远能跑完，而不是中途炸掉。
"""

from __future__ import annotations

from ..config import Settings
from .ark import ArkImageProvider, ArkVideoProvider, OpenAICompatImageProvider, OpenAICompatTTSProvider
from .base import (
    ASPECT_SIZES,
    ImageProvider,
    MediaResult,
    ProviderError,
    TTSProvider,
    VideoProvider,
    estimate_image_cost,
    estimate_tts_cost,
    estimate_video_cost,
    size_for,
)
from .dashscope import (
    DEFAULT_BASE_URL as DASHSCOPE_DEFAULT_BASE_URL,
)
from .dashscope import (
    DEFAULT_IMAGE_MODEL as DASHSCOPE_DEFAULT_IMAGE_MODEL,
)
from .dashscope import (
    DEFAULT_TTS_MODEL as DASHSCOPE_DEFAULT_TTS_MODEL,
)
from .dashscope import (
    DEFAULT_VIDEO_MODEL as DASHSCOPE_DEFAULT_VIDEO_MODEL,
)
from .dashscope import (
    DashScopeImageProvider,
    DashScopeTTSProvider,
    DashScopeVideoProvider,
)
from .mock import MockImageProvider, MockTTSProvider, MockVideoProvider
from .zhipu import (
    DEFAULT_BASE_URL as ZHIPU_DEFAULT_BASE_URL,
)
from .zhipu import (
    FREE_VIDEO_MODEL,
    ZhipuImageProvider,
    ZhipuVideoProvider,
)

__all__ = [
    "ASPECT_SIZES",
    "ArkImageProvider",
    "ArkVideoProvider",
    "DashScopeImageProvider",
    "DashScopeTTSProvider",
    "DashScopeVideoProvider",
    "ImageProvider",
    "MediaResult",
    "MockImageProvider",
    "MockTTSProvider",
    "MockVideoProvider",
    "OpenAICompatImageProvider",
    "OpenAICompatTTSProvider",
    "ProviderError",
    "TTSProvider",
    "VideoProvider",
    "build_providers",
    "estimate_image_cost",
    "estimate_tts_cost",
    "estimate_video_cost",
    "size_for",
]


class ProviderBundle:
    def __init__(
        self,
        image: ImageProvider,
        video: VideoProvider,
        tts: TTSProvider,
        notes: list[str] | None = None,
    ) -> None:
        self.image = image
        self.video = video
        self.tts = tts
        self.notes = notes or []

    def describe(self) -> dict[str, str]:
        return {
            "image": f"{self.image.name}/{self.image.model}",
            "video": f"{self.video.name}/{self.video.model}",
            "tts": f"{self.tts.name}/{self.tts.model}",
        }


def build_providers(settings: Settings) -> ProviderBundle:
    notes: list[str] = []
    style_spec = settings.style_spec
    raw_palette = style_spec.get("palette")
    palette: list[tuple[int, int, int]] = [
        (int(c[0]), int(c[1]), int(c[2]))
        for c in (raw_palette if isinstance(raw_palette, list) else [])
        if isinstance(c, (list, tuple)) and len(c) >= 3
    ]
    style_label = str(style_spec.get("label", ""))

    def _mock_image() -> ImageProvider:
        return MockImageProvider(palette=palette, style_label=style_label)

    # ---------------- 图像 ----------------
    image: ImageProvider
    if settings.is_mock or not settings.image.enabled:
        if not settings.is_mock:
            notes.append(f"图像供应商 {settings.image.name} 未配置 Key，已降级为 mock")
        image = _mock_image()
    else:
        try:
            if settings.image.name == "ark":
                image = ArkImageProvider(
                    api_key=settings.image.api_key,
                    base_url=settings.image.base_url,
                    model=settings.image.model,
                )
            elif settings.image.name == "zhipu":
                image = ZhipuImageProvider(
                    api_key=settings.image.api_key,
                    base_url=settings.image.base_url or ZHIPU_DEFAULT_BASE_URL,
                    model=settings.image.model,
                )
            elif settings.image.name == "dashscope":
                # 万相文生图必须走 DashScope 原生协议：
                # 尺寸用星号分隔（1024*1024）、结果是异步任务、URL 在 results[] 里。
                # 这些都与 OpenAI 兼容端点不通用，不能复用同一个适配器。
                image = DashScopeImageProvider(
                    api_key=settings.image.api_key,
                    base_url=settings.image.base_url or DASHSCOPE_DEFAULT_BASE_URL,
                    model=settings.image.model or DASHSCOPE_DEFAULT_IMAGE_MODEL,
                )
            elif settings.image.name == "openai":
                image = OpenAICompatImageProvider(
                    api_key=settings.image.api_key,
                    base_url=settings.image.base_url,
                    model=settings.image.model,
                )
            else:
                raise ProviderError(f"未知图像供应商：{settings.image.name}")
        except ProviderError as exc:
            notes.append(f"图像供应商初始化失败（{exc}），已降级为 mock")
            image = _mock_image()

    # ---------------- 视频 ----------------
    video: VideoProvider
    if settings.is_mock or not settings.video.enabled:
        if not settings.is_mock:
            notes.append(f"视频供应商 {settings.video.name} 未配置 Key，已降级为 mock")
        video = MockVideoProvider()
    else:
        try:
            if settings.video.name == "ark":
                video = ArkVideoProvider(
                    api_key=settings.video.api_key,
                    base_url=settings.video.base_url,
                    model=settings.video.model,
                )
            elif settings.video.name == "zhipu":
                # 智谱的 CogVideoX-Flash 是官方免费模型，但高峰期长期 429
                video = ZhipuVideoProvider(
                    api_key=settings.video.api_key,
                    base_url=settings.video.base_url or ZHIPU_DEFAULT_BASE_URL,
                    model=settings.video.model or FREE_VIDEO_MODEL,
                )
            elif settings.video.name == "dashscope":
                # 阿里云百炼：真正按量付费，且接受 base64 首帧（一致性完整可用）
                video = DashScopeVideoProvider(
                    api_key=settings.video.api_key,
                    base_url=settings.video.base_url or DASHSCOPE_DEFAULT_BASE_URL,
                    model=settings.video.model or DASHSCOPE_DEFAULT_VIDEO_MODEL,
                )
            elif settings.video.name in {"openai", "kling"}:
                # 各家视频协议差异较大，未内置时给出明确提示而不是静默出错
                raise ProviderError(
                    f"视频供应商 {settings.video.name} 尚未内置适配器，请参照 providers/dashscope.py 实现"
                )
            else:
                raise ProviderError(f"未知视频供应商：{settings.video.name}")
        except ProviderError as exc:
            notes.append(f"视频供应商不可用（{exc}），已降级为 mock")
            video = MockVideoProvider()

    # ---------------- TTS ----------------
    tts: TTSProvider
    if settings.is_mock or not settings.tts.enabled:
        if not settings.is_mock:
            notes.append(f"TTS 供应商 {settings.tts.name} 未配置 Key，已降级为 mock")
        tts = MockTTSProvider()
    else:
        try:
            if settings.tts.name == "dashscope":
                # 百炼没有实现 OpenAI 的 /audio/speech（该路径 404），
                # 必须走原生多模态端点，因此不能复用 OpenAI 兼容适配器
                tts = DashScopeTTSProvider(
                    api_key=settings.tts.api_key,
                    base_url=settings.tts.base_url or DASHSCOPE_DEFAULT_BASE_URL,
                    model=settings.tts.model or DASHSCOPE_DEFAULT_TTS_MODEL,
                )
            else:
                tts = OpenAICompatTTSProvider(
                    api_key=settings.tts.api_key,
                    base_url=settings.tts.base_url,
                    model=settings.tts.model,
                )
        except ProviderError as exc:
            notes.append(f"TTS 初始化失败（{exc}），已降级为 mock")
            tts = MockTTSProvider()

    return ProviderBundle(image, video, tts, notes)
