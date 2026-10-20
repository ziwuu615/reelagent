"""Planner Agent：把种子文本改编成结构化剧本（场景 + 分镜 + 线索清单）。

设计要点："基于用户行为、历史偏好与上下文的创作意图理解"、
"基于多模态大模型的内容理解与结构化建模（视频结构、节奏、美学与风格）"。
"""

from __future__ import annotations

from typing import Any

from ..llm import prompts
from ..schema import Clue, Project, Scene, Script, Shot
from .base import Agent, clue_id

VALID_KINDS = {"character", "prop", "scene"}


class Planner(Agent):
    name = "planner"
    role = "编剧 / 分镜师"

    def run(self, project: Project, *, max_shots: int = 8) -> Script:
        style_label = str(self.rt.settings.style_spec.get("label", project.style))

        data = self.rt.llm_json(
            system=prompts.PLANNER_SYSTEM,
            user=prompts.planner_user(
                seed_text=project.seed_text,
                style=style_label,
                aspect_ratio=project.aspect_ratio,
                max_shots=max_shots,
            ),
            task="plan_script",
            span_name="planner.plan_script",
            temperature=0.75,
        )

        script = self._build_script(data, project, max_shots=max_shots)
        clues = self._build_clues(data, script)

        project.script = script
        project.clues = clues
        self.rt.store.save_project(project)
        return script

    # ------------------------------------------------------------------ #
    def _build_script(self, data: dict[str, Any], project: Project, *, max_shots: int) -> Script:
        script = Script(
            title=str(data.get("title") or "无题")[:40],
            logline=str(data.get("logline") or project.seed_text[:60]),
            style=project.style,
            aspect_ratio=project.aspect_ratio,
        )
        total = 0
        for si, scene_raw in enumerate(data.get("scenes") or [], start=1):
            scene = Scene(
                id=f"sc{si:02d}",
                index=si,
                title=str(scene_raw.get("title") or f"第{si}场")[:40],
                summary=str(scene_raw.get("summary") or "")[:200],
                location=str(scene_raw.get("location") or "")[:60],
                time_of_day=str(scene_raw.get("time_of_day") or "")[:20],
            )
            for sj, shot_raw in enumerate(scene_raw.get("shots") or [], start=1):
                if total >= max_shots:
                    break
                total += 1
                shot = Shot(
                    id=f"{scene.id}_sh{sj:02d}",
                    scene_id=scene.id,
                    index=total,
                    duration_s=_safe_float(shot_raw.get("duration_s"), 3.0, 1.5, 8.0),
                    shot_size=str(shot_raw.get("shot_size") or "中景")[:6],
                    camera=str(shot_raw.get("camera") or "固定")[:6],
                    action=str(shot_raw.get("action") or "")[:300],
                    narration=str(shot_raw.get("narration") or "")[:200],
                    dialogue=str(shot_raw.get("dialogue") or "")[:200],
                    clue_ids=[],
                )
                # 线索名 -> 稳定 ID
                for cname in shot_raw.get("clue_names") or []:
                    cname = str(cname).strip()
                    if cname:
                        cid = clue_id(cname)
                        if cid not in shot.clue_ids:
                            shot.clue_ids.append(cid)
                scene.shots.append(shot)
            if scene.shots:
                script.scenes.append(scene)
            if total >= max_shots:
                break

        if not script.scenes:
            # 兜底：LLM 返回空结构时也要保证流水线能继续
            shot = Shot(id="sc01_sh01", scene_id="sc01", index=1, action=project.seed_text[:200])
            script.scenes = [
                Scene(id="sc01", index=1, title="第1场", summary=project.seed_text[:80], shots=[shot])
            ]
        return script

    # ------------------------------------------------------------------ #
    def _build_clues(self, data: dict[str, Any], script: Script) -> list[Clue]:
        declared: dict[str, dict[str, Any]] = {}
        for raw in data.get("clues") or []:
            name = str(raw.get("name") or "").strip()
            if not name:
                continue
            kind = str(raw.get("kind") or "prop").strip().lower()
            declared[name] = {
                "kind": kind if kind in VALID_KINDS else "prop",
                "description": str(raw.get("description") or "")[:200],
                "appearance": str(raw.get("appearance") or "")[:400],
            }

        # 剧本里引用但未声明的线索，按名称推断类型补上
        used: list[str] = []
        for shot in script.shots:
            for cid in shot.clue_ids:
                if cid not in used:
                    used.append(cid)

        clues: list[Clue] = []
        seen: set[str] = set()
        for name, meta in declared.items():
            cid = clue_id(name)
            if cid in seen:
                continue
            seen.add(cid)
            clues.append(
                Clue(
                    id=cid,
                    kind=meta["kind"],
                    name=name,
                    description=meta["description"],
                    appearance=meta["appearance"],
                )
            )
        for cid in used:
            if cid in seen:
                continue
            seen.add(cid)
            # 从 shot 里反查不到原始名称，用 ID 兜底
            clues.append(Clue(id=cid, kind="character", name=cid, description="剧本引用但未声明"))
        return clues


def _safe_float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, f))
