"""成片合成。

优先使用 ffmpeg 输出 MP4（含音轨）；检测不到 ffmpeg 时自动降级为
**GIF 成片 + WAV 音轨 + 分镜九宫格 + manifest**，仍然交付一个可看的成片。

设计要点："构建面向影视制作的 AIGC 管线"、"FFmpeg 合成"、"导出剪映草稿"。
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from ..schema import Project, Shot


class RenderError(RuntimeError):
    """合成阶段的致命错误。

    单独定义类型是为了让上层能区分「合成失败」与「其他失败」：
    合成失败时绝不能静默产出旧文件，必须让调用方看到。
    """


# --------------------------------------------------------------------------- #
# WAV
# --------------------------------------------------------------------------- #
def concat_wavs(paths: list[Path], out_path: Path) -> Path | None:
    existing = [p for p in paths if p.exists()]
    if not existing:
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(existing[0]), "rb") as first:
        params = first.getparams()
        frames = [first.readframes(first.getnframes())]
    for p in existing[1:]:
        with contextlib.suppress(Exception), wave.open(str(p), "rb") as wf:
            if (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) != (
                params.nchannels,
                params.sampwidth,
                params.framerate,
            ):
                continue
            frames.append(wf.readframes(wf.getnframes()))
    with wave.open(str(out_path), "wb") as out:
        out.setnchannels(params.nchannels)
        out.setsampwidth(params.sampwidth)
        out.setframerate(params.framerate)
        for f in frames:
            out.writeframes(f)
    return out_path


# --------------------------------------------------------------------------- #
# GIF
# --------------------------------------------------------------------------- #
def concat_gifs(paths: list[Path], out_path: Path, *, max_frames: int = 400) -> Path | None:
    """把多个 GIF 片段顺序拼成一个 GIF。

    **读不出来的片段必须报错，不能静默跳过。**

    踩坑记录：这里原本对每个片段用 `contextlib.suppress(Exception)` 包住
    `Image.open()`。当片段其实是 MP4（扩展名被写成了 .gif）时，打开全部失败、
    `all_frames` 为空、函数返回 None，而调用方只是「什么都没生成」——
    于是 final.gif 保持上一次的旧文件，系统却报告成功、指标也是新算的。
    这种「产物与数据不一致且在界面上看不出来」的失败，比直接抛错危险得多。
    """
    existing = [p for p in paths if p.exists()]
    if not existing:
        return None

    all_frames: list[Image.Image] = []
    durations: list[int] = []
    unreadable: list[str] = []

    for p in existing:
        try:
            with Image.open(p) as im:
                n = getattr(im, "n_frames", 1)
                for i in range(n):
                    im.seek(i)
                    all_frames.append(im.convert("P", palette=Image.Palette.ADAPTIVE, colors=200).copy())
                    durations.append(int(im.info.get("duration", 125)))
        except Exception as exc:
            unreadable.append(f"{p.name}（{type(exc).__name__}）")
            continue
        if len(all_frames) >= max_frames:
            all_frames = all_frames[:max_frames]
            durations = durations[:max_frames]
            break

    if unreadable:
        raise RenderError(
            f"有 {len(unreadable)} 个片段无法解码为图像：{', '.join(unreadable[:3])}。"
            "常见原因是片段实际是 MP4 但扩展名写成了 .gif，"
            "或缺少解码依赖。拒绝静默产出不完整的成片。"
        )
    if not all_frames:
        raise RenderError(f"所有 {len(existing)} 个片段都没能读出任何帧，拒绝生成空成片。")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    all_frames[0].save(
        out_path,
        save_all=True,
        append_images=all_frames[1:],
        duration=durations or 125,
        loop=0,
        optimize=True,
    )
    return out_path


# --------------------------------------------------------------------------- #
# 分镜九宫格（作品集里最好用的一张图）
# --------------------------------------------------------------------------- #
def storyboard_sheet(
    shots: list[Shot],
    workspace: Path,
    out_path: Path,
    *,
    cols: int = 3,
    cell_w: int = 320,
) -> Path | None:
    items = [s for s in shots if s.keyframe_path and (workspace / s.keyframe_path).exists()]
    if not items:
        return None

    rows = (len(items) + cols - 1) // cols
    first_keyframe = items[0].keyframe_path
    assert first_keyframe is not None
    with Image.open(workspace / first_keyframe) as im0:
        ratio = im0.height / max(im0.width, 1)
    cell_h = int(cell_w * ratio)
    pad, header = 10, 26
    sheet_w = cols * cell_w + (cols + 1) * pad
    sheet_h = rows * (cell_h + header) + (rows + 1) * pad

    sheet = Image.new("RGB", (sheet_w, sheet_h), (14, 16, 22))
    draw = ImageDraw.Draw(sheet)
    from ..providers.mock import _font, _supports_cjk  # 复用字体探测

    label_font = _font(14) if _supports_cjk() else None

    for idx, shot in enumerate(items):
        r, c = divmod(idx, cols)
        x = pad + c * (cell_w + pad)
        y = pad + r * (cell_h + header + pad)
        with contextlib.suppress(Exception):
            assert shot.keyframe_path is not None
            with Image.open(workspace / shot.keyframe_path) as im:
                tile = im.convert("RGB").resize((cell_w, cell_h), Image.Resampling.LANCZOS)
            sheet.paste(tile, (x, y))
        if label_font:
            label = f"{shot.id}  {shot.shot_size}/{shot.camera}  {shot.duration_s}s"
            draw.text((x, y + cell_h + 4), label, font=label_font, fill=(210, 214, 222))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, "PNG")
    return out_path


# --------------------------------------------------------------------------- #
# ffmpeg
# --------------------------------------------------------------------------- #
def ffmpeg_available(ffmpeg: str | None) -> bool:
    return bool(ffmpeg and (Path(ffmpeg).exists() or shutil.which(ffmpeg)))


#: 原生音轨作为背景时的音量系数。
#:
#: 0.12 ≈ -18dB。选这个值的理由：
#:   - 不能是 0（那等于丢掉模型生成的 BGM/音效）
#:   - 不能接近 1（会和旁白同样响，人声被淹没、整体发闷）
#:   - -18dB 是常见的"背景铺底"区间，旁白仍能清晰居于前景
#:
#: ⚠️ 这个值是**按声学惯例选的，不是试听调出来的** —— 我无法听音频。
#: 它的效果需要用实际听感确认；这里只保证行为确定、音量可测量。
NATIVE_AUDIO_BED_GAIN = 0.12


def has_audio_stream(path: Path, ffmpeg: str) -> bool:
    """探测文件是否含音频流。

    必须显式探测，而不是"猜" —— 片段是否带音轨取决于供应商与模型
    （万相原生有声，本地合成的 GIF 则没有），同一个项目内也可能混合。
    """
    proc = subprocess.run(  # noqa: S603
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return "Audio:" in proc.stderr


def compose_mp4(
    *,
    ffmpeg: str,
    clips: list[Path],
    audio: Path | None,
    out_path: Path,
    fps: int = 25,
) -> Path:
    """用 ffmpeg 把片段（GIF/MP4）与音轨合成 MP4。

    ## 音频策略（显式，不依赖 ffmpeg 的隐式流选择）

    早期实现只写 `-c:a aac` 而不给 `-map`。ffmpeg 的默认流选择会取
    **第一个含音频的输入** —— 也就是片段的原生音轨，导致我单独生成的
    TTS 旁白被**静默丢弃**。实测证据（volumedetect）：

        standard  final -30.8dB/4.04s   片段 -30.8dB/4.04s    TTS -21.0dB/1.28s
        premium   final -17.4dB/5.04s   片段 -17.4dB/5.04s    TTS -23.6dB/4.56s

    成片的音量与时长跟片段音轨**分毫不差**，说明用的不是 TTS。
    而 draft 档（GIF 无音轨）反而用上了 TTS —— 同一份代码在不同档位下
    行为不同，只因片段的音轨有无不同。这种"隐式行为"必须消灭。

    现在的四种情形都显式指定输出：

        片段有音轨 + 有 TTS -> 混音：TTS 前景 + 原生音轨降为背景
        片段有音轨 + 无 TTS -> 用原生音轨
        片段无音轨 + 有 TTS -> 用 TTS
        两者都无           -> 不带音轨
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = out_path.parent / "_concat.txt"
    existing = [c for c in clips if c.exists()]
    if not existing:
        raise RuntimeError("合成失败：没有任何可用片段")
    # 必须写**绝对路径**：concat 解复用器会把相对路径解析成相对
    # **列表文件所在目录**，而不是当前工作目录 —— 传入相对路径时会拼成
    # `a/b/a/b/x.mp4` 这种不存在的路径。真实流水线恰好传的是绝对路径，
    # 所以这个坑一直没暴露；改成绝对路径后函数不再依赖调用方的 cwd。
    with list_file.open("w", encoding="utf-8") as fh:
        for c in existing:
            fh.write(f"file '{c.resolve().as_posix()}'\n")

    has_tts = bool(audio and audio.exists())
    has_native = has_audio_stream(existing[0], ffmpeg)

    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
    ]
    if has_tts:
        cmd += ["-i", str(audio)]

    if has_native and has_tts:
        # 混音：原生音轨降到背景，TTS 保持原音量。
        #   normalize=0  保留我们设定的权重（amix 默认会自动归一化，把两路都压低）
        #   duration=first 以片段总长为基准，避免旁白较短时成片被截断
        # audio 此处必然非 None（has_tts 为真），显式确认以免类型歧义。
        assert audio is not None
        cmd += [
            "-filter_complex",
            (
                f"[0:a]volume={NATIVE_AUDIO_BED_GAIN}[bed];"
                "[1:a]volume=1.0[voice];"
                "[bed][voice]amix=inputs=2:duration=first:normalize=0[mixed]"
            ),
            "-map",
            "0:v",
            "-map",
            "[mixed]",
        ]
    elif has_native:
        cmd += ["-map", "0:v", "-map", "0:a"]
    elif has_tts:
        cmd += ["-map", "0:v", "-map", "1:a"]
    else:
        cmd += ["-map", "0:v"]

    if has_native or has_tts:
        cmd += ["-c:a", "aac", "-shortest"]

    cmd += [
        "-vf",
        f"fps={fps},scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    # cmd 是我们自己拼的参数列表（不经 shell），路径来自受控工作区，
    # 不存在命令注入面；S603 在此为误报。
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)  # noqa: S603
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 合成失败：{proc.stderr.strip()[:500]}")
    return out_path


# --------------------------------------------------------------------------- #
# 对外入口
# --------------------------------------------------------------------------- #
def compose(project: Project, settings, *, out_dir: Path | None = None) -> dict[str, Any]:
    """合成最终成片。返回产物清单。"""
    assert project.script is not None
    ws = settings.workspace
    out_dir = out_dir or (settings.project_dir(project.id) / "final")
    out_dir.mkdir(parents=True, exist_ok=True)

    shots = project.script.shots
    clips = [ws / s.clip_path for s in shots if s.clip_path and (ws / s.clip_path).exists()]
    audios = [ws / s.audio_path for s in shots if s.audio_path and (ws / s.audio_path).exists()]

    result: dict[str, Any] = {
        "clips": len(clips),
        "audios": len(audios),
        "engine": "gif",
        "warnings": [],
    }

    # 音轨
    audio_out = concat_wavs(audios, out_dir / "audio.wav") if audios else None
    if audio_out:
        project.final_audio = settings.rel(audio_out)
        result["audio"] = project.final_audio

    # 分镜九宫格
    sheet = storyboard_sheet(shots, ws, out_dir / "storyboard.png")
    if sheet:
        result["storyboard"] = settings.rel(sheet)

    # 视频
    if ffmpeg_available(settings.ffmpeg) and clips:
        try:
            mp4 = compose_mp4(
                ffmpeg=settings.ffmpeg or "ffmpeg",
                clips=clips,
                audio=audio_out,
                out_path=out_dir / "final.mp4",
            )
            project.final_video = settings.rel(mp4)
            result["engine"] = "ffmpeg"
            result["video"] = project.final_video
        except Exception as exc:
            result["warnings"].append(f"ffmpeg 合成失败，已降级为 GIF：{exc}")
    elif clips:
        result["warnings"].append(
            "未检测到 ffmpeg，已降级输出 GIF 成片（设置 REEL_FFMPEG 或安装 ffmpeg 可输出 MP4）"
        )

    if not project.final_video and clips:
        gif = concat_gifs(clips, out_dir / "final.gif")
        if gif:
            project.final_gif = settings.rel(gif)
            result["gif"] = project.final_gif

    # manifest
    manifest = {
        "project_id": project.id,
        "title": project.script.title,
        "logline": project.script.logline,
        "style": project.style,
        "aspect_ratio": project.aspect_ratio,
        "total_duration_s": project.script.total_duration_s,
        "shots": [
            {
                "id": s.id,
                "scene": s.scene_id,
                "shot_size": s.shot_size,
                "camera": s.camera,
                "duration_s": s.duration_s,
                "action": s.action,
                "narration": s.narration,
                "dialogue": s.dialogue,
                "status": s.status,
                "keyframe": s.keyframe_path,
                "clip": s.clip_path,
                "audio": s.audio_path,
                "provider": s.provider_used,
                "model": s.model_used,
                "cost_usd": round(s.cost_usd, 5),
            }
            for s in shots
        ],
        "clues": [
            {"name": c.name, "kind": c.kind, "appearance": c.appearance, "base_image": c.base_image}
            for c in project.clues
        ],
        "outputs": {k: v for k, v in result.items() if isinstance(v, str)},
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result["manifest"] = settings.rel(out_dir / "manifest.json")
    return result
