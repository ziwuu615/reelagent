"""AIGC 显式标识：角标生成与烧录。

法规要求「显式标识 + 隐式标识」双做，所以这里既验证画面像素真的被改了，
也验证隐式元数据被写出来。只验证「函数没抛异常」是不够的——水印很可能
画到了画布外面、或者只标了 GIF 的第一帧。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from PIL import Image

from reelagent.config import Settings
from reelagent.render.watermark import (
    AigcLabel,
    apply_aigc_label,
    burn_label_gif,
    burn_label_video,
    make_label_png,
)
from tests.support import cleanup_tmp, make_tmp


def _make_gif(path: Path, *, frames: int = 3, size: tuple[int, int] = (160, 90)) -> Path:
    imgs = [Image.new("RGB", size, (30 + i * 20, 40, 60)) for i in range(frames)]
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=100, loop=0)
    return path


class TestLabelPng(unittest.TestCase):
    def test_generates_visible_pixels(self) -> None:
        tmp = make_tmp("aigc")
        try:
            png = make_label_png(AigcLabel(text="AI 生成"), tmp / "label.png")
            with Image.open(png) as im:
                self.assertEqual(im.mode, "RGBA")
                self.assertGreater(im.width, 10)
                self.assertGreater(im.height, 10)
                alpha = im.getchannel("A")
                # 必须真的画了东西：若全透明说明文字没落上去
                self.assertGreater(max(alpha.getdata()), 0)
        finally:
            cleanup_tmp(tmp)

    def test_custom_text_changes_width(self) -> None:
        tmp = make_tmp("aigc")
        try:
            short = make_label_png(AigcLabel(text="AI"), tmp / "a.png")
            long = make_label_png(AigcLabel(text="人工智能生成合成内容"), tmp / "b.png")
            with Image.open(short) as a, Image.open(long) as b:
                self.assertGreater(b.width, a.width)
        finally:
            cleanup_tmp(tmp)


class TestBurnGif(unittest.TestCase):
    def test_every_frame_is_labeled(self) -> None:
        """GIF 会循环播放，只标首帧等于大部分时间没有标识。"""
        tmp = make_tmp("aigc")
        try:
            gif = _make_gif(tmp / "in.gif", frames=3)
            label = make_label_png(AigcLabel(text="AI 生成"), tmp / "label.png")
            out = burn_label_gif(gif, label, tmp / "out.gif")

            with Image.open(gif) as before, Image.open(out) as after:
                self.assertEqual(getattr(after, "n_frames", 1), getattr(before, "n_frames", 1))
                for i in range(getattr(after, "n_frames", 1)):
                    after.seek(i)
                    frame = after.convert("RGB")
                    # 右下角区域必须与原图不同
                    w, h = frame.size
                    corner = frame.crop((w - 60, h - 40, w, h))
                    self.assertGreater(len(set(corner.getdata())), 1, f"第 {i} 帧角标缺失")
        finally:
            cleanup_tmp(tmp)


class TestApplyLabel(unittest.TestCase):
    def test_gif_gets_metadata_and_label(self) -> None:
        tmp = make_tmp("aigc")
        try:
            project_dir = tmp / "projects" / "proj_x" / "final"
            project_dir.mkdir(parents=True)
            gif = _make_gif(project_dir / "final.gif", frames=2)

            artifact, meta = apply_aigc_label(
                ffmpeg=None, artifact=gif, out_dir=project_dir, label=AigcLabel()
            )

            self.assertTrue(artifact.exists())
            self.assertNotIn("error", meta)
            self.assertEqual(meta["AIGC"], "true")
            self.assertEqual(meta["AIGC-Content-Id"], "proj_x")
            self.assertIn("Standards" if False else "AIGC-Standard", meta)
        finally:
            cleanup_tmp(tmp)

    def test_unsupported_suffix_reports_error_without_raising(self) -> None:
        tmp = make_tmp("aigc")
        try:
            weird = tmp / "final.webm"
            weird.write_bytes(b"x")
            artifact, meta = apply_aigc_label(ffmpeg=None, artifact=weird, out_dir=tmp, label=AigcLabel())
            self.assertEqual(artifact, weird)
            self.assertIn("error", meta)
        finally:
            cleanup_tmp(tmp)


@unittest.skipUnless(Settings.load().ffmpeg, "需要 ffmpeg")
class TestBurnMp4(unittest.TestCase):
    def test_mp4_label_burned(self) -> None:
        import subprocess

        ffmpeg = Settings.load().ffmpeg
        assert ffmpeg is not None
        tmp = make_tmp("aigc")
        try:
            src = tmp / "in.mp4"
            subprocess.run(  # noqa: S603
                [
                    ffmpeg,
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=navy:s=320x240:d=1",
                    "-pix_fmt",
                    "yuv420p",
                    str(src),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=True,
            )
            label = make_label_png(AigcLabel(text="AI 生成"), tmp / "label.png")
            out = burn_label_video(ffmpeg, src, label, tmp / "out.mp4")

            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 0)
            # 用 ffmpeg 出一帧，确认右下角真的不再纯色
            frame = tmp / "frame.png"
            subprocess.run(  # noqa: S603
                [
                    ffmpeg,
                    "-y",
                    "-loglevel",
                    "error",
                    "-sseof",
                    "-0.3",
                    "-i",
                    str(out),
                    "-frames:v",
                    "1",
                    str(frame),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=True,
            )
            with Image.open(frame) as im:
                w, h = im.size
                corner = im.convert("RGB").crop((w - 80, h - 50, w, h))
                self.assertGreater(len(set(corner.getdata())), 1, "画面上没看到角标")
        finally:
            cleanup_tmp(tmp)


if __name__ == "__main__":
    unittest.main()
