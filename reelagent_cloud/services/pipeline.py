"""引擎桥接：在 worker 里驱动 `reelagent` 引擎完成一次生成。

这一层要解决四个工程问题：

1. **不长期占用数据库连接**
   一条成片要跑几分钟。绝不能把事务开着等它——连接池会被瞬间耗尽。
   因此：进出各开一个短事务，中间长跑阶段完全不持有连接。

2. **同步引擎 vs 异步 worker**
   引擎内部是同步阻塞的（PIL 渲染、urllib 调供应商）。直接在协程里跑会
   卡死事件循环，因此放到线程池执行，线上程通过
   `asyncio.run_coroutine_threadsafe` 回调主循环写进度。

3. **产物归集**
   引擎在本地临时目录工作，跑完把产物搬进对象存储，并在库里登记 Artifact。

4. **成本结算**
   用引擎汇总的真实供应商成本去结算预扣额度（多退少补）。

另外：**没有配置视频供应商时，生产环境直接拒绝执行**，
而不是悄悄用离线渲染顶上——让用户花钱买到占位图是最坏的产品事故。
"""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select

from reelagent.config import ProviderConfig as EngineProviderConfig
from reelagent.config import Settings as EngineSettings
from reelagent.config import resolve_ffmpeg
from reelagent.pipeline import Pipeline as EnginePipeline
from reelagent.pipeline import PipelineCancelled

from ..config import Settings
from ..db.models import Artifact, Organization, Project
from ..db.session import session_scope
from ..domain.enums import ArtifactKind, JobStage, ProjectStatus
from ..domain.errors import GenerationFailed, UnsupportedOperation
from ..observability import Timer, get_logger, provider_cost_usd_total, shots_generated_total
from ..security import decrypt_byok_map
from . import jobs as jobs_svc
from . import projects as projects_svc
from . import storage as storage_svc

log = get_logger(__name__)

#: 引擎阶段 -> 对外进度阶段
STAGE_TO_JOB_STAGE = {
    "plan": JobStage.PLANNING,
    "assets": JobStage.ASSETS,
    "generate": JobStage.GENERATING,
    "evaluate": JobStage.EVALUATING,
    "render": JobStage.RENDERING,
}

#: 阶段的中文名。报错时给用户看「剧本拆解失败」而不是「plan 失败」。
STAGE_LABELS = {
    "plan": "剧本拆解",
    "assets": "素材准备",
    "generate": "镜头生成",
    "evaluate": "质量评测",
    "render": "成片合成",
}

#: 供应商错误关键字 -> 用户能看懂、能行动的一句话。
#:
#: 直接把原始 JSON 抛给用户毫无意义：他既不知道 "Arrearage" 是什么意思，
#: 也不知道该去做什么。这里只做**保守**的匹配 —— 认不出就原样返回，
#: 绝不猜测性改写（错误的信息比没有信息更糟）。
_PROVIDER_ERROR_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("arrearage", "overdue-payment", "欠费", "insufficient balance", "1113"),
        "AI 供应商账户余额不足或已欠费，请到供应商控制台充值后重试",
    ),
    (
        ("quota", "1302", "exceeded your current quota"),
        "AI 供应商账户配额已用尽，请检查套餐额度或稍后再试",
    ),
    (
        ("invalid_api_key", "incorrect api key", "401", "unauthorized"),
        "AI 供应商鉴权失败，请检查 API Key 是否正确或已失效",
    ),
    (
        ("rate limit", "too many requests", "429"),
        "AI 供应商限流，请稍后重试",
    ),
)


def humanize_provider_error(raw: str) -> str:
    """把供应商的原始错误翻译成一句用户能行动的话。

    认不出来时返回原文（截断），不做猜测。
    """
    text = (raw or "").strip()
    if not text:
        return "未知原因"
    lowered = text.lower()
    for needles, message in _PROVIDER_ERROR_HINTS:
        if any(n.lower() in lowered for n in needles):
            # 附上原文片段：用户报障时能直接粘给供应商技术支持。
            # 240 字符而不是 180 —— 实测阿里云的错误体里，真正的错误码
            # （如 "code":"Arrearage"）大约在 200 字符处，截太短会把它切掉，
            # 而错误码正是技术支持最需要的信息。
            return f"{message}（供应商原文：{text[:240]}）"
    return text[:300]


def first_failed_stage(engine_result: dict[str, Any] | None) -> dict[str, Any] | None:
    """从引擎返回结果里找出第一个失败的阶段，没有则返回 None。

    引擎的设计是：单个阶段失败时**不抛异常**，而是把 StageOutcome 标成
    failed 并正常返回整个结果。这样调用方可以决定是继续还是中止。
    云端必须显式检查这个字段，否则「阶段失败」会被当成「阶段什么都没做」，
    最终表现为「项目已完成、但 0 个镜头」。

    抽成独立函数是为了能单独测试 —— 否则验证这条逻辑要模拟整个引擎运行。
    """
    if not engine_result:
        return None
    for stage in engine_result.get("stages") or []:
        if isinstance(stage, dict) and stage.get("status") == "failed":
            return stage
    return None


#: 需要上传到对象存储的目录 -> Artifact 类型
ARTIFACT_DIRS: list[tuple[str, ArtifactKind, str]] = [
    ("keyframes", ArtifactKind.KEYFRAME, "image/png"),
    ("assets", ArtifactKind.BASE_IMAGE, "image/png"),
    ("clips", ArtifactKind.VIDEO, "image/gif"),
    ("audio", ArtifactKind.AUDIO, "audio/wav"),
]


class GenerationCancelled(Exception):
    """任务被用户取消。"""


@dataclass
class GenerationOutcome:
    engine_project_id: str
    provider_cost_usd: float
    credits_charged: int
    artifacts: list[str]
    shots_total: int
    shots_finished: int
    duration_seconds: float
    evaluation: dict[str, Any]
    workspace: Path


# --------------------------------------------------------------------------- #
# 配置桥接
# --------------------------------------------------------------------------- #
def resolve_engine_mode(settings: Settings, org: Organization) -> str:
    """决定这次生成走真实供应商还是离线渲染。

    生产环境若没有可用 Key 则直接拒绝——宁可不接单，也不能交付假成片。
    """
    byok = decrypt_byok_map(settings, org.byok or {}) if settings.providers.allow_byok else {}
    has_video_key = bool(byok.get("video")) or settings.providers.video.configured

    if has_video_key:
        return "live"
    if settings.is_production:
        raise UnsupportedOperation("当前部署未配置视频生成供应商，暂时无法生成成片。请联系支持。")
    log.warning("engine_mock_mode", org_id=org.id, reason="no_video_provider_key")
    return "mock"


def build_engine_settings(
    settings: Settings,
    org: Organization,
    *,
    workspace: Path,
    mode: str,
    budget_usd: float,
) -> EngineSettings:
    """把云端配置 + 组织 BYOK 组装成引擎配置。"""
    byok = decrypt_byok_map(settings, org.byok or {}) if settings.providers.allow_byok else {}

    def provider(kind: str, cfg) -> EngineProviderConfig:
        return EngineProviderConfig(
            name=cfg.provider or kind,
            model=cfg.model,
            api_key=byok.get(kind) or cfg.api_key.get_secret_value(),
            base_url=cfg.base_url,
        )

    llm_key = byok.get("llm") or settings.providers.llm.api_key.get_secret_value()
    engine = EngineSettings(
        mode=mode,
        workspace=workspace,
        style=org.default_style or settings.style,
        aspect_ratio=settings.aspect_ratio,
        budget_usd=budget_usd,
        context_budget=24_000,
        max_concurrency=settings.worker.shot_concurrency,
        # 4 次尝试：账户级配额按分钟窗口恢复，2 次（约 60 秒总退避）
        # 实测不足以等到窗口重置，会导致镜头被误判为永久失败
        max_attempts=4,
        ffmpeg=resolve_ffmpeg(),
        llm=EngineProviderConfig(
            name="llm",
            model=settings.providers.llm.model,
            api_key=llm_key,
            base_url=settings.providers.llm.base_url,
        ),
        llm_judge_model="",
        image=provider("image", settings.providers.image),
        video=provider("video", settings.providers.video),
        tts=provider("tts", settings.providers.tts),
    )
    engine.ensure_workspace()
    return engine


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
async def execute_generation(
    settings: Settings,
    *,
    job_id: str,
    worker_id: str,
    should_cancel: Callable[[], bool] | None = None,
) -> GenerationOutcome:
    """执行一次「生成项目」任务。由 worker 调用。"""
    loop = asyncio.get_running_loop()

    # ---- 阶段一：读取上下文（短事务，立刻释放连接）----
    async with session_scope() as session:
        job = await jobs_svc.get(session, job_id)
        if job.project_id is None:
            raise UnsupportedOperation("任务缺少项目关联")
        project = await session.get(Project, job.project_id)
        if project is None:
            raise UnsupportedOperation("任务关联的项目已不存在")
        org = await session.get(Organization, project.org_id)
        if org is None:
            raise UnsupportedOperation("项目所属组织已不存在")

        payload = dict(job.payload or {})
        org_id = org.id
        project_id = project.id
        engine_project_id = project.engine_project_id
        seed_text = project.seed_text
        style = project.style
        aspect_ratio = project.aspect_ratio

        if project.status == ProjectStatus.RUNNING and engine_project_id:
            # 断点续跑：沿用引擎已有的项目与工作区
            pass

        mode = resolve_engine_mode(settings, org)
        budget_usd = float(payload.get("budget_usd") or settings.job_budget_usd)

    workspace = settings.engine_workspace / job_id
    workspace.mkdir(parents=True, exist_ok=True)
    engine_settings = build_engine_settings(
        settings, org, workspace=workspace, mode=mode, budget_usd=budget_usd
    )

    max_shots = int(payload.get("max_shots") or settings.max_shots_per_project)
    max_base_images = int(payload.get("max_base_images") or settings.max_base_images)
    force_tier = payload.get("force_tier")
    only_shots = payload.get("only_shots")

    log.info(
        "generation_start",
        job_id=job_id,
        org_id=org_id,
        project_id=project_id,
        mode=mode,
        max_shots=max_shots,
        worker_id=worker_id,
    )

    # ---- 阶段二：准备引擎项目 ----
    engine = EnginePipeline(engine_settings, verbose=False)
    if engine_project_id and engine.load(engine_project_id) is None:
        engine_project_id = ""
    if not engine_project_id:
        created = engine.create(seed_text, style=style, aspect_ratio=aspect_ratio)
        engine_project_id = created.id
        async with session_scope() as session:
            row = await session.get(Project, project_id)
            if row is not None:
                row.engine_project_id = engine_project_id
                await session.flush()

    # ---- 阶段三：在后台线程跑引擎 ----
    def on_stage(stage: str, status: str) -> None:
        job_stage = STAGE_TO_JOB_STAGE.get(stage)
        if job_stage is None:
            return
        if status in {"running", "done"}:
            asyncio.run_coroutine_threadsafe(_update_progress(job_id, job_stage, status == "done"), loop)
        elif status == "failed":
            # 这里只打个 debug：这个回调拿不到异常对象，写出来的日志没有排查价值
            # （上一版就是只写了 stage 名，线上完全不知道失败原因）。
            # 真正的失败信息由 engine_result["stages"] 带出来，
            # 在下面「阶段三之二」统一记录并转成用户可读的错误。
            log.debug("engine_stage_callback_failed", job_id=job_id, stage=stage)

    def cancel_check() -> bool:
        return bool(should_cancel and should_cancel())

    def run_engine() -> dict[str, Any]:
        return engine.run(
            engine_project_id,
            force_tier=force_tier,
            only_shots=only_shots,
            max_shots=max_shots,
            max_base_images=max_base_images,
            use_judge=True,
            on_stage=on_stage,
            should_cancel=cancel_check,
        )

    with Timer() as t:
        try:
            engine_result = await asyncio.to_thread(run_engine)
        except PipelineCancelled as exc:
            log.info("generation_cancelled", job_id=job_id, reason=str(exc))
            raise GenerationCancelled(str(exc)) from exc

    # ---- 阶段三之二：引擎中途失败时，必须让整个任务失败 ----
    #
    # 引擎在阶段失败时**不会抛异常**，而是把结果标成 failed 并正常返回
    # （见 runner._run_stage 的 except 分支：返回 StageOutcome(status="failed")）。
    #
    # 早期实现直接往下走，于是：
    #   - 项目被无条件标记成 ready（0 镜头）
    #   - job 被标记成 succeeded
    #   - 用户看到「已完成」的空项目，完全没有任何错误提示
    #
    # 真实事故：百炼账户欠费，plan 阶段被 HTTP 400 / code=Arrearage 拒绝，
    # 界面显示 ready 0/0、扣费 0、退还全部预扣 —— 看起来像「跑完了但什么都没生成」。
    failed_stage = first_failed_stage(engine_result)
    if failed_stage is not None:
        # 键名是 stage 而不是 name —— 由 StageOutcome.as_dict() 决定：
        #     {"stage": self.name, "status": ..., "error": ...}
        # 上一版写成 .get("name")，取到 None，错误信息渲染成「生成失败（）」，
        # 阶段名是空的。当时的单元测试没抓到，因为测试里手写的是
        # {"name": "plan"} —— 测的是想象出来的格式，不是引擎真实输出。
        stage_key = str(failed_stage.get("stage") or "")
        raw_error = str(failed_stage.get("error") or "")
        message = humanize_provider_error(raw_error)
        # 这里用 error 而不是 warning：它是一次真实的失败，不是可忽略的异常。
        # 同时把原始错误完整记下来 —— 上一版日志只有 stage 名，
        # 排查时完全不知道失败原因，只能靠猜。
        log.error(
            "generation_stage_failed",
            job_id=job_id,
            stage=stage_key,
            stage_label=STAGE_LABELS.get(stage_key, stage_key),
            error=raw_error[:800],
        )
        async with session_scope() as session:
            row = await session.get(Project, project_id)
            if row is not None:
                row.status = ProjectStatus.FAILED
                await session.flush()
        raise GenerationFailed(f"生成失败（{STAGE_LABELS.get(stage_key, stage_key)}）：{message}")

    # ---- 阶段四：归集产物 ----
    artifacts = await _collect_artifacts(
        settings,
        job_id=job_id,
        org_id=org_id,
        project_id=project_id,
        workspace=workspace,
        engine_project_id=engine_project_id,
    )

    engine_project = engine_result.get("project") or {}
    cost_summary = engine_project.get("cost_summary") or {}
    provider_cost = float(cost_summary.get("total_usd") or 0.0)
    summary = engine_result.get("summary") or {}
    shots = summary.get("shots") or {}

    if provider_cost > budget_usd * 1.5:
        log.warning(
            "generation_over_budget",
            job_id=job_id,
            provider_cost_usd=provider_cost,
            budget_usd=budget_usd,
        )

    provider_cost_usd_total.labels(provider="mixed", capability="generation").inc(provider_cost)
    shots_generated_total.labels(status="finished").inc(int(shots.get("done") or 0))

    # ---- 阶段四之二：逐次调用成本落库 ----
    # 必须**在 cleanup_workspace 之前**：原始成本来自工作区里的 reelagent.db，
    # 工作区一删这笔账就没了。失败不影响主流程 —— 产物已经产出，
    # 缺一份分析数据不该把整个任务判失败。
    try:
        await _persist_usage_records(
            job_id=job_id,
            org_id=org_id,
            project_id=project_id,
            workspace=workspace,
            engine_project_id=engine_project_id,
            # 兜底用的「当前配置供应商」。
            #
            # 正常情况下失败调用的 span 已经带上了真实 provider
            # （工具层在调用前就写入），不依赖这个。它只在 span 缺归属时
            # 生效，且落库时会用 `cfg:` 前缀标明「这条归属是配置推断的、
            # 不是上游上报的」—— 让读数据的人自己判断可信度。
            #
            # 之前这个参数从未被传过，导致 ① 失败记录的 provider 恒为空
            # ② `cfg:` 前缀在撒谎（什么都没推断却标成推断）。
            configured_providers={
                "image": settings.providers.image.provider,
                "video": settings.providers.video.provider,
                "tts": settings.providers.tts.provider,
                "llm": settings.providers.llm.provider,
            },
        )
    except Exception as exc:  # pragma: no cover - 兜底，避免影响出片
        log.warning("usage_records_failed", job_id=job_id, error=str(exc))

    # ---- 阶段五：写回结果 ----
    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise UnsupportedOperation("项目已不存在")
        await projects_svc.sync_from_engine(session, project=project, engine_result=engine_result)
        project.cost_summary = cost_summary
        project.status = ProjectStatus.READY
        await session.flush()

    outcome = GenerationOutcome(
        engine_project_id=engine_project_id,
        provider_cost_usd=provider_cost,
        credits_charged=0,
        artifacts=artifacts,
        shots_total=int(shots.get("total") or 0),
        shots_finished=int(shots.get("done") or 0),
        duration_seconds=float(
            (
                (engine_project.get("script") or {}).get("scenes")
                and sum(
                    float(s.get("duration_s") or 0)
                    for sc in (engine_project.get("script") or {}).get("scenes") or []
                    for s in (sc.get("shots") or [])
                )
            )
            or 0.0
        ),
        evaluation=engine_project.get("evaluation") or {},
        workspace=workspace,
    )

    log.info(
        "generation_done",
        job_id=job_id,
        provider_cost_usd=round(provider_cost, 4),
        artifacts=len(artifacts),
        elapsed_s=round(t.seconds, 1),
        shots=outcome.shots_finished,
    )
    return outcome


# --------------------------------------------------------------------------- #
# 进度
# --------------------------------------------------------------------------- #
async def _update_progress(job_id: str, stage: JobStage, finished: bool) -> None:
    """从工作线程回调过来的进度更新。用独立短事务，失败不影响主流程。"""
    try:
        async with session_scope() as session:
            await jobs_svc.update_progress(session, job_id=job_id, stage=stage)
            if finished:
                log.debug("job_stage_done", job_id=job_id, stage=stage.value)
    except Exception as exc:  # pragma: no cover - 进度上报失败不应中断生成
        log.warning("job_progress_update_failed", job_id=job_id, error=str(exc))


# --------------------------------------------------------------------------- #
# 产物归集
# --------------------------------------------------------------------------- #
async def _collect_artifacts(
    settings: Settings,
    *,
    job_id: str,
    org_id: str,
    project_id: str,
    workspace: Path,
    engine_project_id: str,
) -> list[str]:
    """把引擎工作区里的产物上传到对象存储并登记。"""
    storage = storage_svc.get_storage(settings)
    engine_project_dir = workspace / "projects" / engine_project_id
    if not engine_project_dir.exists():
        log.warning("engine_output_missing", job_id=job_id, dir=str(engine_project_dir))
        return []

    uploaded: list[str] = []
    rows: list[Artifact] = []

    # 目录型产物
    for subdir, kind, default_type in ARTIFACT_DIRS:
        directory = engine_project_dir / subdir
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(directory).as_posix()
            key = storage_svc.build_key(
                org_id=org_id, project_id=project_id, category=subdir, filename=path.name
            )
            content_type = storage_svc.content_type_for(path.name) or default_type
            stored = await storage.put_file(key, path, content_type=content_type)
            rows.append(
                _artifact_row(
                    org_id=org_id,
                    project_id=project_id,
                    job_id=job_id,
                    kind=kind,
                    stored=stored,
                    filename=path.name,
                    content_type=content_type,
                    shot_key=rel if subdir in {"keyframes", "clips", "audio"} else "",
                )
            )
            uploaded.append(key)

    # final 目录
    final_dir = engine_project_dir / "final"
    if final_dir.is_dir():
        for path in sorted(final_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() == ".mp4":
                kind = ArtifactKind.VIDEO
            elif path.suffix.lower() == ".gif":
                kind = ArtifactKind.GIF
            elif path.suffix.lower() == ".wav":
                kind = ArtifactKind.AUDIO
            elif path.name == "storyboard.png":
                kind = ArtifactKind.STORYBOARD
            elif path.name == "manifest.json":
                kind = ArtifactKind.MANIFEST
            elif path.name == "report.json":
                kind = ArtifactKind.REPORT
            else:
                continue

            key = storage_svc.build_key(
                org_id=org_id, project_id=project_id, category="final", filename=path.name
            )
            content_type = storage_svc.content_type_for(path.name)
            stored = await storage.put_file(key, path, content_type=content_type)
            rows.append(
                _artifact_row(
                    org_id=org_id,
                    project_id=project_id,
                    job_id=job_id,
                    kind=kind,
                    stored=stored,
                    filename=path.name,
                    content_type=content_type,
                    shot_key="",
                )
            )
            uploaded.append(key)

    if rows:
        async with session_scope() as session:
            # 同一 job 重跑时先清掉旧登记，避免产物重复累积
            existing = list(
                (
                    await session.execute(
                        select(Artifact).where(Artifact.project_id == project_id, Artifact.job_id == job_id)
                    )
                ).scalars()
            )
            for old in existing:
                old.deleted_at = old.deleted_at or _now()
            session.add_all(rows)
            await session.flush()

    log.info("artifacts_collected", job_id=job_id, count=len(rows), bytes=0)
    return uploaded


def _now():
    from ..db.base import utcnow

    return utcnow()


def _artifact_row(
    *,
    org_id: str,
    project_id: str,
    job_id: str,
    kind: ArtifactKind,
    stored: storage_svc.StoredObject,
    filename: str,
    content_type: str,
    shot_key: str,
) -> Artifact:
    from ..domain.ids import PREFIX, new_id

    return Artifact(
        id=new_id(PREFIX["artifact"]),
        org_id=org_id,
        project_id=project_id,
        job_id=job_id,
        shot_key=shot_key[:64],
        kind=kind,
        storage_key=stored.key,
        filename=filename[:255],
        content_type=content_type[:128],
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        is_public=False,
    )


async def _persist_usage_records(
    *,
    job_id: str,
    org_id: str,
    project_id: str,
    workspace: Path,
    engine_project_id: str,
    configured_providers: dict[str, str] | None = None,
) -> int:
    """把引擎记录的**逐次供应商调用**落到 `usage_records`。

    这张表与账本（`ledger_entries`）分工不同，两者都要有：

        ledger_entries -> **用户视角**的额度增减（预扣 / 结算 / 退款），用于对账
        usage_records  -> **供应商视角**的原始调用（模型 / 单价 / 数量 / 成败），
                          用于成本分析与故障排查

    此前 `usage_records` 有表、有模型、有 4 个索引、`/app/usage` 有路由和模板、
    ARCHITECTURE 与 OPERATIONS 文档里甚至写了查询它的 SQL —— **但没有任何代码
    往里写一行**。于是那个页面永远是空的，运维文档里的「成功率 / 延迟分析」
    查出来也是空集。接口存在却不产出数据，比缺接口更难发现。

    逐个字段的取舍：
      - `shot_key` 取自成本记录的 `meta.shot_id`。LLM 类调用没有镜头归属
        （记的是 `task` 与 `span`），因此留空而不是硬塞一个假值。
      - `success` 恒为 True：引擎只在调用成功后才记账，失败不产生成本行。
        真实的失败信息在 spans 里，两处职责不重叠。
      - `credits_charged` 留 0：额度是整单预扣/结算的，把它摊到单次调用上
        属于编造数据；要算单位成本请用 provider_cost_usd。
    """
    from reelagent.harness.state import Store

    from ..db.models import UsageRecord
    from ..domain.ids import PREFIX, new_id

    db_path = workspace / "reelagent.db"
    if not db_path.exists():
        log.warning("usage_source_missing", job_id=job_id, path=str(db_path))
        return 0

    store = Store(db_path)
    try:
        costs = store.list_costs(engine_project_id)
        spans = store.list_provider_spans(engine_project_id)
    finally:
        store.close()

    rows: list[UsageRecord] = []

    # ---- 来源一：金额（全部是成功调用）----
    for c in costs:
        meta = c.get("meta") or {}
        rows.append(
            UsageRecord(
                id=new_id(PREFIX["usage"]),
                org_id=org_id,
                project_id=project_id,
                job_id=job_id,
                shot_key=str(meta.get("shot_id") or "")[:64],
                capability=str(c.get("category") or "")[:16],
                provider=str(c.get("provider") or "")[:32],
                model=str(c.get("model") or "")[:64],
                unit=str(c.get("unit") or "call")[:16],
                quantity=float(c.get("quantity") or 0.0),
                provider_cost_usd=round(float(c.get("usd") or 0.0), 6),
                credits_charged=0,
                # 成功调用的耗时来自成本行（引擎在 record_media_cost 里一起写）。
                # 此前只有失败的 span 有延迟，成功调用恒为 NULL ——
                # 于是「哪个模型更快」这个问题只能靠手工实测，系统自己答不出来。
                latency_ms=(int(float(c["latency_ms"])) if c.get("latency_ms") is not None else None),
                success=True,
                error_code="",
            )
        )

    # ---- 来源二：失败调用（金额为 0，但延迟与错误码只在这里有）----
    #
    # 为什么不能只取 costs：costs **只记成功调用**，缺了它，
    # OPERATIONS.md 里的失败率查询会永远返回 0%、延迟永远为 NULL。
    providers = configured_providers or {}
    for s in spans:
        if s.get("status") == "ok":
            continue
        capability = _capability_of(s.get("name") or "")
        upstream_known = bool(s.get("provider"))
        provider = s.get("provider") or providers.get(capability, "")
        latency = s.get("duration_ms")
        rows.append(
            UsageRecord(
                id=new_id(PREFIX["usage"]),
                org_id=org_id,
                project_id=project_id,
                job_id=job_id,
                shot_key=str(s.get("shot_id") or "")[:64],
                capability=capability,
                provider=provider[:32],
                model=str(s.get("model") or "")[:64],
                unit="call",
                quantity=1.0,
                provider_cost_usd=0.0,  # 失败不产生费用（与引擎记账口径一致）
                credits_charged=0,
                latency_ms=int(latency) if latency else None,
                success=False,
                # 前缀标明归属来源：上游上报 vs 配置推断
                error_code=(("" if upstream_known else "cfg:") + _short_error_code(s.get("error") or ""))[
                    :32
                ],
            )
        )

    if not rows:
        return 0

    async with session_scope() as session:
        session.add_all(rows)
        await session.flush()

    log.info("usage_records_persisted", job_id=job_id, count=len(rows))
    return len(rows)


#: 工具名 -> 能力类别。运维查询按 capability 分组，需要一个稳定的映射。
_TOOL_CAPABILITY = {
    "generate_keyframe": "image",
    "generate_clip": "video",
    "synthesize_speech": "tts",
}


def _capability_of(span_name: str) -> str:
    """从 span 名推断能力类别；认不出来就归为 llm（编排/文本类）。"""
    for tool, capability in _TOOL_CAPABILITY.items():
        if tool in span_name:
            return capability
    return "llm"


def _short_error_code(error: str) -> str:
    """从错误文本里抽一个短标识，用于按错误码聚合。

    优先取上游业务码（智谱的 1305/1302、HTTP 状态码），否则退回异常类名。
    错误全文在 spans 表里，这里只要可聚合的标签。
    """
    for pattern in (r'"code"\s*:\s*"?(\d+)', r"\bcode\s+(\d{3,5})\b", r"\bHTTP\s+(\d{3})\b"):
        m = re.search(pattern, error)
        if m:
            return m.group(1)
    m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", error)
    return m.group(1) if m else "unknown"


def cleanup_workspace(workspace: Path) -> None:
    """任务结束后回收临时工作区。产物已经进对象存储，本地副本不再需要。

    **必须在读取引擎成本数据之后调用** —— `usage_records` 的原始数据来源
    就是工作区里的 `reelagent.db`，删早了这笔账就永久丢了。
    """
    try:
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
    except Exception as exc:  # pragma: no cover
        log.warning("workspace_cleanup_failed", path=str(workspace), error=str(exc))


# --------------------------------------------------------------------------- #
# 单镜重跑
# --------------------------------------------------------------------------- #
async def execute_shot_regeneration(
    settings: Settings,
    *,
    job_id: str,
    worker_id: str,
    should_cancel: Callable[[], bool] | None = None,
) -> GenerationOutcome:
    """只重跑指定镜头。

    利用引擎的断点恢复能力：已完成镜头会被跳过，只重做被标记的那些，
    因此成本与耗时都远低于整条重跑。
    """
    async with session_scope() as session:
        job = await jobs_svc.get(session, job_id)
        payload = dict(job.payload or {})
        shot_keys = list(payload.get("shot_keys") or [])
        if not shot_keys:
            raise UnsupportedOperation("未指定要重跑的镜头")

        project_id = job.project_id
        project = await session.get(Project, project_id) if project_id else None
        if project is None:
            raise UnsupportedOperation("项目不存在")

        # 清空这些镜头的产物登记，迫使引擎重新生成
        rows = list(
            (
                await session.execute(
                    select(Artifact).where(
                        Artifact.project_id == project.id,
                        Artifact.shot_key.in_(shot_keys),
                    )
                )
            ).scalars()
        )
        for row in rows:
            row.deleted_at = _now()

        shots = await projects_svc.list_shots(session, project.id)
        for shot in shots:
            if shot.shot_key in shot_keys:
                shot.clip_key = ""
                shot.keyframe_key = ""
                shot.audio_key = ""
                shot.status = "pending"
                shot.error = ""
        await session.flush()

    # 复用整条生成流程，只是限定镜头范围
    async with session_scope() as session:
        job = await jobs_svc.get(session, job_id)
        job.payload = {**job.payload, "only_shots": shot_keys}
        await session.flush()

    return await execute_generation(settings, job_id=job_id, worker_id=worker_id, should_cancel=should_cancel)
