"""离线 mock 供应商。

关键设计：**产出真实可播放的媒体文件**，而不是空占位。
- MockImageProvider  -> 用 Pillow 合成一张带风格配色的关键帧 PNG
- MockVideoProvider  -> 对关键帧做 Ken Burns 运动，输出真正的 GIF 动图
- MockTTSProvider    -> 输出时长与文本匹配的真实 WAV 音频

这样整条流水线在零 API Key 的情况下也能交付一个「能看的成片」。
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import struct
import time
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .base import (
    ImageProvider,
    MediaResult,
    TTSProvider,
    VideoProvider,
)

# --------------------------------------------------------------------------- #
# 中文字体探测
# --------------------------------------------------------------------------- #
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def _find_font() -> str | None:
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


_FONT_PATH = _find_font()


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if _FONT_PATH:
        with contextlib.suppress(Exception):
            return ImageFont.truetype(_FONT_PATH, size)
    return ImageFont.load_default()


def _supports_cjk() -> bool:
    return _FONT_PATH is not None


def _line_height(font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> int:
    """字体行高。

    用 `getbbox` 而不是 `font.size`：`ImageFont.load_default()` 返回的
    位图字体没有 `size` 属性，直接访问会 AttributeError。
    """
    try:
        bbox = font.getbbox("字Ag")
        if bbox:
            return max(1, int(bbox[3] - bbox[1]))
    except Exception:  # noqa: S110 - 极端字体下退化为固定行高即可，无需中断渲染
        pass
    return 14


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _seed_of(text: str, salt: int = 0) -> int:
    # usedforsecurity=False：这里只是要做「跨进程稳定」的哈希，不是安全用途。
    # 显式声明可以避免在 FIPS 模式下被拒绝，也让安全扫描知道这是有意为之。
    return int(hashlib.md5(f"{text}|{salt}".encode(), usedforsecurity=False).hexdigest()[:8], 16)


def _palette_from(palette: list[tuple[int, int, int]], seed: int) -> list[tuple[int, int, int]]:
    if not palette:
        palette = [(20, 20, 28), (70, 80, 110), (160, 170, 190), (240, 236, 228)]
    shift = seed % len(palette)
    rotated = palette[shift:] + palette[:shift]
    return rotated


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _wrap(text: str, width: int) -> list[str]:
    """按字符数粗暴折行（中英混排够用）。"""
    lines: list[str] = []
    cur = ""
    for ch in text:
        cur += ch
        if len(cur) >= width:
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    return lines[:12]


def _gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGB", (1, h))
    px = img.load()
    if px is None:  # pragma: no cover - PIL 类型标注如此，运行时不会为 None
        raise RuntimeError("无法获取像素缓冲")
    for y in range(h):
        px[0, y] = _lerp(top, bottom, y / max(h - 1, 1))
    return img.resize((w, h), Image.Resampling.BILINEAR)


# --------------------------------------------------------------------------- #
# 图像
# --------------------------------------------------------------------------- #
class MockImageProvider(ImageProvider):
    name = "mock"
    model = "mock-canvas-v1"

    def __init__(self, palette: list[tuple[int, int, int]] | None = None, *, style_label: str = "") -> None:
        self.palette = palette or []
        self.style_label = style_label

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
        out_path.parent.mkdir(parents=True, exist_ok=True)
        s = seed or _seed_of(prompt)
        pal = _palette_from(self.palette, s % 4)

        img = _gradient(size, pal[0], pal[1])
        w, h = size

        # 背景几何体，制造层次
        bg_draw = ImageDraw.Draw(img, "RGBA")
        for i in range(3):
            cx = int(w * (0.2 + 0.3 * ((s >> (i * 3)) % 3)))
            cy = int(h * (0.25 + 0.25 * ((s >> (i * 2 + 1)) % 3)))
            r = int(min(w, h) * (0.22 + 0.08 * i))
            col = pal[min(2 + i, len(pal) - 1)]
            bg_draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*col, 46))
        # 注意：filter() 返回的是新对象，因此下面必须重新创建 draw。
        # 早期版本在这里踩过坑：draw 仍绑定在被丢弃的旧缓冲区上，
        # 导致之后所有的文字与色板都被画进了不存在的图像里。
        img = img.filter(ImageFilter.GaussianBlur(radius=max(2, min(w, h) // 90)))

        # 参考图（线索基准图）—— 一致性策略的可见证据
        if ref_images:
            for i, ref in enumerate(ref_images[:2]):
                with contextlib.suppress(Exception):
                    with Image.open(ref) as handle:
                        rim: Image.Image = handle.convert("RGB")
                        tile_h = int(h * 0.30)
                        tile_w = int(tile_h * rim.width / max(rim.height, 1))
                        rim = rim.resize((tile_w, tile_h), Image.Resampling.LANCZOS)
                    x = 16 + i * (tile_w + 12)
                    y = h - tile_h - 16
                    img.paste(rim, (x, y))

        # 重新绑定到最终的图像对象
        draw = ImageDraw.Draw(img, "RGBA")

        # 参考图边框
        if ref_images:
            for i, ref in enumerate(ref_images[:2]):
                with contextlib.suppress(Exception):
                    with Image.open(ref) as handle2:
                        tile_h = int(h * 0.30)
                        tile_w = int(tile_h * handle2.width / max(handle2.height, 1))
                        tile_w = int(tile_h * rim.width / max(rim.height, 1))
                    x = 16 + i * (tile_w + 12)
                    y = h - tile_h - 16
                    draw.rectangle(
                        [x - 2, y - 2, x + tile_w + 1, y + tile_h + 1],
                        outline=(255, 255, 255, 200),
                        width=2,
                    )

        # 文字
        if _supports_cjk():
            title_f = _font(max(18, w // 22))
            body_f = _font(max(13, w // 40))
            draw.rectangle([0, 0, w, int(h * 0.26)], fill=(0, 0, 0, 120))
            head = f"{self.style_label}" if self.style_label else "KEYFRAME"
            draw.text((20, 16), head, font=title_f, fill=(255, 255, 255, 235))
            y = 18 + max(20, w // 22)
            for line in _wrap(prompt, max(14, w // 16))[:6]:
                draw.text((20, y), line, font=body_f, fill=(238, 238, 238, 225))
                y += _line_height(body_f) + 6

        # 底部色板条，标示风格配色
        bar_h = 10
        seg = w // max(len(pal), 1)
        for i, c in enumerate(pal):
            draw.rectangle([i * seg, h - bar_h, (i + 1) * seg, h], fill=c)

        img.save(out_path, "PNG")
        elapsed = (time.perf_counter() - started) * 1000
        return MediaResult(
            path=out_path,
            provider=self.name,
            model=self.model,
            cost_usd=0.0,
            latency_ms=elapsed,
            meta={"size": size, "refs": len(ref_images or [])},
        )


# --------------------------------------------------------------------------- #
# 视频（输出 GIF）
# --------------------------------------------------------------------------- #
class MockVideoProvider(VideoProvider):
    #: 离线本地合成在无 ffmpeg 时输出 GIF；有 ffmpeg 时也能写 MP4。
    #: 这里声明 GIF，因为它是**永远可用**的那个格式（不依赖外部二进制）。
    output_suffix = "gif"

    """用 Ken Burns 运动把关键帧变成真实 GIF 动图。"""

    name = "mock"
    model = "mock-motion-v1"

    def __init__(self, *, fps: int = 8, max_frames: int = 32) -> None:
        self.fps = fps
        self.max_frames = max_frames

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
        out_path.parent.mkdir(parents=True, exist_ok=True)
        s = seed or _seed_of(prompt)

        # 本地运动合成依赖关键帧作为首帧。若调用方没给（例如视频供应商不支持
        # 图生视频而流水线降级），就按提示词合成一张底板，保证离线路径始终可用。
        if keyframe is not None and keyframe.is_file():
            source: Path = keyframe
        else:
            source = (
                MockImageProvider()
                .generate(
                    prompt=prompt,
                    out_path=out_path.with_suffix(".keyframe.png"),
                    size=size,
                    seed=s,
                )
                .path
            )

        with Image.open(source) as im:
            base: Image.Image = im.convert("RGB")

        if base.size != size:
            base = base.resize(size, Image.Resampling.LANCZOS)

        n = max(4, min(self.max_frames, round(duration_s * self.fps)))
        w, h = size
        frames: list[Image.Image] = []

        # 运动模式：推近 / 拉远 / 横移 / 静止微动
        mode = ["in", "out", "pan", "still"][s % 4]
        zoom0, zoom1 = (1.0, 1.14) if mode == "in" else (1.14, 1.0) if mode == "out" else (1.06, 1.06)
        pan = 0.06 if mode == "pan" else 0.0

        for i in range(n):
            t = i / max(n - 1, 1)
            z = zoom0 + (zoom1 - zoom0) * t
            cw, ch = int(w / z), int(h / z)
            cx = int((w - cw) / 2 + (pan * w * (t - 0.5)))
            cy = int((h - ch) / 2)
            cx = max(0, min(w - cw, cx))
            frame = base.crop((cx, cy, cx + cw, cy + ch)).resize((w, h), Image.Resampling.LANCZOS)

            # 轻微亮度呼吸，让动图更明显
            if mode == "still":
                factor = 1.0 + 0.05 * math.sin(t * math.pi * 2)
                frame = frame.point(lambda p, f=factor: min(255, int(p * f)))

            frames.append(frame)

        # GIF 调色板量化
        pal_frames = [f.convert("P", palette=Image.Palette.ADAPTIVE, colors=200) for f in frames]
        pal_frames[0].save(
            out_path,
            save_all=True,
            append_images=pal_frames[1:],
            duration=int(1000 / self.fps),
            loop=0,
            optimize=True,
        )
        elapsed = (time.perf_counter() - started) * 1000
        return MediaResult(
            path=out_path,
            provider=self.name,
            model=self.model,
            cost_usd=0.0,
            latency_ms=elapsed,
            meta={"frames": n, "fps": self.fps, "motion": mode},
        )


# --------------------------------------------------------------------------- #
# TTS（输出 WAV）
# --------------------------------------------------------------------------- #
class MockTTSProvider(TTSProvider):
    """生成与文本时长匹配的真实 WAV。

    不是静音：用带包络的共振峰式音调模拟语流节奏，
    这样合成出来的成片时长/节奏是可听、可验证的。
    """

    name = "mock"
    model = "mock-voice-v1"

    def __init__(self, *, sample_rate: int = 22050, chars_per_sec: float = 5.0) -> None:
        self.sample_rate = sample_rate
        self.chars_per_sec = chars_per_sec

    def generate(self, *, text: str, out_path: Path, voice: str = "") -> MediaResult:
        started = time.perf_counter()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        text = (text or "").strip()
        duration = max(0.8, len(text) / self.chars_per_sec) if text else 0.8
        sr = self.sample_rate
        total = int(duration * sr)

        s = _seed_of(text or "silence", len(text))
        base_freq = 120 + (s % 60)  # 基频，模拟不同音色
        syl = int(sr * 0.18)  # 音节长度

        frames = bytearray()
        for i in range(total):
            t = i / sr
            pos_in_syl = (i % syl) / syl
            # 音节包络：起音快、衰减慢
            env = math.exp(-3.0 * pos_in_syl) * (1 - math.exp(-40 * pos_in_syl))
            f0 = base_freq * (1.0 + 0.08 * math.sin(2 * math.pi * 0.7 * t))
            val = (
                0.60 * math.sin(2 * math.pi * f0 * t)
                + 0.25 * math.sin(2 * math.pi * 2 * f0 * t)
                + 0.15 * math.sin(2 * math.pi * 3 * f0 * t)
            )
            sample = int(max(-1.0, min(1.0, val * env * 0.35)) * 32767)
            frames += struct.pack("<h", sample)

        with wave.open(str(out_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(bytes(frames))

        elapsed = (time.perf_counter() - started) * 1000
        return MediaResult(
            path=out_path,
            provider=self.name,
            model=self.model,
            cost_usd=0.0,
            latency_ms=elapsed,
            meta={"duration_s": round(duration, 2), "chars": len(text)},
        )
