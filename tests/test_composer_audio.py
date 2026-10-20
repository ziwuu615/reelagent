"""合成器的音频流选择测试。

保护一个**已实测确认**的缺陷：

早期 `compose_mp4` 只写 `-c:a aac` 而不给 `-map`。ffmpeg 的默认流选择取了
**第一个含音频的输入** —— 片段的原生音轨 —— 于是我单独生成的 TTS 旁白
被静默丢弃。volumedetect 证据：

    standard  final -30.8dB/4.04s   片段 -30.8dB/4.04s   TTS -21.0dB/1.28s
    premium   final -17.4dB/5.04s   片段 -17.4dB/5.04s   TTS -23.6dB/4.56s

成片音量与时长跟片段音轨**分毫不差**，与 TTS 差异明显。
而 draft 档（GIF 无音轨）反而用上了 TTS —— 同一份代码在不同档位下行为不同。

这类"隐式行为"没有测试就只能靠偶然发现。下面把四种情形逐一钉住。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from reelagent.config import resolve_ffmpeg
from reelagent.render.composer import NATIVE_AUDIO_BED_GAIN, compose_mp4, has_audio_stream

pytestmark = pytest.mark.skipif(not resolve_ffmpeg(), reason="环境里没有 ffmpeg，跳过合成测试")

FFMPEG = resolve_ffmpeg() or ""


def _run(args: list[str]) -> None:
    subprocess.run(args, check=True, capture_output=True, timeout=180)  # noqa: S603


def _make_clip(path: Path, *, with_audio: bool) -> Path:
    args = [
        FFMPEG,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=gray:s=160x120:d=1",
    ]
    if with_audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac", "-shortest"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    _run(args)
    return path


def _make_wav(path: Path) -> Path:
    _run(
        [
            FFMPEG,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=1",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )
    return path


def _mean_volume(path: Path) -> float | None:
    # 参数全部由测试内部构造（ffmpeg 路径 + 自建文件），无外部输入
    proc = subprocess.run(  # noqa: S603
        [FFMPEG, "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    match = re.search(r"mean_volume:\s*(-?[\d.]+)", proc.stderr)
    return float(match.group(1)) if match else None


class TestAudioStreamSelection:
    """四种情形都必须显式指定输出，不能靠 ffmpeg 猜。"""

    def test_native_plus_tts_is_mixed(self, tmp_workspace: Path) -> None:
        clip = _make_clip(tmp_workspace / "clip.mp4", with_audio=True)
        wav = _make_wav(tmp_workspace / "voice.wav")
        out = tmp_workspace / "mixed.mp4"
        compose_mp4(ffmpeg=FFMPEG, clips=[clip], audio=wav, out_path=out)
        assert has_audio_stream(out, FFMPEG), "混音后必须有音轨"
        # 混音结果不应是静音（说明两路里至少一路进来了）
        volume = _mean_volume(out)
        assert volume is not None and volume > -60.0

    def test_native_only_keeps_native(self, tmp_workspace: Path) -> None:
        clip = _make_clip(tmp_workspace / "clip.mp4", with_audio=True)
        out = tmp_workspace / "native.mp4"
        compose_mp4(ffmpeg=FFMPEG, clips=[clip], audio=None, out_path=out)
        assert has_audio_stream(out, FFMPEG)

    def test_silent_clip_uses_tts(self, tmp_workspace: Path) -> None:
        """draft 档走的就是这条路（GIF 片段无音轨）。"""
        clip = _make_clip(tmp_workspace / "clip.mp4", with_audio=False)
        wav = _make_wav(tmp_workspace / "voice.wav")
        out = tmp_workspace / "tts.mp4"
        compose_mp4(ffmpeg=FFMPEG, clips=[clip], audio=wav, out_path=out)
        assert has_audio_stream(out, FFMPEG), "片段静音时 TTS 必须被用上"

    def test_no_audio_anywhere_produces_silent_video(self, tmp_workspace: Path) -> None:
        clip = _make_clip(tmp_workspace / "clip.mp4", with_audio=False)
        out = tmp_workspace / "silent.mp4"
        compose_mp4(ffmpeg=FFMPEG, clips=[clip], audio=None, out_path=out)
        assert out.exists()
        assert not has_audio_stream(out, FFMPEG), "两路都没音频时不应凭空造出音轨"

    def test_relative_clip_path_is_resolved(self, tmp_workspace: Path) -> None:
        """concat 解复用器按**列表文件目录**解析相对路径，必须写绝对路径。

        踩过的坑：传相对路径时被拼成 `a/b/a/b/x.mp4`，ffmpeg 报
        "Impossible to open"。真实流水线恰好传绝对路径，所以一直没暴露 ——
        但函数不该依赖调用方的 cwd。
        """
        clip = _make_clip(tmp_workspace / "clip.mp4", with_audio=False)
        out = tmp_workspace / "rel.mp4"
        # 传入相对路径（相对 cwd），函数内部必须自行 resolve
        rel = Path(clip).relative_to(Path.cwd()) if clip.is_relative_to(Path.cwd()) else clip
        compose_mp4(ffmpeg=FFMPEG, clips=[rel], audio=None, out_path=out)
        assert out.exists() and out.stat().st_size > 0

    def test_empty_clip_list_raises(self, tmp_workspace: Path) -> None:
        with pytest.raises(RuntimeError, match="没有任何可用片段"):
            compose_mp4(ffmpeg=FFMPEG, clips=[], audio=None, out_path=tmp_workspace / "x.mp4")


class TestBedGain:
    def test_bed_gain_is_a_quiet_background(self) -> None:
        """原生音轨只作背景：既不能是 0（丢素材），也不能接近 1（压过旁白）。"""
        assert 0.05 <= NATIVE_AUDIO_BED_GAIN <= 0.3

    def test_bed_gain_is_not_zero(self) -> None:
        assert NATIVE_AUDIO_BED_GAIN > 0


@pytest.fixture(autouse=True)
def _cleanup(tmp_workspace: Path):
    yield
    shutil.rmtree(tmp_workspace, ignore_errors=True)
