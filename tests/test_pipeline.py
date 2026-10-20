"""流水线端到端测试：跑通、断点恢复、checkpoint 校验、门禁。

这些用例直接覆盖项目最核心的工程主张：断点恢复必须真的能恢复，
而不是"看起来能恢复"。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from reelagent.config import ProviderConfig, Settings
from reelagent.pipeline import Pipeline
from tests.support import cleanup_tmp, make_tmp

SEED = "林晚在深夜的便利店找到一把黄铜钥匙。老人说这盒牛奶他等了很久。钥匙上刻着她的生日。"


def make_settings(tmp: Path) -> Settings:
    return Settings(
        mode="mock",
        workspace=tmp,
        style="cinematic",
        aspect_ratio="9:16",
        budget_usd=2.0,
        max_concurrency=2,
        max_attempts=2,
        llm=ProviderConfig("llm", "mock"),
        image=ProviderConfig("mock", "mock"),
        video=ProviderConfig("mock", "mock"),
        tts=ProviderConfig("mock", "mock"),
    )


class TestPipelineEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tmp("pipe")
        self.settings = make_settings(self.tmp)
        self.pipe = Pipeline(self.settings, verbose=False)
        self.project = self.pipe.create(SEED)
        self.result = self.pipe.run(self.project.id, max_shots=3, max_base_images=3)

    def tearDown(self):
        cleanup_tmp(self.tmp)

    def _stage(self, name: str) -> dict:
        for s in self.result["stages"]:
            if s["stage"] == name:
                return s
        raise AssertionError(f"缺少阶段 {name}")

    def test_all_stages_done(self):
        statuses = {s["stage"]: s["status"] for s in self.result["stages"]}
        self.assertEqual(
            statuses,
            {"plan": "done", "assets": "done", "generate": "done", "evaluate": "done", "render": "done"},
        )

    def test_plan_produced_structured_script(self):
        out = self._stage("plan")["output"]
        self.assertGreaterEqual(out["shots"], 1)
        self.assertLessEqual(out["shots"], 3)
        self.assertGreaterEqual(out["clues"], 1)
        self.assertGreater(out["total_duration_s"], 0)

    def test_base_images_generated_for_clues(self):
        """回归测试：基准图曾经因为 span(kind=...) 参数冲突而全部静默失败。"""
        out = self._stage("assets")["output"]
        self.assertGreater(out["total"], 0, "必须抽出线索")
        self.assertGreater(
            out["with_base_image"],
            0,
            "必须至少生成一张基准图——为 0 说明一致性策略没生效",
        )

    def test_shot_artifacts_exist_on_disk(self):
        project = self.pipe.load(self.project.id)
        self.assertIsNotNone(project.script)
        ws = self.settings.workspace
        for shot in project.script.shots:
            self.assertTrue(shot.keyframe_path, f"{shot.id} 缺关键帧路径")
            self.assertTrue((ws / shot.keyframe_path).exists(), f"{shot.id} 关键帧文件不存在")
            self.assertTrue(shot.clip_path, f"{shot.id} 缺片段路径")
            self.assertTrue((ws / shot.clip_path).exists(), f"{shot.id} 片段文件不存在")
            if shot.needs_audio:
                self.assertTrue(shot.audio_path, f"{shot.id} 有台词却没配音")
                self.assertTrue((ws / shot.audio_path).exists())

    def test_render_outputs_viewable_artifacts(self):
        out = self._stage("render")["output"]
        self.assertIn("manifest", out)
        ws = self.settings.workspace
        self.assertTrue((ws / out["manifest"]).exists())
        # 没有 ffmpeg 时必须降级出 GIF，而不是什么都没有
        has_video = bool(out.get("video") or out.get("gif"))
        self.assertTrue(has_video, f"没有任何成片输出：{out}")

    def test_evaluation_metrics_and_gate(self):
        m = self._stage("evaluate")["output"]["metrics"]
        self.assertTrue(m["consistency_strategy_active"], "一致性策略应判定为已生效")
        self.assertIsNotNone(m["clue_reference_coverage"])
        self.assertAlmostEqual(m["clue_reference_coverage"], 1.0)
        self.assertAlmostEqual(m["prompt_clue_fidelity"], 1.0)
        self.assertGreater(m["overall"], 0.5)

    def test_judge_ran_and_scored(self):
        out = self._stage("evaluate")["output"]
        self.assertIsNotNone(out.get("judge_overall"))
        self.assertGreaterEqual(out["judge_overall"], 1.0)

    def test_tracing_spans_recorded(self):
        names = {s["name"] for s in self.pipe.spans(self.project.id)}
        for expected in (
            "stage.plan",
            "stage.generate",
            "tool.generate_keyframe",
            "tool.generate_clip",
            "director.shot",
        ):
            self.assertIn(expected, names)

    def test_cost_records_exist_for_media_calls(self):
        summary = self.pipe.status(self.project.id)
        cats = set(summary["cost"]["by_category"])
        self.assertIn("image", cats)
        self.assertIn("video", cats)


class TestResumeAndCheckpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tmp("pipe")
        self.settings = make_settings(self.tmp)
        self.pipe = Pipeline(self.settings, verbose=False)
        self.project = self.pipe.create(SEED)
        self.pipe.run(self.project.id, max_shots=3, max_base_images=2)

    def tearDown(self):
        cleanup_tmp(self.tmp)

    def test_second_run_skips_everything(self):
        result = self.pipe.run(self.project.id)
        statuses = [s["status"] for s in result["stages"]]
        self.assertEqual(statuses, ["skipped"] * 5)

    def test_missing_artifact_triggers_regeneration(self):
        """核心用例：产物被外部删除后，checkpoint 必须失效并重新生成。"""
        project = self.pipe.load(self.project.id)
        assert project.script is not None
        victim = project.script.shots[0]
        clip = self.settings.workspace / victim.clip_path
        self.assertTrue(clip.exists())
        clip.unlink()

        result = self.pipe.run(self.project.id, from_stage="generate")
        gen = next(s for s in result["stages"] if s["stage"] == "generate")
        self.assertEqual(gen["status"], "done")

        # 文件必须被重新生成
        self.assertTrue(clip.exists(), "被删除的片段没有被重新生成")
        # 其余镜头不应重做
        self.assertGreaterEqual(gen["output"]["resumed_skipped"], 1)

        # 修复行为要留下可观测痕迹
        names = {s["name"] for s in self.pipe.spans(self.project.id)}
        self.assertIn("director.checkpoint_repaired", names)

    def test_missing_artifact_reruns_generate_without_from_stage(self):
        """产物缺失时，普通 resume（不指定 from_stage）也必须重跑 generate。

        这是断点恢复最容易失效的场景：阶段级状态说「做完了」，
        而镜头级现实是「没做完」。

        为什么真实会发生：Director 刻意让「单镜失败不拖垮整条流水线」，
        所以只要有镜头失败，generate 阶段仍会被标记 done。
        若 resume 只看阶段状态，这些失败镜头就永远得不到重试 ——
        断点恢复恰好在最需要它的场景下失效。
        """
        project = self.pipe.load(self.project.id)
        assert project.script is not None
        victim = project.script.shots[0]
        clip = self.settings.workspace / victim.clip_path
        self.assertTrue(clip.exists())
        clip.unlink()

        # 关键：这里**不传** from_stage，模拟用户直接执行 resume
        result = self.pipe.run(self.project.id)

        gen = next(s for s in result["stages"] if s["stage"] == "generate")
        self.assertEqual(gen["status"], "done", "存在未完成镜头时，generate 阶段不应被跳过")
        self.assertTrue(clip.exists(), "缺失的片段没有被重新生成")

    def test_all_artifacts_intact_still_skips(self):
        """反向保证：产物齐全时必须照旧跳过，不能因为修复而每次都全量重跑。"""
        result = self.pipe.run(self.project.id)
        statuses = [s["status"] for s in result["stages"]]
        self.assertEqual(statuses, ["skipped"] * 5)

    def test_partial_damage_keeps_good_artifacts(self):
        """关键帧还在、片段被删 -> 只重做片段，不重做关键帧。"""
        project = self.pipe.load(self.project.id)
        assert project.script is not None
        victim = project.script.shots[0]
        kf = self.settings.workspace / victim.keyframe_path
        clip = self.settings.workspace / victim.clip_path
        clip.unlink()

        self.pipe.run(self.project.id, from_stage="generate")

        project2 = self.pipe.load(self.project.id)
        assert project2.script is not None
        shot2 = next(s for s in project2.script.shots if s.id == victim.id)
        self.assertEqual(shot2.keyframe_path, victim.keyframe_path)
        self.assertTrue(kf.exists())
        self.assertTrue((self.settings.workspace / shot2.clip_path).exists())

    def test_failed_stage_marks_project_failed(self):
        """阶段抛异常时要落到 failed 并且不吞掉错误，修好后能从中断处继续。"""
        from reelagent.pipeline.runner import Pipeline as PipelineBase

        class Boom(PipelineBase):
            """第一次跑 assets 时熔断，之后恢复正常——用来模拟"故障已修复"。"""

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.boom = True

            def _dispatch(self, name, rt, project, **kw):
                if name == "assets" and self.boom:
                    self.boom = False
                    raise RuntimeError("模拟供应商熔断")
                return super()._dispatch(name, rt, project, **kw)

        tmp = make_tmp("pipe")
        pipe = Boom(make_settings(tmp), verbose=False)
        proj = pipe.create(SEED)
        result = pipe.run(proj.id, max_shots=2)

        statuses = {s["stage"]: s["status"] for s in result["stages"]}
        self.assertEqual(statuses["assets"], "failed")
        self.assertNotIn("generate", statuses)  # 中断后续阶段
        assets = next(s for s in result["stages"] if s["stage"] == "assets")
        self.assertIn("模拟供应商熔断", assets["error"])

        summary = pipe.status(proj.id)
        rec = next(s for s in summary["stages"] if s["name"] == "assets")
        self.assertEqual(rec["status"], "failed")

        # 故障修复后从断点继续：plan 已完成应跳过，其余跑完
        result2 = pipe.run(proj.id, from_stage="assets", max_shots=2)
        st2 = {s["stage"]: s["status"] for s in result2["stages"]}
        self.assertEqual(st2["plan"], "skipped")
        self.assertEqual(st2["assets"], "done")
        self.assertEqual(st2["generate"], "done")
        self.assertEqual(st2["render"], "done")

        # 项目状态也应从 failed 恢复
        proj2 = pipe.load(proj.id)
        self.assertNotEqual(proj2.status, "failed")
        cleanup_tmp(tmp)


class TestDeterminism(unittest.TestCase):
    """同一份输入必须产出同一份结果。

    这是"评测可回归 / CI 门禁"的前提：如果两次运行的画面都不同，
    就没法判断一次改动是"变好了"还是"随机波动"。
    """

    def test_same_seed_text_yields_identical_metrics(self):
        metrics = []
        for _ in range(2):
            tmp = make_tmp("det")
            pipe = Pipeline(make_settings(tmp), verbose=False)
            proj = pipe.create(SEED)
            pipe.run(proj.id, max_shots=3, max_base_images=2)
            loaded = pipe.load(proj.id)
            assert loaded.evaluation is not None
            metrics.append(loaded.evaluation["metrics"])
            cleanup_tmp(tmp)

        for key in (
            "overall",
            "palette_stability",
            "cross_shot_clue_similarity",
            "clue_reference_coverage",
            "prompt_clue_fidelity",
        ):
            self.assertEqual(
                metrics[0][key],
                metrics[1][key],
                f"指标 {key} 两次运行不一致：{metrics[0][key]} vs {metrics[1][key]}",
            )

    def test_stable_seed_not_affected_by_python_hash_randomization(self):
        """内置 hash() 对 str 按进程随机化，绝不能用来做随机种子。"""
        from reelagent.utils import stable_seed

        self.assertEqual(stable_seed("sc01_sh01"), stable_seed("sc01_sh01"))
        self.assertNotEqual(stable_seed("sc01_sh01"), stable_seed("sc01_sh02"))
        self.assertNotEqual(stable_seed("shot", salt="image"), stable_seed("shot", salt="video"))
        self.assertLess(stable_seed("x"), 99991)


if __name__ == "__main__":
    unittest.main()
