"""配置层：读取 .env + 环境变量，提供全局 Settings。

不依赖 python-dotenv，自己解析 .env，保持核心零依赖。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# .env 解析
# --------------------------------------------------------------------------- #
_ENV_LOADED = False


def load_dotenv(path: Path | None = None, *, override: bool = False) -> dict[str, str]:
    """极简 .env 解析。已存在的环境变量默认不覆盖。"""
    global _ENV_LOADED
    if path is None:
        path = project_root() / ".env"
    loaded: dict[str, str] = {}
    if not path.exists():
        _ENV_LOADED = True
        return loaded
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    _ENV_LOADED = True
    return loaded


def resolve_ffmpeg() -> str | None:
    """按优先级解析 ffmpeg：显式配置 > 系统 PATH > 内置静态二进制。

    **所有需要 ffmpeg 的地方都必须走这个函数**，不要各自写
    shutil.which("ffmpeg")。

    踩过的坑：云端的引擎桥接里单独写了 shutil.which("ffmpeg")，
    于是「装了内置二进制但系统没装 ffmpeg」的机器上，
    命令行能产出 MP4、而**产品路径永远产出不了成片视频** ——
    两条路径对同一件事给出了不同答案，且不报错。
    """
    return _env("REEL_FFMPEG", "") or shutil.which("ffmpeg") or _bundled_ffmpeg()


def _bundled_ffmpeg() -> str | None:
    """回退到 imageio-ffmpeg 自带的静态二进制。

    ffmpeg 是输出 MP4 成片的硬依赖。要求用户自己装系统级 ffmpeg 会显著抬高
    「一键部署」的门槛（Windows 上尤其麻烦），而 imageio-ffmpeg 是纯 pip 依赖、
    自带各平台二进制，装上就有。

    找不到时返回 None：此时合成降级为 GIF，功能不中断，只是成片格式较弱。
    """
    try:
        import imageio_ffmpeg

        return str(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        return None


def project_root() -> Path:
    """项目根目录（reelagent 包的上一级）。"""
    return Path(__file__).resolve().parent.parent


def _env(key: str, default: str = "") -> str:
    if not _ENV_LOADED:
        load_dotenv()
    return os.environ.get(key, default) or default


def _env_any(*keys: str, default: str = "") -> str:
    """按顺序取第一个非空的配置项。

    存在的理由：**引擎与云端历史上用了两套变量命名** ——
    引擎是扁平名（`REEL_VIDEO_API_KEY`），云端是嵌套名
    （`REEL_PROVIDERS__VIDEO__API_KEY`）。结果是同一个 `.env`
    在云端配好了，命令行跑 `reelagent demo` 时却静默退回 mock，
    排查起来非常费劲。

    这里让引擎同时接受两种写法（扁平名优先，保持向后兼容），
    这样一份 `.env` 就能同时驱动引擎 CLI、worker 和 Web 服务。
    """
    if not _ENV_LOADED:
        load_dotenv()
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(float(_env(key, str(default))))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# 风格预设
# --------------------------------------------------------------------------- #
#: 风格 -> (色调, 光照关键词, 画质关键词)
STYLE_PRESETS: dict[str, dict[str, object]] = {
    "cinematic": {
        "label": "电影感",
        "palette": [(18, 22, 34), (46, 58, 82), (120, 142, 176), (222, 214, 196)],
        "lighting": "戏剧性侧逆光、低照度、体积光",
        "look": "35mm 胶片颗粒、浅景深、2.39:1 构图",
    },
    "anime": {
        "label": "日式动画",
        "palette": [(248, 246, 240), (255, 214, 170), (140, 190, 235), (255, 140, 150)],
        "lighting": "高调柔光、清晰轮廓光",
        "look": "赛璐璐上色、干净线稿、饱和度高",
    },
    "cyberpunk": {
        "label": "赛博朋克",
        "palette": [(10, 8, 24), (34, 20, 70), (255, 42, 130), (0, 232, 224)],
        "lighting": "霓虹反射、湿地面高光、强对比",
        "look": "体积雾、色散、赛博建筑",
    },
    "ink": {
        "label": "水墨",
        "palette": [(246, 244, 238), (198, 196, 188), (92, 96, 100), (26, 28, 30)],
        "lighting": "留白、柔和渐变",
        "look": "宣纸质感、墨韵晕染、写意",
    },
    "realistic": {
        "label": "写实记录",
        "palette": [(30, 30, 30), (96, 96, 90), (168, 160, 148), (238, 236, 230)],
        "lighting": "自然光、柔和阴影",
        "look": "纪录片质感、真实皮肤纹理、手持感",
    },
}

DEFAULT_STYLE = "cinematic"


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
@dataclass
class ProviderConfig:
    name: str
    model: str
    api_key: str = ""
    base_url: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)


@dataclass
class Settings:
    mode: str = "mock"
    workspace: Path = field(default_factory=lambda: project_root() / "workspace")
    style: str = DEFAULT_STYLE
    aspect_ratio: str = "9:16"
    budget_usd: float = 2.0
    context_budget: int = 24000
    max_concurrency: int = 3
    max_attempts: int = 3
    ffmpeg: str | None = None

    llm: ProviderConfig = field(default_factory=lambda: ProviderConfig(name="llm", model=""))
    llm_judge_model: str = ""
    image: ProviderConfig = field(default_factory=lambda: ProviderConfig(name="image", model=""))
    video: ProviderConfig = field(default_factory=lambda: ProviderConfig(name="video", model=""))
    tts: ProviderConfig = field(default_factory=lambda: ProviderConfig(name="tts", model=""))

    @property
    def is_mock(self) -> bool:
        return self.mode.strip().lower() != "live"

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, **overrides) -> Settings:
        workspace_raw = _env("REEL_WORKSPACE", "workspace")
        ws = Path(workspace_raw)
        if not ws.is_absolute():
            ws = project_root() / ws

        ffmpeg = resolve_ffmpeg()

        s = cls(
            mode=_env("REEL_MODE", "mock"),
            workspace=ws,
            style=_env("REEL_STYLE", DEFAULT_STYLE),
            aspect_ratio=_env("REEL_ASPECT_RATIO", "9:16"),
            budget_usd=_env_float("REEL_BUDGET_USD", 2.0),
            context_budget=_env_int("REEL_CONTEXT_BUDGET", 24000),
            max_concurrency=_env_int("REEL_MAX_CONCURRENCY", 3),
            max_attempts=_env_int("REEL_MAX_ATTEMPTS", 3),
            ffmpeg=ffmpeg,
            llm=ProviderConfig(
                name="llm",
                model=_env_any("REEL_LLM_MODEL", "REEL_PROVIDERS__LLM__MODEL", default="deepseek-chat"),
                api_key=_env_any("REEL_LLM_API_KEY", "REEL_PROVIDERS__LLM__API_KEY"),
                base_url=_env_any(
                    "REEL_LLM_BASE_URL",
                    "REEL_PROVIDERS__LLM__BASE_URL",
                    default="https://api.deepseek.com/v1",
                ),
            ),
            llm_judge_model=_env("REEL_LLM_JUDGE_MODEL"),
            image=ProviderConfig(
                name=_env_any("REEL_IMAGE_PROVIDER", "REEL_PROVIDERS__IMAGE__PROVIDER", default="ark"),
                model=_env_any(
                    "REEL_IMAGE_MODEL",
                    "REEL_PROVIDERS__IMAGE__MODEL",
                    default="doubao-seedream-5-0-flash-260915",
                ),
                api_key=_env_any("REEL_IMAGE_API_KEY", "REEL_PROVIDERS__IMAGE__API_KEY"),
                base_url=_env_any(
                    "REEL_IMAGE_BASE_URL",
                    "REEL_PROVIDERS__IMAGE__BASE_URL",
                    default="https://ark.cn-beijing.volces.com/api/v3",
                ),
            ),
            video=ProviderConfig(
                name=_env_any("REEL_VIDEO_PROVIDER", "REEL_PROVIDERS__VIDEO__PROVIDER", default="ark"),
                model=_env_any(
                    "REEL_VIDEO_MODEL",
                    "REEL_PROVIDERS__VIDEO__MODEL",
                    default="doubao-seedance-2-0-fast-260128",
                ),
                api_key=_env_any("REEL_VIDEO_API_KEY", "REEL_PROVIDERS__VIDEO__API_KEY"),
                base_url=_env_any(
                    "REEL_VIDEO_BASE_URL",
                    "REEL_PROVIDERS__VIDEO__BASE_URL",
                    default="https://ark.cn-beijing.volces.com/api/v3",
                ),
            ),
            tts=ProviderConfig(
                name=_env_any("REEL_TTS_PROVIDER", "REEL_PROVIDERS__TTS__PROVIDER", default="dashscope"),
                model=_env_any("REEL_TTS_MODEL", "REEL_PROVIDERS__TTS__MODEL", default="qwen3-tts-flash"),
                api_key=_env_any("REEL_TTS_API_KEY", "REEL_PROVIDERS__TTS__API_KEY"),
                base_url=_env_any(
                    "REEL_TTS_BASE_URL",
                    "REEL_PROVIDERS__TTS__BASE_URL",
                    default="https://dashscope.aliyuncs.com/api/v1",
                ),
            ),
        )
        for key, value in overrides.items():
            if value is not None and hasattr(s, key):
                setattr(s, key, value)
        return s

    # ------------------------------------------------------------------ #
    @property
    def style_spec(self) -> dict[str, object]:
        return STYLE_PRESETS.get(self.style, STYLE_PRESETS[DEFAULT_STYLE])

    def ensure_workspace(self) -> Path:
        self.workspace.mkdir(parents=True, exist_ok=True)
        return self.workspace

    def project_dir(self, project_id: str) -> Path:
        d = self.workspace / "projects" / project_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def artifacts_dir(self, project_id: str, kind: str) -> Path:
        d = self.project_dir(project_id) / kind
        d.mkdir(parents=True, exist_ok=True)
        return d

    def rel(self, path: Path) -> str:
        """转成相对 workspace 的 POSIX 路径，便于跨平台持久化。"""
        try:
            return path.resolve().relative_to(self.workspace.resolve()).as_posix()
        except ValueError:
            return path.as_posix()

    def abs(self, rel_path: str) -> Path:
        p = Path(rel_path)
        return p if p.is_absolute() else (self.workspace / p)

    def describe(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "workspace": str(self.workspace),
            "style": self.style,
            "budget_usd": self.budget_usd,
            "context_budget": self.context_budget,
            "max_concurrency": self.max_concurrency,
            "ffmpeg": self.ffmpeg or "(未检测到，将降级为 GIF+WAV 输出)",
            "llm": {"model": self.llm.model, "configured": self.llm.enabled},
            "image": {
                "provider": self.image.name,
                "model": self.image.model,
                "configured": self.image.enabled,
            },
            "video": {
                "provider": self.video.name,
                "model": self.video.model,
                "configured": self.video.enabled,
            },
            "tts": {"provider": self.tts.name, "model": self.tts.model, "configured": self.tts.enabled},
        }
