"""数据模型层。

刻意采用 dataclass 而非 Pydantic：核心零第三方依赖，任何机器 clone 即跑，
同时 `to_dict / from_dict` 保证了完整的 JSON 往返能力——这是断点恢复的基础。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, cast


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def now() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class Serializable:
    """提供 JSON 往返能力的 dataclass 基类。

    子类必须是 dataclass（这是本基类的契约），因此这里用 `cast` 把 self
    收窄为 dataclass 实例，而不是散落 `# type: ignore`。
    """

    def to_dict(self) -> dict[str, Any]:
        return asdict(cast("Any", self))

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        known = {f.name for f in fields(cast("Any", cls))}
        return cls(**{k: v for k, v in data.items() if k in known})


# --------------------------------------------------------------------------- #
# 线索（Clue）—— 跨镜头一致性的核心抽象
# --------------------------------------------------------------------------- #
#: 线索类型
CLUE_CHARACTER = "character"
CLUE_PROP = "prop"
CLUE_SCENE = "scene"


@dataclass
class Clue(Serializable):
    """一条需要跨镜头保持视觉一致的线索。

    做法与工业界一致：先生成一张「基准图」（base_image），
    之后所有分镜的图像/视频 prompt 都会把这条线索的基准图作为参考注入，
    从而避免长视频里最常见的「角色变脸 / 场景漂移」。
    """

    id: str
    kind: str  # character | prop | scene
    name: str
    description: str = ""
    appearance: str = ""  # 外貌/材质/风格的稳定描述，参与每个 shot 的 prompt
    base_image: str | None = None  # 基准图相对路径
    style_tags: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 镜头（Shot）—— 流水线最小执行单元，也是断点恢复的最小粒度
# --------------------------------------------------------------------------- #
SHOT_PENDING = "pending"
SHOT_KEYFRAME_DONE = "keyframe_done"
SHOT_CLIP_DONE = "clip_done"
SHOT_AUDIO_DONE = "audio_done"
SHOT_FAILED = "failed"


@dataclass
class Shot(Serializable):
    id: str
    scene_id: str
    index: int
    duration_s: float = 3.0

    # ---- 导演意图 ----
    shot_size: str = "中景"  # 景别：特写/近景/中景/全景/远景
    camera: str = "固定"  # 运镜：固定/推/拉/摇/移/跟
    action: str = ""  # 画面动作描述
    narration: str = ""  # 旁白
    dialogue: str = ""  # 台词

    # ---- 一致性约束 ----
    clue_ids: list[str] = field(default_factory=list)

    # ---- 派生 prompt ----
    image_prompt: str = ""
    video_prompt: str = ""

    # ---- 生成产物（相对 workspace 的路径）----
    keyframe_path: str | None = None
    clip_path: str | None = None
    audio_path: str | None = None

    # ---- 执行状态（断点恢复依据）----
    status: str = SHOT_PENDING
    attempts: int = 0
    error: str | None = None
    provider_used: str = ""
    model_used: str = ""
    cost_usd: float = 0.0

    #: 本镜头实际采用的成本档位（draft/standard/premium）。
    #: 云端 shots 表早就有 tier 列，但引擎此前从不写它，导致那一列永远是空的 ——
    #: 用户能看到总花费，却看不到「钱花在哪个镜头、用的哪一档」。
    tier: str = ""

    @property
    def needs_audio(self) -> bool:
        return bool((self.dialogue or self.narration or "").strip())

    @property
    def is_finished(self) -> bool:
        """是否「记录上已完成生成」——**只看字段，不访问文件系统**。

        ⚠ 这不等价于「产物真的存在」。文件可能已被外部删除，而
        `clip_path` 字段仍有值，此时本属性依然为 True。

        这里曾有一条与此实现不符的 docstring，声称「判定依据是产物是否存在」，
        导致依赖它做断点恢复的调用方产生错误预期（阶段会被误判为已完成而跳过）。
        需要确认文件真的在磁盘上时，请用 `artifacts_present()`。
        """
        if not self.clip_path:
            return False
        return not (self.needs_audio and not self.audio_path)

    def artifacts_present(self, base_dir: Path) -> bool:
        """产物是否**真的**落在磁盘上。

        断点恢复必须用这个判据：只信字段会在「文件被外部删除」或
        「状态写成功但落盘失败」时把未完成的工作误判成已完成。
        """
        if not self.is_finished:
            return False
        if self.clip_path and not (base_dir / self.clip_path).exists():
            return False
        return not (self.needs_audio and self.audio_path and not (base_dir / self.audio_path).exists())


# --------------------------------------------------------------------------- #
# 场景 / 剧本
# --------------------------------------------------------------------------- #
@dataclass
class Scene(Serializable):
    id: str
    index: int
    title: str = ""
    summary: str = ""
    location: str = ""
    time_of_day: str = ""
    shots: list[Shot] = field(default_factory=list)


@dataclass
class Script(Serializable):
    title: str = ""
    logline: str = ""  # 一句话故事
    style: str = "cinematic"
    aspect_ratio: str = "9:16"
    scenes: list[Scene] = field(default_factory=list)

    @property
    def shots(self) -> list[Shot]:
        return [s for sc in self.scenes for s in sc.shots]

    @property
    def total_duration_s(self) -> float:
        return sum(s.duration_s for s in self.shots)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "logline": self.logline,
            "style": self.style,
            "aspect_ratio": self.aspect_ratio,
            "scenes": [sc.to_dict() for sc in self.scenes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Script:
        scenes = []
        for sc in data.get("scenes", []):
            shots = [Shot.from_dict(s) for s in sc.get("shots", [])]
            scenes.append(Scene.from_dict({**sc, "shots": shots}))
        return cls(
            title=data.get("title", ""),
            logline=data.get("logline", ""),
            style=data.get("style", "cinematic"),
            aspect_ratio=data.get("aspect_ratio", "9:16"),
            scenes=scenes,
        )


# --------------------------------------------------------------------------- #
# 项目
# --------------------------------------------------------------------------- #
PROJECT_CREATED = "created"
PROJECT_PLANNED = "planned"
PROJECT_ASSETS_READY = "assets_ready"
PROJECT_GENERATED = "generated"
PROJECT_EVALUATED = "evaluated"
PROJECT_RENDERED = "rendered"
PROJECT_FAILED = "failed"


@dataclass
class Project(Serializable):
    id: str
    seed_text: str
    style: str = "cinematic"
    aspect_ratio: str = "9:16"
    mode: str = "mock"  # mock | live
    status: str = PROJECT_CREATED
    created_at: float = field(default_factory=now)
    updated_at: float = field(default_factory=now)

    script: Script | None = None
    clues: list[Clue] = field(default_factory=list)

    # 评测结果（Evaluator 写入）
    evaluation: dict[str, Any] | None = None
    # 成本与用量汇总
    cost_summary: dict[str, Any] | None = None
    # 最终产物
    final_video: str | None = None
    final_gif: str | None = None
    final_audio: str | None = None

    def touch(self) -> None:
        self.updated_at = now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "seed_text": self.seed_text,
            "style": self.style,
            "aspect_ratio": self.aspect_ratio,
            "mode": self.mode,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "script": self.script.to_dict() if self.script else None,
            "clues": [c.to_dict() for c in self.clues],
            "evaluation": self.evaluation,
            "cost_summary": self.cost_summary,
            "final_video": self.final_video,
            "final_gif": self.final_gif,
            "final_audio": self.final_audio,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Project:
        return cls(
            id=data["id"],
            seed_text=data.get("seed_text", ""),
            style=data.get("style", "cinematic"),
            aspect_ratio=data.get("aspect_ratio", "9:16"),
            mode=data.get("mode", "mock"),
            status=data.get("status", PROJECT_CREATED),
            created_at=data.get("created_at", now()),
            updated_at=data.get("updated_at", now()),
            script=Script.from_dict(data["script"]) if data.get("script") else None,
            clues=[Clue.from_dict(c) for c in data.get("clues", [])],
            evaluation=data.get("evaluation"),
            cost_summary=data.get("cost_summary"),
            final_video=data.get("final_video"),
            final_gif=data.get("final_gif"),
            final_audio=data.get("final_audio"),
        )


# --------------------------------------------------------------------------- #
# 上下文预算 / 用量
# --------------------------------------------------------------------------- #
@dataclass
class Usage(Serializable):
    """单次 LLM 调用的用量。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.cached_tokens + other.cached_tokens,
        )
