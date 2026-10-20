"""结构化日志。

生产输出 JSON（可直接被 Loki / ELK / CloudWatch 采集），
本地输出带颜色的 console。

**密钥脱敏是硬需求**：日志里出现 API Key、密码、会话 Cookie 是最常见的事故来源，
因此这里在处理器层面做正则擦除，而不是指望每个调用点都记得脱敏。
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

from .context import current_context

# --------------------------------------------------------------------------- #
# 脱敏
# --------------------------------------------------------------------------- #
_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|apikey|secret|password|passwd|token|authorization|bearer|"
    r"client[_-]?secret|webhook[_-]?secret|session|cookie|pepper|dsn)",
    re.IGNORECASE,
)
#: sk-xxxx / Bearer xxxx 这类裸值
_SECRET_VALUE_PATTERN = re.compile(
    r"\b(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,}|"
    r"whsec_[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{12,})\b"
)

REDACTED = "***REDACTED***"


def redact_value(value: Any, *, depth: int = 0) -> Any:
    """递归擦除疑似密钥的值。深度受限，避免大对象上的性能问题。"""
    if depth > 4:
        return value
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and _SECRET_KEY_PATTERN.search(k):
                out[k] = REDACTED
            else:
                out[k] = redact_value(v, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return type(value)(redact_value(v, depth=depth + 1) for v in value)
    if isinstance(value, str):
        return _SECRET_VALUE_PATTERN.sub(REDACTED, value)
    return value


def _redaction_processor(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return redact_value(event_dict)


def _context_processor(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key, value in current_context().items():
        event_dict.setdefault(key, value)
    return event_dict


def _drop_color_message(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    event_dict.pop("color_message", None)
    return event_dict


# --------------------------------------------------------------------------- #
# 初始化
# --------------------------------------------------------------------------- #
_configured = False


def configure_logging(
    *,
    level: str = "INFO",
    fmt: str = "json",
    service: str = "reelagent",
    version: str = "1.0.0",
) -> None:
    """把 structlog 与标准库 logging 打通。

    为什么不用 PrintLoggerFactory：那样 uvicorn / SQLAlchemy / httpx 的日志
    会绕过我们的处理器，既不统一格式也**不做密钥脱敏**。
    走 `ProcessorFormatter` 后，第三方库的日志同样经过脱敏链。
    """
    global _configured

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _context_processor,
        _drop_color_message,
        _redaction_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: Any = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(logging.getLevelNamesMapping().get(level.upper(), logging.INFO))

    # 第三方库降噪：这些库在 INFO 级别会淹没业务日志
    for noisy in (
        "uvicorn.access",
        "uvicorn.error",
        "sqlalchemy.engine.Engine",
        "sqlalchemy.pool",
        "httpx",
        "httpcore",
        "aiosqlite",
        "asyncio",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("reelagent").setLevel(logging.getLevelNamesMapping().get(level.upper(), logging.INFO))
    logging.getLogger("reelagent_cloud").setLevel(
        logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    )

    structlog.contextvars.bind_contextvars(service=service, version=version)
    _configured = True


def get_logger(name: str | None = None) -> Any:
    if not _configured:
        configure_logging()
    return structlog.get_logger(name) if name else structlog.get_logger()
