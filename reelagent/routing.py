"""模型路由与成本控制。

对应 JD：
  - "探索模型路由策略，根据任务类型、上下文长度、延迟要求、成本约束和效果目标，
     动态选择合适模型与调用方式"（字节即梦 Agent Harness）
  - "在效果、稳定性与 Token Efficiency 之间取得合理平衡"（腾讯 WorkBuddy）

路由决策三档（tier）：
  draft    —— 关键帧 + 免费/廉价运动合成，不调用视频模型（最省）
  standard —— 调用标准视频模型
  premium  —— 调用高保真视频模型

难度打分考虑：是否含人物线索（一致性要求高）、是否首镜（需要建立世界观）、
景别（特写对画质更敏感）、是否含台词（对口型要求高）、剩余预算。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .providers.base import estimate_video_cost
from .schema import Clue, Shot

TIER_DRAFT = "draft"
TIER_STANDARD = "standard"
TIER_PREMIUM = "premium"
#: 极速档：与 premium 同为 720P，但走 `wan3.0-video-prime`（优速版）。
#:
#: 为什么单独设一档而不是让 premium 直接用优速版：
#: **速度与画质是两种不同的需求**，压成一档会迫使想要高画质又想省钱的
#: 用户二选一。实测（2026-10-16，480P/2s 同提示词并行）：
#:     基础版 95.0 秒  vs  优速版 58.3 秒   -> 快 63%
#: 价格贵 54%。所以「多花钱换时间」应该是一个**显式选择**。
TIER_EXPRESS = "express"


#: 各档位的**完整规格**。
#:
#: 用一个数据类而不是并行的几张 dict：档位的属性是**一起变**的，
#: 拆成多表就必然会漏。
#: 踩过的坑：加 express 档时我先改了 `TIER_RESOLUTION_SCALE`，
#: 忘了改 `TIER_VIDEO_RESOLUTION`，运行到 `TIER_VIDEO_RESOLUTION['express']`
#: 才 KeyError —— 而单元测试**全绿**，因为没有任何断言检查「所有档位在所有表里齐全」。
#: 合并成一张表后，「缺规格」这件事在类型层面就不可能发生。
@dataclass(frozen=True)
class TierSpec:
    """一个档位的全部生成参数。"""

    #: 档位标识（draft/standard/premium/express）
    tier: str
    #: 视频分辨率档名（480P/720P/1080P），用于按官方单价计价
    resolution: str
    #: 尺寸倍率。基准见 `providers.ASPECT_SIZES`（9:16 为 540x960）。
    #:   1.0 -> 540x960   短边 540 -> 480P 档
    #:   1.4 -> 756x1344  短边 756 -> 720P 档
    resolution_scale: float
    #: 是否调用真实视频模型。draft 为 False（改用本地运动合成）。
    use_video_model: bool
    #: 是否使用优速版模型（express 为 True）。只影响速度，不影响画质。
    use_fast_model: bool = False


#: 档位注册表 —— **唯一的真实来源**。
#:
#: 档位差异必须体现在成片上，不能只体现在账单里。
#: 早期实现里 standard 与 premium 走完全相同的生成路径，
#: 计费侧却按 premium 多收 35% 额度 —— 用户多付钱拿到一样的东西。
TIERS: dict[str, TierSpec] = {
    TIER_DRAFT: TierSpec(
        tier=TIER_DRAFT,
        resolution="480P",  # 不调模型，仅作占位
        resolution_scale=1.0,
        use_video_model=False,
    ),
    TIER_STANDARD: TierSpec(
        tier=TIER_STANDARD,
        resolution="480P",
        resolution_scale=1.0,
        use_video_model=True,
    ),
    TIER_PREMIUM: TierSpec(
        tier=TIER_PREMIUM,
        resolution="720P",
        resolution_scale=1.4,
        use_video_model=True,
    ),
    TIER_EXPRESS: TierSpec(
        tier=TIER_EXPRESS,
        resolution="720P",  # 与 premium 同画质
        resolution_scale=1.4,
        use_video_model=True,
        use_fast_model=True,  # 差别只在模型速度
    ),
}


def tier_spec(tier: str | None, *, default: str = TIER_STANDARD) -> TierSpec:
    """取档位规格；未知档位回落到 `default`（默认 standard）。

    回落而不是抛错，是因为这个函数会被路由与计价两侧调用，
    两侧的容错策略应当一致 —— 一侧抛错、一侧回落，会出现
    「计了 standard 的钱、跑了 premium 的活」这种更难查的问题。
    """
    return TIERS.get(tier or "", TIERS[default])


@dataclass
class RouteDecision:
    tier: str
    reason: str
    use_video_model: bool
    est_cost_usd: float
    difficulty: float
    #: 分辨率倍率。让「贵一档」真的产出更高分辨率，而不是只多收钱。
    resolution_scale: float = 1.0
    #: 是否使用**优速版**视频模型（express 档为真）。
    #: 它不改变画质，只改变生成速度 —— 实测快 63%，价格贵 54%。
    use_fast_model: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "reason": self.reason,
            "use_video_model": self.use_video_model,
            "est_cost_usd": round(self.est_cost_usd, 4),
            "difficulty": round(self.difficulty, 3),
            "resolution_scale": round(self.resolution_scale, 2),
            "use_fast_model": self.use_fast_model,
        }


class ModelRouter:
    def __init__(
        self,
        settings: Settings,
        *,
        spent_usd: Callable[[], float] | None = None,
    ) -> None:
        self.settings = settings
        self._spent = spent_usd or (lambda: 0.0)
        self.decisions: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    def difficulty(self, shot: Shot, clues: list[Clue], *, is_first: bool) -> float:
        """0~1 的难度分，越高越值得花钱。"""
        score = 0.30
        kinds = {c.id: c.kind for c in clues}
        shot_clue_kinds = [kinds.get(cid, "") for cid in shot.clue_ids]
        if "character" in shot_clue_kinds:
            score += 0.22  # 有人物 -> 一致性风险高
        if len(shot.clue_ids) >= 3:
            score += 0.08  # 元素多 -> 越容易崩
        if is_first:
            score += 0.10  # 首镜要立住世界观
        if shot.shot_size in {"特写", "近景"}:
            score += 0.10  # 特写对细节敏感
        if shot.dialogue:
            score += 0.08  # 有台词 -> 对口型
        if shot.duration_s >= 4:
            score += 0.06  # 长镜头更易崩
        if shot.attempts > 0:
            score += 0.12  # 已经失败过 -> 升级
        return max(0.0, min(1.0, score))

    # ------------------------------------------------------------------ #
    def decide(
        self,
        shot: Shot,
        clues: list[Clue],
        *,
        is_first: bool = False,
        force_tier: str | None = None,
    ) -> RouteDecision:
        diff = self.difficulty(shot, clues, is_first=is_first)
        remaining = self.settings.budget_usd - self._spent()

        if force_tier:
            tier = force_tier
            reason = f"手动指定 {tier}"
        elif remaining <= 0:
            tier = TIER_DRAFT
            reason = f"预算已用尽（上限 {self.settings.budget_usd:.2f} USD），强制草稿档"
        elif diff >= 0.66:
            tier = TIER_PREMIUM
            reason = f"难度 {diff:.2f} 偏高（人物一致性/细节要求高）"
        elif diff >= 0.45:
            tier = TIER_STANDARD
            reason = f"难度 {diff:.2f} 中等"
        else:
            tier = TIER_DRAFT
            reason = f"难度 {diff:.2f} 偏低，草稿档即可"

        # 预算紧张时把 premium 降级为 standard
        if tier == TIER_PREMIUM and remaining < estimate_video_cost(shot.duration_s, "720P") * 3:
            tier = TIER_STANDARD
            reason += "；剩余预算不足以支撑多镜 premium，降级为 standard"

        # 一次取到该档位的**全部**生成参数，不再分别查几张表。
        # 早期这里有 `TIER_RESOLUTION_SCALE.get(...)` 与
        # `TIER_VIDEO_RESOLUTION.get(...)` 两次独立查询，两张表可能不一致。
        spec = tier_spec(tier)
        est = (
            0.0
            if not spec.use_video_model
            else estimate_video_cost(shot.duration_s, spec.resolution, fast=spec.use_fast_model)
        )

        decision = RouteDecision(
            tier=tier,
            reason=reason,
            use_video_model=spec.use_video_model,
            est_cost_usd=est,
            difficulty=diff,
            resolution_scale=spec.resolution_scale,
            use_fast_model=spec.use_fast_model,
        )
        self.decisions.append({"shot_id": shot.id, **decision.as_dict()})
        return decision

    # ------------------------------------------------------------------ #
    def summary(self) -> dict[str, Any]:
        if not self.decisions:
            return {"total": 0}
        by_tier: dict[str, int] = {}
        for d in self.decisions:
            by_tier[d["tier"]] = by_tier.get(d["tier"], 0) + 1
        skipped = sum(1 for d in self.decisions if not d["use_video_model"])
        saved = sum(
            estimate_video_cost(3.0) - d["est_cost_usd"] for d in self.decisions if not d["use_video_model"]
        )
        return {
            "total": len(self.decisions),
            "by_tier": by_tier,
            "video_calls_skipped": skipped,
            "estimated_saved_usd": round(max(0.0, saved), 4),
            "decisions": self.decisions,
        }
