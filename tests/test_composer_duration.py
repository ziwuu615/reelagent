"""合成器的**时长语义**测试。

保护一个真实事故：

3 镜 draft 项目，片段合计 8.66 秒，但 TTS 旁白只有 6.40 秒。
`compose_mp4` 里那句 `-shortest` 让输出停在**最短的那条流**上，
于是成片被截到 6.40 秒 —— 丢了 2.3 秒画面，第三镜的收尾整段消失。

而当时：
  - 项目状态 `ready`、镜头 3/3 完成、成本正常记账
  - 没有报错、没有告警、指标全绿
  - 唯一的线索是「成片时长恰好等于音频时长」这种需要主动比对才发现的巧合

期望语义：**成片时长以画面为准，旁白不足就补静音。**
反过来（旁白比画面长）也应以画面为准，不能因为音频长就把画面拖长。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from reelagent.config import resolve_ffmpeg
from reelagent.render.composer import compose_mp4

pytestmark = pytest.mark.skipif(not resolve_ffmpeg(), reason="环境里没有 ffmpeg，跳过合成测试")

FFMPEG = resolve_ffmpeg() or ""


def _run(args: list[str]) -> None:
    subprocess.run(args, check=True, capture_output=True, timeout=180)  # noqa: S603


def _make_clip(path: Path, *, seconds: float, with_audio: bool) -> Path:
    """造一个指定时长的片段。"""
    args = [
        FFMPEG,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c=gray:s=160x120:d={seconds}",
    ]
    if with_audio:
        args += [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:a",
            "aac",
            "-shortest",
        ]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    _run(args)
    return path


def _make_wav(path: Path, *, seconds: float) -> Path:
    """造一个指定时长的旁白音轨。"""
    _run(
        [
            FFMPEG,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=880:duration={seconds}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )
    return path


def _duration(path: Path) -> float:
    out = subprocess.run(  # noqa: S603
        [FFMPEG, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        # 与 composer 侧保持一致：不指定编码时 Windows 会按 locale(GBK) 解码，
        # ffmpeg 的 stderr 含非 GBK 字节会让 reader 线程抛错、stderr 变成 None。
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", out.stderr)
    assert m, f"读不出时长：{out.stderr[:200]}"
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


class TestFootageDurationWins:
    """成片时长以画面为准 —— 这是核心断言。"""

    def test_short_tts_does_not_truncate_footage(self, tmp_workspace: Path) -> None:
        """**这条就是事故的回归测试。**

        画面 2 秒 + 2 秒 = 4 秒，旁白只有 1 秒。
        修复前：成片 1 秒（画面被截掉 3 秒）。
        修复后：成片 ≈ 4 秒，旁白后面的部分补静音。
        """
        clips = [
            _make_clip(tmp_workspace / "c1.mp4", seconds=2, with_audio=False),
            _make_clip(tmp_workspace / "c2.mp4", seconds=2, with_audio=False),
        ]
        wav = _make_wav(tmp_workspace / "short.wav", seconds=1)
        out = tmp_workspace / "short_tts.mp4"

        compose_mp4(ffmpeg=FFMPEG, clips=clips, audio=wav, out_path=out)

        got = _duration(out)
        assert got >= 3.5, (
            f"成片只有 {got:.2f}s，被旁白长度截断了（画面 4s、旁白 1s）。"
            "这正是 -shortest 截断画面的那个缺陷。"
        )
        assert got <= 4.6, f"成片 {got:.2f}s 明显超过画面总长，说明音频把画面拖长了"

    def test_long_tts_does_not_extend_footage(self, tmp_workspace: Path) -> None:
        """反方向：旁白比画面长时，不能把画面拖长（会出黑帧/静帧）。"""
        clips = [_make_clip(tmp_workspace / "c.mp4", seconds=2, with_audio=False)]
        wav = _make_wav(tmp_workspace / "long.wav", seconds=8)
        out = tmp_workspace / "long_tts.mp4"

        compose_mp4(ffmpeg=FFMPEG, clips=clips, audio=wav, out_path=out)

        got = _duration(out)
        assert got <= 2.6, f"成片 {got:.2f}s 超过了画面 2s，音频不该拖长画面"
        assert got >= 1.5, f"成片 {got:.2f}s 短得离谱，画面本身就有 2s"

    def test_mixed_audio_short_tts_keeps_footage(self, tmp_workspace: Path) -> None:
        """原生音轨 + 较短旁白的混音分支，同样不能被截断。"""
        clips = [
            _make_clip(tmp_workspace / "a.mp4", seconds=2, with_audio=True),
            _make_clip(tmp_workspace / "b.mp4", seconds=2, with_audio=True),
        ]
        wav = _make_wav(tmp_workspace / "mix_short.wav", seconds=1)
        out = tmp_workspace / "mix_short.mp4"

        compose_mp4(ffmpeg=FFMPEG, clips=clips, audio=wav, out_path=out)

        got = _duration(out)
        assert got >= 3.5, (
            f"混音分支成片只有 {got:.2f}s，被旁白截断了（画面 4s）。"
            "amix 的 duration=first 需要配合 apad 才能既保画面又保旁白。"
        )

    def test_silent_output_uses_footage_length(self, tmp_workspace: Path) -> None:
        """完全没有音频时，成片时长就是画面时长（作为对照基线）。"""
        clips = [
            _make_clip(tmp_workspace / "x.mp4", seconds=2, with_audio=False),
            _make_clip(tmp_workspace / "y.mp4", seconds=2, with_audio=False),
        ]
        out = tmp_workspace / "silent.mp4"
        compose_mp4(ffmpeg=FFMPEG, clips=clips, audio=None, out_path=out)
        got = _duration(out)
        assert 3.5 <= got <= 4.6, f"静音成片时长 {got:.2f}s 与画面 4s 不符"
