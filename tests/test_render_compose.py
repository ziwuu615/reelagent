"""合成阶段的回归测试。

这里保护的是**静默失败**类问题 —— 它们不会报错，只会产出错误的结果：

    1. 片段扩展名与内容不符（磁盘上是 MP4，名字是 .gif），
       合成阶段读不出来却静默跳过，最终成片停留在旧版本、
       指标却是新算的。界面上完全看不出来。
    2. 镜头状态存在两处副本（shots 表 + 项目 JSON），
       只更新一处会让运行期读到失效路径。

这类问题的共同点是「系统报告成功，产物是错的」，比直接崩溃危险得多。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reelagent.providers.ark import ArkVideoProvider
from reelagent.providers.dashscope import DashScopeVideoProvider
from reelagent.providers.mock import MockVideoProvider
from reelagent.providers.zhipu import ZhipuVideoProvider
from reelagent.render.composer import RenderError, concat_gifs


class TestProviderDeclaresContainer:
    """容器扩展名必须由供应商声明，不能由调用方写死。"""

    @pytest.mark.parametrize(
        ("provider_cls", "expected"),
        [
            (MockVideoProvider, "gif"),
            (ZhipuVideoProvider, "mp4"),
            (ArkVideoProvider, "mp4"),
            (DashScopeVideoProvider, "mp4"),
        ],
    )
    def test_output_suffix(self, provider_cls: type, expected: str) -> None:
        assert provider_cls.output_suffix == expected

    def test_suffix_derivation_rewrites_caller_extension(self) -> None:
        """即使调用方传 .gif，真实视频模型也必须落成 .mp4。

        这正是曾经导致成片静默过期的原因：调用处写死了 .gif，
        而模型返回的是 MP4，磁盘上于是出现「名不副实」的文件。
        """
        for provider in (DashScopeVideoProvider(api_key="x"), ZhipuVideoProvider(api_key="x")):
            out = Path("sc01_sh01.gif").with_suffix(f".{provider.output_suffix}").name
            assert out == "sc01_sh01.mp4"


class TestConcatGifsFailsLoudly:
    """读不出片段必须报错，绝不静默返回 None。"""

    def test_raises_when_clip_is_not_an_image(self, tmp_workspace: Path) -> None:
        """核心用例：扩展名是 .gif 但内容不是图像时，必须显式失败。

        旧实现用 contextlib.suppress 吞掉异常并返回 None，
        调用方只是「什么都没生成」—— 旧成片被保留，系统却报告成功。
        """
        fake = tmp_workspace / "clip.gif"
        # 写入 MP4 文件头，模拟「扩展名说谎」
        fake.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64)

        with pytest.raises(RenderError, match="无法解码"):
            concat_gifs([fake], tmp_workspace / "final.gif")

    def test_missing_files_yield_none_not_error(self, tmp_workspace: Path) -> None:
        """文件缺失不算错误 —— 那是「没有片段可合成」，返回 None 即可。

        这与「文件存在但解不开」要区分开：后者说明数据坏了，必须显式报错。
        """
        assert concat_gifs([tmp_workspace / "missing.gif"], tmp_workspace / "out.gif") is None

    def test_returns_none_for_empty_input(self, tmp_workspace: Path) -> None:
        """空输入不是错误：没有片段时不该抛异常，只是无成片可合成。"""
        assert concat_gifs([], tmp_workspace / "out.gif") is None

    def test_successful_concat_of_real_gifs(self, tmp_workspace: Path) -> None:
        """正向用例：真实 GIF 必须能正常拼接（避免修复把正常路径也拦死）。

        每帧用不同颜色：GIF 编码器会合并连续的相同帧，
        用纯色构造会让「3 帧」被优化成 1 帧，断言就失去意义了。
        """
        from PIL import Image

        paths = []
        for i in range(2):
            p = tmp_workspace / f"clip{i}.gif"
            frames = [Image.new("RGB", (64, 64), (i * 60 + j * 25, 60, 90)) for j in range(3)]
            frames[0].save(p, save_all=True, append_images=frames[1:], duration=100, loop=0)
            paths.append(p)

        out = concat_gifs(paths, tmp_workspace / "final.gif")
        assert out is not None and out.exists()
        with Image.open(out) as im:
            # 允许编码器合并相邻同色帧，因此断言「多帧」而不是精确帧数
            assert getattr(im, "n_frames", 1) >= 2, "拼接结果应包含多个帧"
