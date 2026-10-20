"""项目用例。

「创建项目」不是一次 INSERT，而是一个跨模块的用例：
  1. 校验套餐限制（镜头数、项目数、并发数）
  2. 预估额度并**预扣**（钱先扣，事后退）
  3. 建项目记录
  4. 入队任务

四步必须在同一事务里，否则会出现「扣了钱没建项目」或「建了项目没扣钱」。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from reelagent.routing import TIER_DRAFT, TIER_EXPRESS, TIER_PREMIUM, TIER_STANDARD

from ..config import Settings
from ..db.base import utcnow
from ..db.models import Artifact, Job, Organization, Project, Shot
from ..domain.enums import (
    ArtifactKind,
    AuditAction,
    JobKind,
    ProjectStatus,
)
from ..domain.errors import Forbidden, NotFound, ValidationFailed
from ..domain.ids import PREFIX, new_id
from ..domain.plans import get_plan
from ..observability import get_logger, projects_created_total
from ..security import Principal
from . import audit, credits, jobs

log = get_logger(__name__)

MAX_SEED_LENGTH = 20_000
MIN_SEED_LENGTH = 6


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
async def get_org(session: AsyncSession, org_id: str) -> Organization:
    org = await session.get(Organization, org_id)
    if org is None:
        raise NotFound("组织不存在")
    return org


async def get_project(session: AsyncSession, *, org_id: str, project_id: str) -> Project:
    project = (
        await session.execute(
            select(Project).where(
                Project.id == project_id,
                Project.org_id == org_id,  # 租户过滤写在查询里，不靠调用方自觉
                Project.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFound("项目不存在")
    return project


async def list_projects(
    session: AsyncSession,
    *,
    org_id: str,
    status: ProjectStatus | None = None,
    search: str = "",
    limit: int = 24,
    offset: int = 0,
) -> tuple[list[Project], int]:
    conditions = [Project.org_id == org_id, Project.deleted_at.is_(None)]
    if status is not None:
        conditions.append(Project.status == status)
    if search:
        like = f"%{search.strip()[:60]}%"
        conditions.append(or_(Project.title.ilike(like), Project.seed_text.ilike(like)))

    total = int(await session.scalar(select(func.count(Project.id)).where(*conditions)) or 0)
    rows = await session.execute(
        select(Project)
        .where(*conditions)
        .order_by(Project.created_at.desc())
        .limit(min(limit, 100))
        .offset(max(0, offset))
    )
    return list(rows.scalars()), total


async def list_shots(session: AsyncSession, project_id: str) -> list[Shot]:
    rows = await session.execute(select(Shot).where(Shot.project_id == project_id).order_by(Shot.order_index))
    return list(rows.scalars())


async def list_artifacts(
    session: AsyncSession, *, org_id: str, project_id: str, kinds: list[ArtifactKind] | None = None
) -> list[Artifact]:
    conditions = [
        Artifact.org_id == org_id,
        Artifact.project_id == project_id,
        Artifact.deleted_at.is_(None),
    ]
    if kinds:
        conditions.append(Artifact.kind.in_(kinds))
    rows = await session.execute(select(Artifact).where(*conditions).order_by(Artifact.created_at))
    return list(rows.scalars())


async def find_final_artifact(session: AsyncSession, *, org_id: str, project_id: str) -> Artifact | None:
    """取成片：优先 MP4，其次 GIF。"""
    for kind in (ArtifactKind.VIDEO, ArtifactKind.GIF):
        row = (
            await session.execute(
                select(Artifact)
                .where(
                    Artifact.org_id == org_id,
                    Artifact.project_id == project_id,
                    Artifact.kind == kind,
                    Artifact.deleted_at.is_(None),
                )
                .order_by(Artifact.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is not None:
            return row
    return None


async def counts_for_org(session: AsyncSession, org_id: str) -> dict[str, int]:
    total = int(
        await session.scalar(
            select(func.count(Project.id)).where(Project.org_id == org_id, Project.deleted_at.is_(None))
        )
        or 0
    )
    active = int(
        await session.scalar(
            select(func.count(Project.id)).where(
                Project.org_id == org_id,
                Project.deleted_at.is_(None),
                Project.status.in_([ProjectStatus.QUEUED, ProjectStatus.RUNNING]),
            )
        )
        or 0
    )
    ready = int(
        await session.scalar(
            select(func.count(Project.id)).where(
                Project.org_id == org_id,
                Project.deleted_at.is_(None),
                Project.status == ProjectStatus.READY,
            )
        )
        or 0
    )
    return {"total": total, "active": active, "ready": ready}


# --------------------------------------------------------------------------- #
# 创建
# --------------------------------------------------------------------------- #
def validate_seed_text(text: str) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) < MIN_SEED_LENGTH:
        raise ValidationFailed(f"素材至少需要 {MIN_SEED_LENGTH} 个字")
    if len(cleaned) > MAX_SEED_LENGTH:
        raise ValidationFailed(f"素材过长（上限 {MAX_SEED_LENGTH} 字），请拆分成多条")
    return cleaned


#: 档位的合法取值。复用引擎的定义而不是各写一套字符串 ——
#: 两边不一致时会出现「界面能选但引擎不认」这种最难查的问题。
_KNOWN_TIERS = frozenset({TIER_DRAFT, TIER_STANDARD, TIER_PREMIUM, TIER_EXPRESS})

#: 界面上的「自动」不是引擎的档位，而是「不强制」的意思。
_AUTO_TIER = "auto"


def normalize_tier(value: str | None) -> str | None:
    """把界面传来的档位规范化成引擎可用的值。

    - `auto` / 空 / None  -> None（交给引擎按难度自动路由）
    - draft / standard / premium -> 原样返回
    - 其它任何值 -> 抛错

    **必须白名单校验**：这个值会一路传到引擎的路由器，
    非法值在那里不会报错，只会走到 `TIER_RESOLUTION_SCALE.get(tier, 1.0)`
    这种兜底分支，静默按默认分辨率生成 —— 用户以为选了某个档位，
    实际拿到的是另一个，且没有任何提示。
    """
    if value is None:
        return None
    cleaned = value.strip().lower()
    if not cleaned or cleaned == _AUTO_TIER:
        return None
    if cleaned not in _KNOWN_TIERS:
        raise ValidationFailed(f"未知的生成档位：{value}（可选：auto/draft/standard/premium）")
    return cleaned


async def create_project(
    session: AsyncSession,
    settings: Settings,
    *,
    principal: Principal,
    seed_text: str,
    title: str = "",
    style: str | None = None,
    aspect_ratio: str | None = None,
    shots: int | None = None,
    auto_start: bool = True,
    idempotency_key: str | None = None,
    force_tier: str | None = None,
) -> tuple[Project, Job | None, credits.CreditQuote]:
    """创建项目（可选立即入队生成）。返回项目、任务与额度预估。

    `idempotency_key` 用于客户端重试安全：同一 Key 重复提交会**原样返回首次创建的项目**，
    不新建项目、不重复扣费。判定依据是 `jobs(org_id, idempotency_key)` 的唯一约束。
    """
    org = await get_org(session, principal.org_id)
    if org.is_suspended:
        raise Forbidden("组织已被停用")

    plan = get_plan(org.plan)

    # 档位规范化 + 套餐权限校验。
    # 校验放在服务层而不是路由层：面板和 API 两条入口共用同一条规则，
    # 否则很容易出现「界面挡住了但 API 能绕过去」的权限漏洞。
    force_tier = normalize_tier(force_tier)
    if force_tier == TIER_PREMIUM and not plan.allow_premium_tier:
        raise Forbidden(f"当前套餐（{plan.name}）不支持最高质量档位，请升级后再试")

    # 幂等短路：先看这个 Key 是否已经有对应任务
    if idempotency_key:
        existing_job = (
            await session.execute(
                select(Job).where(Job.org_id == org.id, Job.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()
        if existing_job is not None and existing_job.project_id:
            existing_project = await session.get(Project, existing_job.project_id)
            if existing_project is not None and existing_project.deleted_at is None:
                log.info(
                    "project_create_idempotent",
                    project_id=existing_project.id,
                    key=idempotency_key,
                )
                cached_quote = credits.quote_project(
                    settings,
                    shots=existing_project.total_shots or plan.max_shots_per_project,
                    allow_premium=plan.allow_premium_tier,
                )
                return existing_project, existing_job, cached_quote

    seed = validate_seed_text(seed_text)

    # --- 套餐限制 ---
    counts = await counts_for_org(session, org.id)
    if plan.max_projects >= 0 and counts["total"] >= plan.max_projects:
        raise Forbidden(f"{plan.name}最多保留 {plan.max_projects} 个项目，请删除旧项目或升级套餐")

    active_jobs = await jobs.active_count(session, org.id)
    if auto_start and active_jobs >= plan.max_concurrent_jobs:
        raise Forbidden(
            f"{plan.name}同时只能有 {plan.max_concurrent_jobs} 个生成任务在跑，请等待完成或升级套餐"
        )

    shot_count = shots or plan.max_shots_per_project
    shot_count = max(1, min(shot_count, plan.max_shots_per_project, settings.max_shots_per_project))

    style_value = style or org.default_style or settings.style
    aspect_value = aspect_ratio or settings.aspect_ratio

    quote = credits.quote_project(
        settings, shots=shot_count, allow_premium=plan.allow_premium_tier, tier=force_tier
    )
    # 成本超上限时**当场拒绝**，而不是先扣钱、再在生成中途把画质降下来。
    # 让用户明确知道「这个配置太贵，做不了」，比悄悄降质诚实。
    credits.assert_affordable(settings, quote)

    project = Project(
        id=new_id(PREFIX["project"]),
        org_id=org.id,
        created_by=principal.user_id,
        title=(title or _derive_title(seed))[:160],
        seed_text=seed,
        style=style_value,
        aspect_ratio=aspect_value,
        status=ProjectStatus.DRAFT,
        total_shots=shot_count,
    )
    session.add(project)
    await session.flush()

    await audit.record(
        session,
        action=AuditAction.PROJECT_CREATED,
        org_id=org.id,
        actor_user_id=principal.user_id,
        actor_type=principal.kind,
        target_type="project",
        target_id=project.id,
        meta={"shots": shot_count, "style": style_value, "estimated_credits": quote.credits},
    )
    projects_created_total.inc()

    job: Job | None = None
    if auto_start:
        job = await start_generation(
            session,
            settings,
            principal=principal,
            project=project,
            shot_count=shot_count,
            quote=quote,
            idempotency_key=idempotency_key,
            force_tier=force_tier,
        )

    log.info(
        "project_created",
        project_id=project.id,
        org_id=org.id,
        shots=shot_count,
        estimated_credits=quote.credits,
        auto_start=auto_start,
    )
    return project, job, quote


async def start_generation(
    session: AsyncSession,
    settings: Settings,
    *,
    principal: Principal,
    project: Project,
    shot_count: int,
    quote: credits.CreditQuote | None = None,
    idempotency_key: str | None = None,
    force_tier: str | None = None,
) -> Job:
    """预扣额度并入队生成任务。

    `force_tier` 是用户控制花费的主要手段：`draft` 档不调用视频模型，
    视频成本直接归零（一次运行约省 70%）。传 None 则交给引擎按难度自动路由。
    """
    org = await get_org(session, principal.org_id)
    plan = get_plan(org.plan)

    if quote is None:
        quote = credits.quote_project(
            settings, shots=shot_count, allow_premium=plan.allow_premium_tier, tier=force_tier
        )

    # 预扣：先扣钱再干活。失败会全额退还（见 jobs.fail）。
    # 幂等键带上前缀，避免与 enqueue 的 job 级幂等键互相干扰（两者用途不同）。
    reserve_key = f"reserve:{(idempotency_key or project.id)}"
    await credits.ensure_sufficient(session, org.id, quote.credits)
    await credits.consume(
        session,
        org_id=org.id,
        amount=quote.credits,
        description=f"预扣：生成《{project.title}》",
        project_id=project.id,
        idempotency_key=reserve_key,
    )

    job = await jobs.enqueue(
        session,
        settings,
        org_id=org.id,
        kind=JobKind.GENERATE_PROJECT,
        project_id=project.id,
        created_by=principal.user_id,
        payload={
            "max_shots": shot_count,
            "style": project.style,
            "aspect_ratio": project.aspect_ratio,
            "max_base_images": settings.max_base_images,
            # 预算必须由**本次预扣额**推导，不能用固定常量。
            #
            # 踩过的坑：这里原本是 `settings.job_budget_usd`（固定 $2.0），
            # 与镜头数和档位无关。5 镜 premium 的真实成本约 $2.2，
            # 于是引擎在第 5 镜预算不足时把 premium **静默降级**成 standard ——
            # 用户按 premium 付费，最后一镜却拿到标准画质。
            # 计费与交付必须同源，否则一定对不上。
            "budget_usd": credits.budget_for(settings, quote),
            "allow_premium": plan.allow_premium_tier,
            # 成本档位。None 表示交给引擎的路由器按难度自动决定；
            # 显式指定时引擎会跳过自动判断直接采用。
            # 这是用户控制花费的主要手段：draft 档不调用视频模型，
            # 视频成本直接归零（一次运行可省约 70%）。
            "force_tier": force_tier,
        },
        priority=plan.priority,
        idempotency_key=idempotency_key or f"gen:{project.id}",
        credits_reserved=quote.credits,
    )
    project.status = ProjectStatus.QUEUED
    project.credits_spent = int(project.credits_spent) + quote.credits
    await session.flush()
    return job


# --------------------------------------------------------------------------- #
# 变更
# --------------------------------------------------------------------------- #
async def update_project(
    session: AsyncSession,
    *,
    org_id: str,
    project_id: str,
    user_id: str | None,
    title: str | None = None,
    style: str | None = None,
) -> Project:
    project = await get_project(session, org_id=org_id, project_id=project_id)
    if title is not None:
        cleaned = title.strip()
        if not cleaned:
            raise ValidationFailed("标题不能为空")
        project.title = cleaned[:160]
    if style is not None:
        project.style = style[:32]
    return project


async def delete_project(session: AsyncSession, *, org_id: str, project_id: str, user_id: str | None) -> None:
    """软删除。产物保留一段时间以便误删恢复，由后台清理任务真正回收磁盘。"""
    project = await get_project(session, org_id=org_id, project_id=project_id)

    running = await session.scalar(
        select(func.count(Job.id)).where(
            Job.project_id == project_id,
            Job.status.in_(["pending", "running"]),
        )
    )
    if running:
        raise Forbidden("项目正在生成中，请先取消任务再删除")

    project.deleted_at = utcnow()
    project.status = ProjectStatus.ARCHIVED
    await audit.record(
        session,
        action=AuditAction.PROJECT_DELETED,
        org_id=org_id,
        actor_user_id=user_id,
        target_type="project",
        target_id=project_id,
    )


async def archive_project(session: AsyncSession, *, org_id: str, project_id: str) -> Project:
    project = await get_project(session, org_id=org_id, project_id=project_id)
    project.status = ProjectStatus.ARCHIVED
    return project


# --------------------------------------------------------------------------- #
# 同步引擎产出
# --------------------------------------------------------------------------- #
async def sync_from_engine(
    session: AsyncSession,
    *,
    project: Project,
    engine_result: dict[str, Any],
) -> None:
    """把引擎跑完的结果写回数据库（剧本、线索、评测、镜头）。"""
    engine_project = engine_result.get("project") or {}
    script = engine_project.get("script") or {}
    clues = engine_project.get("clues") or []
    evaluation = engine_project.get("evaluation") or {}

    project.script = script
    project.clues = clues
    project.evaluation = evaluation
    project.engine_project_id = engine_project.get("id") or project.engine_project_id

    shots_payload = [s for scene in (script.get("scenes") or []) for s in (scene.get("shots") or [])]
    project.total_shots = len(shots_payload)
    project.finished_shots = sum(1 for s in shots_payload if s.get("clip_path"))
    project.duration_seconds = float(sum(float(s.get("duration_s") or 0) for s in shots_payload))

    existing = {s.shot_key: s for s in await list_shots(session, project.id)}
    for idx, shot_data in enumerate(shots_payload):
        key = str(shot_data.get("id") or f"shot{idx}")
        row = existing.get(key)
        if row is None:
            row = Shot(
                id=new_id(PREFIX["shot"]),
                project_id=project.id,
                org_id=project.org_id,
                shot_key=key,
                order_index=idx,
            )
            session.add(row)
        _apply_shot_data(row, shot_data, idx)

    await session.flush()


def _apply_shot_data(row: Shot, data: dict[str, Any], idx: int) -> None:
    row.order_index = idx
    row.scene_key = str(data.get("scene_id") or "")
    row.status = str(data.get("status") or "pending")
    row.shot_size = str(data.get("shot_size") or "")
    row.camera = str(data.get("camera") or "")
    row.duration_s = float(data.get("duration_s") or 3.0)
    row.action = str(data.get("action") or "")
    row.narration = str(data.get("narration") or "")
    row.dialogue = str(data.get("dialogue") or "")
    row.image_prompt = str(data.get("image_prompt") or "")
    row.video_prompt = str(data.get("video_prompt") or "")
    row.clue_ids = list(data.get("clue_ids") or [])
    # 档位：引擎在路由时决定（draft/standard/premium），用于在 Studio 界面与
    # 账单里按镜头归因花费。数据库的 tier 列与模板里的展示**早就写好了**，
    # 此前只是没有任何一层去填它 —— 于是界面永远不显示档位。
    row.tier = str(data.get("tier") or "")
    row.provider = str(data.get("provider_used") or "")
    row.model = str(data.get("model_used") or "")
    row.cost_usd = float(data.get("cost_usd") or 0.0)
    row.attempts = int(data.get("attempts") or 0)
    row.error = str(data.get("error") or "")


async def update_shot_prompts(
    session: AsyncSession, *, org_id: str, project_id: str, shot_key: str, image_prompt: str
) -> Shot:
    """让用户在 Studio 里手动改写提示词后再重跑单镜。"""
    row = (
        await session.execute(
            select(Shot).where(
                Shot.project_id == project_id, Shot.org_id == org_id, Shot.shot_key == shot_key
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("镜头不存在")
    row.image_prompt = image_prompt.strip()[:4000]
    return row


def _derive_title(seed: str) -> str:
    first = seed.strip().splitlines()[0] if seed.strip() else "未命名项目"
    return first[:24]
