"""流水线状态机：可断点恢复的五阶段执行。

    plan -> assets -> generate -> evaluate -> render

每个阶段完成后写入 checkpoint。进程被 Ctrl-C 或崩溃后，
再次执行 `run --project <id>` 会从第一个未完成的阶段继续，
已完成镜头不会重复生成（不重复花钱）。

对应 JD："长任务的状态持久化与 checkpoint 恢复"、"数据飞轮"、
        "任务规划、工作流编排"。
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..agents import ArtDirector, Director, Evaluator, Planner
from ..config import Settings
from ..harness import Store
from ..render import compose
from ..runtime import Runtime
from ..schema import (
    PROJECT_ASSETS_READY,
    PROJECT_CREATED,
    PROJECT_EVALUATED,
    PROJECT_FAILED,
    PROJECT_GENERATED,
    PROJECT_PLANNED,
    PROJECT_RENDERED,
    Project,
    new_id,
)

STAGES = ("plan", "assets", "generate", "evaluate", "render")


class PipelineCancelled(RuntimeError):
    """被上层请求取消。由 `should_cancel` 回调触发，用于 SaaS 场景的「取消任务」。"""


_STAGE_STATUS = {
    "plan": PROJECT_PLANNED,
    "assets": PROJECT_ASSETS_READY,
    "generate": PROJECT_GENERATED,
    "evaluate": PROJECT_EVALUATED,
    "render": PROJECT_RENDERED,
}


@dataclass
class StageOutcome:
    name: str
    status: str  # done | skipped | failed
    duration_ms: float = 0.0
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.name,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 1),
            "error": self.error,
            **({"output": self.output} if self.output else {}),
        }


class Pipeline:
    def __init__(self, settings: Settings | None = None, *, verbose: bool = True) -> None:
        self.settings = settings or Settings.load()
        self.verbose = verbose
        self.settings.ensure_workspace()

    # ------------------------------------------------------------------ #
    def _store(self) -> Store:
        return Store(self.settings.workspace / "reelagent.db")

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)

    # ------------------------------------------------------------------ #
    def create(
        self,
        seed_text: str,
        *,
        style: str | None = None,
        aspect_ratio: str | None = None,
        mode: str | None = None,
    ) -> Project:
        project = Project(
            id=new_id("proj"),
            seed_text=seed_text.strip(),
            style=style or self.settings.style,
            aspect_ratio=aspect_ratio or self.settings.aspect_ratio,
            mode=mode or self.settings.mode,
            status=PROJECT_CREATED,
        )
        with self._store() as store:
            store.save_project(project)
        return project

    # ------------------------------------------------------------------ #
    def run(
        self,
        project_id: str,
        *,
        from_stage: str | None = None,
        only_shots: list[str] | None = None,
        force_tier: str | None = None,
        use_judge: bool = True,
        max_shots: int = 8,
        max_base_images: int = 6,
        on_stage: Callable[[str, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """执行流水线。

        两个宿主回调让引擎可以在 SaaS 场景下被驱动：
          - `on_stage(stage, status)`：阶段开始/结束时上报进度
          - `should_cancel() -> bool`：在阶段边界检查取消请求

        引擎本身不认识数据库、队列与 HTTP，这些能力全部由宿主注入。
        """
        started = time.perf_counter()
        outcomes: list[StageOutcome] = []

        with self._store() as store:
            project = store.load_project(project_id)
            if project is None:
                raise SystemExit(f"找不到项目：{project_id}（用 `reelagent list` 查看）")

            rt = Runtime(self.settings, project_id, store=store)
            self._log(f"项目 {project.id}｜{project.style}｜{project.aspect_ratio}｜模式 {project.mode}")
            if rt.providers.notes:
                for note in rt.providers.notes:
                    self._log(f"  ⚠ {note}")

            rerun_from = STAGES.index(from_stage) if from_stage in STAGES else len(STAGES)
            if from_stage in STAGES:
                for name in STAGES[rerun_from:]:
                    store.reset_stage(project_id, name)
                self._log(f"已重置 {from_stage} 及其之后的阶段状态")

            try:
                for idx, name in enumerate(STAGES):
                    if idx < rerun_from and store.stage_is_done(project_id, name):
                        # 阶段标记完成 ≠ 这个阶段真的没活可干。
                        #
                        # Director 刻意让「单镜失败不拖垮整条流水线」，
                        # 所以某个镜头失败时 generate 阶段依然会被标记 done。
                        # 若 resume 只看阶段状态，那些失败/被降级的镜头
                        # 就永远得不到重试 —— 断点恢复恰好在最需要它的场景下失效。
                        if name == "generate" and self._has_unfinished_shots(store, project_id):
                            self._log(f"↻ 阶段 {name} 虽标记完成，但仍有未完成镜头，重新执行")
                            # 上游重跑 → 下游必须失效。
                            #
                            # evaluate 是基于片段算指标的，render 是把片段合成为成片的。
                            # 若只重跑 generate 而不重置下游，会出现**成片是旧片段合成的、
                            # 指标却是新算的**这种数据与产物不一致的状态 —— 而且从界面上
                            # 完全看不出来，比直接报错危险得多。
                            for later in STAGES[idx + 1 :]:
                                store.reset_stage(project_id, later)
                                self._log(f"  ↻ 因 {name} 重跑，重置下游阶段 {later}")
                            if on_stage:
                                on_stage(name, "running")
                        else:
                            outcomes.append(StageOutcome(name, "skipped"))
                            self._log(f"↷ 跳过已完成阶段 {name}")
                            if on_stage:
                                on_stage(name, "skipped")
                            continue

                    if should_cancel and should_cancel():
                        raise PipelineCancelled(f"任务在阶段 {name} 之前被取消")

                    if on_stage:
                        on_stage(name, "running")

                    outcome = self._run_stage(
                        name,
                        rt,
                        project,
                        store,
                        max_shots=max_shots,
                        max_base_images=max_base_images,
                        only_shots=only_shots,
                        force_tier=force_tier,
                        use_judge=use_judge,
                    )
                    outcomes.append(outcome)
                    if on_stage:
                        on_stage(name, outcome.status)
                    if outcome.status == "failed":
                        project.status = PROJECT_FAILED
                        store.save_project(project)
                        break
            finally:
                project.cost_summary = store.cost_summary(project_id)
                store.save_project(project)
                summary = store.summary(project_id)
                rt.close()

        elapsed = (time.perf_counter() - started) * 1000
        return {
            "project_id": project_id,
            "elapsed_ms": round(elapsed, 1),
            "stages": [o.as_dict() for o in outcomes],
            "summary": summary,
            "project": project.to_dict(),
        }

    # ------------------------------------------------------------------ #
    def _run_stage(
        self,
        name: str,
        rt: Runtime,
        project: Project,
        store: Store,
        *,
        max_shots: int,
        max_base_images: int,
        only_shots: list[str] | None,
        force_tier: str | None,
        use_judge: bool,
    ) -> StageOutcome:
        self._log(f"▶ 阶段 {name}")
        store.stage_start(project.id, name)
        t0 = time.perf_counter()
        try:
            with rt.tracer.span(f"stage.{name}", kind="stage") as sp:
                output = self._dispatch(
                    name,
                    rt,
                    project,
                    max_shots=max_shots,
                    max_base_images=max_base_images,
                    only_shots=only_shots,
                    force_tier=force_tier,
                    use_judge=use_judge,
                )
                sp.set(**(output or {}))
            ms = (time.perf_counter() - t0) * 1000
            store.stage_done(project.id, name, output)
            project.status = _STAGE_STATUS.get(name, project.status)
            store.save_project(project)
            self._log(f"✓ 阶段 {name} 完成（{ms / 1000:.1f}s）")
            return StageOutcome(name, "done", ms, output)
        except Exception as exc:
            ms = (time.perf_counter() - t0) * 1000
            detail = f"{type(exc).__name__}: {exc}"
            store.stage_failed(project.id, name, detail)
            self._log(f"✗ 阶段 {name} 失败：{detail}")
            if self.verbose:
                traceback.print_exc()
            return StageOutcome(name, "failed", ms, error=detail)

    # ------------------------------------------------------------------ #
    def _has_unfinished_shots(self, store: Store, project_id: str) -> bool:
        """是否存在产物尚未真正落盘的镜头。

        用 `Shot.artifacts_present()`（会真的去查文件）而不是 `is_finished`
        （只看字段）。差别在真实场景里很关键：片段文件被删除后，
        `clip_path` 字段仍有值，`is_finished` 会谎报已完成，
        于是整个 generate 阶段被跳过，用户拿不到成片却查不出原因。
        """
        try:
            shots = store.load_shots(project_id)
        except Exception:  # pragma: no cover - 读状态失败时保守地当作「有活要干」
            return True
        base = self.settings.workspace
        return any(not shot.artifacts_present(base) for shot in shots.values())

    # ------------------------------------------------------------------ #
    def _dispatch(self, name: str, rt: Runtime, project: Project, **kw: Any) -> dict[str, Any]:
        if name == "plan":
            script = Planner(rt).run(project, max_shots=kw["max_shots"])
            return {
                "title": script.title,
                "scenes": len(script.scenes),
                "shots": len(script.shots),
                "clues": len(project.clues),
                "total_duration_s": script.total_duration_s,
            }
        if name == "assets":
            art = ArtDirector(rt)
            clues = art.run(project, max_base_images=kw["max_base_images"])
            return art.describe_assets(clues)
        if name == "generate":
            return Director(rt, verbose=self.verbose).run(
                project, force_tier=kw["force_tier"], only_shots=kw["only_shots"]
            )
        if name == "evaluate":
            result = Evaluator(rt).run(project, use_judge=kw["use_judge"])
            return {
                "gate_passed": result["gate"]["passed"],
                "failures": result["gate"]["failures"],
                "metrics": result["metrics"],
                "judge_overall": (result.get("judge") or {}).get("overall"),
            }
        if name == "render":
            return compose(project, rt.settings)
        raise ValueError(f"未知阶段：{name}")

    # ------------------------------------------------------------------ #
    def list_projects(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._store() as store:
            return store.list_projects(limit)

    def status(self, project_id: str) -> dict[str, Any]:
        with self._store() as store:
            if store.load_project(project_id) is None:
                raise SystemExit(f"找不到项目：{project_id}")
            return store.summary(project_id)

    def spans(self, project_id: str) -> list[dict[str, Any]]:
        with self._store() as store:
            return store.spans(project_id)

    def span_stats(self, project_id: str) -> dict[str, dict[str, float]]:
        with self._store() as store:
            return store.span_stats(project_id)

    def load(self, project_id: str) -> Project | None:
        with self._store() as store:
            return store.load_project(project_id)
