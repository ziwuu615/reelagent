"""万相视频接口的请求结构测试。

保护一个**已经真实发生过**的缺陷：

适配器最初用 `input.img_url` 传关键帧，而文档要求
`input.media: [{"type": "first_frame", "url": ...}]`。
服务端对不认识的参数**返回 200 并静默忽略**，于是：

  1. 关键帧根本没被当作首帧 —— 成片实为纯文生视频，跨镜头一致性形同虚设，
     而 meta 里还如实写着 `consistency="image_to_video"`（写的时候是真的，
     只是这个"真"没有传到服务端）；
  2. 参数里缺 `ratio`，输出宽高比不受控 —— 实测 9:16 的项目在 480P 下
     产出 854x480 **横屏**视频。

这类缺陷最难发现：HTTP 200、日志正常、产物齐全、成本照扣。
所以这里断言的是**请求载荷的字面结构**，而不是"调用成功"。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from reelagent.providers.dashscope import DashScopeVideoProvider, ratio_for

pytestmark = pytest.mark.usefixtures("db")


def _capture_payload(tmp_workspace: Path, *, with_keyframe: bool, size: tuple[int, int] = (576, 1024)):
    """跑一次 generate（网络全被替身接管），返回实际构造出的请求载荷。"""
    keyframe = None
    if with_keyframe:
        keyframe = tmp_workspace / "kf.png"
        keyframe.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

    provider = DashScopeVideoProvider(api_key="dummy")
    captured: dict = {}

    def fake_submit(payload):
        captured.update(payload)
        return "task_fake"

    out = tmp_workspace / "out.mp4"
    with (
        patch.object(provider, "_submit", side_effect=fake_submit),
        patch.object(provider, "_await", return_value={"video_url": "https://x/y.mp4"}),
        patch("reelagent.providers.dashscope._download"),
    ):
        result = provider.generate(
            prompt="测试提示词",
            out_path=out,
            duration_s=5,
            size=size,
            keyframe=keyframe,
        )
    return captured, result


class TestFirstFrameUsesMediaArray:
    """首帧必须走 media 数组 —— 这是本文件存在的首要原因。"""

    def test_keyframe_goes_into_media_array(self, tmp_workspace: Path) -> None:
        payload, _ = _capture_payload(tmp_workspace, with_keyframe=True)
        assert "media" in payload["input"], "首帧必须放在 input.media 里"
        media = payload["input"]["media"]
        assert isinstance(media, list) and len(media) == 1
        assert media[0]["type"] == "first_frame"
        assert media[0]["url"].startswith("data:image/")

    def test_legacy_img_url_is_not_used(self, tmp_workspace: Path) -> None:
        """`img_url` 是旧写法，服务端会静默忽略它，绝不能再用。"""
        payload, _ = _capture_payload(tmp_workspace, with_keyframe=True)
        assert "img_url" not in payload["input"]

    def test_no_keyframe_means_no_media(self, tmp_workspace: Path) -> None:
        payload, _ = _capture_payload(tmp_workspace, with_keyframe=False)
        assert "media" not in payload["input"]

    def test_consistency_flag_reflects_reality(self, tmp_workspace: Path) -> None:
        _, with_kf = _capture_payload(tmp_workspace, with_keyframe=True)
        _, without = _capture_payload(tmp_workspace, with_keyframe=False)
        assert with_kf.meta["consistency"] == "image_to_video"
        assert without.meta["consistency"] == "text_only"


class TestRatioParameter:
    """缺 ratio 会让输出比例失控（实测出过横屏）。"""

    def test_adaptive_when_keyframe_present(self, tmp_workspace: Path) -> None:
        payload, _ = _capture_payload(tmp_workspace, with_keyframe=True)
        assert payload["parameters"]["ratio"] == "adaptive"

    def test_explicit_ratio_without_keyframe(self, tmp_workspace: Path) -> None:
        payload, _ = _capture_payload(tmp_workspace, with_keyframe=False, size=(576, 1024))
        assert payload["parameters"]["ratio"] == "9:16"

    def test_ratio_always_present(self, tmp_workspace: Path) -> None:
        for with_kf in (True, False):
            payload, _ = _capture_payload(tmp_workspace, with_keyframe=with_kf)
            assert "ratio" in payload["parameters"], "缺 ratio 会拿到供应商的默认比例"


class TestRatioFor:
    @pytest.mark.parametrize(
        ("size", "expected"),
        [
            ((576, 1024), "9:16"),
            ((1024, 576), "16:9"),
            ((768, 768), "1:1"),
            ((800, 600), "4:3"),
            ((600, 800), "3:4"),
        ],
    )
    def test_known_shapes(self, size: tuple[int, int], expected: str) -> None:
        assert ratio_for(size) == expected

    def test_unknown_shape_falls_back_to_adaptive(self) -> None:
        """认不出来的比例交给供应商自适应，而不是硬塞一个错的。"""
        assert ratio_for((1000, 333)) == "adaptive"

    def test_zero_height_is_safe(self) -> None:
        assert ratio_for((100, 0)) == "adaptive"
