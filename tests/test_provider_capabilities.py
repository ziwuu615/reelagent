"""视频供应商「本地关键帧」能力声明的测试。

为什么需要单独测这个：
流水线是「先生成关键帧 → 再做图生视频」。但不同供应商对参考图的接受方式不同 ——
火山方舟接受 base64 内联图片，智谱要求公网 URL。如果不显式建模这个差异，
只有两种糟糕的结局：

    1. 适配器抛错 → 整条流水线在视频阶段全灭
    2. 适配器静默忽略关键帧 → 做出「一致性支持、实际没生效」的假象

第 2 种更危险，因为它会让项目的核心指标（跨镜头一致性）变成假数据。
所以能力被声明为显式属性，并由流水线据此降级 + 记录。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reelagent.config import ProviderConfig, Settings
from reelagent.providers import build_providers
from reelagent.providers.ark import ArkVideoProvider
from reelagent.providers.base import MediaResult, VideoProvider
from reelagent.providers.mock import MockVideoProvider
from reelagent.providers.zhipu import ZhipuVideoProvider


class TestKeyframeCapabilityDeclaration:
    def test_zhipu_declares_no_local_keyframe(self) -> None:
        assert ZhipuVideoProvider(api_key="dummy").accepts_local_keyframe is False

    def test_ark_declares_local_keyframe_support(self) -> None:
        assert (
            ArkVideoProvider(api_key="dummy", base_url="https://ark.example/v3").accepts_local_keyframe
            is True
        )

    def test_mock_accepts_local_keyframe(self) -> None:
        assert MockVideoProvider().accepts_local_keyframe is True

    def test_base_default_is_permissive(self) -> None:
        """基类默认 True：新供应商若不声明，按「支持」处理，
        以免在不支持图生视频时被无声降级。"""
        assert VideoProvider.accepts_local_keyframe is True


class TestZhipuRecordsDegradation:
    """智谱拿到关键帧时必须记录「已忽略」，而不是默默丢掉。"""

    def _run(self, monkeypatch: pytest.MonkeyPatch, tmp_workspace: Path, keyframe: Path | None) -> dict:
        from reelagent.providers import zhipu

        # 把网络交互全部替换掉，只验证适配器的判定与元数据
        monkeypatch.setattr(zhipu, "_submit_task", lambda *a, **k: ("task-1", {"id": "task-1"}))
        monkeypatch.setattr(zhipu, "_await_task", lambda *a, **k: "https://example.invalid/v.mp4")

        def fake_download(url: str, out_path: Path, **kwargs: object) -> Path:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"\x00" * 32)
            return out_path

        monkeypatch.setattr(zhipu, "_download", fake_download)

        provider = ZhipuVideoProvider(api_key="dummy", model="cogvideox-flash")
        result = provider.generate(
            prompt="测试",
            out_path=tmp_workspace / "clip.mp4",
            duration_s=5.0,
            size=(1280, 720),
            keyframe=keyframe,
        )
        return result.meta

    def test_no_keyframe_marks_text_only(self, monkeypatch: pytest.MonkeyPatch, tmp_workspace: Path) -> None:
        meta = self._run(monkeypatch, tmp_workspace, keyframe=None)
        assert meta["consistency"] == "text_only"

    def test_passed_keyframe_is_marked_ignored(
        self, monkeypatch: pytest.MonkeyPatch, tmp_workspace: Path
    ) -> None:
        kf = tmp_workspace / "kf.png"
        kf.write_bytes(b"\x89PNG\r\n\x1a\n")
        meta = self._run(monkeypatch, tmp_workspace, keyframe=kf)
        assert meta["consistency"] == "text_only(keyframe_ignored)"

    def test_free_model_reports_zero_cost(self, monkeypatch: pytest.MonkeyPatch, tmp_workspace: Path) -> None:
        from reelagent.providers import zhipu

        monkeypatch.setattr(zhipu, "_submit_task", lambda *a, **k: ("t", {"id": "t"}))
        monkeypatch.setattr(zhipu, "_await_task", lambda *a, **k: "https://example.invalid/v.mp4")
        monkeypatch.setattr(
            zhipu,
            "_download",
            lambda url, out, **k: (
                out.parent.mkdir(parents=True, exist_ok=True),
                out.write_bytes(b"x" * 32),
                out,
            )[-1],
        )
        provider = ZhipuVideoProvider(api_key="dummy", model="cogvideox-flash")
        res = provider.generate(prompt="t", out_path=tmp_workspace / "a.mp4", duration_s=5.0, size=(64, 64))
        assert res.cost_usd == 0.0, "免费模型必须记 0 成本，否则预算控制会被高估"


class TestFactoryPreservesCapability:
    @pytest.mark.parametrize(("name", "expected"), [("zhipu", False), ("ark", True)])
    def test_factory_preserves_capability(self, name: str, expected: bool) -> None:
        s = Settings(
            mode="live",
            video=ProviderConfig(name=name, model="m", api_key="dummy"),
        )
        bundle = build_providers(s)
        assert getattr(bundle.video, "accepts_local_keyframe", True) is expected


class TestMockWorksWithoutKeyframe:
    """流水线降级后会把 keyframe=None 传给 mock，mock 必须仍然能出片。

    用 `.gif` 而非 `.mp4`：无 ffmpeg 时 mock 走 GIF+WAV 降级路径，
    扩展名决定 Pillow 选用哪个编码器 —— 写 `.mp4` 会直接抛
    "unknown file extension"。
    """

    def test_mock_generates_without_keyframe(self, tmp_workspace: Path) -> None:
        provider = MockVideoProvider(max_frames=6)
        result = provider.generate(
            prompt="雨夜街角",
            out_path=tmp_workspace / "clip.gif",
            duration_s=1.0,
            size=(128, 128),
            keyframe=None,
        )
        assert isinstance(result, MediaResult)
        assert Path(result.path).exists()
        assert Path(result.path).stat().st_size > 0
