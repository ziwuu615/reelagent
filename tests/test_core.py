"""核心组件单元测试。

用标准库 unittest 编写，因此 `python -m unittest discover tests` 可以直接跑，
不需要安装 pytest（pytest 也能识别这些用例）。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from reelagent.config import ProviderConfig, Settings
from reelagent.harness import ContextWindow, Store, ToolRegistry, Tracer, run_with_retry
from reelagent.harness.executor import RetryPolicy
from reelagent.llm.mock import MockLLM
from reelagent.providers.mock import MockImageProvider, MockTTSProvider, MockVideoProvider
from reelagent.routing import TIER_DRAFT, TIER_PREMIUM, ModelRouter
from reelagent.schema import Clue, Project, Scene, Script, Shot
from tests.support import cleanup_tmp, make_tmp


def make_settings(tmp: Path, **kw) -> Settings:
    base = {
        "mode": "mock",
        "workspace": tmp,
        "style": "cinematic",
        "aspect_ratio": "9:16",
        "budget_usd": 2.0,
        "max_concurrency": 2,
        "max_attempts": 2,
        "llm": ProviderConfig("llm", "deepseek-chat"),
        "image": ProviderConfig("mock", "mock"),
        "video": ProviderConfig("mock", "mock"),
        "tts": ProviderConfig("mock", "mock"),
    }
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


class TestSchema(unittest.TestCase):
    def test_script_roundtrip(self):
        shot = Shot(id="sc01_sh01", scene_id="sc01", index=1, action="走", clue_ids=["c1"])
        script = Script(title="T", scenes=[Scene(id="sc01", index=1, shots=[shot])])
        restored = Script.from_dict(script.to_dict())
        self.assertEqual(restored.title, "T")
        self.assertEqual(len(restored.shots), 1)
        self.assertEqual(restored.shots[0].clue_ids, ["c1"])
        self.assertEqual(restored.shots[0].action, "走")

    def test_project_roundtrip_with_clues(self):
        p = Project(id="p1", seed_text="s", clues=[Clue(id="c1", kind="character", name="主角")])
        p.script = Script(
            title="T", scenes=[Scene(id="sc01", index=1, shots=[Shot(id="s1", scene_id="sc01", index=1)])]
        )
        r = Project.from_dict(p.to_dict())
        self.assertEqual(r.clues[0].name, "主角")
        self.assertIsNotNone(r.script)
        self.assertEqual(len(r.script.shots), 1)

    def test_is_finished_depends_on_artifacts_not_status(self):
        """is_finished 必须看产物是否存在，而不是乐观的 status 字段。"""
        shot = Shot(id="s1", scene_id="sc01", index=1, clip_path="clips/a.gif", status="clip_done")
        self.assertTrue(shot.is_finished)

        # status 说完成了，但产物被外部删掉了 -> 必须判定为未完成
        ghost = Shot(id="s2", scene_id="sc01", index=1, status="clip_done", clip_path=None)
        self.assertFalse(ghost.is_finished)

        # 有台词但没有配音 -> 未完成
        voiced = Shot(
            id="s3",
            scene_id="sc01",
            index=1,
            clip_path="clips/b.gif",
            dialogue="你好",
            audio_path=None,
            status="clip_done",
        )
        self.assertFalse(voiced.is_finished)

        voiced.audio_path = "audio/b.wav"
        self.assertTrue(voiced.is_finished)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tmp("t")
        self.store = Store(self.tmp / "t.db")

    def tearDown(self):
        self.store.close()
        cleanup_tmp(self.tmp)

    def test_stage_lifecycle(self):
        self.assertFalse(self.store.stage_is_done("p", "plan"))
        self.store.stage_start("p", "plan")
        self.assertFalse(self.store.stage_is_done("p", "plan"))
        self.store.stage_done("p", "plan", {"shots": 3})
        self.assertTrue(self.store.stage_is_done("p", "plan"))
        rec = self.store.stage_get("p", "plan")
        self.assertEqual(rec.output, {"shots": 3})

    def test_stage_attempts_increment(self):
        self.store.stage_start("p", "g")
        self.store.stage_failed("p", "g", "boom")
        self.store.stage_start("p", "g")
        rec = self.store.stage_get("p", "g")
        self.assertEqual(rec.attempts, 2)
        self.assertIsNone(rec.error)  # stage_start 会清掉上次的错误

    def test_shot_persistence(self):
        shot = Shot(id="s1", scene_id="sc01", index=1, clip_path="c.gif", status="clip_done")
        self.store.save_shot("p", shot)
        loaded = self.store.load_shots("p")
        self.assertEqual(loaded["s1"].clip_path, "c.gif")

    def test_cost_summary_groups_by_category(self):
        for _ in range(3):
            self.store.record_cost(
                project_id="p",
                category="image",
                provider="ark",
                model="seedream",
                unit="image",
                quantity=1,
                usd=0.03,
            )
        self.store.record_cost(
            project_id="p",
            category="llm",
            provider="llm",
            model="deepseek-chat",
            unit="token",
            quantity=100,
            usd=0.001,
        )
        s = self.store.cost_summary("p")
        self.assertAlmostEqual(s["total_usd"], 0.091, places=4)
        self.assertAlmostEqual(s["by_category"]["image"]["usd"], 0.09, places=4)
        self.assertEqual(s["by_category"]["image"]["items"][0]["calls"], 3)

    def test_span_stats(self):
        self.store.record_span(
            span_id="a",
            project_id="p",
            parent_id=None,
            name="x",
            kind="tool",
            status="ok",
            started_at=0.0,
            ended_at=1.0,
        )
        self.store.record_span(
            span_id="b",
            project_id="p",
            parent_id=None,
            name="x",
            kind="tool",
            status="failed",
            started_at=0.0,
            ended_at=3.0,
        )
        st = self.store.span_stats("p")
        self.assertEqual(st["x"]["count"], 2)
        self.assertEqual(st["x"]["failed"], 1)
        self.assertAlmostEqual(st["x"]["max_ms"], 3000.0, places=1)


class TestTracer(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tmp("t")
        self.store = Store(self.tmp / "t.db")
        self.tracer = Tracer(self.store, "p")

    def tearDown(self):
        self.store.close()
        cleanup_tmp(self.tmp)

    def test_with_block_records_span(self):
        with self.tracer.span("op", kind="tool", shot_id="s1") as sp:
            sp.set(extra=1)
        spans = self.store.spans("p")
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["name"], "op")
        self.assertEqual(spans[0]["status"], "ok")

    def test_record_helper_persists_without_with(self):
        """这是踩过的坑：span(...).set(...) 不落库，必须用 record()。"""
        self.tracer.record("failed.op", status="failed", error="boom", clue="x")
        spans = self.store.spans("p")
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["status"], "failed")
        self.assertEqual(spans[0]["error"], "boom")

    def test_attrs_containing_kind_do_not_collide(self):
        """attrs 里含 'kind' 键时不能与 span(kind=...) 形参冲突。"""
        with self.tracer.span("op2", kind="retry", attrs={"kind": "character"}, attempts=2):
            pass
        spans = self.store.spans("p")
        self.assertEqual(len(spans), 1)
        self.assertIn("character", spans[0]["attrs"])

    def test_exception_marks_span_failed_and_propagates(self):
        with self.assertRaises(ValueError), self.tracer.span("boom", kind="tool"):
            raise ValueError("x")
        spans = self.store.spans("p")
        self.assertEqual(spans[0]["status"], "failed")
        self.assertIn("ValueError", spans[0]["error"])


class TestExecutor(unittest.TestCase):
    def test_retry_eventually_succeeds(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("not yet")
            return "ok"

        result = run_with_retry(flaky, policy=RetryPolicy(attempts=5, base_delay=0.001))
        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 3)

    def test_retry_exhausted_raises_with_history(self):
        def always_fail():
            raise RuntimeError("nope")

        with self.assertRaises(RuntimeError) as ctx:
            run_with_retry(always_fail, policy=RetryPolicy(attempts=3, base_delay=0.001), name="x")
        self.assertIn("3 次尝试后失败", str(ctx.exception))

    def test_attrs_with_kind_key_does_not_crash(self):
        """回归测试：attrs={'kind': ...} 曾导致 TypeError 被静默吞掉。"""
        tmp = make_tmp("t")
        store = Store(tmp / "t.db")
        tracer = Tracer(store, "p")
        out = run_with_retry(
            lambda: 42,
            policy=RetryPolicy(attempts=1),
            tracer=tracer,
            name="op",
            attrs={"kind": "character", "clue": "主角"},
        )
        self.assertEqual(out, 42)
        self.assertTrue(any(s["name"] == "op" for s in store.spans("p")))
        store.close()


class TestToolRegistry(unittest.TestCase):
    def test_register_call_and_specs(self):
        reg = ToolRegistry()
        reg.register("echo", "回显", {"type": "object", "properties": {}}, lambda: "hi")
        self.assertEqual(reg.call("echo"), "hi")
        specs = reg.specs()
        self.assertEqual(specs[0]["name"], "echo")
        self.assertIn("inputSchema", specs[0])  # MCP tools/list 结构

    def test_unknown_tool_raises(self):
        from reelagent.harness import ToolError

        reg = ToolRegistry()
        with self.assertRaises(ToolError):
            reg.call("nope")

    def test_failure_is_reported_and_logged(self):
        from reelagent.harness import ToolError

        reg = ToolRegistry()

        def boom():
            raise ValueError("bad")

        reg.register("boom", "炸", {"type": "object", "properties": {}}, boom)
        with self.assertRaises(ToolError):
            reg.call("boom")
        self.assertFalse(reg.call_log[0]["ok"])


class TestContextWindow(unittest.TestCase):
    def test_compress_reduces_tokens(self):
        cw = ContextWindow(system="sys", budget=200, llm=MockLLM(), keep_recent=2)
        for i in range(40):
            cw.add("user", f"这是第 {i} 条很长的历史消息，用来把上下文撑爆。" * 3)
        before = cw.tokens
        self.assertGreater(before, 200)
        changed = cw.maybe_compress()
        self.assertTrue(changed)
        self.assertLess(cw.tokens, before)
        self.assertEqual(cw.stats.compressions, 1)
        self.assertGreater(cw.stats.tokens_saved, 0)

    def test_enforce_budget_hard_limit(self):
        cw = ContextWindow(system="s", budget=60, llm=None, keep_recent=1)
        for i in range(20):
            cw.add("user", f"消息{i}" * 20)
        cw.enforce_budget()
        self.assertLessEqual(cw.tokens, 60)

    def test_pinned_content_survives(self):
        cw = ContextWindow(system="s", budget=100, llm=None, keep_recent=1)
        cw.pin("关键事实：主角是栗色长卷发")
        for i in range(20):
            cw.add("user", f"噪音{i}" * 20)
        cw.enforce_budget()
        joined = "\n".join(m["content"] for m in cw.messages())
        self.assertIn("栗色长卷发", joined)


class TestRouting(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tmp("t")
        self.settings = make_settings(self.tmp)
        self.clues = [Clue(id="c1", kind="character", name="主角")]

    def test_low_difficulty_goes_draft(self):
        router = ModelRouter(self.settings)
        shot = Shot(id="s1", scene_id="sc", index=1, shot_size="全景", camera="固定", duration_s=2.0)
        self.assertEqual(router.decide(shot, self.clues).tier, TIER_DRAFT)

    def test_hard_shot_goes_premium(self):
        router = ModelRouter(self.settings)
        shot = Shot(
            id="s1",
            scene_id="sc",
            index=1,
            shot_size="特写",
            camera="推",
            duration_s=5.0,
            clue_ids=["c1"],
            dialogue="台词",
            attempts=1,
        )
        self.assertEqual(router.decide(shot, self.clues).tier, TIER_PREMIUM)

    def test_budget_exhausted_forces_draft(self):
        router = ModelRouter(self.settings, spent_usd=lambda: 99.0)
        shot = Shot(id="s1", scene_id="sc", index=1, shot_size="特写", duration_s=5.0, clue_ids=["c1"])
        d = router.decide(shot, self.clues)
        self.assertEqual(d.tier, TIER_DRAFT)
        self.assertFalse(d.use_video_model)

    def test_summary_counts_skipped_video_calls(self):
        router = ModelRouter(self.settings)
        router.decide(Shot(id="s1", scene_id="sc", index=1, shot_size="全景"), self.clues)
        router.decide(
            Shot(
                id="s2",
                scene_id="sc",
                index=1,
                shot_size="特写",
                clue_ids=["c1"],
                duration_s=5.0,
                dialogue="x",
            ),
            self.clues,
        )
        s = router.summary()
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["video_calls_skipped"], 1)
        self.assertGreater(s["estimated_saved_usd"], 0)


class TestMockProviders(unittest.TestCase):
    """mock 供应商必须产出**真实可用**的媒体文件，而不是空占位。"""

    def setUp(self):
        self.tmp = make_tmp("t")
        self.settings = make_settings(self.tmp)

    def test_image_is_real_png(self):
        from PIL import Image

        out = self.tmp / "kf.png"
        res = MockImageProvider(
            palette=[(10, 10, 20), (80, 90, 120), (200, 200, 210)], style_label="电影感"
        ).generate(prompt="一个雨夜便利店的镜头，中景，主体固定特征：黑色短发", out_path=out, size=(180, 320))
        self.assertTrue(out.exists())
        with Image.open(out) as im:
            self.assertEqual(im.size, (180, 320))
            self.assertEqual(im.format, "PNG")
        self.assertGreater(out.stat().st_size, 1000)
        self.assertEqual(res.cost_usd, 0.0)

    def test_video_is_animated_gif(self):
        from PIL import Image

        kf = self.tmp / "kf.png"
        MockImageProvider().generate(prompt="场景", out_path=kf, size=(120, 200))
        gif = self.tmp / "clip.gif"
        res = MockVideoProvider(fps=6).generate(
            prompt="推进", keyframe=kf, out_path=gif, duration_s=2.0, size=(120, 200)
        )
        self.assertTrue(gif.exists())
        with Image.open(gif) as im:
            self.assertGreater(getattr(im, "n_frames", 1), 1)  # 真的是动图
        self.assertGreaterEqual(res.meta["frames"], 4)

    def test_tts_is_real_wav_with_matching_duration(self):
        import wave

        out = self.tmp / "a.wav"
        text = "深夜的便利店只剩下最后一盏灯"
        MockTTSProvider(chars_per_sec=5.0).generate(text=text, out_path=out)
        self.assertTrue(out.exists())
        with wave.open(str(out), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
        duration = frames / rate
        self.assertAlmostEqual(duration, len(text) / 5.0, places=1)
        self.assertGreater(duration, 1.0)


class TestPlanner(unittest.TestCase):
    def test_mock_planner_produces_valid_script(self):
        from reelagent.agents import Planner
        from reelagent.runtime import Runtime

        tmp = make_tmp("t")
        settings = make_settings(tmp)
        store = Store(tmp / "t.db")
        rt = Runtime(settings, "p", store=store)
        project = Project(id="p", seed_text="林晚走进便利店，找到一把黄铜钥匙。老人说等了很久。")

        script = Planner(rt).run(project, max_shots=4)

        self.assertLessEqual(len(script.shots), 4)
        self.assertGreater(len(script.shots), 0)
        self.assertGreater(len(project.clues), 0)
        # 每个镜头的线索 ID 必须都能在 clues 里找到
        known = {c.id for c in project.clues}
        for shot in script.shots:
            for cid in shot.clue_ids:
                self.assertIn(cid, known)
        rt.close()

    def test_clue_ids_are_deterministic(self):
        from reelagent.agents.base import clue_id

        self.assertEqual(clue_id("主角"), clue_id("主角"))
        self.assertNotEqual(clue_id("主角"), clue_id("配角"))


class TestConsistencyMetrics(unittest.TestCase):
    def test_coverage_is_none_when_nothing_to_measure(self):
        """没有可度量对象时必须返回 None，而不是伪装成 1.0 满分。

        注意区分两种"没有数据"：
          - 线索没有基准图  -> coverage 不可度量 -> None
          - 线索有 appearance 但 prompt 里没注入 -> 保真度是**可度量**的，且应为 0.0
        """
        from reelagent.eval.consistency import (
            build_metrics,
            clue_reference_coverage,
            prompt_clue_fidelity,
        )

        shot = Shot(id="s1", scene_id="sc", index=1, clue_ids=["c1"], status="clip_done")
        clues = {"c1": Clue(id="c1", kind="character", name="主角", appearance="黑发")}

        # 线索没有 base_image -> 覆盖率无从度量
        cov, detail = clue_reference_coverage([shot], clues)
        self.assertIsNone(cov)
        self.assertEqual(detail["eligible"], 0)

        # 但 appearance 是有的，且 prompt 为空 -> 保真度可度量，且正确判定为 0
        fid, misses = prompt_clue_fidelity([shot], clues)
        self.assertEqual(fid, 0.0)
        self.assertEqual(len(misses), 1)

        # 完全没有线索绑定时，两个指标都不可度量
        bare = Shot(id="s2", scene_id="sc", index=1, status="clip_done")
        self.assertIsNone(prompt_clue_fidelity([bare], clues)[0])

        tmp = make_tmp("t")
        m = build_metrics([bare], list(clues.values()), tmp)
        self.assertFalse(m["consistency_strategy_active"])
        cleanup_tmp(tmp)

    def test_fidelity_detects_missing_appearance(self):
        from reelagent.eval.consistency import prompt_clue_fidelity

        clues = {"c1": Clue(id="c1", kind="character", name="主角", appearance="栗色长卷发单眼皮鼻梁挺直")}
        good = Shot(
            id="s1",
            scene_id="sc",
            index=1,
            clue_ids=["c1"],
            image_prompt="中景，栗色长卷发单眼皮鼻梁挺直，走在街上",
        )
        bad = Shot(id="s2", scene_id="sc", index=1, clue_ids=["c1"], image_prompt="中景，一个人走在街上")
        score, misses = prompt_clue_fidelity([good, bad], clues)
        self.assertAlmostEqual(score, 0.5)
        self.assertEqual(len(misses), 1)

    def test_wilson_lower_bound_is_conservative(self):
        from reelagent.eval.consistency import wilson_lower_bound

        self.assertLess(wilson_lower_bound(2, 2), 1.0)
        self.assertEqual(wilson_lower_bound(0, 0), 0.0)
        self.assertLess(wilson_lower_bound(9, 10), 0.9)


if __name__ == "__main__":
    unittest.main()
