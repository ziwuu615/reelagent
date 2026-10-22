"""控制台路由（/app/*）——登录后的产品界面。

全部要求浏览器会话（`SessionPrincipal`），不接受 API Key：
这些页面涉及计费、成员与密钥管理，而 API Key 会出现在 CI 与第三方集成中，
泄漏概率高得多。

CSRF 采用**双提交 Cookie + 表单隐藏字段**：
会话 Cookie 已是 SameSite=Lax（阻断跨站 POST），这里再叠一层，
防止同站 XSS 场景下的越权提交。
"""

from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from reelagent.config import STYLE_PRESETS

from ...config import Settings
from ...db.models import Job
from ...deps import AppSettings, DbSession
from ...domain.enums import (
    ArtifactKind,
    AuditAction,
    JobKind,
    JobStatus,
    LedgerKind,
    MembershipRole,
    PlanTier,
    ProjectStatus,
)
from ...domain.errors import AppError, Forbidden
from ...domain.plans import PLANS, all_plans, get_plan
from ...observability import get_logger
from ...security import (
    Principal,
    SessionPrincipal,
    decrypt_byok_map,
    encrypt_byok_map,
    mask_api_key,
)
from ...services import accounts, audit, billing, credits, jobs, projects
from ..templating import get_templates

log = get_logger(__name__)
router = APIRouter(prefix="/app", tags=["console"], include_in_schema=False)


# --------------------------------------------------------------------------- #
# 通用
# --------------------------------------------------------------------------- #
def _ctx(request: Request, principal: Principal, **extra) -> dict:
    """页面公共上下文。csrf_token 从 Cookie 读出后写进表单隐藏字段。"""
    settings: Settings = request.app.state.settings
    base = {
        "principal": principal,
        "csrf_token": request.cookies.get(settings.security.csrf_cookie, ""),
        "nav": "dashboard",
        "flash": request.query_params.get("flash", ""),
        "error": request.query_params.get("error", ""),
    }
    base.update(extra)
    return base


def _require_csrf(request: Request, submitted: str) -> None:
    settings: Settings = request.app.state.settings
    cookie = request.cookies.get(settings.security.csrf_cookie, "")
    header = request.headers.get(settings.security.csrf_header, "")
    provided = submitted or header
    if not cookie or not provided or not hmac.compare_digest(cookie, provided):
        log.warning("console_csrf_failed", path=request.url.path)
        raise Forbidden("页面已过期，请刷新后重试")


async def _org_state(session, settings: Settings, principal: Principal) -> dict:
    org = await projects.get_org(session, principal.org_id)
    plan = get_plan(org.plan)
    counts = await projects.counts_for_org(session, org.id)
    active_count = await jobs.active_count(session, org.id)
    sub = await billing.subscription_for(session, org.id)
    return {
        "org": org,
        "plan": plan,
        "counts": counts,
        # 注意命名为 active_job_count：dashboard 路由会用 `active_jobs` 传「任务列表」，
        # 若这里也叫 active_jobs，`**state` 与显式关键字会在调用处冲突（TypeError）。
        "active_job_count": active_count,
        "subscription": sub,
        "plan_summary": billing.current_plan_summary(org, sub),
    }


# --------------------------------------------------------------------------- #
# 仪表盘
# --------------------------------------------------------------------------- #
@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
):
    state = await _org_state(session, settings, principal)
    recent, total = await projects.list_projects(session, org_id=principal.org_id, limit=6)
    running_jobs = await jobs.list_for_org(session, principal.org_id, limit=5, active_only=True)
    ledger = await credits.history(session, principal.org_id, limit=6)

    return get_templates().TemplateResponse(
        request,
        "console/dashboard.html",
        _ctx(
            request,
            principal,
            nav="dashboard",
            recent=recent,
            total_projects=total,
            # 任务列表（模板用它逐条渲染）
            active_jobs=running_jobs,
            ledger=ledger,
            welcome=request.query_params.get("welcome") == "1",
            signup_credits=PLANS[PlanTier.FREE].monthly_credits,
            **state,
        ),
    )


# --------------------------------------------------------------------------- #
# 项目
# --------------------------------------------------------------------------- #
@router.get("/projects", response_class=HTMLResponse)
async def project_list(
    request: Request,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
    status_filter: str = "",
    q: str = "",
    page: int = 1,
):
    page = max(1, page)
    per_page = 12
    status_value: ProjectStatus | None = None
    if status_filter:
        try:
            status_value = ProjectStatus(status_filter)
        except ValueError:
            status_value = None

    items, total = await projects.list_projects(
        session,
        org_id=principal.org_id,
        status=status_value,
        search=q,
        limit=per_page,
        offset=(page - 1) * per_page,
    )
    state = await _org_state(session, settings, principal)

    # 每个项目挂上最近一次任务，便于列表直接显示进度
    job_map: dict[str, Job] = {}
    for item in items:
        rows = await jobs.list_for_project(session, item.id)
        if rows:
            job_map[item.id] = rows[0]

    return get_templates().TemplateResponse(
        request,
        "console/projects.html",
        _ctx(
            request,
            principal,
            nav="projects",
            items=items,
            job_map=job_map,
            total=total,
            page=page,
            per_page=per_page,
            pages=max(1, (total + per_page - 1) // per_page),
            status_filter=status_filter,
            query=q,
            all_statuses=[s.value for s in ProjectStatus],
            **state,
        ),
    )


@router.get("/projects/new", response_class=HTMLResponse)
async def project_new(
    request: Request,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
):
    state = await _org_state(session, settings, principal)
    plan = state["plan"]
    quote = credits.quote_project(
        settings, shots=plan.max_shots_per_project, allow_premium=plan.allow_premium_tier
    )
    examples = [
        "深夜的便利店只剩下最后一盏灯。林晚把湿透的伞收在门口，走向货架。她在最底层找到一盒过期牛奶，生产日期是三年前的今天。收银台后的老人抬起头，说这盒牛奶他等了很久。",
        "老式收音机里传来一段没有台标的广播。她拧动旋钮，声音越来越清晰，念的却是她昨天写在日记里的话。",
        "沙漠公路尽头有一座加油站，招牌上的价格停留在 1998 年。加油员递来一张纸条：你确定要往回走吗？",
    ]
    return get_templates().TemplateResponse(
        request,
        "console/project_new.html",
        _ctx(
            request,
            principal,
            nav="new",
            styles=list(STYLE_PRESETS.items()),
            examples=examples,
            quote=quote,
            max_shots=plan.max_shots_per_project,
            **state,
        ),
    )


@router.post("/projects")
async def project_create(
    request: Request,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
    seed_text: str = Form(...),
    title: str = Form(""),
    style: str = Form("cinematic"),
    aspect_ratio: str = Form("9:16"),
    shots: int = Form(0),
    quality_tier: str = Form("auto"),
    csrf_token: str = Form(""),
):
    _require_csrf(request, csrf_token)

    try:
        project, job, quote = await projects.create_project(
            session,
            settings,
            principal=principal,
            seed_text=seed_text,
            title=title,
            style=style,
            aspect_ratio=aspect_ratio,
            shots=shots or None,
            auto_start=True,
            # 档位：auto 表示交给引擎按难度路由；其余为显式指定。
            # 合法性由服务层白名单校验，这里不重复判断，
            # 避免两处规则不一致（那正是权限漏洞的常见来源）。
            force_tier=quality_tier,
        )
    except AppError as exc:
        log.info("project_create_rejected", org_id=principal.org_id, reason=exc.code)
        return RedirectResponse(
            f"/app/projects/new?error={exc.message}", status_code=status.HTTP_303_SEE_OTHER
        )

    log.info("project_create_ok", project_id=project.id, job_id=job.id if job else "", credits=quote.credits)
    return RedirectResponse(
        f"/app/projects/{project.id}?flash=已开始生成，预计 3-8 分钟完成",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_detail(
    request: Request,
    project_id: str,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
):
    project = await projects.get_project(session, org_id=principal.org_id, project_id=project_id)
    shots = await projects.list_shots(session, project_id)
    job_rows = await jobs.list_for_project(session, project_id)
    artifacts = await projects.list_artifacts(session, org_id=principal.org_id, project_id=project_id)

    # 成片：先找明确标记的 FINAL_VIDEO，找不到再退回旧的 VIDEO/GIF。
    #
    # 必须分两步，不能用 `next(a for a in artifacts if a.kind in (...))` ——
    # artifacts 按创建时间**升序**，那个写法会返回第一个镜头片段。
    # 旧代码就是这样（那时只有 VIDEO），意味着项目详情页的「成片」
    # 实际拿到的是第一段片段。现在靠 FINAL_VIDEO 区分才能取对。
    final = next((a for a in artifacts if a.kind is ArtifactKind.FINAL_VIDEO), None)
    if final is None:
        # 改造前产生的旧数据：成片与片段共用 VIDEO，无法可靠区分。
        # 取最后一个（成片在批次里最后写入），这是旧数据唯一可用的近似。
        legacy = [a for a in artifacts if a.kind in (ArtifactKind.VIDEO, ArtifactKind.GIF)]
        final = legacy[-1] if legacy else None
    storyboard = next((a for a in artifacts if a.kind == ArtifactKind.STORYBOARD), None)
    audio = next((a for a in artifacts if a.kind == ArtifactKind.AUDIO), None)
    keyframes = {a.shot_key: a for a in artifacts if a.kind == ArtifactKind.KEYFRAME}
    state = await _org_state(session, settings, principal)

    template = "console/project_detail.html"
    if project.status in (ProjectStatus.QUEUED, ProjectStatus.RUNNING):
        template = "console/project_running.html"

    return get_templates().TemplateResponse(
        request,
        template,
        _ctx(
            request,
            principal,
            nav="projects",
            project=project,
            shots=shots,
            jobs=job_rows,
            latest_job=job_rows[0] if job_rows else None,
            artifacts=artifacts,
            final=final,
            storyboard=storyboard,
            audio=audio,
            keyframes=keyframes,
            evaluation=project.evaluation or {},
            cost=project.cost_summary or {},
            **state,
        ),
    )


@router.get("/projects/{project_id}/studio", response_class=HTMLResponse)
async def project_studio(
    request: Request,
    project_id: str,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
):
    project = await projects.get_project(session, org_id=principal.org_id, project_id=project_id)
    shots = await projects.list_shots(session, project_id)
    artifacts = await projects.list_artifacts(session, org_id=principal.org_id, project_id=project_id)
    keyframes = {a.shot_key: a for a in artifacts if a.kind == ArtifactKind.KEYFRAME}
    base_images = {c.get("id"): c for c in (project.clues or [])}
    state = await _org_state(session, settings, principal)

    return get_templates().TemplateResponse(
        request,
        "console/studio.html",
        _ctx(
            request,
            principal,
            nav="projects",
            project=project,
            shots=shots,
            keyframes=keyframes,
            clues=project.clues or [],
            clue_map=base_images,
            max_shots=state["plan"].max_shots_per_project,
            **state,
        ),
    )


@router.post("/projects/{project_id}/regenerate")
async def project_regenerate(
    request: Request,
    project_id: str,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
    shot_keys: str = Form(""),
    csrf_token: str = Form(""),
):
    """重跑指定镜头。费用只按被重跑的镜头计，因此必须逐镜选择。"""
    _require_csrf(request, csrf_token)
    keys = [k.strip() for k in shot_keys.split(",") if k.strip()]
    if not keys:
        return RedirectResponse(
            f"/app/projects/{project_id}/studio?error=请先选择要重跑的镜头",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    project = await projects.get_project(session, org_id=principal.org_id, project_id=project_id)

    try:
        quote = credits.quote_project(settings, shots=len(keys), allow_premium=principal is not None)
        await credits.ensure_sufficient(session, principal.org_id, quote.credits)
        await credits.consume(
            session,
            org_id=principal.org_id,
            amount=quote.credits,
            description=f"重跑 {len(keys)} 个镜头",
            project_id=project.id,
            idempotency_key=f"regen:{project.id}:{','.join(keys)}:{int(time.time())}",
        )
        await jobs.enqueue(
            session,
            settings,
            org_id=principal.org_id,
            kind=JobKind.REGENERATE_SHOT,
            project_id=project.id,
            created_by=principal.user_id,
            payload={"shot_keys": keys, "max_shots": project.total_shots or len(keys)},
            priority=5,
            credits_reserved=quote.credits,
        )
        await audit.record(
            session,
            action=AuditAction.SHOT_REGENERATED,
            org_id=principal.org_id,
            actor_user_id=principal.user_id,
            target_type="project",
            target_id=project.id,
            meta={"shots": keys},
        )
        project.status = ProjectStatus.QUEUED
        await session.flush()
    except AppError as exc:
        return RedirectResponse(
            f"/app/projects/{project_id}/studio?error={exc.message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse(
        f"/app/projects/{project_id}?flash=已提交重跑 {len(keys)} 个镜头",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/projects/{project_id}/shots/{shot_key}/prompt")
async def project_shot_prompt(
    request: Request,
    project_id: str,
    shot_key: str,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
    image_prompt: str = Form(""),
    csrf_token: str = Form(""),
):
    _require_csrf(request, csrf_token)
    await projects.get_project(session, org_id=principal.org_id, project_id=project_id)
    await projects.update_shot_prompts(
        session,
        org_id=principal.org_id,
        project_id=project_id,
        shot_key=shot_key,
        image_prompt=image_prompt,
    )
    return RedirectResponse(
        f"/app/projects/{project_id}/studio?flash=提示词已保存",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/projects/{project_id}/cancel")
async def project_cancel(
    request: Request,
    project_id: str,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
    csrf_token: str = Form(""),
):
    _require_csrf(request, csrf_token)
    await projects.get_project(session, org_id=principal.org_id, project_id=project_id)
    rows = await jobs.list_for_project(session, project_id)
    cancelled = 0
    for row in rows:
        if row.status in (JobStatus.PENDING, JobStatus.RUNNING):
            await jobs.cancel(session, job_id=row.id, org_id=principal.org_id)
            cancelled += 1
    await audit.record(
        session,
        action=AuditAction.JOB_CANCELLED,
        org_id=principal.org_id,
        actor_user_id=principal.user_id,
        target_type="project",
        target_id=project_id,
        meta={"count": cancelled},
    )
    return RedirectResponse(
        f"/app/projects/{project_id}?flash=已请求取消，未消耗的额度将自动退还",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/projects/{project_id}/delete")
async def project_delete(
    request: Request,
    project_id: str,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
    csrf_token: str = Form(""),
):
    _require_csrf(request, csrf_token)
    try:
        await projects.delete_project(
            session, org_id=principal.org_id, project_id=project_id, user_id=principal.user_id
        )
    except AppError as exc:
        return RedirectResponse(
            f"/app/projects/{project_id}?error={exc.message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse("/app/projects?flash=项目已删除", status_code=status.HTTP_303_SEE_OTHER)


# --------------------------------------------------------------------------- #
# 用量与账单
# --------------------------------------------------------------------------- #
@router.get("/usage", response_class=HTMLResponse)
async def usage_page(
    request: Request,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
    page: int = 1,
):
    page = max(1, page)
    per_page = 30
    entries = await credits.history(session, principal.org_id, limit=per_page, offset=(page - 1) * per_page)
    reconcile = await credits.reconcile(session, principal.org_id)
    state = await _org_state(session, settings, principal)
    return get_templates().TemplateResponse(
        request,
        "console/usage.html",
        _ctx(
            request,
            principal,
            nav="usage",
            entries=entries,
            page=page,
            reconcile=reconcile,
            ledger_kinds=[k.value for k in LedgerKind],
            **state,
        ),
    )


@router.get("/billing", response_class=HTMLResponse)
async def billing_page(
    request: Request,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
):
    state = await _org_state(session, settings, principal)
    return get_templates().TemplateResponse(
        request,
        "console/billing.html",
        _ctx(
            request,
            principal,
            nav="billing",
            all_plans=all_plans(),
            **state,
        ),
    )


@router.post("/billing/checkout")
async def billing_checkout(
    request: Request,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
    tier: str = Form(...),
    csrf_token: str = Form(""),
):
    _require_csrf(request, csrf_token)
    try:
        plan_tier = PlanTier(tier)
        org = await projects.get_org(session, principal.org_id)
        url = await billing.create_checkout_session(
            session,
            settings,
            org=org,
            tier=plan_tier,
            success_url=settings.public_url("/app/billing?flash=订阅已生效"),
            cancel_url=settings.public_url("/app/billing?error=已取消支付"),
            user_email=principal.user.email if principal.user else "",
            user_id=principal.user_id,
        )
    except AppError as exc:
        return RedirectResponse(f"/app/billing?error={exc.message}", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/billing/portal")
async def billing_portal(
    request: Request,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
    csrf_token: str = Form(""),
):
    _require_csrf(request, csrf_token)
    try:
        org = await projects.get_org(session, principal.org_id)
        url = await billing.create_portal_session(
            session, settings, org=org, return_url=settings.public_url("/app/billing")
        )
    except AppError as exc:
        return RedirectResponse(f"/app/billing?error={exc.message}", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


# --------------------------------------------------------------------------- #
# 设置
# --------------------------------------------------------------------------- #
async def _settings_context(
    request: Request,
    settings: Settings,
    session,
    principal: Principal,
    **extra,
) -> dict:
    state = await _org_state(session, settings, principal)
    keys = await accounts.list_api_keys(session, principal.org_id)
    members = await accounts.memberships_for_org(session, principal.org_id)
    byok_plain = decrypt_byok_map(settings, state["org"].byok or {})
    return _ctx(
        request,
        principal,
        nav="settings",
        api_keys=keys,
        members=members,
        byok_status={k: bool(v) for k, v in byok_plain.items()},
        mask=mask_api_key,
        new_key="",
        **state,
        **extra,
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
):
    ctx = await _settings_context(request, settings, session, principal)
    return get_templates().TemplateResponse(request, "console/settings.html", ctx)


@router.post("/settings/org")
async def settings_org(
    request: Request,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
    name: str = Form(""),
    default_style: str = Form("cinematic"),
    csrf_token: str = Form(""),
):
    _require_csrf(request, csrf_token)
    if principal.role.rank < 2:
        raise Forbidden("需要管理员权限")
    org = await projects.get_org(session, principal.org_id)
    if name.strip():
        org.name = name.strip()[:120]
    org.default_style = default_style[:32]
    await session.flush()
    return RedirectResponse("/app/settings?flash=已保存", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/api-keys", response_class=HTMLResponse)
async def settings_create_key(
    request: Request,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
    name: str = Form(""),
    csrf_token: str = Form(""),
):
    _require_csrf(request, csrf_token)
    if principal.role.rank < 2:
        raise Forbidden("需要管理员权限")

    error = ""
    full_key = ""
    try:
        _key, full_key = await accounts.create_api_key(
            session,
            settings,
            org_id=principal.org_id,
            user_id=principal.user_id or "",
            name=name,
        )
    except AppError as exc:
        error = exc.message

    # 明文密钥**只在此次响应体中出现一次**，绝不放进 URL——
    # 查询串会进入浏览器历史、Referer 与服务端访问日志。
    ctx = await _settings_context(
        request,
        settings,
        session,
        principal,
        new_key=full_key,
        error=error,
        flash="" if error else "密钥已创建，请立即复制保存——关闭页面后无法再次查看",
    )
    return get_templates().TemplateResponse(request, "console/settings.html", ctx)


@router.post("/settings/api-keys/{key_id}/revoke")
async def settings_revoke_key(
    request: Request,
    key_id: str,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
    csrf_token: str = Form(""),
):
    _require_csrf(request, csrf_token)
    if principal.role.rank < 2:
        raise Forbidden("需要管理员权限")
    await accounts.revoke_api_key(
        session, org_id=principal.org_id, key_id=key_id, user_id=principal.user_id or ""
    )
    return RedirectResponse("/app/settings?flash=密钥已撤销", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/byok")
async def settings_byok(
    request: Request,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
    video_key: str = Form(""),
    image_key: str = Form(""),
    llm_key: str = Form(""),
    csrf_token: str = Form(""),
):
    """自带密钥（BYOK）。

    密钥**加密后落库**，列表中只显示「已配置/未配置」，
    任何接口都不会把它明文返回——包括管理端。
    """
    _require_csrf(request, csrf_token)
    if principal.role.rank < 2:
        raise Forbidden("需要管理员权限")
    if not settings.providers.allow_byok:
        raise Forbidden("当前部署未开放自带密钥")

    org = await projects.get_org(session, principal.org_id)
    current = decrypt_byok_map(settings, org.byok or {})
    for field, value in (("video", video_key), ("image", image_key), ("llm", llm_key)):
        if value.strip():
            current[field] = value.strip()
    org.byok = encrypt_byok_map(settings, current)
    await session.flush()
    return RedirectResponse("/app/settings?flash=密钥已更新", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/members")
async def settings_add_member(
    request: Request,
    settings: AppSettings,
    session: DbSession,
    principal: SessionPrincipal,
    email: str = Form(""),
    role: str = Form("member"),
    csrf_token: str = Form(""),
):
    _require_csrf(request, csrf_token)
    if principal.role.rank < 2:
        raise Forbidden("需要管理员权限")

    try:
        await accounts.add_member(
            session,
            org_id=principal.org_id,
            email=email,
            role=MembershipRole(role) if role in {r.value for r in MembershipRole} else MembershipRole.MEMBER,
            invited_by=principal.user_id or "",
        )
    except AppError as exc:
        return RedirectResponse(f"/app/settings?error={exc.message}", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/app/settings?flash=成员已添加", status_code=status.HTTP_303_SEE_OTHER)
