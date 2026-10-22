"""媒体生成工具：把供应商能力包装成可注册、可调用、可追踪的 Tool。

设计要点："Tool/Skill 抽象与注册机制"、"标准化 Toolcall 调用与技能编排"。

每个工具都会：
  1. 写出 span（可观测）
  2. 记录成本（成本归因）
  3. 失败向上抛 ToolError（交给 run_with_retry 处理）
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageStat

from ..harness.registry import ToolError, arr_prop, num_prop, obj_schema, str_prop
from ..providers.base import size_for

if TYPE_CHECKING:  # pragma: no cover
    from ..runtime import Runtime


#: 判定「退化图像」的阈值。
#:
#: 真实事故：某次生产运行里，图像供应商对其中一镜返回了**纯黑图**
#: （标准差 0.0、全图只有 1 种颜色），而流水线照单全收：
#:   - 关键帧落盘成功、状态正常、没有报错
#:   - 下游 Ken Burns 只能从纯色图裁出完全相同的帧，
#:     最终合成出**单帧 GIF**（1.4 KB，另两镜是 4.6 MB / 3.6 MB）
#:   - 成片里那一段是黑屏，而项目状态显示 ready、3/3 镜头完成
#:
#: 这类失败极难发现：HTTP 200、产物齐全、指标全绿，
#: 唯一的线索是文件大小异常。所以必须在写入后立刻校验。
#:
#: 阈值定得很保守（标准差 < 2.0）：真实照片哪怕平淡如白墙，
#: 标准差通常也在 15 以上；取 2.0 只会在图像确实是纯色时触发。
_MIN_STDDEV = 2.0
_MAX_DEGENERATE_COLORS = 2


def _assert_image_not_degenerate(path: Path, *, what: str) -> None:
    """确认生成的图像不是纯色/空白图，否则抛 ToolError 交给重试机制。"""
    try:
        with Image.open(path) as im:
            rgb = im.convert("RGB")
            stat = ImageStat.Stat(rgb)
            stddev = sum(stat.stddev) / 3.0
            colors = rgb.getcolors(maxcolors=1 << 20)
    except Exception as exc:
        raise ToolError(f"{what} 无法读取为图片：{exc}") from exc

    n_colors = len(colors) if colors else 0
    if stddev < _MIN_STDDEV or n_colors <= _MAX_DEGENERATE_COLORS:
        raise ToolError(
            f"{what} 是退化图像（唯一色 {n_colors}、标准差 {stddev:.2f}）：供应商返回了纯色或空白图，需要重试"
        )


def register_media_tools(rt: Runtime) -> None:
    settings = rt.settings
    image_p = rt.providers.image
    video_p = rt.providers.video
    tts_p = rt.providers.tts

    # ------------------------------------------------------------------ #
    def generate_keyframe(
        prompt: str,
        out_name: str,
        ref_images: list[str] | None = None,
        seed: int = 0,
        scale: float = 1.0,
        shot_id: str = "",
        subdir: str = "keyframes",
    ) -> dict[str, Any]:
        # subdir 让"镜头关键帧"与"线索基准图"分开放，避免产物冗余
        out = rt.dir_for(rt.project_id, subdir) / out_name
        refs = [settings.abs(r) for r in (ref_images or []) if r]
        refs = [r for r in refs if r.exists()]
        size = size_for(settings.aspect_ratio, scale)

        # 与视频供应商同理：参考图是「跨镜头一致性」的载体，但各家的传参方式不同。
        # 火山方舟接受 base64 内联；智谱要求公网 URL。
        # 若供应商不支持本地参考图，就**不带参考图**生成——这一步会削弱一致性，
        # 因此必须记录，不能假装比对过了。
        accepts_refs = getattr(image_p, "accepts_local_ref_images", True)
        if not accepts_refs:
            refs = []
        consistency = "reference_guided" if accepts_refs else "prompt_only_degraded"

        with rt.tracer.span(
            "tool.generate_keyframe",
            kind="tool",
            shot_id=shot_id,
            refs=len(refs),
            consistency=consistency,
            # 在调用**之前**写下供应商归属。
            #
            # 为什么不能只在成功之后 sp.set：失败时 res 根本不存在，
            # 于是失败的 span 永远没有 provider/model，`usage_records` 里
            # 只能记成空字符串，失败率就无法按供应商归因
            # （实测：image 20% 失败率，但看不出是哪个供应商）。
            #
            # 这两个值在调用前就已确定，写下来是**事实**而不是拿当前配置
            # 回填的猜测 —— 后者会产生「看起来有归属、实际是猜的」数据。
            provider=getattr(image_p, "name", "") or "",
            model=getattr(image_p, "model", "") or "",
        ) as sp:
            started = time.perf_counter()
            res = image_p.generate(prompt=prompt, out_path=out, size=size, ref_images=refs, seed=seed)
            # 供应商可能返回纯色图（见 _assert_image_not_degenerate 的说明）。
            # 必须在**计费与落盘确认之前**就发现，否则会把一张黑图当成有效产物。
            _assert_image_not_degenerate(out, what=f"关键帧（{shot_id or out.name}）")
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            rt.record_media_cost(
                category="image",
                provider=res.provider,
                model=res.model,
                unit="image",
                quantity=1,
                usd=res.cost_usd,
                meta={"shot_id": shot_id, "consistency": consistency},
                latency_ms=elapsed_ms,
            )
            sp.set(
                provider=res.provider,
                model=res.model,
                cost_usd=res.cost_usd,
                latency_ms=elapsed_ms,
            )
            return {
                "path": rt.rel(res.path),
                "provider": res.provider,
                "model": res.model,
                "cost_usd": res.cost_usd,
                "refs_used": len(refs),
                "consistency": consistency,
            }

    # ------------------------------------------------------------------ #
    def generate_clip(
        prompt: str,
        keyframe: str,
        out_name: str,
        duration_s: float = 3.0,
        seed: int = 0,
        use_video_model: bool = True,
        shot_id: str = "",
        scale: float = 1.0,
        use_fast: bool = False,
    ) -> dict[str, Any]:
        kf = settings.abs(keyframe)
        # 分辨率由路由档位决定：premium 给更高分辨率，让「贵一档」
        # 真的体现在成片上，而不只是体现在账单里。
        size = size_for(settings.aspect_ratio, scale)

        provider = video_p if use_video_model else None
        if provider is None:
            # 草稿档：用本地运动合成，不产生费用
            from ..providers.mock import MockVideoProvider

            provider = MockVideoProvider()

        # 片段容器的扩展名由**供应商**决定，不能由调用方写死。
        #
        # 踩坑记录：早期版本把 out_name 固定为 `{shot_id}.gif`，
        # 而真实视频模型返回的都是 MP4，于是磁盘上出现「扩展名 .gif、内容是 MP4」
        # 的文件；合成阶段用 Pillow 打开必然失败，异常当时又被静默吞掉，
        # 最终成片停留在旧版本而指标是新算的 —— 界面上完全看不出来。
        suffix = getattr(provider, "output_suffix", "mp4")
        out = rt.dir_for(rt.project_id, "clips") / Path(out_name).with_suffix(f".{suffix}").name

        # 供应商是否接受本地关键帧决定本镜头走图生视频还是文生视频。
        # 智谱要求公网 URL，因此只能文生视频——这是一次**真实的能力降级**：
        # 成片照样产出，但跨镜头一致性不再成立。必须记录，不能默默略过，
        # 否则「一致性」这个核心指标会变成假象。
        accepts_kf = getattr(provider, "accepts_local_keyframe", True)
        if accepts_kf and not kf.exists():
            raise FileNotFoundError(f"关键帧不存在：{keyframe}")
        consistency = "image_to_video" if accepts_kf else "text_only_degraded"

        with rt.tracer.span(
            "tool.generate_clip",
            kind="tool",
            shot_id=shot_id,
            routed=("video_model" if use_video_model else "local_motion"),
            consistency=consistency,
            # 调用前写下供应商归属，理由同 generate_keyframe：
            # 失败时拿不到 res，只能靠这里。视频调用最容易因限流/额度失败，
            # 没有归属就无法回答「哪个模型的失败率最高」。
            provider=getattr(provider, "name", "") or "",
            model=getattr(provider, "model", "") or "",
        ) as sp:
            started = time.perf_counter()
            res = provider.generate(
                prompt=prompt,
                keyframe=kf if accepts_kf else None,
                out_path=out,
                duration_s=duration_s,
                size=size,
                seed=seed,
                use_fast=use_fast,
            )
            # 记录**实际被计费的秒数**，而不是原始 duration_s。
            #
            # 供应商按整数秒计费（3.2 秒按 3 秒收费），若 quantity 记原始值，
            # 就会出现「数量 3.2、金额按 3 算」的错位 —— 按单位价做分析时
            # 算出来的单价会偏，且与账单对不上。
            billed_seconds = float(res.meta.get("duration_s") or duration_s)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            rt.record_media_cost(
                category="video",
                provider=res.provider,
                model=res.model,
                unit="second",
                quantity=billed_seconds,
                usd=res.cost_usd,
                meta={
                    "shot_id": shot_id,
                    "routed": use_video_model,
                    "consistency": consistency,
                    "requested_seconds": duration_s,
                    "fast_model": use_fast,
                },
                latency_ms=elapsed_ms,
            )
            sp.set(
                provider=res.provider,
                model=res.model,
                cost_usd=res.cost_usd,
                latency_ms=elapsed_ms,
            )
            return {
                "path": rt.rel(res.path),
                "provider": res.provider,
                "model": res.model,
                "cost_usd": res.cost_usd,
                "frames": res.meta.get("frames"),
                "consistency": consistency,
            }

    # ------------------------------------------------------------------ #
    def synthesize_speech(text: str, out_name: str, shot_id: str = "") -> dict[str, Any]:
        out = rt.dir_for(rt.project_id, "audio") / out_name
        with rt.tracer.span(
            "tool.synthesize_speech",
            kind="tool",
            shot_id=shot_id,
            chars=len(text),
            # 调用前写下供应商归属（理由见 generate_keyframe）
            provider=getattr(tts_p, "name", "") or "",
            model=getattr(tts_p, "model", "") or "",
        ) as sp:
            started = time.perf_counter()
            res = tts_p.generate(text=text, out_path=out)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            rt.record_media_cost(
                category="tts",
                provider=res.provider,
                model=res.model,
                unit="char",
                quantity=len(text),
                usd=res.cost_usd,
                meta={"shot_id": shot_id},
                latency_ms=elapsed_ms,
            )
            sp.set(
                provider=res.provider,
                model=res.model,
                cost_usd=res.cost_usd,
                latency_ms=elapsed_ms,
            )
            return {
                "path": rt.rel(res.path),
                "provider": res.provider,
                "model": res.model,
                "cost_usd": res.cost_usd,
                "duration_s": res.meta.get("duration_s"),
            }

    # ------------------------------------------------------------------ #
    rt.tools.register(
        "generate_keyframe",
        "根据提示词生成一张关键帧图像。可传入参考图（角色/场景基准图）以保持跨镜头一致性。",
        obj_schema(
            prompt=str_prop("图像提示词", required=True),
            out_name=str_prop("输出文件名，如 s01_sh01.png", required=True),
            ref_images=arr_prop("参考图相对路径列表（线索基准图）"),
            seed=num_prop("随机种子"),
            scale=num_prop("分辨率缩放系数", default=1.0),
            shot_id=str_prop("所属镜头 ID"),
            subdir=str_prop("输出子目录，默认 keyframes；线索基准图用 assets"),
        ),
        generate_keyframe,
        tags=["media", "image"],
    )

    rt.tools.register(
        "generate_clip",
        "把关键帧转成动态视频片段。use_video_model=false 时走本地运动合成（草稿档，零成本）。",
        obj_schema(
            prompt=str_prop("视频提示词", required=True),
            keyframe=str_prop("关键帧相对路径", required=True),
            out_name=str_prop("输出文件名，如 s01_sh01.gif", required=True),
            duration_s=num_prop("时长（秒）", default=3.0),
            seed=num_prop("随机种子"),
            use_video_model={"type": "boolean", "description": "是否调用真实视频模型", "default": True},
            scale={"type": "number", "description": "分辨率倍率（路由档位决定）", "default": 1.0},
            use_fast={"type": "boolean", "description": "是否用优速版模型（express 档）", "default": False},
            shot_id=str_prop("所属镜头 ID"),
        ),
        generate_clip,
        tags=["media", "video"],
    )

    rt.tools.register(
        "synthesize_speech",
        "把旁白或台词合成为语音。",
        obj_schema(
            text=str_prop("要朗读的文本", required=True),
            out_name=str_prop("输出文件名，如 s01_sh01.wav", required=True),
            shot_id=str_prop("所属镜头 ID"),
        ),
        synthesize_speech,
        tags=["media", "audio"],
    )
