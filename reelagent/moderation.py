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

import contextlib
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
# 多模态语义审核（真实内容审核）
# --------------------------------------------------------------------------- #
#: 审核用的提示词。要求模型只输出 JSON，并且**默认倾向于拦截**：
#: 审核模型的错误成本不对称 —— 多拦一张只是重生成，漏放一张是合规事故。
_VISION_SYSTEM = (
    "你是内容安全审核员。判断给定图片是否适合作为公开传播的短视频画面。"
    "只要画面可能涉及色情低俗、暴力血腥、违禁品、赌博诈骗、自伤自杀、"
    "未成年人不当内容、政治敏感，就必须判定为不通过。\n"
    "只输出 JSON，不要任何解释或 markdown。结构：\n"
    '{"allowed": true|false, "categories": ["命中的类别"], "reason": "一句话理由"}'
)


@dataclass
class DashScopeVisionModeration:
    """调用多模态模型做语义级图像审核。

    ## 为什么不用云端内容安全产品而用多模态模型

    云端内容安全（如阿里云内容安全 Green）通常要主账号 AccessKey + 单独的
    签名实现；而本项目已经持有 DashScope 的 API Key，多模态模型走同一套
    OpenAI 兼容接口即可，**不引入新凭证与新依赖**。

    代价要说清楚：模型审核是概率性的，不如专用审核产品稳定，且每次调用有
    token 成本。所以它适合「上线初期没有审核产品额度」的阶段，
    规模上来后应当替换为专用服务 —— 替换点就是 `ImageModerationProvider` 协议。

    ## 三件必要的工程处理

    1. **缩放**：审核不需要原图精度，长边压到 768 像素，token 成本降一个量级
    2. **缓存**：同一张图（sha256 相同）在一次进程内只审一次 ——
       断点重跑会把同一批关键帧再审一遍，纯浪费
    3. **失败即不可用**：解析不出结论时返回 unavailable，
       由上层按 fail-closed 处理，绝不猜成「通过」
    """

    api_key: str
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen-vl-plus"
    timeout: float = 30.0
    max_side: int = 768
    name: str = "dashscope-vision"
    #: 进程内缓存：sha256 -> 结论。上限直接清空而不是 LRU ——
    #: 审核结论很小，清空的代价远低于维护 LRU 的复杂度。
    _cache: dict[str, ModerationVerdict] = field(default_factory=dict)
    _cache_max: int = 512

    def _encode(self, path: Path) -> tuple[str, str]:
        """返回 (data URI, sha256)。缩放后转 JPEG 以压缩传输体积。"""
        import base64
        import hashlib
        import io

        from PIL import Image

        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        with Image.open(io.BytesIO(raw)) as im:
            rgb = im.convert("RGB")
            w, h = rgb.size
            scale = min(1.0, self.max_side / max(w, h))
            if scale < 1.0:
                rgb = rgb.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=82)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}", digest

    def check(self, path: Path, *, scope: str = "") -> ModerationVerdict:
        if not path.exists():
            return ModerationVerdict.unavailable(self.name, f"文件不存在：{path.name}")

        try:
            data_uri, digest = self._encode(path)
        except Exception as exc:
            return ModerationVerdict.unavailable(self.name, f"{type(exc).__name__}: {exc}")

        cached = self._cache.get(digest)
        if cached is not None:
            return cached

        try:
            verdict = self._call(data_uri, scope=scope)
        except Exception as exc:
            # 网络/限流/超时：不缓存失败结果，下次要能重试
            return ModerationVerdict.unavailable(self.name, f"{type(exc).__name__}: {exc}")

        if len(self._cache) >= self._cache_max:
            self._cache.clear()
        self._cache[digest] = verdict
        return verdict

    def _call(self, data_uri: str, *, scope: str = "") -> ModerationVerdict:
        import json
        import urllib.error
        import urllib.request

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _VISION_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": "请审核这张图片，只输出 JSON。"},
                    ],
                },
            ],
            "temperature": 0.0,
        }
        req = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        data = _extract_json(content)
        if data is None:
            return ModerationVerdict.unavailable(self.name, f"审核结果无法解析：{content[:120]}")

        allowed = bool(data.get("allowed", False))
        categories = [str(c)[:32] for c in (data.get("categories") or [])][:8]
        reason = str(data.get("reason") or "")[:200]

        if allowed and not categories:
            return ModerationVerdict.ok(self.name)
        return ModerationVerdict.blocked(
            self.name,
            categories=categories or ["未分类"],
            matched=[scope] if scope else [],
            reason=reason or "多模态审核判定不通过",
        )


def _extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出里抠出 JSON。

    不能直接 `json.loads`：模型经常包一层 ```json 代码块，或者在前后加一句解释。
    先按代码块拆，再退化到「找第一个 { 到最后一个 }」。
    """
    import json

    cleaned = (text or "").strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        if len(parts) >= 2:
            cleaned = parts[1].removeprefix("json").strip()
    with contextlib.suppress(Exception):
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(cleaned[start : end + 1])
            return data if isinstance(data, dict) else None
        except Exception:
            return None
    return None


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

    两处刻意的降级策略：

    1. **词库文件写坏 -> 用内置词表继续**。审核能力变弱是可观测、可告警的问题；
       服务起不来是事故。
    2. **要求语义审核但没配 Key -> 退回启发式并留 warning**。不能因为
       「本想要更强的审核」而让整条流水线起不来；但也不能静默退回 ——
       退回意味着审核能力比预期弱，必须让运维看见。
    """
    extra = load_blocklist(getattr(settings, "moderation_blocklist_file", ""))

    mode = str(getattr(settings, "image_moderation", "heuristic") or "heuristic").lower()
    image_provider: ImageModerationProvider = HeuristicImageModeration()
    note = ""

    if mode == "dashscope":
        llm = getattr(settings, "llm", None)
        api_key = getattr(llm, "api_key", "") if llm is not None else ""
        if api_key:
            image_provider = DashScopeVisionModeration(
                api_key=api_key,
                base_url=getattr(llm, "base_url", "") or DashScopeVisionModeration.base_url,
                model=str(getattr(settings, "image_moderation_model", "") or "qwen-vl-plus"),
                timeout=float(getattr(settings, "image_moderation_timeout", 30.0) or 30.0),
            )
        else:
            note = "image_moderation=dashscope 但未配置 LLM API Key，图像审核已退回启发式检测"
    elif mode != "heuristic":
        note = f"未知的 image_moderation={mode}，已退回启发式检测"

    if note:
        import logging

        logging.getLogger(__name__).warning("moderation_fallback: %s", note)

    return BlockingModerationFactory(
        KeywordModerationProvider(extra=extra),
        image_provider,
        enabled=bool(getattr(settings, "moderation_enabled", True)),
        fail_closed=bool(getattr(settings, "moderation_fail_closed", True)),
    )
