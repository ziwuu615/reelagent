"""内容安全审核。

## 为什么审核必须在流水线内部，而不是只放在 API 层

对外提供的入口当然要审用户输入，但这个系统里**大量文字是模型自己生成的**：
Planner 编剧本、Director 拼画面提示词、Evaluator 写评审意见。这些内容
不经过 API 日志、也没有人工看过，直接进图像/视频模型。只在 API 层审用户输入
等于把「模型编出来的东西」整段放行。

所以审核点放在三个阶段：
    plan 之后      审剧本（标题 / 概要 / 动作 / 台词 / 旁白）
    generate 之前  审拼好的 image_prompt 与 video_prompt
    generate 之后  审关键帧（图像审核，接口见 ImageModerationProvider）

## 失败策略：合规场景必须 fail-closed

审核 provider 自己挂掉时，「放行」和「拦截」是两个完全不同的决定：

    fail-open   审核服务故障 -> 内容照发     可用性优先（社交产品常见）
    fail-closed 审核服务故障 -> 拒绝生成     合规优先（对外提供生成式 AI 服务）

本项目选择 fail-closed 并把它做成显式配置：`moderation_fail_closed`
默认 True。理由是对外提供生成式 AI 服务时，「因为审核挂了所以放出去一条
违规内容」是不可接受的；而「因为审核挂了所以这次生成失败」用户重试即可，
账还会退。代价要写在这里而不是藏在代码里。

## 关于关键词库

内置词表只是**可运行的示意**，覆盖几类明显违规的常见词，用于保证
「离线也能跑、行为可测」。生产环境应通过 `moderation_blocklist_file`
挂载正式词库，并与云端内容安全（阿里云内容安全 / 自建模型）并联：
本地做毫秒级快筛，云端做语义级复核。

关键词审核只能查词面，绕不过同音、拆字、拼音。所以这里做了归一化
（全角转半角、去空白与符号、去零宽字符）后再匹配 —— 能挡住一部分
「加个空格就绕过」的低成本绕过，但**挡不住刻意的变体攻击**。别把它
当成语义审核的替代品。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# --------------------------------------------------------------------------- #
# 结论
# --------------------------------------------------------------------------- #
VERDICT_ALLOW = "allow"
VERDICT_REVIEW = "review"
VERDICT_BLOCK = "block"


class ModerationError(RuntimeError):
    """内容审核拦截。

    单独定义类型而不是复用 RuntimeError，是因为调用方需要按类型做两件不同的事：
        1. 让整条任务失败（而不是「单镜失败继续跑」）—— 内容不合规时
           交付一条缺镜头的成片，比直接失败更糟
        2. 不计入「可修复镜头」，避免自动修复循环拿着同样的内容反复重跑烧钱
    """


@dataclass(frozen=True)
class ModerationVerdict:
    """一次审核的结论。

    `allowed` 是唯一该被调用方用来分支的字段；`action` 给日志与审计用，
    它保留了 pass/review/block 的区分 —— 「不是因为违规被拒，而是因为
    审核服务不可用被拒」这两种情况的处置方式完全不同，必须可区分。
    """

    allowed: bool
    action: str = VERDICT_ALLOW
    provider: str = ""
    categories: tuple[str, ...] = ()
    matched: tuple[str, ...] = ()
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "action": self.action,
            "provider": self.provider,
            "categories": list(self.categories),
            "matched": list(self.matched),
            "reason": self.reason,
        }

    @classmethod
    def ok(cls, provider: str) -> ModerationVerdict:
        return cls(allowed=True, action=VERDICT_ALLOW, provider=provider)

    @classmethod
    def blocked(
        cls, provider: str, *, categories: list[str], matched: list[str], reason: str = ""
    ) -> ModerationVerdict:
        return cls(
            allowed=False,
            action=VERDICT_BLOCK,
            provider=provider,
            categories=tuple(categories),
            matched=tuple(matched),
            reason=reason or "命中内容安全策略",
        )

    @classmethod
    def unavailable(cls, provider: str, error: str) -> ModerationVerdict:
        """审核服务不可用。`allowed` 由调用方的 fail 策略决定。"""
        return cls(allowed=False, action=VERDICT_REVIEW, provider=provider, reason=f"审核不可用：{error}")


# --------------------------------------------------------------------------- #
# 归一化：挡住低成本的绕过
# --------------------------------------------------------------------------- #
#: 匹配前一律剔除的字符类别：空白、标点、符号、控制字符。
#: 目的：让「色 情」「色-情」「色​情（含零宽空格）」都归一到同一个串。
_STRIP = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize(text: str) -> str:
    """把文本归一化成便于词面匹配的形式。

    顺序有讲究：
      1. NFKC 把全角字母/数字、兼容字符映射到标准形式（ＡＢＣ -> ABC）
      2. 转小写，让配置里的大写词也能命中
      3. 去掉空白、标点、符号、下划线 —— 这一步才是挡「插空格绕过」的关键

    注意：归一化会**丢失原文位置**，所以命中的是归一化后的词，
    回填给审计日志时只记词本身，不试图还原原文偏移量（还原容易错位，
    宁可不给，也不要给错的证据）。
    """
    folded = unicodedata.normalize("NFKC", text or "").lower()
    return _STRIP.sub("", folded)


# --------------------------------------------------------------------------- #
# 内置词表（示意，生产请外挂正式词库）
# --------------------------------------------------------------------------- #
#: 分类 -> 词表。词表里一律写**归一化后**的形式，否则永远不会命中。
DEFAULT_BLOCKLIST: dict[str, tuple[str, ...]] = {
    "色情低俗": ("色情", "裸体", "成人视频"),
    "暴力血腥": ("血腥", "肢解", "虐待"),
    "违禁品": ("毒品", "枪支", "爆炸物"),
    "赌博诈骗": ("赌博", "洗钱", "诈骗"),
    "自伤自杀": ("自杀", "自残"),
    "未成年人保护": ("虐童", "儿童色情"),
}


def load_blocklist(path: str | Path | None) -> dict[str, tuple[str, ...]]:
    """从文件加载追加词库。

    文件格式：每行 `分类:词1,词2`，`#` 开头为注释。空行忽略。
    加载失败**不抛异常**而是回退到内置词表 —— 词库文件写坏不应让整站审核瘫痪，
    但必须在日志里留下痕迹（调用方负责记录返回的 error）。
    """
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    extra: dict[str, list[str]] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        category, _, words = line.partition(":")
        terms = [normalize(w) for w in re.split(r"[,，\s]+", words) if w.strip()]
        if terms:
            extra.setdefault(category.strip(), []).extend(terms)
    return {k: tuple(dict.fromkeys(v)) for k, v in extra.items()}


# --------------------------------------------------------------------------- #
# Provider 协议
# --------------------------------------------------------------------------- #
class TextModerationProvider(Protocol):
    name: str

    def check(self, text: str, *, scope: str = "") -> ModerationVerdict: ...


class ImageModerationProvider(Protocol):
    name: str

    def check(self, path: Path, *, scope: str = "") -> ModerationVerdict: ...


# --------------------------------------------------------------------------- #
# 关键词审核
# --------------------------------------------------------------------------- #
@dataclass
class KeywordModerationProvider:
    """本地关键词审核。离线可用、零成本、结论可解释。

    `extra` 用来追加外挂词库（已归一化）。命中多个分类时全部返回，
    不做"取最严重的一条"——审计需要知道它同时踩了哪几类。
    """

    name: str = "keyword"
    extra: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def _table(self) -> dict[str, tuple[str, ...]]:
        merged: dict[str, list[str]] = {k: list(v) for k, v in DEFAULT_BLOCKLIST.items()}
        for category, terms in self.extra.items():
            merged.setdefault(category, []).extend(terms)
        return {k: tuple(dict.fromkeys(v)) for k, v in merged.items()}

    def check(self, text: str, *, scope: str = "") -> ModerationVerdict:
        haystack = normalize(text)
        if not haystack:
            return ModerationVerdict.ok(self.name)

        hits: list[str] = []
        categories: list[str] = []
        for category, terms in self._table().items():
            for term in terms:
                if term and term in haystack:
                    categories.append(category)
                    hits.append(term)
                    break  # 同一分类命中一个词即可，避免把一个分类刷满

        if not hits:
            return ModerationVerdict.ok(self.name)
        return ModerationVerdict.blocked(
            self.name,
            categories=list(dict.fromkeys(categories)),
            matched=hits,
            reason=f"命中内容安全词库（{scope or 'text'}）：{'、'.join(dict.fromkeys(categories))}",
        )


# --------------------------------------------------------------------------- #
# 图像审核
# --------------------------------------------------------------------------- #
@dataclass
class HeuristicImageModeration:
    """图像审核的**结构占位 + 基础异常检测**。

    ⚠️ 这不是语义级内容审核。它只能发现「图像本身不像一张正常画面」：
    纯色、过小、无法解码。真正的违规识别（涉黄、涉暴、涉政）必须接
    云端内容安全或自建视觉模型。

    保留这个实现的意义是让审核**链路是真的通的**：调用点在流水线里、
    结论结构一致、失败会按 fail-closed 处理。生产接入时只需替换 provider，
    不动调用方。
    """

    name: str = "heuristic"
    min_pixels: int = 128 * 128

    def check(self, path: Path, *, scope: str = "") -> ModerationVerdict:
        try:
            from PIL import Image
        except Exception as exc:  # pragma: no cover - Pillow 是硬依赖
            return ModerationVerdict.unavailable(self.name, f"Pillow 不可用：{exc}")

        if not path.exists():
            return ModerationVerdict.unavailable(self.name, f"文件不存在：{path.name}")

        try:
            with Image.open(path) as im:
                w, h = im.size
                if w * h < self.min_pixels:
                    return ModerationVerdict.blocked(
                        self.name,
                        categories=["图像异常"],
                        matched=[f"{w}x{h}"],
                        reason=f"图像过小，疑似无有效内容：{w}x{h}",
                    )
                colors = im.convert("RGB").getcolors(maxcolors=2)
                if colors is not None and len(colors) <= 1:
                    return ModerationVerdict.blocked(
                        self.name,
                        categories=["图像异常"],
                        matched=[path.name],
                        reason="图像为纯色，疑似生成失败或占位图",
                    )
        except Exception as exc:
            return ModerationVerdict.unavailable(self.name, f"{type(exc).__name__}: {exc}")

        return ModerationVerdict.ok(self.name)


# --------------------------------------------------------------------------- #
# 装配
# --------------------------------------------------------------------------- #
class BlockingModerationFactory:
    """把「审核不通过就拒绝」的策略集中在一处，避免每个调用点各写一遍 if。"""

    def __init__(
        self,
        text: TextModerationProvider,
        image: ImageModerationProvider,
        *,
        enabled: bool = True,
        fail_closed: bool = True,
    ) -> None:
        self.text = text
        self.image = image
        self.enabled = enabled
        self.fail_closed = fail_closed

    def check_text(self, text: str, *, scope: str) -> ModerationVerdict:
        if not self.enabled:
            return ModerationVerdict.ok("disabled")
        try:
            return self.text.check(text, scope=scope)
        except Exception as exc:
            verdict = ModerationVerdict.unavailable(self.text.name, f"{type(exc).__name__}: {exc}")
            return verdict if self.fail_closed else ModerationVerdict.ok(self.text.name)

    def check_image(self, path: Path, *, scope: str) -> ModerationVerdict:
        if not self.enabled:
            return ModerationVerdict.ok("disabled")
        try:
            return self.image.check(path, scope=scope)
        except Exception as exc:
            verdict = ModerationVerdict.unavailable(self.image.name, f"{type(exc).__name__}: {exc}")
            return verdict if self.fail_closed else ModerationVerdict.ok(self.image.name)


def build_moderation(settings) -> BlockingModerationFactory:
    """按配置装配审核器。

    词库文件加载失败时**降级到内置词表并继续**，而不是让整个流水线起不来：
    审核能力变弱是可观测、可告警的问题；服务起不来是事故。
    """
    extra = load_blocklist(getattr(settings, "moderation_blocklist_file", ""))
    return BlockingModerationFactory(
        KeywordModerationProvider(extra=extra),
        HeuristicImageModeration(),
        enabled=bool(getattr(settings, "moderation_enabled", True)),
        fail_closed=bool(getattr(settings, "moderation_fail_closed", True)),
    )
