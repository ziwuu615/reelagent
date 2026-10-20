"""引擎配置加载的兼容性测试。

背景：引擎与云端**历史上用了两套环境变量命名** ——
    引擎（扁平）：`REEL_VIDEO_API_KEY`
    云端（嵌套）：`REEL_PROVIDERS__VIDEO__API_KEY`
后果是同一个 `.env` 在云端生效，命令行跑 `reelagent demo` 时却静默退回
mock 供应商。这种「配置看起来对、行为却是错的」问题排查成本很高，
所以用测试把「两种写法都认」这个契约固定下来。
"""

from __future__ import annotations

import pytest

from reelagent import config as config_mod
from reelagent.config import ProviderConfig, Settings
from reelagent.providers import build_providers

#: 供应商相关的全部环境变量名（扁平 + 嵌套），测试前统一清理，
#: 避免读到开发者本机 `.env` 造成结果不确定。
_ALL_KEYS = [
    f"REEL_{kind}_{field}"
    for kind in ("LLM", "IMAGE", "VIDEO", "TTS")
    for field in ("PROVIDER", "MODEL", "API_KEY", "BASE_URL")
] + [
    f"REEL_PROVIDERS__{kind}__{field}"
    for kind in ("LLM", "IMAGE", "VIDEO", "TTS")
    for field in ("PROVIDER", "MODEL", "API_KEY", "BASE_URL", "ENABLED")
]


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """清空供应商相关环境变量，并禁止再去读磁盘上的 .env。

    把 `_ENV_LOADED` 置为 True 是关键：`load_dotenv()` 只在首次调用时读文件，
    否则本机 `.env` 会污染断言结果。
    """
    for key in _ALL_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config_mod, "_ENV_LOADED", True)
    return monkeypatch


class TestUnifiedEnvNaming:
    def test_nested_names_are_honored(self, clean_env: pytest.MonkeyPatch) -> None:
        """云端风格的嵌套变量名必须能被引擎读到。"""
        clean_env.setenv("REEL_PROVIDERS__VIDEO__PROVIDER", "zhipu")
        clean_env.setenv("REEL_PROVIDERS__VIDEO__MODEL", "cogvideox-flash")
        clean_env.setenv("REEL_PROVIDERS__VIDEO__API_KEY", "test-key-123")

        s = Settings.load()

        assert s.video.name == "zhipu"
        assert s.video.model == "cogvideox-flash"
        assert s.video.api_key == "test-key-123"

    def test_flat_names_still_work(self, clean_env: pytest.MonkeyPatch) -> None:
        """扁平名是既有写法，必须保持向后兼容。"""
        clean_env.setenv("REEL_IMAGE_PROVIDER", "ark")
        clean_env.setenv("REEL_IMAGE_MODEL", "doubao-seedream-5-0-flash-260915")

        s = Settings.load()

        assert s.image.name == "ark"
        assert s.image.model == "doubao-seedream-5-0-flash-260915"

    def test_flat_name_takes_precedence(self, clean_env: pytest.MonkeyPatch) -> None:
        """两种都设置时扁平名优先——显式覆盖嵌套默认值应当生效。"""
        clean_env.setenv("REEL_VIDEO_PROVIDER", "ark")
        clean_env.setenv("REEL_PROVIDERS__VIDEO__PROVIDER", "zhipu")

        assert Settings.load().video.name == "ark"

    def test_missing_everything_falls_back_to_default(self, clean_env: pytest.MonkeyPatch) -> None:
        s = Settings.load()
        assert s.video.name == "ark"
        assert s.image.name == "ark"
        assert s.video.api_key == "", "未配置时 Key 必须为空，以便触发降级"


class TestZhipuProviderWiring:
    """智谱适配器接入工厂的回归保护。"""

    def test_video_provider_is_constructed(self) -> None:
        s = Settings(
            mode="live",
            video=ProviderConfig(name="zhipu", model="cogvideox-flash", api_key="dummy"),
        )
        bundle = build_providers(s)
        assert type(bundle.video).__name__ == "ZhipuVideoProvider"

    def test_image_provider_is_constructed(self) -> None:
        s = Settings(
            mode="live",
            image=ProviderConfig(name="zhipu", model="glm-image", api_key="dummy"),
        )
        bundle = build_providers(s)
        assert type(bundle.image).__name__ == "ZhipuImageProvider"

    def test_missing_key_degrades_instead_of_crashing(self) -> None:
        """没有 Key 时必须降级为 mock 并留下说明，而不是抛异常中断流水线。"""
        s = Settings(mode="live", video=ProviderConfig(name="zhipu", model="cogvideox-flash"))
        bundle = build_providers(s)

        assert type(bundle.video).__name__ == "MockVideoProvider"
        assert any("视频" in note for note in bundle.notes)
