"""请求/任务上下文。

用 contextvars 串起一条请求或一个 Job 的全链路标识，
让每一行日志都能自动带上 request_id / org_id / job_id，无需层层传参。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
org_id_var: ContextVar[str] = ContextVar("org_id", default="")
user_id_var: ContextVar[str] = ContextVar("user_id", default="")
job_id_var: ContextVar[str] = ContextVar("job_id", default="")
project_id_var: ContextVar[str] = ContextVar("project_id", default="")


def current_context() -> dict[str, str]:
    """供日志处理器读取的上下文字典（空值自动省略）。"""
    ctx = {
        "request_id": request_id_var.get(),
        "org_id": org_id_var.get(),
        "user_id": user_id_var.get(),
        "job_id": job_id_var.get(),
        "project_id": project_id_var.get(),
    }
    return {k: v for k, v in ctx.items() if v}


@contextmanager
def bind(**kwargs: str) -> Iterator[None]:
    """临时绑定若干上下文字段，退出时自动还原。"""
    vars_map = {
        "request_id": request_id_var,
        "org_id": org_id_var,
        "user_id": user_id_var,
        "job_id": job_id_var,
        "project_id": project_id_var,
    }
    tokens = []
    for key, value in kwargs.items():
        var = vars_map.get(key)
        if var is not None and value:
            tokens.append((var, var.set(str(value))))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
