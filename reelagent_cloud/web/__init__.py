"""模板环境。

Jinja2 全局变量与过滤器集中在这里，模板里不写业务逻辑，
只做展示格式化。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.templating import Jinja2Templates

from ..config import Settings
from ..domain.enums import JobStage, JobStatus, ProjectStatus
from ..domain.plans import PLANS, all_plans

TEMPLATE_DIR = __import__("pathlib").Path(__file__).resolve().parent / "templates"
STATIC_DIR = __import__("pathlib").Path(__file__).resolve().parent / "static"


# --------------------------------------------------------------------------- #
# 过滤器
# --------------------------------------------------------------------------- #
def fmt_datetime(value: Any, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone().strftime(fmt)
    return str(value)


def fmt_relative(value: Any) -> str:
    if not isinstance(value, datetime):
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - value
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    if seconds < 86400 * 30:
        return f"{seconds // 86400} 天前"
    return value.astimezone().strftime("%Y-%m-%d")


def fmt_bytes(value: Any) -> str:
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def fmt_duration(value: Any) -> str:
    try:
        seconds = float(value or 0)
    except (TypeError, ValueError):
        return "0.0s"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}分{rest}秒"


def fmt_credits(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def fmt_money_cny(value: Any) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        return "¥0"
    return "免费" if amount == 0 else f"¥{amount:.0f}"


def fmt_percent(value: Any, digits: int = 0) -> str:
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def status_label(value: Any) -> str:
    """把枚举翻译成中文标签，供界面展示。"""
    raw = value.value if hasattr(value, "value") else str(value)
    return {
        # 项目
        ProjectStatus.DRAFT.value: "草稿",
        ProjectStatus.QUEUED.value: "排队中",
        ProjectStatus.RUNNING.value: "生成中",
        ProjectStatus.READY.value: "已完成",
        ProjectStatus.FAILED.value: "失败",
        ProjectStatus.ARCHIVED.value: "已归档",
        # 任务
        JobStatus.PENDING.value: "排队中",
        JobStatus.SUCCEEDED.value: "成功",
        JobStatus.DEAD.value: "失败",
        JobStatus.CANCELLED.value: "已取消",
    }.get(raw, raw)


def status_tone(value: Any) -> str:
    """状态 -> 徽章配色（模板里直接用）。"""
    raw = value.value if hasattr(value, "value") else str(value)
    if raw in {ProjectStatus.READY.value, JobStatus.SUCCEEDED.value, "active", "delivered"}:
        return "ok"
    if raw in {
        ProjectStatus.RUNNING.value,
        ProjectStatus.QUEUED.value,
        JobStatus.PENDING.value,
        JobStatus.RUNNING.value,
        "past_due",
    }:
        return "busy"
    if raw in {ProjectStatus.FAILED.value, JobStatus.DEAD.value, "failed", "unpaid"}:
        return "bad"
    if raw == JobStatus.CANCELLED.value:
        return "muted"
    return "neutral"


def stage_label(value: Any) -> str:
    raw = value.value if hasattr(value, "value") else str(value)
    return {
        JobStage.QUEUED.value: "排队中",
        JobStage.PLANNING.value: "拆解剧本",
        JobStage.ASSETS.value: "生成角色与场景",
        JobStage.GENERATING.value: "逐镜生成",
        JobStage.EVALUATING.value: "质量评测",
        JobStage.RENDERING.value: "合成成片",
        JobStage.DONE.value: "完成",
    }.get(raw, raw)


def shot_size_badge(value: Any) -> str:
    return {"特写": "特写", "近景": "近景", "中景": "中景", "全景": "全景", "远景": "远景"}.get(
        str(value), str(value)
    )


# --------------------------------------------------------------------------- #
# 环境
# --------------------------------------------------------------------------- #
def build_templates(settings: Settings) -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

    templates.env.filters.update(
        {
            "dt": fmt_datetime,
            "relative": fmt_relative,
            "bytes": fmt_bytes,
            "duration": fmt_duration,
            "credits": fmt_credits,
            "money": fmt_money_cny,
            "percent": fmt_percent,
            "status_label": status_label,
            "status_tone": status_tone,
            "stage_label": stage_label,
            "shot_size": shot_size_badge,
        }
    )

    templates.env.globals.update(
        {
            "settings": settings,
            "product_name": settings.product_name,
            "plans": all_plans(),
            "plan_map": PLANS,
            "support_email": settings.support_email,
            "base_url": settings.base_url.rstrip("/"),
            "billing_enabled": settings.billing.enabled,
            "current_year": datetime.now().year,
        }
    )
    return templates
