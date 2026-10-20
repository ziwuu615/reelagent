"""媒体供应商抽象层：图像 / 视频 / TTS。

统一接口的目的是让 Director Agent 不关心底层是哪家模型：
换供应商只需要增加一个适配器，上层流水线一行都不用改。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# 画幅
# --------------------------------------------------------------------------- #
ASPECT_SIZES: dict[str, tuple[int, int]] = {
    "9:16": (540, 960),
    "16:9": (960, 540),
    "1:1": (768, 768),
    "4:3": (800, 600),
    "3:4": (600, 800),
}


def size_for(aspect_ratio: str, scale: float = 1.0) -> tuple[int, int]:
    w, h = ASPECT_SIZES.get(aspect_ratio, ASPECT_SIZES["9:16"])
    return max(64, int(w * scale)), max(64, int(h * scale))


# --------------------------------------------------------------------------- #
# 结果
# --------------------------------------------------------------------------- #
@dataclass
class MediaResult:
    path: Path
    provider: str = ""
    model: str = ""
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


class ProviderError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# 接口
# --------------------------------------------------------------------------- #
class ImageProvider(ABC):
    name = "base"
    model = ""

    #: 是否支持传入**本地**参考图文件（跨镜头一致性的载体）。
    #:
    #:   True  —— 接受 base64 内联图片（火山方舟），关键帧可参考线索基准图，
    #:            角色/场景在不同镜头间保持一致。
    #:   False —— 要求公网可访问的 URL（智谱）。此时流水线会**不带参考图**生成，
    #:            逐镜头提示词仍可用，但跨镜头一致性显著减弱。
    #:
    #: 与 VideoProvider.accepts_local_keyframe 同一设计意图：把能力差异显式声明，
    #: 由流水线据此降级并记录，而不是让适配器抛错拖垮整条链路、
    #: 或者静默忽略参考图制造「一致性已生效」的假象。
    accepts_local_ref_images: bool = True

    @abstractmethod
    def generate(
        self,
        *,
        prompt: str,
        out_path: Path,
        size: tuple[int, int],
        ref_images: list[Path] | None = None,
        seed: int = 0,
    ) -> MediaResult: ...


class VideoProvider(ABC):
    name = "base"
    model = ""

    #: 是否支持传入**本地**关键帧文件。
    #:
    #:   True  —— 供应商接受内联图片（火山方舟走 base64），流水线走**图生视频**，
    #:            跨镜头角色/场景一致性成立。
    #:   False —— 供应商要求公网可访问的图片 URL（智谱即如此）。此时流水线退化为
    #:            **纯文生视频**：成片仍然能出，但**跨镜头一致性会降级**。
    #:
    #: 这个差异必须显式建模而不是靠 try/except 兜。原因是两种处理方式都不可接受：
    #: 让适配器抛错会让整条流水线在视频阶段全灭；让适配器静默忽略关键帧，
    #: 则会做出「一致性看起来支持、实际没生效」的假象 —— 那比直接失败更危险。
    accepts_local_keyframe: bool = True

    #: 片段产物的容器扩展名。**由供应商声明，不由调用方写死**。
    #:
    #: 踩坑记录：早期版本在调用处把输出名固定成 `{shot_id}.gif`，
    #: 而真实视频模型（方舟/智谱/万相）返回的都是 MP4。于是磁盘上出现了
    #: 「扩展名 .gif、内容是 MP4」的文件：合成阶段用 Pillow 打开必然失败，
    #: 而该异常当时被静默吞掉，最终成片停留在旧版本、指标却是新算的 ——
    #: 一种从界面上完全看不出来的数据/产物不一致。
    output_suffix: str = "mp4"

    @abstractmethod
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
    ) -> MediaResult: ...


class TTSProvider(ABC):
    name = "base"
    model = ""

    @abstractmethod
    def generate(self, *, text: str, out_path: Path, voice: str = "") -> MediaResult: ...


# --------------------------------------------------------------------------- #
# 成本估算（USD）
# --------------------------------------------------------------------------- #
#: 人民币兑美元汇率。
#:
#: ⚠️ 这是**假设值，不是查证的实时汇率**，必须如实标注。
#: 官方定价页对**视频**直接给美元（无需换算），但对**图像/语音**给的是元价，
#: 所以这两项的成本估算依赖本汇率。
#:
#: 7.2 是近年人民币兑美元的常用近似值。它带来的误差：
#:   - 若实际汇率为 7.0，图像/TTS 成本会被**高估 2.8%**
#:   - 若实际汇率为 7.4，会被**低估 2.7%**
#: 相对视频（占总成本 60%+ 且本来就是美元计价）来说影响很小，
#: 但它是本模块里唯一没有官方来源的数字 —— 不要让读者误以为它有出处。
#:
#: 若需要精确对账，应改用账单里的实际扣费金额，而不是靠汇率反推。
CNY_PER_USD = 7.2

#: ⚠️ 以下单价**全部来自官方定价页**，注释里写明来源与抓取日期。
#:
#: 这些值直接影响预算保护与额度预扣：估低了用户会在跑到一半时被判额度不足，
#: 估高了会多扣用户的钱。所以不能凭印象填，也不能用"大概差不多"的数。
#: 修改任何一项时，请同时更新 `scripts/audit_pricing_constants.py` 里的来源表。

#: 百炼 wan2.5-t2i-preview：0.2 元/张（华北2 北京）
#: https://help.aliyun.com/zh/model-studio/wan2-5-t2i  （抓取于 2026-10-16）
IMAGE_UNIT_COST = 0.2 / CNY_PER_USD  # ≈ $0.02778

#: 百炼 qwen3-tts-flash：0.8 元/万字符（华北2 北京）
#: https://help.aliyun.com/zh/model-studio/qwen3-tts-flash  （抓取于 2026-10-16）
TTS_UNIT_COST_PER_1K_CHAR = (0.8 / 10) / CNY_PER_USD  # 0.08 元/千字符 ≈ $0.01111

#: 百炼 wan3.0-video：**按分辨率计价，价差达 4 倍**（华北2 北京，官方直接给美元）
#: https://www.alibabacloud.com/help/en/model-studio/wan3-0-video  （抓取于 2026-10-16）
#:
#:     480P   $0.041256 / 秒
#:     720P   $0.082513 / 秒   （480P 的 2 倍）
#:     1080P  $0.165025 / 秒   （480P 的 4 倍）
#:
#: 踩过的坑：早期只有一个 `VIDEO_UNIT_COST_PER_S = 0.045`，函数也不收分辨率
#: 参数 —— 于是 720P 按 480P 计价（低估约 45%），1080P 低估约 73%。
#: 用户实际花了约 19 元，系统内部记录的估算却只有约 13 元，差额无法解释。
#: 价格随档位变化时，「一个常量打天下」必然算错。
VIDEO_PRICE_PER_S: dict[str, float] = {
    "480P": 0.041256,
    "720P": 0.082513,
    "1080P": 0.165025,
}

#: 默认档位的单价，保留给不关心分辨率的旧调用点
VIDEO_UNIT_COST_PER_S = VIDEO_PRICE_PER_S["480P"]

#: **优速版**（`wan3.0-video-prime`）的单价。官方定位是「保持高质量的同时
#: 大幅提升生成速度」，价格贵约 54%。
#:
#: 实测（2026-10-16，480P / 2 秒，同提示词并行提交）：
#:     wan3.0-video        95.0 秒
#:     wan3.0-video-prime  58.3 秒   -> 快 63%
#: 同分辨率、同时长、码率接近，差异就是速度。
#:
#: 来源：https://www.alibabacloud.com/help/zh/model-studio/wan3-0-video-prime
#: （华北2 北京，抓取于 2026-10-16）
VIDEO_PRICE_PER_S_FAST: dict[str, float] = {
    "480P": 0.063600,
    "720P": 0.127199,
    "1080P": 0.254399,
}


def estimate_image_cost(n: int = 1) -> float:
    return IMAGE_UNIT_COST * n


def estimate_video_cost(duration_s: float, resolution: str = "480P", *, fast: bool = False) -> float:
    """估算一次视频生成的花费。

    `resolution` 必须传真实档位（480P/720P/1080P）—— 价差达 4 倍，
    用默认值算 720P 会低估近一半。

    `fast=True` 表示走优速版模型（同分辨率下贵约 54%，但实测快 63%）。
    两套价格的键完全相同，因此分辨率映射可以复用。
    """
    table = VIDEO_PRICE_PER_S_FAST if fast else VIDEO_PRICE_PER_S
    key = str(resolution).upper()
    unit = table.get(key, table["480P"])
    return unit * max(duration_s, 0.5)


def estimate_tts_cost(text: str) -> float:
    return TTS_UNIT_COST_PER_1K_CHAR * (len(text) / 1000.0)
