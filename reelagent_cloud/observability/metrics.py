"""Prometheus 指标。

约定：**标签基数必须可控**。HTTP 指标一律使用路由模板（`/api/projects/{id}`）
而不是真实路径，否则每个 ID 都会变成一条时间序列，把 Prometheus 打爆。
"""

from __future__ import annotations

import time
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

#: 独立 registry，避免与进程内其它库的默认 collector 冲突，也便于测试隔离
REGISTRY = CollectorRegistry(auto_describe=True)

# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
http_requests_total = Counter(
    "reelagent_http_requests_total",
    "HTTP 请求总数",
    ["method", "route", "status"],
    registry=REGISTRY,
)
http_request_duration = Histogram(
    "reelagent_http_request_duration_seconds",
    "HTTP 请求耗时",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
    registry=REGISTRY,
)
http_requests_in_flight = Gauge(
    "reelagent_http_requests_in_flight",
    "处理中的 HTTP 请求数",
    registry=REGISTRY,
)

# --------------------------------------------------------------------------- #
# 任务
# --------------------------------------------------------------------------- #
jobs_total = Counter(
    "reelagent_jobs_total",
    "任务处理结果总数",
    ["kind", "status"],
    registry=REGISTRY,
)
job_duration = Histogram(
    "reelagent_job_duration_seconds",
    "任务执行耗时",
    ["kind"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600),
    registry=REGISTRY,
)
job_queue_depth = Gauge(
    "reelagent_job_queue_depth",
    "各状态任务数量",
    ["status"],
    registry=REGISTRY,
)
job_retries_total = Counter(
    "reelagent_job_retries_total",
    "任务重试次数",
    ["kind"],
    registry=REGISTRY,
)
job_lease_expired_total = Counter(
    "reelagent_job_lease_expired_total",
    "因 worker 崩溃导致租约过期的任务数",
    registry=REGISTRY,
)

# --------------------------------------------------------------------------- #
# 供应商
# --------------------------------------------------------------------------- #
provider_calls_total = Counter(
    "reelagent_provider_calls_total",
    "供应商调用总数",
    ["provider", "capability", "status"],
    registry=REGISTRY,
)
provider_call_duration = Histogram(
    "reelagent_provider_call_duration_seconds",
    "供应商调用耗时",
    ["provider", "capability"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800),
    registry=REGISTRY,
)
provider_cost_usd_total = Counter(
    "reelagent_provider_cost_usd_total",
    "供应商成本累计（美元）",
    ["provider", "capability"],
    registry=REGISTRY,
)
provider_circuit_open = Gauge(
    "reelagent_provider_circuit_open",
    "供应商熔断器状态（1=打开）",
    ["provider", "capability"],
    registry=REGISTRY,
)

# --------------------------------------------------------------------------- #
# 业务
# --------------------------------------------------------------------------- #
credits_consumed_total = Counter(
    "reelagent_credits_consumed_total",
    "累计消耗额度",
    ["org_id"],
    registry=REGISTRY,
)
projects_created_total = Counter(
    "reelagent_projects_created_total",
    "累计创建项目数",
    registry=REGISTRY,
)
shots_generated_total = Counter(
    "reelagent_shots_generated_total",
    "镜头生成结果",
    ["status"],
    registry=REGISTRY,
)
worker_active = Gauge(
    "reelagent_worker_active",
    "活跃 worker 数",
    registry=REGISTRY,
)
worker_jobs_running = Gauge(
    "reelagent_worker_jobs_running",
    "当前正在执行的 Job 数",
    ["worker_id"],
    registry=REGISTRY,
)

# --------------------------------------------------------------------------- #
# 安全
# --------------------------------------------------------------------------- #
auth_failures_total = Counter(
    "reelagent_auth_failures_total",
    "认证失败次数",
    ["reason"],
    registry=REGISTRY,
)
rate_limit_hits_total = Counter(
    "reelagent_rate_limit_hits_total",
    "触发限流的次数",
    ["scope"],
    registry=REGISTRY,
)


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
class Timer:
    """轻量计时上下文：`with Timer() as t: ...; t.seconds`"""

    def __enter__(self) -> Timer:
        self._start = time.perf_counter()
        self.seconds = 0.0
        return self

    def __exit__(self, *exc: object) -> None:
        self.seconds = time.perf_counter() - self._start


def observe_http(method: str, route: str, status: int, seconds: float) -> None:
    http_requests_total.labels(method=method, route=route, status=str(status)).inc()
    http_request_duration.labels(method=method, route=route).observe(seconds)


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def snapshot() -> dict[str, Any]:
    """状态页用的轻量快照（只读几个关键量，不做全量解析）。"""

    def _value(metric: Any, **labels: str) -> float:
        try:
            return float(metric.labels(**labels)._value.get())
        except Exception:
            return 0.0

    return {
        "jobs_running": _value(worker_jobs_running, worker_id="__all__"),
        "queue_pending": _value(job_queue_depth, status="pending"),
        "queue_running": _value(job_queue_depth, status="running"),
    }
