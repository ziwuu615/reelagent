"""Director Agent：逐镜生成编排。

这是整个流水线里唯一真正"贵"和"慢"的阶段，因此也是工程手段最密集的地方：
  - **断点恢复**：已完成（clip_done / audio_done）的镜头直接跳过，不重复花钱
  - **并发编排**：Sub-Agent 式并发执行多个镜头（受 max_concurrency 约束）
  - **模型路由**：按难度与剩余预算动态选择档位，必要时跳过视频模型省钱
  - **重试与降级**：单镜失败按指数退避重试，仍失败则标记 failed 并继续其余镜头
  - **一致性注入**：把线索基准图作为参考图传入生成请求

设计要点："Sub-Agent 并发编排"、"长任务的状态持久化与 checkpoint 恢复"、
        "模型路由策略"、"重试与降级"。
"""

from __future__ import annotations

import concurrent.futures as cf
import contextlib
import threading
from typing import Any

from ..harness import RetryPolicy, run_with_retry
from ..llm import prompts
from ..routing import ModelRouter
from ..schema import (
    SHOT_AUDIO_DONE,
    SHOT_CLIP_DONE,
    SHOT_FAILED,
    SHOT_KEYFRAME_DONE,
    SHOT_PENDING,
    Clue,
    Project,
    Shot,
)
from ..utils import stable_seed
from .base import Agent

_print_lock = threading.Lock()


class Director(Agent):
    name = "director"
    role = "执行导演"

    def __init__(self, rt, *, verbose: bool = True) -> None:
        super().__init__(rt)
        self.verbose = verbose
        self.router = ModelRouter(rt.settings, spent_usd=rt.spent_usd)

    # ------------------------------------------------------------------ #
    def run(
        self,
        project: Project,
        *,
        force_tier: str | None = None,
        only_shots: list[str] | None = None,
    ) -> dict[str, Any]:
        assert project.script is not None, "必须先运行 Planner"
        shots = project.script.shots
        if only_shots:
            shots = [s for s in shots if s.id in set(only_shots)]

        clue_map = {c.id: c for c in project.clues}
        ws = self.rt.settings.workspace
        # 从 checkpoint 恢复：只信任"DB 状态 + 产物真实存在"两者同时成立的部分。
        # 只信 DB 会导致产物被外部删除后无法重建（这个坑在测试中真实踩到过）。
        persisted = self.rt.store.load_shots(project.id)
        repaired = 0
        for shot in shots:
            prev = persisted.get(shot.id)
            if prev is None:
                continue
            if self._restore(shot, prev, ws):
                repaired += 1
        if repaired:
            self.rt.tracer.record(
                "director.checkpoint_repaired",
                status="ok",
                message="检测到 checkpoint 记录与磁盘产物不一致，已回退对应镜头的状态",
                repaired_shots=repaired,
            )
            self._log(f"checkpoint 校验：{repaired} 个镜头的产物缺失或不全，将重新生成")

        todo = [s for s in shots if not self._is_complete(s)]
        skipped = len(shots) - len(todo)
        if skipped:
            self._log(f"断点恢复：跳过 {skipped} 个已完成镜头，剩余 {len(todo)} 个")

        first_shot_id = shots[0].id if shots else ""

        with cf.ThreadPoolExecutor(max_workers=max(1, self.rt.settings.max_concurrency)) as pool:
            futures = {
                pool.submit(
                    self._do_shot, project, shot, clue_map, force_tier, shot.id == first_shot_id
                ): shot
                for shot in todo
            }
            for fut in cf.as_completed(futures):
                shot = futures[fut]
                try:
                    fut.result()
                except Exception as exc:  # 单镜失败不拖垮整条流水线
                    shot.status = SHOT_FAILED
                    shot.error = f"{type(exc).__name__}: {exc}"[:500]
                    self.rt.store.save_shot(project.id, shot)
                    self._log(f"镜头 {shot.id} 失败：{shot.error}")

        self.rt.store.save_project(project)
        summary = {
            "shots_total": len(shots),
            "generated": sum(1 for s in shots if s.is_finished),
            "failed": sum(1 for s in shots if s.status == SHOT_FAILED),
            "resumed_skipped": skipped,
            "routing": self.router.summary(),
        }
        return summary

    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_complete(shot: Shot) -> bool:
        """镜头在**记录层面**是否已完成。

        ⚠ 只看字段，不访问文件系统：`clip_path` 有值不代表文件还在磁盘上。

        真正的产物校验在 `_restore()` 里逐字段做（`_keep`），
        但那只在 Director **实际运行**时才会执行。所以判断
        「这个阶段能不能跳过」时不能用本函数 —— 一旦阶段被跳过，
        Director 不运行，`_restore()` 的修复也就永远没机会发生，
        被外部删除的产物会一直缺失。
        需要文件级判据时请用 `Shot.artifacts_present(workspace)`。
        """
        return shot.is_finished

    @staticmethod
    def _restore(shot: Shot, prev: Shot, workspace) -> bool:
        """把 checkpoint 里仍然有效的产物恢复到 shot 上。

        逐字段做存在性校验，因此"关键帧还在但片段被删"这类部分损坏也能正确处理：
        保留仍然有效的部分，只重做缺失的部分。
        返回 True 表示确实发生了回退（记录与磁盘不一致）。
        """
        degraded = False

        def _keep(rel: str | None) -> str | None:
            nonlocal degraded
            if rel and (workspace / rel).exists():
                return rel
            if rel:
                degraded = True
            return None

        kf = _keep(prev.keyframe_path)
        clip = _keep(prev.clip_path)
        audio = _keep(prev.audio_path)

        shot.image_prompt = prev.image_prompt
        shot.video_prompt = prev.video_prompt
        shot.attempts = prev.attempts
        shot.provider_used = prev.provider_used
        shot.model_used = prev.model_used
        shot.cost_usd = prev.cost_usd
        shot.keyframe_path = kf
        shot.clip_path = clip
        shot.audio_path = audio

        # 状态按"实际存在的产物"重算，而不是沿用 DB 里的乐观状态
        if clip and (audio or not shot.needs_audio):
            shot.status = SHOT_AUDIO_DONE if audio else SHOT_CLIP_DONE
        elif clip:
            shot.status = SHOT_CLIP_DONE
        elif kf:
            shot.status = SHOT_KEYFRAME_DONE
        else:
            shot.status = SHOT_PENDING
        return degraded

    # ------------------------------------------------------------------ #
    def _do_shot(
        self,
        project: Project,
        shot: Shot,
        clue_map: dict[str, Clue],
        force_tier: str | None,
        is_first: bool,
    ) -> None:
        if self._is_complete(shot):
            return

        bound_clues = [clue_map[c] for c in shot.clue_ids if c in clue_map]
        decision = self.router.decide(shot, project.clues, is_first=is_first, force_tier=force_tier)
        # 落到镜头记录上，便于界面与账单按档位归因
        shot.tier = decision.tier
        shot.attempts += 1

        with self.rt.tracer.span(
            "director.shot",
            kind="agent",
            shot_id=shot.id,
            tier=decision.tier,
            difficulty=round(decision.difficulty, 3),
            attempt=shot.attempts,
        ) as sp:
            # ---- 1. 生成结构化 prompt（含线索 appearance 注入）----
            if not shot.image_prompt or not shot.video_prompt:
                self._build_prompts(shot, bound_clues, project)

            # ---- 2. 关键帧（带基准图参考）----
            if not shot.keyframe_path:
                refs = [c.base_image for c in bound_clues if c.base_image]
                result = run_with_retry(
                    lambda: self.rt.tools.call(
                        "generate_keyframe",
                        {
                            "prompt": shot.image_prompt,
                            "out_name": f"{shot.id}.png",
                            "ref_images": refs,
                            "seed": stable_seed(shot.id),
                            "shot_id": shot.id,
                            # 关键帧与片段必须同档分辨率：图生视频时
                            # 输出尺寸跟随输入图，两者不一致会导致成片尺寸抖动。
                            "scale": decision.resolution_scale,
                        },
                    ),
                    policy=RetryPolicy(attempts=self.rt.settings.max_attempts),
                    tracer=self.rt.tracer,
                    name="director.keyframe",
                    attrs={"shot_id": shot.id, "refs": len(refs)},
                )
                shot.keyframe_path = result["path"]
                shot.provider_used = result["provider"]
                shot.model_used = result["model"]
                shot.cost_usd += result["cost_usd"]
                shot.status = SHOT_KEYFRAME_DONE
                self.rt.store.save_shot(project.id, shot)

            # ---- 3. 动态片段 ----
            if not shot.clip_path:
                clip_args = {
                    "prompt": shot.video_prompt,
                    "keyframe": shot.keyframe_path,
                    "out_name": f"{shot.id}.gif",
                    "duration_s": shot.duration_s,
                    "seed": stable_seed(shot.id, salt="video"),
                    "shot_id": shot.id,
                    "scale": decision.resolution_scale,
                    # express 档走优速版视频模型：同画质，实测快 63%。
                    # 注意它只对**视频**生效 —— 关键帧仍用基础图像模型。
                    "use_fast": decision.use_fast_model,
                }
                try:
                    result = run_with_retry(
                        lambda: self.rt.tools.call(
                            "generate_clip",
                            {**clip_args, "use_video_model": decision.use_video_model},
                        ),
                        policy=RetryPolicy(attempts=self.rt.settings.max_attempts),
                        tracer=self.rt.tracer,
                        name="director.clip",
                        attrs={"shot_id": shot.id, "tier": decision.tier},
                    )
                except Exception as exc:
                    # 视频模型故障（限流、超时、额度耗尽）时降级为本地运动合成。
                    #
                    # 为什么降级而不是让镜头失败：单个供应商的瞬时抖动不该让
                    # 用户的成片开天窗。草稿档的本地合成零成本、必然成功，
                    # 成片仍可交付。
                    #
                    # 降级是**执行期事件**，所以记入链路追踪而不是 Shot 字段：
                    # Shot 表达的是产物状态，而「这次为什么没用上视频模型」
                    # 属于过程信息，两者的生命周期与用途不同。
                    if not decision.use_video_model:
                        raise  # 本来就走本地合成还失败，那是真故障
                    self.rt.tracer.record(
                        "director.clip.degraded_to_local",
                        status="degraded",
                        shot_id=shot.id,
                        reason=f"{type(exc).__name__}: {str(exc)[:200]}",
                        tier=decision.tier,
                    )
                    result = run_with_retry(
                        lambda: self.rt.tools.call("generate_clip", {**clip_args, "use_video_model": False}),
                        policy=RetryPolicy(attempts=self.rt.settings.max_attempts),
                        tracer=self.rt.tracer,
                        name="director.clip_fallback",
                        attrs={"shot_id": shot.id, "fallback": True},
                    )
                shot.clip_path = result["path"]
                shot.cost_usd += result["cost_usd"]
                shot.status = SHOT_CLIP_DONE
                self.rt.store.save_shot(project.id, shot)

            # ---- 4. 配音 ----
            #
            # 配音是**附属能力**：画面才是这个产品的产出。
            # 因此音频失败绝不能把整个镜头判为失败 —— 那会让一个已经
            # 生成好关键帧和片段的镜头前功尽弃，用户看到的成片缺一段，
            # 而原因仅仅是一段旁白没合成出来。
            #
            # 这里改为「尽力而为」：失败就跳过音频、记录降级，继续走完渲染。
            speech = shot.dialogue or shot.narration
            if speech and not shot.audio_path:
                try:
                    result = run_with_retry(
                        lambda: self.rt.tools.call(
                            "synthesize_speech",
                            {"text": speech, "out_name": f"{shot.id}.wav", "shot_id": shot.id},
                        ),
                        policy=RetryPolicy(attempts=self.rt.settings.max_attempts),
                        tracer=self.rt.tracer,
                        name="director.audio",
                        attrs={"shot_id": shot.id, "chars": len(speech)},
                    )
                    shot.audio_path = result["path"]
                    shot.cost_usd += result["cost_usd"]
                    shot.status = SHOT_AUDIO_DONE
                except Exception as exc:
                    # 不重试、不上抛：记录降级后继续。
                    # 成片会没有这段配音，但画面完整 —— 这是更合理的取舍。
                    with contextlib.suppress(Exception):
                        self.rt.tracer.span(
                            "director.audio_skipped",
                            shot_id=shot.id,
                            error=f"{type(exc).__name__}: {exc}"[:300],
                        )
                    self._log(f"! {shot.id} 配音失败，跳过音频继续（画面不受影响）")

            if shot.status == SHOT_CLIP_DONE and not speech:
                shot.status = SHOT_CLIP_DONE

            shot.error = None
            sp.set(
                tier=decision.tier,
                reason=decision.reason,
                keyframe=bool(shot.keyframe_path),
                clip=bool(shot.clip_path),
                cost_usd=round(shot.cost_usd, 5),
            )
            self.rt.store.save_shot(project.id, shot)
            self._log(f"✓ {shot.id} [{decision.tier}] {shot.shot_size}/{shot.camera}")

    # ------------------------------------------------------------------ #
    def _build_prompts(self, shot: Shot, bound_clues: list[Clue], project: Project) -> None:
        style_spec = self.rt.settings.style_spec
        style_repr = f"{style_spec.get('label')}｜{style_spec.get('lighting')}｜{style_spec.get('look')}"
        clue_lines = [f"{c.name}：{c.appearance}" for c in bound_clues if c.appearance.strip()]
        try:
            data = self.rt.llm_json(
                system=prompts.DIRECTOR_SYSTEM,
                user=prompts.director_user(
                    action=shot.action or "主体在画面中",
                    shot_size=shot.shot_size,
                    camera=shot.camera,
                    style_spec=style_repr,
                    aspect_ratio=project.aspect_ratio,
                    clue_lines=clue_lines,
                ),
                task="direct_prompt",
                span_name="director.build_prompt",
                temperature=0.4,
            )
            shot.image_prompt = str(data.get("image_prompt") or "").strip() or self._fallback_prompt(
                shot, clue_lines, style_repr
            )
            shot.video_prompt = str(data.get("video_prompt") or "").strip() or f"{shot.camera}，{shot.action}"
        except Exception as exc:
            self.rt.tracer.record(
                "director.build_prompt.failed",
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
                shot_id=shot.id,
            )
            shot.image_prompt = self._fallback_prompt(shot, clue_lines, style_repr)
            shot.video_prompt = f"{shot.camera}，{shot.action}"

    @staticmethod
    def _fallback_prompt(shot: Shot, clue_lines: list[str], style_repr: str) -> str:
        parts = [shot.shot_size, shot.action]
        if clue_lines:
            parts.append("；".join(clue_lines))
        parts.append(style_repr)
        return "，".join(p for p in parts if p)

    # ------------------------------------------------------------------ #
    def _log(self, msg: str) -> None:
        if self.verbose:
            with _print_lock:
                print(f"  [director] {msg}", flush=True)
