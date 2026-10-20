"""ArtDirector Agent：资产设定与「基准图」生成。

这是跨镜头一致性方案的第一半：
  为每条线索产出一段**只描述稳定特征**的 appearance 文本，
  再据此生成一张基准图（角色设定图 / 场景设定图 / 道具设定图）。
  后续每个镜头都会把基准图作为参考图注入生成请求。

设计要点："先生成一张角色设计图当基准，之后所有分镜和视频都参考它"（ArcReel 同款思路）、
        "主体与场景一致性"（字节即梦）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..harness import RetryPolicy, run_with_retry
from ..llm import prompts
from ..schema import Clue, Project
from ..utils import stable_seed
from .base import Agent

#: 基准图生成优先级：角色 > 场景 > 道具
_KIND_PRIORITY = {"character": 0, "scene": 1, "prop": 2}


class ArtDirector(Agent):
    name = "art_director"
    role = "美术指导"

    def run(self, project: Project, *, max_base_images: int = 6) -> list[Clue]:
        clues = project.clues
        if not clues:
            return clues

        self._fill_appearances(clues, project)
        self._generate_base_images(project, max_base_images=max_base_images)

        self.rt.store.save_project(project)
        return clues

    # ------------------------------------------------------------------ #
    def _fill_appearances(self, clues: list[Clue], project: Project) -> None:
        """一次性补齐所有线索的稳定外观描述。"""
        pending = [c for c in clues if not c.appearance.strip()]
        if not pending:
            return

        style_spec = self.rt.settings.style_spec
        style_repr = f"{style_spec.get('label')}｜{style_spec.get('look')}"
        clue_repr = "\n".join(f"- {c.name}（{c.kind}）：{c.description or '（无描述）'}" for c in pending)
        try:
            data = self.rt.llm_json(
                system=prompts.ART_SYSTEM,
                user=prompts.art_user(clue_repr, style_repr),
                task="art_direction",
                span_name="art_director.fill_appearances",
                temperature=0.5,
            )
        except Exception as exc:
            self.rt.tracer.record("art_director.fill_appearances.failed", status="failed", error=str(exc))
            data = {}

        by_name = {str(item.get("name", "")).strip(): item for item in (data.get("clues") or [])}
        for clue in pending:
            item = by_name.get(clue.name)
            if item and str(item.get("appearance", "")).strip():
                clue.appearance = str(item["appearance"]).strip()[:400]
            else:
                # 兜底：用 description 构造一个确定性描述，保证一致性链路不中断
                clue.appearance = (clue.description or f"{clue.name}（{clue.kind}）").strip()[:200]

    # ------------------------------------------------------------------ #
    def _generate_base_images(self, project: Project, *, max_base_images: int) -> None:
        ordered = sorted(project.clues, key=lambda c: (_KIND_PRIORITY.get(c.kind, 9), c.name))
        targets = [c for c in ordered if not c.base_image][:max_base_images]
        if not targets:
            return

        style_spec = self.rt.settings.style_spec
        style_look = str(style_spec.get("look", ""))
        label = str(style_spec.get("label", ""))

        for clue in targets:
            kind_cn = {"character": "角色设定图", "scene": "场景设定图", "prop": "道具设定图"}.get(
                clue.kind, "设定图"
            )
            prompt = (
                f"{clue.appearance}。{kind_cn}，单人/单物居中，正面平视，纯净背景，"
                f"{style_look}，{label}风格，无文字水印"
            )
            out_name = f"{clue.id}.png"

            def _generate(p: str = prompt, n: str = out_name, cid: str = clue.id) -> dict[str, Any]:
                """具名闭包而非 lambda：lambda 的默认参数在 mypy 下无法推断类型。"""
                return self.rt.tools.call(
                    "generate_keyframe",
                    {
                        "prompt": p,
                        "out_name": n,
                        "ref_images": [],
                        "seed": stable_seed(cid, salt="base_image"),
                        "scale": 0.75,
                        "shot_id": f"asset:{cid}",
                        "subdir": "assets",
                    },
                )

            try:
                result = run_with_retry(
                    _generate,
                    policy=RetryPolicy(attempts=2, base_delay=0.4),
                    tracer=self.rt.tracer,
                    name="art_director.base_image",
                    attrs={"clue": clue.name, "clue_kind": clue.kind},
                )
                clue.base_image = result["path"]
            except Exception as exc:
                self.rt.tracer.record(
                    "art_director.base_image.failed",
                    status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                    clue=clue.name,
                    kind_of_clue=clue.kind,
                )

    # ------------------------------------------------------------------ #
    def describe_assets(self, clues: list[Clue]) -> dict[str, Any]:
        return {
            "total": len(clues),
            "with_appearance": sum(1 for c in clues if c.appearance),
            "with_base_image": sum(1 for c in clues if c.base_image),
            "by_kind": {k: sum(1 for c in clues if c.kind == k) for k in ("character", "prop", "scene")},
        }


def asset_path(settings, project_id: str, clue: Clue) -> Path | None:
    if not clue.base_image:
        return None
    p = settings.abs(clue.base_image)
    return p if p.exists() else None
