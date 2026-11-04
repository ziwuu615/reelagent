"""产物 TTL 与 GC。

这是「上线三个月后才会发现」的那类问题，所以测试钉住三件事：

  1. 保留期按类型分级，且 `0` 表示**不回收**而不是「立刻删」
  2. 过期产物真的被删掉（对象 + 记录都处理），并统计释放字节
  3. **所属任务仍在跑**的产物不能被删 —— 否则正在生成的片子会突然缺片段
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from reelagent_cloud.config import Settings
from reelagent_cloud.db.base import utcnow
from reelagent_cloud.db.models import Artifact, Job, Organization, Project
from reelagent_cloud.db.session import session_scope
from reelagent_cloud.domain.enums import ArtifactKind, JobKind, JobStatus
from reelagent_cloud.domain.ids import PREFIX, new_id
from reelagent_cloud.services import retention
from reelagent_cloud.services import storage as storage_svc

pytestmark = pytest.mark.usefixtures("db")


def test_ttl_defaults_are_tiered() -> None:
    """交付物留久、中间产物留短。"""
    assert retention.ttl_days_for(ArtifactKind.FINAL_VIDEO) == 30
    assert retention.ttl_days_for(ArtifactKind.VIDEO) == 7
    assert retention.ttl_days_for(ArtifactKind.KEYFRAME) == 7


def test_zero_means_never_collect(settings: Settings) -> None:
    """0 = 关闭回收。当成「立刻删」会把线上产物一次清空。"""
    settings.retention.video_days = 0
    assert retention.ttl_days_for(ArtifactKind.VIDEO, settings) is None
    assert retention.expires_at_for(ArtifactKind.VIDEO, settings=settings) is None


def test_config_overrides_default(settings: Settings) -> None:
    settings.retention.video_days = 3
    assert retention.ttl_days_for(ArtifactKind.VIDEO, settings) == 3


async def _seed(
    settings: Settings,
    *,
    kind: ArtifactKind = ArtifactKind.VIDEO,
    expired: bool = True,
    job_status: JobStatus | None = None,
) -> tuple[str, str, object]:
    """造一条产物（带真实对象文件），返回 (artifact_id, storage_key, 文件路径)。"""
    now = utcnow()
    org_id = new_id(PREFIX["org"])
    project_id = new_id(PREFIX["project"])
    job_id = new_id(PREFIX["job"]) if job_status else None
    artifact_id = new_id(PREFIX["artifact"])
    key = storage_svc.build_key(org_id=org_id, project_id=project_id, category="clips", filename="a.mp4")

    path = settings.storage.local_dir / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 1024)

    async with session_scope() as session:
        session.add(Organization(id=org_id, name="t", slug=f"s-{org_id[-6:]}", credits_balance=0))
        session.add(
            Project(
                id=project_id,
                org_id=org_id,
                title="p",
                seed_text="x",
                style="cinematic",
                aspect_ratio="9:16",
            )
        )
        if job_id:
            session.add(
                Job(
                    id=job_id,
                    org_id=org_id,
                    project_id=project_id,
                    kind=JobKind.GENERATE_PROJECT,
                    status=job_status,
                )
            )
        session.add(
            Artifact(
                id=artifact_id,
                org_id=org_id,
                project_id=project_id,
                job_id=job_id,
                shot_key="a.mp4",
                kind=kind,
                storage_key=key,
                filename="a.mp4",
                content_type="video/mp4",
                size_bytes=1024,
                sha256="0" * 64,
                expires_at=(now - timedelta(days=1)) if expired else (now + timedelta(days=30)),
            )
        )
        await session.flush()

    return artifact_id, key, path


async def test_expired_artifact_is_collected(settings: Settings) -> None:
    artifact_id, _key, path = await _seed(settings, expired=True)
    assert path.exists()

    async with session_scope() as session:
        stats = await retention.gc_expired_artifacts(session, settings, now=utcnow())

    assert stats["deleted"] == 1
    assert stats["freed_bytes"] == 1024
    assert not path.exists(), "对象文件必须真的被删除"

    async with session_scope() as session:
        row = await session.get(Artifact, artifact_id)
        assert row is not None
        assert row.deleted_at is not None, "必须留软删时间戳，便于审计"


async def test_fresh_artifact_is_kept(settings: Settings) -> None:
    _artifact_id, _key, path = await _seed(settings, kind=ArtifactKind.FINAL_VIDEO, expired=False)

    async with session_scope() as session:
        stats = await retention.gc_expired_artifacts(session, settings, now=utcnow())

    assert stats["deleted"] == 0
    assert path.exists()


async def test_artifact_of_running_job_is_skipped(settings: Settings) -> None:
    """正在跑的任务，其产物即使到 TTL 也不能删——删了会让成片缺片段。"""
    _artifact_id, _key, path = await _seed(settings, expired=True, job_status=JobStatus.RUNNING)

    async with session_scope() as session:
        stats = await retention.gc_expired_artifacts(session, settings, now=utcnow())

    assert stats["deleted"] == 0, "运行中任务的产物不能回收"
    assert path.exists()


async def test_already_deleted_object_does_not_loop_forever(settings: Settings) -> None:
    """对象已被外部删除时，仍要把记录标记掉，否则每轮都白扫。"""
    artifact_id, _key, path = await _seed(settings, expired=True)
    path.unlink()

    async with session_scope() as session:
        stats = await retention.gc_expired_artifacts(session, settings, now=utcnow())

    assert stats["deleted"] == 1
    async with session_scope() as session:
        row = await session.get(Artifact, artifact_id)
        assert row is not None and row.deleted_at is not None
