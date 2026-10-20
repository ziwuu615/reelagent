"""系统路由：健康检查、就绪探针、指标、状态页。

探针语义区分（K8s 里搞错会导致滚动更新时流量打到没准备好的实例）：
  /livez    进程活着即可。**不查数据库**——数据库抖动不该导致容器被杀掉重启。
  /readyz   能接流量：数据库可连、迁移已就绪。不满足则从负载均衡摘除。
  /health   人类可读的汇总（同 readyz，但返回详细诊断）。
  /metrics  Prometheus 抓取端点，可选加访问令牌保护。
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text

from ...config import Settings
from ...db.session import get_engine
from ...deps import AppSettings, DbSession
from ...domain.enums import MembershipRole
from ...observability import render_metrics
from ...security import CurrentPrincipal
from ...services import jobs as jobs_svc

router = APIRouter(tags=["system"])

VERSION = "1.0.0"


@router.get("/livez", include_in_schema=False)
async def livez() -> dict[str, str]:
    """存活探针：只要进程能响应就返回 200，不触碰任何外部依赖。"""
    return {"status": "alive"}


@router.get("/readyz", include_in_schema=False)
async def readyz(settings: AppSettings) -> Response:
    """就绪探针：数据库可用才算就绪。"""
    checks = await _run_checks(settings)
    healthy = all(c["ok"] for c in checks.values() if c.get("critical"))
    return JSONResponse(
        {"status": "ready" if healthy else "not_ready", "checks": checks},
        status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@router.get("/health", include_in_schema=False)
async def health(settings: AppSettings) -> Response:
    checks = await _run_checks(settings)
    healthy = all(c["ok"] for c in checks.values() if c.get("critical"))
    return JSONResponse(
        {
            "status": "ok" if healthy else "degraded",
            "version": VERSION,
            "env": settings.env.value,
            "checks": checks,
        },
        status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


async def _run_checks(settings: Settings) -> dict[str, dict[str, object]]:
    checks: dict[str, dict[str, object]] = {}

    # 数据库
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = {
            "ok": True,
            "critical": True,
            "backend": "sqlite" if settings.database.is_sqlite else "postgresql",
        }
    except Exception as exc:
        checks["database"] = {"ok": False, "critical": True, "error": type(exc).__name__}

    # 存储
    try:
        from ...services.storage import get_storage

        storage = get_storage(settings)
        probe_key = "_healthcheck/probe.txt"
        await storage.put_bytes(probe_key, b"ok")
        await storage.delete(probe_key)
        checks["storage"] = {"ok": True, "critical": True, "backend": storage.backend}
    except Exception as exc:
        checks["storage"] = {"ok": False, "critical": True, "error": type(exc).__name__}

    # 供应商配置（非关键：没配也应当能起服务，只是不能生成）
    providers = settings.providers
    checks["providers"] = {
        "ok": providers.video.configured,
        "critical": False,
        "video": providers.video.provider if providers.video.configured else "未配置",
        "image": providers.image.provider if providers.image.configured else "未配置",
        "llm": providers.llm.provider if providers.llm.configured else "未配置",
    }

    checks["billing"] = {
        "ok": True,
        "critical": False,
        "enabled": settings.billing.enabled,
    }
    return checks


@router.get("/metrics", include_in_schema=False)
async def metrics(settings: AppSettings) -> Response:
    if not settings.observability.metrics_enabled:
        return PlainTextResponse("metrics disabled", status_code=404)
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)


@router.get("/api/v1/system/status", tags=["system"])
async def system_status(
    settings: AppSettings, session: DbSession, principal: CurrentPrincipal
) -> dict[str, object]:
    """状态数据：队列深度、版本、配置快照。

    **需要登录**。配置快照（供应商、存储后端、计费开关等）默认只对管理员可见——
    它虽然不含密钥，但仍然是攻击者做信息收集的现成素材。
    """
    depths = await jobs_svc.queue_depths(session)
    payload: dict[str, object] = {
        "version": VERSION,
        "env": settings.env.value,
        "queues": depths,
    }
    if principal.role.rank >= MembershipRole.ADMIN.rank:
        payload["config"] = settings.safe_dump()
    return payload


@router.get("/api/v1/system/ping", tags=["system"])
async def ping() -> dict[str, str]:
    return {"pong": "ok"}
