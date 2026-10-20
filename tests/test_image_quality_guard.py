"""图像退化检测的测试。

**这些用例保护的是一个真实事故**：某次生产运行里，图像供应商对其中一镜
返回了**纯黑图**（标准差 0.0、全图只有 1 种颜色），流水线照单全收：

  - 关键帧落盘成功、镜头状态 `audio_done`、项目状态 `ready`、3/3 完成
  - 下游 Ken Burns 只能从纯色图裁出完全相同的帧 → **单帧 GIF**（1.4 KB）
  - 另两镜的 GIF 是 4.6 MB / 3.6 MB，大小差异是当时唯一的线索
  - 成片里那一段是黑屏，但所有指标都是绿的

所以这些断言不是"锦上添花"，它们对应的是一个会静默产出废片的路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from reelagent.harness.registry import ToolError
from reelagent.tools.media_tools import _assert_image_not_degenerate


def _solid(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (120, 200)) -> Path:
    """生成一张纯色图 —— 模拟供应商返回的空白产物。"""
    Image.new("RGB", size, color).save(path)
    return path


def _detailed(path: Path, size: tuple[int, int] = (120, 200)) -> Path:
    """生成一张有内容的图：横向渐变 + 噪点，代表正常产物。"""
    im = Image.new("RGB", size)
    px = im.load()
    assert px is not None
    for y in range(size[1]):
        for x in range(size[0]):
            # 渐变让相邻像素不同；加入确定性"噪点"制造足够的标准差
            px[x, y] = ((x * 2) % 256, (y * 3) % 256, ((x + y) * 5) % 256)
    im.save(path)
    return path


class TestDegenerateImagesAreRejected:
    """纯色图必须被拦下，交给重试机制，而不是当成有效产物。"""

    @pytest.mark.parametrize(
        ("color", "name"),
        [
            ((0, 0, 0), "纯黑"),
            ((255, 255, 255), "纯白"),
            ((128, 128, 128), "纯灰"),
            ((0, 0, 255), "纯蓝"),
        ],
    )
    def test_solid_color_rejected(self, tmp_workspace: Path, color: tuple[int, int, int], name: str) -> None:
        path = _solid(tmp_workspace / f"solid_{color[0]}_{color[1]}_{color[2]}.png", color)
        with pytest.raises(ToolError, match="退化图像"):
            _assert_image_not_degenerate(path, what=name)

    def test_error_message_is_actionable(self, tmp_workspace: Path) -> None:
        """报错信息必须能让人一眼看出问题，而不是只说"失败了"。"""
        path = _solid(tmp_workspace / "black.png", (0, 0, 0))
        with pytest.raises(ToolError) as exc:
            _assert_image_not_degenerate(path, what="关键帧（sc01_sh02）")

        msg = str(exc.value)
        assert "sc01_sh02" in msg, "报错要带上出问题的镜头，否则无从定位"
        assert "标准差" in msg and "唯一色" in msg, "要给出判定依据，而不只是结论"
        assert "重试" in msg


class TestNormalImagesPass:
    """正常图片不能被误杀 —— 否则这个校验会让整条流水线跑不动。"""

    def test_gradient_with_noise_passes(self, tmp_workspace: Path) -> None:
        path = _detailed(tmp_workspace / "detail.png")
        _assert_image_not_degenerate(path, what="正常图")  # 不抛异常即通过

    def test_low_contrast_but_real_image_passes(self, tmp_workspace: Path) -> None:
        """低对比度的真实图（例如几乎全白的雾景）不应被判为退化。

        阈值取 2.0 就是为了这个：真实照片哪怕平淡，标准差通常也在 15 以上，
        极端平坦的雾景也有 2-4。

        这里的构造刻意贴近**真实 AI 输出**：颜色数量是成百上千级，
        只是整体对比度低。之前我用一张只有 2 种颜色的合成图来测，
        那是把自己造的边界情况当成了生产现实 —— 纯色判据本来就该拦它。
        """
        import math

        from PIL import ImageStat

        size = (160, 240)
        im = Image.new("RGB", size)
        px = im.load()
        assert px is not None
        max_r = math.hypot(size[0] / 2, size[1] / 2)
        for y in range(size[1]):
            for x in range(size[0]):
                r = math.hypot(x - size[0] / 2, y - size[1] / 2)
                v = int(250 - 14 * (r / max_r))
                px[x, y] = (v, v, max(0, v - 1))
        path = tmp_workspace / "fog.png"
        im.save(path)

        # 先确认这张图确实"颜色很多但对比度低"，否则这个用例就失去意义
        with Image.open(path) as chk:
            rgb = chk.convert("RGB")
            stddev = sum(ImageStat.Stat(rgb).stddev) / 3.0
            colors = len(rgb.getcolors(maxcolors=1 << 20) or [])
        assert colors > 5, f"构造的图颜色太少（{colors}），测不出想要的场景"
        assert stddev > 2.0, f"构造的图对比度太低（std={stddev:.2f}），会被误判"

        _assert_image_not_degenerate(path, what="雾景")  # 不抛异常即通过


class TestUnreadableFileIsRejected:
    def test_missing_file_raises(self, tmp_workspace: Path) -> None:
        with pytest.raises(ToolError, match="无法读取"):
            _assert_image_not_degenerate(tmp_workspace / "nope.png", what="缺失文件")

    def test_corrupt_file_raises(self, tmp_workspace: Path) -> None:
        """供应商偶尔会返回 0 字节或截断的文件，同样不能放过。"""
        path = tmp_workspace / "corrupt.png"
        path.write_bytes(b"not really a png")
        with pytest.raises(ToolError):
            _assert_image_not_degenerate(path, what="损坏文件")
