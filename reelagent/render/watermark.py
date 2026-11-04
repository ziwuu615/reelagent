"""AIGC 显式标识与隐式标识。

## 法规依据

《人工智能生成合成内容标识办法》自 2025-09-01 施行，要求对 AI 生成合成内容
同时做两类标识：

    显式标识  用户可见：画面上的文字水印/角标
    隐式标识  写在文件元数据里的生成信息（服务提供者、内容编号等）

只做水印不够（元数据可被批量核查），只做元数据也不够（普通用户看不到）。
所以这里两者都做，并把标识信息同时写进 manifest，便于外部核验。

## 为什么用「后处理一步」而不是塞进合成滤镜链

合成函数 `compose_mp4` 已经有四种音频分支与一条 `-vf` 链，再往里插 overlay
需要把 `-vf` 改写成 `-filter_complex` 并照顾每条分支的 `-map`，改动面大、
回归风险高。

标识是**产物级**的后处理：成片已经生成好了，再烤一遍水印。这样：
    1. 合成逻辑零改动，已有测试全部继续有效
    2. 标识可以单独开关、单独重跑（客户申请去除/补打都不影响成片内容）
    3. 任何新的合成后端都自动获得标识能力，不会漏

代价是多一次转码。对「一条成片几十秒」的量级，这个代价可以接受。
"""

from __future__ import annotations

import contextlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

#: 常见中文字体候选。找不到时退回 Pillow 内置位图字体（英文可读、中文会缺字），
#: 因此**必须**在部署时确保至少命中一个，否则水印会变成方块。
_FONT_CANDIDATES: tuple[str, ...] = (
    # Windows
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
)

#: 角标离画面边缘的像素距离
_LABEL_MARGIN = 24

#: 角标背景的不透明度（0-255）。要「看得见但不喧宾夺主」：
#: 太淡等于没标识，太浓会毁掉画面。0.45 左右是肉眼可辨又能读清的区间。
_BG_ALPHA = 115


def find_font() -> str | None:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = find_font()
    if path:
        with contextlib.suppress(Exception):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


@dataclass(frozen=True)
class AigcLabel:
    """标识文案与元数据。

    `text` 是烧在画面上的可见文字；`metadata` 是写进容器元数据与 manifest 的
    隐式信息。两者内容不同、用途不同，不要合并成一个字符串。
    """

    text: str = "AI 生成"
    provider: str = "ReelAgent"
    standard: str = "《人工智能生成合成内容标识办法》"

    def metadata(self, *, content_id: str) -> dict[str, str]:
        return {
            "AIGC": "true",
            "AIGC-Provider": self.provider,
            "AIGC-Standard": self.standard,
            "AIGC-Content-Id": content_id,
        }


def make_label_png(
    label: AigcLabel,
    out_path: Path,
    *,
    font_size: int = 28,
    margin: int = _LABEL_MARGIN,
) -> Path:
    """生成一张带半透明底的角色标 PNG。

    用图片而不是 ffmpeg 的 drawtext：drawtext 依赖 fontconfig 与字体路径，
    在容器里经常配不对（中文直接变成方块）。PIL 这边字体探测是同一份逻辑，
    出问题的面小得多，而且测试里可以直接断言像素。
    """
    font = _font(font_size)
    # 用 getbbox 而不是 textlength：Pillow 对位图字体的 textlength 支持不一致
    left, top, right, bottom = font.getbbox(label.text)
    text_w, text_h = right - left, bottom - top
    pad_x, pad_y = 16, 10

    canvas = Image.new("RGBA", (text_w + pad_x * 2, text_h + pad_y * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        [(0, 0), (canvas.width - 1, canvas.height - 1)],
        radius=8,
        fill=(0, 0, 0, _BG_ALPHA),
    )
    draw.text((pad_x - left, pad_y - top), label.text, font=font, fill=(255, 255, 255, 235))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path)
    return out_path


def burn_label_video(ffmpeg: str, video: Path, label_png: Path, out: Path) -> Path:
    """把角标烤进 MP4。

    `-c:a copy` 保留原音轨，不重新编码音频 —— 少一次有损转换。
    视频必须重编码（overlay 是滤镜），这是无法避免的。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(video),
        "-i",
        str(label_png),
        "-filter_complex",
        f"[0:v][1:v]overlay=W-w-{_LABEL_MARGIN}:H-h-{_LABEL_MARGIN}[v]",
        "-map",
        "[v]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(out),
    ]
    proc = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"标识烧录失败：{proc.stderr.strip()[:300]}")
    return out


def burn_label_gif(gif: Path, label_png: Path, out: Path) -> Path:
    """把角标合成到 GIF 的每一帧。

    逐帧粘贴而不是只标首帧：GIF 会循环播放，只标首帧等于大部分时间没有标识。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(gif) as im, Image.open(label_png) as label:
        frames: list[Image.Image] = []
        durations: list[int] = []
        for i in range(getattr(im, "n_frames", 1)):
            im.seek(i)
            frame = im.convert("RGBA")
            pos = (
                frame.width - label.width - _LABEL_MARGIN,
                frame.height - label.height - _LABEL_MARGIN,
            )
            frame.alpha_composite(label, dest=(max(0, pos[0]), max(0, pos[1])))
            frames.append(frame.convert("P", palette=Image.Palette.ADAPTIVE, colors=200))
            durations.append(int(im.info.get("duration", 125)))

        if not frames:
            return gif
        frames[0].save(
            out,
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            optimize=False,
        )
    return out


def burn_label_gif_ffmpeg(ffmpeg: str, gif: Path, label_png: Path, out: Path) -> Path:
    """用 ffmpeg 给 GIF 打角标。

    为什么优先走 ffmpeg 而不是逐帧 PIL：mock 路径的一条片子能产出几百帧
    GIF，逐帧 `convert("RGBA")` + `alpha_composite` + `convert("P")` 会把
    一次合成拖到几十秒甚至更久 —— 测试套件直接被拖垮，生产上也是白等。
    ffmpeg 的 overlay 是 C 实现，同一个活儿快一到两个数量级。
    PIL 版本保留为 ffmpeg 缺失时的兜底。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(gif),
        "-i",
        str(label_png),
        "-filter_complex",
        f"[0:v][1:v]overlay=W-w-{_LABEL_MARGIN}:H-h-{_LABEL_MARGIN}[v]",
        "-map",
        "[v]",
        "-loop",
        "0",
        str(out),
    ]
    proc = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"GIF 标识烧录失败：{proc.stderr.strip()[:300]}")
    return out


def apply_aigc_label(
    *,
    ffmpeg: str | None,
    artifact: Path,
    out_dir: Path,
    label: AigcLabel,
) -> tuple[Path, dict[str, str]]:
    """给成片打标识并返回 (新产物路径, 隐式标识元数据)。

    任何一步失败都**不阻断交付**：标识失败只记 warning 并返回原产物。
    理由：成片已经花钱生成好了，因为水印失败就丢掉产物是最差的结果；
    但标识失败必须可观测（调用方把 warning 写进结果），不能静默。

    这条策略有合规风险，所以 `PRODUCTION_READINESS.md` 里把它列为
    「上线前必须改为阻断」的项之一。
    """
    content_id = artifact.parent.parent.name if artifact.parent.name == "final" else artifact.stem
    meta = label.metadata(content_id=content_id)

    try:
        label_png = make_label_png(label, out_dir / "_aigc_label.png")
    except Exception as exc:
        return artifact, {**meta, "error": f"生成标识图失败：{exc}"}

    try:
        if artifact.suffix.lower() == ".mp4" and ffmpeg:
            tmp = out_dir / f"{artifact.stem}_labeled{artifact.suffix}"
            burn_label_video(ffmpeg, artifact, label_png, tmp)
            tmp.replace(artifact)
            return artifact, meta
        if artifact.suffix.lower() == ".gif":
            tmp = out_dir / f"{artifact.stem}_labeled{artifact.suffix}"
            if ffmpeg:
                burn_label_gif_ffmpeg(ffmpeg, artifact, label_png, tmp)
            else:
                burn_label_gif(artifact, label_png, tmp)
            tmp.replace(artifact)
            return artifact, meta
    except Exception as exc:
        return artifact, {**meta, "error": f"标识烧录失败：{exc}"}
    finally:
        # 角标图只是中间产物，烧完就删。
        # 留在 final/ 目录里会被当成一个「未知产物」混在交付物中，
        # 虽然产物归集逻辑会忽略它，但目录干净比「靠下游忽略」更可靠。
        # 生成成本极低（一张小 PNG），删掉不影响重试。
        with contextlib.suppress(Exception):
            label_png.unlink(missing_ok=True)

    return artifact, {**meta, "error": "无可用后端（mp4 需要 ffmpeg）"}
