"""产物生命周期：TTL 与垃圾回收。

## 为什么必须做 GC

对象存储只增不减是最典型的「上线三个月后才发现」的事故：单条成片 5.3 MB
看着不大，但一个项目在 `clips/ keyframes/ audio/ assets/` 下会留下成片 **3~5 倍**
体积的中间产物，而且永远不删。1000 条成片就是 20 GB 起步，且没有任何一行代码
会回收它 —— 成本随用量线性上涨，直到账单变成事故。

除了钱，还有合规：用户注销后数据仍在，属于「未落实删除权」。

## 保留策略：按类型分级

不能一刀切。中间产物的价值随时间衰减得比成片快得多：

| 类型 | 默认保留 | 理由 |
|---|---|---|
| 成片（MP4/GIF/音轨/分镜/manifest） | 30 天 | 用户要下载、要分享，是交付物 |
| 镜头片段 | 7 天 | 只在重跑与排查时有用，过期就是纯成本 |
| 关键帧 | 7 天 | 同上，且可由基准图 + prompt 重建 |
| 基准图 | 30 天 | 重跑时要复用，重建要花钱 |

## 软删 + 可审计

`deleted_at` 先置位、再删对象，两个动作都记录。这样：
  - 对象删失败时行还在，下一轮会重试（不会出现「记录没了但文件还在」的黑洞）
  - 审计能回答「这条产物什么时候被回收的」
  - 误删可以靠备份恢复，而不是「以为还在」

## 不碰正在进行中的任务

`expires_at` 是创建时算出来的，理论上不会过期得太早；但如果一条任务跑了很久
（重试 + 修复轮），早期片段可能已经到 TTL。删掉它会让正在跑的任务突然缺片段。
所以 GC 会显式跳过「所属任务仍未终结」的产物。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import utcnow
from ..db.models import Artifact, Job
from ..domain.enums import ArtifactKind, JobStatus
from ..observability import get_logger
from . import storage as storage_svc

log = get_logger(__name__)

#: 按类型的默认保留天数。None 表示永不自动回收。
DEFAULT_TTL_DAYS: dict[ArtifactKind, int | None] = {
    ArtifactKind.FINAL_VIDEO: 30,
    ArtifactKind.GIF: 30,
    ArtifactKind.AUDIO: 30,
    ArtifactKind.STORYBOARD: 30,
    ArtifactKind.MANIFEST: 30,
    ArtifactKind.REPORT: 30,
    ArtifactKind.VIDEO: 7,
    ArtifactKind.KEYFRAME: 7,
    ArtifactKind.BASE_IMAGE: 30,
}

#: 仍未终结的任务状态。这些任务名下的产物即使到 TTL 也先不删。
_ACTIVE_JOB_STATUS = (JobStatus.PENDING, JobStatus.RUNNING)


def ttl_days_for(kind: ArtifactKind, settings: Any = None) -> int | None:
    """取某类产物的保留天数。

    配置覆盖优先级：`settings.retention.<kind>_days` > `DEFAULT_TTL_DAYS`。
    配置里给 0 表示不回收（保留期是「关闭」而不是「立刻删」）——
    把 0 当「立刻删」会让一次误配把线上产物清空，这个歧义不能留。
    """
    override = None
    retention = getattr(settings, "retention", None)
    if retention is not None:
        override = getattr(retention, f"{kind.value}_days", None)
    if override is not None:
        return None if override == 0 else int(override)
    return DEFAULT_TTL_DAYS.get(kind)


def expires_at_for(
    kind: ArtifactKind, *, now: datetime | None = None, settings: Any = None
) -> datetime | None:
    days = ttl_days_for(kind, settings)
    if days is None:
        return None
    return (now or utcnow()) + timedelta(days=days)


async def gc_expired_artifacts(
    session: AsyncSession,
    settings: Any,
    *,
    now: datetime | None = None,
    batch: int = 200,
) -> dict[str, Any]:
    """回收已过期的产物。返回统计，供指标与告警使用。

    幂等：对象已经不存在时 `delete()` 返回 False，但仍然把 `deleted_at` 置位 ——
    否则这条记录会永远留在候选集里，每一轮都白扫一次。
    """
    now = now or utcnow()
    storage = storage_svc.build_storage(settings)

    active_job_ids = select(Job.id).where(Job.status.in_(_ACTIVE_JOB_STATUS))
    stmt = (
        select(Artifact)
        .where(
            and_(
                Artifact.expires_at.is_not(None),
                Artifact.expires_at <= now,
                Artifact.deleted_at.is_(None),
                ~Artifact.job_id.in_(active_job_ids) | Artifact.job_id.is_(None),
            )
        )
        .order_by(Artifact.expires_at.asc())
        .limit(batch)
    )
    rows = (await session.scalars(stmt)).all()

    deleted = 0
    freed = 0
    errors: list[str] = []
    for artifact in rows:
        try:
            removed = await storage.delete(artifact.storage_key)
            if not removed:
                log.info("gc_object_missing", key=artifact.storage_key)
        except Exception as exc:  # pragma: no cover - 存储层异常
            errors.append(f"{artifact.id}: {type(exc).__name__}")
            continue

        await session.execute(update(Artifact).where(Artifact.id == artifact.id).values(deleted_at=now))
        deleted += 1
        freed += int(artifact.size_bytes or 0)

    if deleted:
        await session.commit()
        log.info("gc_completed", deleted=deleted, freed_bytes=freed, errors=len(errors))

    return {
        "scanned": len(rows),
        "deleted": deleted,
        "freed_bytes": freed,
        "errors": errors,
    }


async def purge_project_artifacts(
    session: AsyncSession, settings: Any, *, project_id: str, now: datetime | None = None
) -> dict[str, Any]:
    """立即回收某个项目下的全部产物（账号注销 / 用户主动删除时用）。

    与 GC 的区别：不看 TTL，直接删。所以它**只该被用户主动发起的删除调用**，
    绝不能被定时任务顺手调用。
    """
    now = now or utcnow()
    storage = storage_svc.build_storage(settings)
    rows = (
        await session.scalars(
            select(Artifact).where(and_(Artifact.project_id == project_id, Artifact.deleted_at.is_(None)))
        )
    ).all()

    deleted = 0
    freed = 0
    for artifact in rows:
        try:
            await storage.delete(artifact.storage_key)
        except Exception as exc:  # pragma: no cover
            log.warning("purge_object_failed", key=artifact.storage_key, error=str(exc))
            continue
        await session.execute(update(Artifact).where(Artifact.id == artifact.id).values(deleted_at=now))
        deleted += 1
        freed += int(artifact.size_bytes or 0)

    if deleted:
        await session.commit()
    return {"project_id": project_id, "deleted": deleted, "freed_bytes": freed}
