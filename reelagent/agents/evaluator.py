"""Evaluator Agent：生成质量评测。

两条腿走路，这是本项目评测设计的关键取舍：

1. **确定性指标**（reelagent/eval/consistency.py）
   色调稳定性、同线索跨镜头相似度、一致性策略覆盖率、prompt 保真度。
   零成本、可复现、可做 CI 门禁。

2. **LLM-as-Judge**
   叙事连贯性、视觉一致性、节奏、prompt 忠实度四个 rubric 打分，
   并输出 top_issues 与可执行建议。

只用其中任何一个都不够：前者不懂"故事好不好"，后者不可复现且会漂移。
"""

from __future__ import annotations

from typing import Any

from ..eval import build_metrics, wilson_lower_bound
from ..llm import prompts
from ..schema import PROJECT_EVALUATED, Project
from .base import Agent


class Evaluator(Agent):
    name = "evaluator"
    role = "评审"

    def run(self, project: Project, *, use_judge: bool = True) -> dict[str, Any]:
        assert project.script is not None
        shots = project.script.shots
        workspace = self.rt.settings.workspace

        metrics = build_metrics(shots, project.clues, workspace)

        # 覆盖率用 Wilson 下界汇报，避免镜头少时的高估（无可度量对象时跳过）
        covered = metrics["coverage_detail"].get("covered", 0)
        eligible = metrics["coverage_detail"].get("eligible", 0)
        metrics["coverage_wilson_lower"] = wilson_lower_bound(covered, eligible) if eligible else None

        judge: dict[str, Any] | None = None
        judge_error: str | None = None
        if use_judge:
            try:
                judge = self.rt.llm_json(
                    system=prompts.JUDGE_SYSTEM,
                    user=prompts.judge_user(self._shot_table(project), project.style),
                    task="judge",
                    span_name="evaluator.judge",
                    temperature=0.2,
                    judge=True,
                )
            except Exception as exc:
                judge_error = f"{type(exc).__name__}: {exc}"
                self.rt.tracer.record("evaluator.judge.failed", status="failed", error=judge_error)

        result: dict[str, Any] = {
            "metrics": metrics,
            "judge": judge,
            "judge_error": judge_error,
            "shots": len(shots),
            "finished": sum(1 for s in shots if s.is_finished),
            "failed": sum(1 for s in shots if s.status == "failed"),
        }
        result["gate"] = self._gate(metrics, judge)

        project.evaluation = result
        project.status = PROJECT_EVALUATED
        self.rt.store.save_project(project)
        return result

    # ------------------------------------------------------------------ #
    @staticmethod
    def _gate(metrics: dict[str, Any], judge: dict[str, Any] | None) -> dict[str, Any]:
        """CI 门禁：给出通过/不通过判定与失败原因。

        对应 JD："建设评测集管理与 CI 门禁，使每次 Prompt、模型、工具变更
        都有量化的收益与回归判定。"
        """
        failures: list[str] = []

        cov = metrics["clue_reference_coverage"]
        fid = metrics["prompt_clue_fidelity"]

        # 一致性策略未生效是最严重的问题：说明基准图/外观描述根本没被用起来
        if cov is None and fid is None:
            failures.append(
                "一致性策略未生效：没有任何镜头绑定到「有基准图 / 有外观描述」的线索，"
                "跨镜头一致性无从保证（请检查 Planner 的 clue_names 与 ArtDirector 是否生成成功）"
            )
        else:
            if fid is not None and fid < 0.95:
                failures.append(f"一致性文本保真度 {fid:.2f} < 0.95：存在镜头未把线索 appearance 注入 prompt")
            if cov is not None and cov < 0.90:
                failures.append(f"基准图注入覆盖率 {cov:.2f} < 0.90")
        if metrics["palette_stability"] < 0.5:
            failures.append(f"色调稳定性 {metrics['palette_stability']:.2f} < 0.50，画面风格漂移")
        if judge and float(judge.get("overall") or 0) < 3.0:
            failures.append(f"LLM 评审总分 {judge.get('overall')} < 3.0")
        return {
            "passed": not failures,
            "failures": failures,
            "thresholds": {
                "prompt_clue_fidelity": 0.95,
                "clue_reference_coverage": 0.90,
                "palette_stability": 0.50,
                "judge_overall": 3.0,
            },
        }

    # ------------------------------------------------------------------ #
    @staticmethod
    def _shot_table(project: Project) -> str:
        assert project.script is not None
        clue_map = {c.id: c for c in project.clues}
        lines = [
            "| 镜头 | 景别 | 运镜 | 时长 | 动作 | 绑定线索 | 生成提示词 |",
            "|---|---|---|---|---|---|---|",
        ]
        for shot in project.script.shots:
            clue_names = "、".join(clue_map[c].name for c in shot.clue_ids if c in clue_map) or "-"
            action = (shot.action or "").replace("|", "/")[:60]
            prompt = (shot.image_prompt or "").replace("|", "/")[:90]
            lines.append(
                f"| {shot.id} | {shot.shot_size} | {shot.camera} | {shot.duration_s}s | "
                f"{action} | {clue_names} | {prompt} |"
            )
        lines.append("")
        lines.append("线索外观（appearance）：")
        for clue in project.clues:
            lines.append(f"- {clue.name}（{clue.kind}）：{clue.appearance[:120]}")
        return "\n".join(lines)
