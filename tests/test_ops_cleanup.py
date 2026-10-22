"""四项「低优先级」收尾修复的回归测试。

这些缺陷都不影响出片，所以一直排在有价值的问题后面。但它们共同的特点是
**只在特定时刻才咬人**：出了事故才想看工作区（已删）、下载成片时拿错文件
（顺序变了才发生）、想知道传了多少字节（日志写死 0）。

## 四个缺陷

1. **工作区执行完就删** —— 出问题后无法复查引擎现场。
   真实教训：查「文本保真度为什么是 0.3」时工作区已被删，
   只能靠 DB 里残留的 JSON 勉强还原。

2. **`artifacts_collected` 日志的 `bytes=0` 是硬编码的**
   `log.info(..., bytes=0)` —— 从没测量过，排查时毫无用处。

3. **成片与镜头片段的 `kind` 都是 `video`**
   取成片只能靠创建时间倒序猜。一条流水线写入多个 mp4，
   顺序一变就把某个镜头片段当成成片给用户下载 —— 不报错，只是静默给错。

4. **免费版界面上有可用的「创建密钥」按钮**，点了才收到 403。
   一个「看起来能用、实际一定失败」的入口比没有入口更糟。
"""

from __future__ import annotations

import pathlib
import time

from fastapi.testclient import TestClient

from reelagent_cloud.domain.enums import ArtifactKind
from reelagent_cloud.services.pipeline import cleanup_workspace, sweep_stale_workspaces


class TestWorkspaceRetention:
    """失败时保留现场，成功时清掉，超期后由清扫兜底。"""

    def test_keep_true_preserves_workspace(self, tmp_workspace: pathlib.Path) -> None:
        ws = tmp_workspace / "job_keep"
        (ws / "projects" / "proj_x").mkdir(parents=True)
        (ws / "reelagent.db").write_text("x" * 100, encoding="utf-8")

        cleanup_workspace(ws, keep=True)

        assert ws.exists(), "失败任务的工作区必须保留，否则无法排查"
        assert (ws / "reelagent.db").exists()

    def test_keep_false_removes_workspace(self, tmp_workspace: pathlib.Path) -> None:
        ws = tmp_workspace / "job_drop"
        (ws / "projects").mkdir(parents=True)
        (ws / "reelagent.db").write_text("x" * 100, encoding="utf-8")

        cleanup_workspace(ws, keep=False)

        assert not ws.exists(), "成功任务的工作区应当清掉（产物已在对象存储）"

    def test_cleanup_missing_dir_is_safe(self, tmp_workspace: pathlib.Path) -> None:
        cleanup_workspace(tmp_workspace / "not_exist", keep=False)

    def test_sweep_removes_old_keeps_recent(self, tmp_workspace: pathlib.Path) -> None:
        root = tmp_workspace / "engine"
        old = root / "job_old"
        new = root / "job_new"
        for d in (old, new):
            d.mkdir(parents=True)
            (d / "f.txt").write_text("x", encoding="utf-8")

        # 把 old 的 mtime 推到 100 小时前
        past = time.time() - 100 * 3600
        for p in [old, *old.rglob("*")]:
            import os

            os.utime(p, (past, past))

        removed = sweep_stale_workspaces(root, max_age_hours=72)

        assert removed == 1
        assert not old.exists(), "超期工作区必须被清掉，否则磁盘会无限增长"
        assert new.exists(), "近期工作区不能动"

    def test_sweep_missing_root_is_safe(self, tmp_workspace: pathlib.Path) -> None:
        assert sweep_stale_workspaces(tmp_workspace / "nope") == 0


class TestFinalVideoKind:
    """成片必须有独立的 kind，不能和镜头片段混在一起。"""

    def test_enum_has_distinct_final_video(self) -> None:
        assert ArtifactKind.FINAL_VIDEO.value == "final_video"
        assert ArtifactKind.FINAL_VIDEO != ArtifactKind.VIDEO

    def test_collect_assigns_final_video_and_real_bytes(self, settings, tmp_workspace: pathlib.Path) -> None:
        """final/*.mp4 要被记成 FINAL_VIDEO，且 size_bytes 是真实字节数。

        size_bytes 同时是 `artifacts_collected` 日志里 bytes 的来源 ——
        那个字段以前硬编码为 0。
        """
        import asyncio

        from reelagent_cloud.api.ratelimit import reset_limiter_sync
        from reelagent_cloud.db import init_engine
        from reelagent_cloud.db.session import create_all
        from reelagent_cloud.services import pipeline as pipeline_svc
        from reelagent_cloud.services.storage import reset_storage

        reset_storage()
        reset_limiter_sync()
        init_engine(settings.database)
        loop = asyncio.get_event_loop_policy().new_event_loop()
        loop.run_until_complete(create_all())

        ws = tmp_workspace / "job1"
        proj_dir = ws / "projects" / "proj_engine"
        (proj_dir / "final").mkdir(parents=True)
        (proj_dir / "clips").mkdir(parents=True)
        final_bytes = b"F" * 2048
        clip_bytes = b"C" * 1024
        (proj_dir / "final" / "final.mp4").write_bytes(final_bytes)
        (proj_dir / "clips" / "sc01_sh01.mp4").write_bytes(clip_bytes)

        ok = loop.run_until_complete(
            pipeline_svc._collect_artifacts(
                settings,
                job_id="job1",
                org_id="org1",
                project_id="proj1",
                workspace=ws,
                engine_project_id="proj_engine",
            )
        )
        assert len(ok) == 2, "成片与镜头片段都应被收集"

        async def _rows():
            from sqlalchemy import select

            from reelagent_cloud.db.models import Artifact
            from reelagent_cloud.db.session import session_scope

            async with session_scope() as session:
                return list((await session.execute(select(Artifact))).scalars())

        rows = loop.run_until_complete(_rows())
        by_kind = {r.kind: r for r in rows}

        assert ArtifactKind.FINAL_VIDEO in by_kind, f"成片应记成 final_video，实际 {list(by_kind)}"
        assert by_kind[ArtifactKind.FINAL_VIDEO].size_bytes == len(final_bytes)
        assert by_kind[ArtifactKind.VIDEO].size_bytes == len(clip_bytes)
        assert by_kind[ArtifactKind.VIDEO].filename == "sc01_sh01.mp4"


class TestSettingsPagePlanGate:
    """免费版界面上不该出现可用的「创建密钥」入口。"""

    def test_free_plan_shows_upgrade_hint(self, authed_client: TestClient) -> None:
        html = authed_client.get("/app/settings").text

        assert "API Key 需要专业版" in html, "免费版必须说明需要升级，而不是给一个必失败的表单"
        # 不该出现能提交的创建表单
        assert 'action="/app/settings/api-keys"' not in html, "免费版不应渲染创建密钥表单（提交必然 403）"

    def test_other_admin_controls_still_work(self, authed_client: TestClient) -> None:
        """确认没有连带把整页弄坏。"""
        resp = authed_client.get("/app/settings")
        assert resp.status_code == 200
        assert "BYOK" in resp.text


class TestBytesIsMeasured:
    def test_log_uses_accumulated_bytes(self) -> None:
        """`artifacts_collected` 的 bytes 必须是累计值，不能是字面量 0。

        只检查**日志调用那一行**，不做全文匹配 —— 全文匹配会误伤
        说明「以前写死 0」的注释（第一版测试就是这么挂的）。
        """
        import re

        src = pathlib.Path("reelagent_cloud/services/pipeline.py").read_text(encoding="utf-8")
        line = next((ln for ln in src.splitlines() if 'log.info("artifacts_collected"' in ln), "")
        assert line, "找不到 artifacts_collected 的日志调用"
        assert "bytes=total_bytes" in line, f"bytes 必须是累计值，实际: {line.strip()}"
        m = re.search(r"bytes\s*=\s*(\d+)\s*[,)]", line)
        assert m is None, f"bytes 不能是字面量: {line.strip()}"
        assert "total_bytes += int(stored.size_bytes" in src


class TestWorkerKeepsFailedWorkspace:
    def test_worker_passes_keep_flag(self) -> None:
        src = pathlib.Path("reelagent_cloud/workers/runner.py").read_text(encoding="utf-8")
        assert "keep=not succeeded" in src, "worker 必须把「是否成功」传给 cleanup_workspace"
        assert "sweep_stale_workspaces" in src, "失败工作区需要定期清扫，否则会撑满磁盘"


class TestPickFinalAmongManyVideos:
    """一条流水线会写入多个 mp4，取成片不能靠时间顺序猜。

        旧代码
    ext(a for a in artifacts if a.kind in (VIDEO, GIF)) 配合
        **升序**的 artifacts 列表，返回的是第一个镜头片段 ——
        项目详情页的「成片」实际从未取到成片。
    """

    def test_console_prefers_final_video_over_clips(self) -> None:
        src = pathlib.Path("reelagent_cloud/api/routes/console.py").read_text(encoding="utf-8")
        i = src.find("final = next")
        assert i > 0
        seg = src[i : i + 700]
        # 必须是「先单独找 FINAL_VIDEO」，而不是把三种 kind 放进同一个集合
        assert "a.kind is ArtifactKind.FINAL_VIDEO" in seg, seg
        assert "legacy" in seg, "旧数据需要单独的退回分支"

    def test_service_lookup_order_starts_with_final_video(self) -> None:
        src = pathlib.Path("reelagent_cloud/services/projects.py").read_text(encoding="utf-8")
        i = src.find("for kind in (ArtifactKind.FINAL_VIDEO")
        assert i > 0, "find_final_artifact 必须把 FINAL_VIDEO 排在第一个"
