"""JSON API（/api/v1/*）。

设计约定：
  - **鉴权**：会话 Cookie 或 `Authorization: Bearer reel_xxx` 均可
  - **租户隔离**：所有查询强制带 org 过滤，绝不依赖调用方传 org_id
  - **幂等**：写操作支持 `Idempotency-Key` 头，重复提交返回同一资源
  - **限流**：按身份（用户 / API Key / IP）分别限流
  - **错误**：统一 `{"error": {"code","message","details"}, "request_id"}`

这套 API 是「产品可被集成」的关键——客户把生成能力接进自己的流水线，
比在网页上点按钮的价值高得多。
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import JSONResponse

from ...db.models import Job
from ...deps import AppSettings, DbSession
from ...domain.enums import ProjectStatus
from ...domain.errors import Forbidden, ValidationFailed
from ...domain.plans import all_plans, get_plan
from ...observability import get_logger
from ...security import CurrentPrincipal, WritablePrincipal
from ...services import accounts, billing, credits, jobs, projects
from ..ratelimit import get_limiter, limits_from

log = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["api"])


# --------------------------------------------------------------------------- #
# 序列化
# --------------------------------------------------------------------------- #
def _project_brief(p: projects.Project) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "status": p.status.value,
        "style": p.style,
        "aspect_ratio": p.aspect_ratio,
        "total_shots": p.total_shots,
        "finished_shots": p.finished_shots,
        "duration_seconds": round(p.duration_seconds, 2),
        "credits_spent": p.credits_spent,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


def _job_view(j: Job) -> dict:
    return {
        "id": j.id,
        "kind": j.kind.value,
        "status": j.status.value,
        "stage": j.stage.value,
        "progress": round(j.progress, 3),
        "attempts": j.attempts,
        "max_attempts": j.max_attempts,
        "error": j.error or None,
        "error_code": j.error_code or None,
        "result": j.result or {},
        "created_at": j.created_at.isoformat(),
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
    }


# --------------------------------------------------------------------------- #
# 当前身份
# --------------------------------------------------------------------------- #
@router.get("/me", summary="当前身份与套餐")
async def me(session: DbSession, principal: CurrentPrincipal, settings: AppSettings) -> dict:
    org = await projects.get_org(session, principal.org_id)
    get_plan(org.plan)
    sub = await billing.subscription_for(session, org.id)
    balance = await credits.get_balance(session, org.id)
    counts = await projects.counts_for_org(session, org.id)
    return {
        "actor": {
            "kind": principal.kind,
            "user_id": principal.user_id,
            "email": principal.user.email if principal.user else None,
            "role": principal.role.value,
            "scopes": sorted(principal.scopes),
        },
        "organization": {"id": org.id, "name": org.name, "slug": org.slug},
        "plan": billing.current_plan_summary(org, sub),
        "credits": {"balance": balance, "unit": "credits", "usd_rate": settings.billing.credits_per_usd},
        "usage": counts,
    }


@router.get("/plans", summary="套餐列表（公开）")
async def list_plans() -> dict:
    return {
        "items": [
            {
                "tier": p.tier.value,
                "name": p.name,
                "tagline": p.tagline,
                "price_cny": p.price_cny,
                "monthly_credits": p.monthly_credits,
                "max_shots_per_project": p.max_shots_per_project,
                "max_concurrent_jobs": p.max_concurrent_jobs,
                "max_projects": p.max_projects,
                "max_org_members": p.max_org_members,
                "allow_premium_tier": p.allow_premium_tier,
                "support": p.support,
                "highlights": p.highlights,
            }
            for p in all_plans()
        ]
    }


# --------------------------------------------------------------------------- #
# 项目
# --------------------------------------------------------------------------- #
@router.get("/projects", summary="项目列表")
async def api_list_projects(
    session: DbSession,
    principal: CurrentPrincipal,
    status_filter: str = Query("", alias="status"),
    q: str = Query("", description="按标题或素材搜索"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    status_value = None
    if status_filter:
        try:
            status_value = ProjectStatus(status_filter)
        except ValueError as exc:
            raise ValidationFailed(f"未知状态：{status_filter}") from exc

    items, total = await projects.list_projects(
        session, org_id=principal.org_id, status=status_value, search=q, limit=limit, offset=offset
    )
    return {"items": [_project_brief(p) for p in items], "total": total, "limit": limit, "offset": offset}


@router.post("/projects", status_code=status.HTTP_201_CREATED, summary="创建项目并开始生成")
async def api_create_project(
    request: Request,
    session: DbSession,
    principal: WritablePrincipal,
    settings: AppSettings,
    idempotency_key: str = Header("", alias="Idempotency-Key"),
) -> JSONResponse:
    """创建项目。

    请求体为 JSON。支持 `Idempotency-Key` 头：同一 Key 重复提交不会重复扣费，
    直接返回首次创建的项目 —— 这是客户端重试安全的前提。
    """
    limiter = get_limiter(settings)
    await limiter.check(limits_from(settings)["project_create"], principal.org_id)
    if principal.kind == "api_key" and principal.api_key is not None:
        await limiter.check(limits_from(settings)["api_key"], principal.api_key.id)

    try:
        body = await request.json()
    except Exception as exc:
        raise ValidationFailed("请求体必须是合法 JSON") from exc
    if not isinstance(body, dict):
        raise ValidationFailed("请求体必须是 JSON 对象")

    seed_text = str(body.get("seed_text") or body.get("text") or "").strip()
    if not seed_text:
        raise ValidationFailed("缺少 seed_text 字段")

    # 未显式提供 Idempotency-Key 时，用内容指纹兜底，避免同一素材被连点两次扣两份钱
    key = idempotency_key.strip()[:120] or _fingerprint(seed_text, body)

    project, job, quote = await projects.create_project(
        session,
        settings,
        principal=principal,
        seed_text=seed_text,
        title=str(body.get("title") or ""),
        style=body.get("style"),
        aspect_ratio=body.get("aspect_ratio"),
        shots=int(body["shots"]) if body.get("shots") else None,
        auto_start=bool(body.get("auto_start", True)),
        idempotency_key=key,
    )

    return JSONResponse(
        {
            "project": _project_brief(project),
            "job": _job_view(job) if job else None,
            "quote": quote.as_dict(),
            "idempotency_key": key,
        },
        status_code=status.HTTP_201_CREATED,
    )


@router.get("/projects/{project_id}", summary="项目详情")
async def api_get_project(
    project_id: str,
    session: DbSession,
    principal: CurrentPrincipal,
    include_shots: bool = True,
) -> dict:
    project = await projects.get_project(session, org_id=principal.org_id, project_id=project_id)
    payload: dict = _project_brief(project)
    payload["logline"] = (project.script or {}).get("logline", "")
    payload["clues"] = project.clues or []
    payload["evaluation"] = project.evaluation or {}
    payload["cost"] = project.cost_summary or {}

    if include_shots:
        shots = await projects.list_shots(session, project_id)
        payload["shots"] = [
            {
                "key": s.shot_key,
                "order": s.order_index,
                "status": s.status,
                "shot_size": s.shot_size,
                "camera": s.camera,
                "duration_s": s.duration_s,
                "action": s.action,
                "narration": s.narration,
                "dialogue": s.dialogue,
                "image_prompt": s.image_prompt,
                "video_prompt": s.video_prompt,
                "clue_ids": s.clue_ids,
                "tier": s.tier,
                "cost_usd": round(s.cost_usd, 5),
                "error": s.error or None,
            }
            for s in shots
        ]
    return payload


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除项目")
async def api_delete_project(
    project_id: str,
    session: DbSession,
    principal: WritablePrincipal,
) -> JSONResponse:
    await projects.delete_project(
        session, org_id=principal.org_id, project_id=project_id, user_id=principal.user_id
    )
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)


# --------------------------------------------------------------------------- #
# 任务
# --------------------------------------------------------------------------- #
@router.get("/jobs", summary="任务列表")
async def api_list_jobs(
    session: DbSession,
    principal: CurrentPrincipal,
    active_only: bool = False,
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    rows = await jobs.list_for_org(session, principal.org_id, limit=limit, active_only=active_only)
    return {"items": [_job_view(j) for j in rows], "total": len(rows)}


@router.get("/jobs/{job_id}", summary="任务详情（可用于轮询进度）")
async def api_get_job(job_id: str, session: DbSession, principal: CurrentPrincipal) -> dict:
    job = await jobs.get(session, job_id, org_id=principal.org_id)
    return _job_view(job)


@router.post("/jobs/{job_id}/cancel", summary="取消任务")
async def api_cancel_job(job_id: str, session: DbSession, principal: WritablePrincipal) -> dict:
    job = await jobs.cancel(session, job_id=job_id, org_id=principal.org_id)
    return _job_view(job)


# --------------------------------------------------------------------------- #
# 额度
# --------------------------------------------------------------------------- #
@router.get("/credits", summary="额度余额与流水")
async def api_credits(
    session: DbSession,
    principal: CurrentPrincipal,
    limit: int = Query(30, ge=1, le=200),
) -> dict:
    balance = await credits.get_balance(session, principal.org_id)
    entries = await credits.history(session, principal.org_id, limit=limit)
    return {
        "balance": balance,
        "items": [
            {
                "id": e.id,
                "kind": e.kind.value,
                "amount": e.amount,
                "balance_after": e.balance_after,
                "description": e.description,
                "project_id": e.project_id,
                "job_id": e.job_id,
                "created_at": e.created_at.isoformat(),
            }
            for e in entries
        ],
    }


@router.post("/credits/quote", summary="预估生成成本")
async def api_quote(
    session: DbSession,
    principal: CurrentPrincipal,
    settings: AppSettings,
    shots: int = Query(6, ge=1, le=24),
) -> dict:
    org = await projects.get_org(session, principal.org_id)
    plan = get_plan(org.plan)
    quote = credits.quote_project(settings, shots=shots, allow_premium=plan.allow_premium_tier)
    balance = await credits.get_balance(session, org.id)
    return {
        **quote.as_dict(),
        "balance": balance,
        "affordable": balance >= quote.credits,
        "max_shots": plan.max_shots_per_project,
    }


# --------------------------------------------------------------------------- #
# API Key 自助管理
# --------------------------------------------------------------------------- #
@router.get("/api-keys", summary="列出 API Key")
async def api_list_keys(session: DbSession, principal: CurrentPrincipal) -> dict:
    keys = await accounts.list_api_keys(session, principal.org_id)
    return {
        "items": [
            {
                "id": k.id,
                "name": k.name,
                "prefix": k.prefix,
                "masked": f"reel_{k.prefix}_{'*' * 8}",
                "scopes": k.scopes,
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                "request_count": k.request_count,
                "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None,
                "active": k.is_active,
            }
            for k in keys
        ]
    }


@router.post("/api-keys", status_code=status.HTTP_201_CREATED, summary="创建 API Key")
async def api_create_key(
    request: Request,
    session: DbSession,
    principal: CurrentPrincipal,
    settings: AppSettings,
) -> JSONResponse:
    if principal.kind == "api_key":
        raise Forbidden("不能用 API Key 创建新的 API Key，请使用浏览器登录")
    if principal.role.rank < 2:
        raise Forbidden("需要管理员权限")

    try:
        body = await request.json()
    except Exception:
        body = {}

    key, full_key = await accounts.create_api_key(
        session,
        settings,
        org_id=principal.org_id,
        user_id=principal.user_id or "",
        name=str(body.get("name") or "API Key"),
        scopes=list(body.get("scopes") or ["read", "write"]),
        expires_in_days=int(body["expires_in_days"]) if body.get("expires_in_days") else None,
    )
    return JSONResponse(
        {
            "id": key.id,
            "name": key.name,
            "prefix": key.prefix,
            "scopes": key.scopes,
            # 明文仅此一次
            "key": full_key,
            "warning": "请立即保存，此密钥不会再次显示",
        },
        status_code=status.HTTP_201_CREATED,
    )


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT, summary="撤销 API Key")
async def api_revoke_key(key_id: str, session: DbSession, principal: CurrentPrincipal) -> JSONResponse:
    if principal.kind == "api_key":
        raise Forbidden("不能用 API Key 撤销密钥")
    await accounts.revoke_api_key(
        session, org_id=principal.org_id, key_id=key_id, user_id=principal.user_id or ""
    )
    return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)


# --------------------------------------------------------------------------- #
# 审计
# --------------------------------------------------------------------------- #
@router.get("/audit", summary="审计日志")
async def api_audit(
    session: DbSession,
    principal: CurrentPrincipal,
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    from sqlalchemy import select

    from ...db.models import AuditLog

    if principal.kind == "api_key":
        raise Forbidden("审计日志仅对浏览器会话开放")

    rows = await session.execute(
        select(AuditLog)
        .where(AuditLog.org_id == principal.org_id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    return {
        "items": [
            {
                "id": a.id,
                "action": a.action.value,
                "actor": a.actor_user_id,
                "actor_type": a.actor_type,
                "target": f"{a.target_type}:{a.target_id}" if a.target_id else "",
                "ip": a.ip,
                "meta": a.meta,
                "created_at": a.created_at.isoformat(),
            }
            for a in rows.scalars()
        ]
    }


# --------------------------------------------------------------------------- #
def _fingerprint(seed_text: str, body: dict) -> str:
    raw = f"{seed_text}|{body.get('style')}|{body.get('aspect_ratio')}|{body.get('shots')}"
    return "auto-" + hashlib.sha256(raw.encode()).hexdigest()[:40]
