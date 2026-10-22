"""评估驱动的自动修复（闭环）测试。

## 这个功能补的是什么缺口

引入之前，评测门禁的结果只有两个去向：写进 `project.evaluation`、打印到日志。
**不合格的镜头不会被重跑，项目照样标记完成。**

于是会出现：3 镜里有一镜生成失败 -> 基准图注入覆盖率掉到 0.67 -> 门禁判不通过
-> 但流水线继续往下走 -> 成片缺一个镜头，而状态是「完成」。

现在形成闭环：评测 → 找出**可修复的**镜头 → 只重跑它们 → 重新评测。

## 为什么必须区分「可修复 / 不可修复」

重跑是要真花钱的。如果对确定性失败也重试，就是烧钱空转：

  - 镜头生成失败（供应商抖动）-> 重跑有救       -> 可修复
  - prompt 缺线索外观        -> prompt 是拼装的  -> 重跑结果一模一样，不可修复
  - 色调漂移 / 评审低分      -> 全局指标         -> 归因不到镜头，不可修复
"""

from __future__ import annotations

import pytest

from reelagent.agents.evaluator import Evaluator, _retryable_shots
from reelagent.eval.consistency import clue_reference_coverage, wilson_lower_bound
from reelagent.schema import Clue, Shot


def _shot(shot_id: str, *, status: str, clues: list[str]) -> Shot:
    return Shot(id=shot_id, scene_id="sc01", index=1, clue_ids=clues, status=status)


def _clue(clue_id: str, *, with_base_image: bool) -> Clue:
    return Clue(
        id=clue_id,
        kind="character",
        name=clue_id,
        appearance="深灰色风衣，领口磨白",
        base_image=f"assets/{clue_id}.png" if with_base_image else "",
    )


def _metrics(*, cov: float | None, uncovered: list[str], fid: float | None = 1.0) -> dict:
    """构造 _gate 需要的最小 metrics 形状。"""
    return {
        "clue_reference_coverage": cov,
        "prompt_clue_fidelity": fid,
        "palette_stability": 0.9,
        "coverage_detail": {"eligible": 3, "covered": 3 - len(uncovered), "uncovered_shots": uncovered},
    }


class TestCoveragePointsAtShots:
    """覆盖率不能只报数字，必须指出是哪几镜没跟上。"""

    def test_reports_uncovered_shot_ids(self) -> None:
        clues = {"c1": _clue("c1", with_base_image=True)}
        shots = [
            _shot("sc01_sh01", status="audio_done", clues=["c1"]),
            _shot("sc01_sh02", status="failed", clues=["c1"]),
            _shot("sc01_sh03", status="keyframe_done", clues=["c1"]),
        ]
        cov, detail = clue_reference_coverage(shots, clues)

        assert cov == pytest.approx(2 / 3, rel=1e-3)
        assert detail["uncovered_shots"] == ["sc01_sh02"], "必须能定位到具体镜头"

    def test_all_done_has_empty_uncovered(self) -> None:
        clues = {"c1": _clue("c1", with_base_image=True)}
        shots = [_shot("sc01_sh01", status="audio_done", clues=["c1"])]
        _cov, detail = clue_reference_coverage(shots, clues)
        assert detail["uncovered_shots"] == []

    def test_no_eligible_shots_returns_none(self) -> None:
        """没有绑定「带基准图线索」的镜头时，返回 None 而不是 1.0。

        「没有可度量的对象」和「全部达标」是两回事。
        """
        cov, detail = clue_reference_coverage([_shot("sc01_sh01", status="audio_done", clues=[])], {})
        assert cov is None
        assert detail["uncovered_shots"] == []

    def test_clue_without_base_image_is_not_eligible(self) -> None:
        clues = {"c1": _clue("c1", with_base_image=False)}
        cov, _detail = clue_reference_coverage([_shot("sc01_sh01", status="audio_done", clues=["c1"])], clues)
        assert cov is None


class TestRetryableShotsExtraction:
    def test_extracts_from_coverage_detail(self) -> None:
        assert _retryable_shots(_metrics(cov=0.67, uncovered=["sc01_sh02"]), []) == ["sc01_sh02"]

    def test_empty_when_nothing_uncovered(self) -> None:
        assert _retryable_shots(_metrics(cov=1.0, uncovered=[]), []) == []

    @pytest.mark.parametrize("bad", [{}, {"coverage_detail": None}, {"coverage_detail": {}}])
    def test_tolerates_missing_detail(self, bad) -> None:
        assert _retryable_shots(bad, []) == []


class TestGateSeparatesFixableFromNot:
    """门禁必须把「可修复」单独标出来 —— 这是闭环的前提。"""

    def test_failed_shot_is_retryable(self) -> None:
        gate = Evaluator._gate(_metrics(cov=0.67, uncovered=["sc01_sh02"]), None, [])

        assert gate["passed"] is False
        assert gate["retryable_shots"] == ["sc01_sh02"]

    def test_low_fidelity_is_not_retryable(self) -> None:
        """prompt 缺外观描述是确定性的，重跑一百次结果一样，不能重试。"""
        gate = Evaluator._gate(_metrics(cov=1.0, uncovered=[], fid=0.5), None, [])

        assert gate["passed"] is False
        assert gate["retryable_shots"] == [], "确定性失败不能进重试列表"

    def test_palette_drift_is_not_retryable(self) -> None:
        """全局色调漂移归因不到具体镜头。"""
        metrics = _metrics(cov=1.0, uncovered=[])
        metrics["palette_stability"] = 0.1
        gate = Evaluator._gate(metrics, None, [])

        assert gate["passed"] is False
        assert gate["retryable_shots"] == []

    def test_low_judge_score_is_not_retryable(self) -> None:
        gate = Evaluator._gate(_metrics(cov=1.0, uncovered=[]), {"overall": 2.0}, [])

        assert gate["passed"] is False
        assert gate["retryable_shots"] == []

    def test_passing_gate_has_no_retryable(self) -> None:
        gate = Evaluator._gate(_metrics(cov=1.0, uncovered=[]), {"overall": 4.5}, [])

        assert gate["passed"] is True
        assert gate["failures"] == []
        assert gate["retryable_shots"] == []


class TestWilsonLowerBound:
    """覆盖率汇报用置信下界而不是点估计 —— 样本少的时候不吹牛。"""

    def test_small_sample_is_penalised(self) -> None:
        """3/3 全覆盖，但样本只有 3，不能声称 100%。"""
        lower = wilson_lower_bound(3, 3)
        assert 0.3 < lower < 1.0, f"3 个样本全过时的下界应明显低于 1.0，实际 {lower}"

    def test_large_sample_approaches_one(self) -> None:
        lower = wilson_lower_bound(300, 300)
        assert lower > 0.98, f"300 个样本全过时下界应接近 1.0，实际 {lower}"

    def test_ordering_is_monotonic_in_sample_size(self) -> None:
        """同样的比例，样本越多下界越高。"""
        assert wilson_lower_bound(3, 3) < wilson_lower_bound(30, 30) < wilson_lower_bound(300, 300)

    def test_zero_total_is_zero(self) -> None:
        assert wilson_lower_bound(0, 0) == 0.0

    def test_partial_success(self) -> None:
        lower = wilson_lower_bound(2, 3)
        assert 0.0 < lower < 2 / 3, "下界必须低于点估计"


class TestRepairBudgetConfig:
    def test_default_budget_is_bounded(self) -> None:
        """默认必须封顶 —— 每轮都真花钱。"""
        from reelagent.config import Settings

        assert 0 < Settings.load().max_repair_rounds <= 3

    def test_zero_disables_repair(self) -> None:
        from reelagent.config import Settings

        assert Settings(max_repair_rounds=0).max_repair_rounds == 0


class TestRepairLoopEndToEnd:
    """真正的闭环验证：让某个镜头在**关键帧阶段**失败，看引擎会不会只重跑那一镜。

    ## 为什么制造关键帧失败，而不是片段失败

    导演对片段失败有**降级策略**：视频模型挂了就退回本地运动合成，
    镜头仍然标记为 clip_done（director.py:260-285）。设计意图是对的 ——
    单个供应商抖动不该让用户的成片开天窗。

    但也因此，片段失败**不会**让镜头进入 failed 状态，也就不会
    拉低覆盖率。真正能让镜头失败的路径是关键帧（图像）失败。

    顺带记录一个已知缺口：降级只在 tracer 里留痕，
    clue_reference_coverage 与门禁都看不见它 ——
    用户付了 premium 的钱、拿到本地合成，而指标全绿。
    这需要给 Shot 增加降级标记并持久化，属于独立的一项改动。

    ## 为什么 max_attempts=1

    默认 3 次会在 shot 内部就把抖动吸收掉，走不到评估环节。
    「修复循环」处理的是**已经重试过仍然失败**的镜头，
    与 shot 内重试是两个层次。
    """

    def test_failed_shot_is_rerun_and_recovered(self, tmp_workspace) -> None:
        import reelagent.providers.mock as mock_mod
        from reelagent.config import Settings
        from reelagent.pipeline import Pipeline

        settings = Settings(
            mode="mock",
            workspace=tmp_workspace / "engine",
            max_repair_rounds=1,
            max_concurrency=1,
            max_attempts=1,
        )
        pipe = Pipeline(settings, verbose=False)
        project = pipe.create(
            "深夜的便利店，店员打了个哈欠。穿深灰风衣的女人推门进来，雨水从伞尖滴落。"
            "她走到货架前，指尖划过一排牛奶盒，停在其中一盒上。最后转身走进雨里。",
            style="cinematic",
            aspect_ratio="9:16",
        )

        # 只让**关键帧**的第一次生成失败。
        #
        # 不能简单地「第一次调用就失败」—— assets 阶段先生成基准图，
        # 那次失败会被 ArtDirector 自己的重试吸收掉，根本到不了评测。
        # 按输出路径区分阶段（关键帧落在 keyframes/ 下）才是精确的。
        original = mock_mod.MockImageProvider.generate
        state = {"keyframe_calls": 0, "failed_once": False}

        def flaky(self, **kwargs):
            out_path = str(kwargs.get("out_path") or kwargs.get("path") or "")
            if "keyframes" in out_path:
                state["keyframe_calls"] += 1
                if not state["failed_once"]:
                    state["failed_once"] = True
                    raise RuntimeError("模拟关键帧供应商瞬时故障")
            return original(self, **kwargs)

        mock_mod.MockImageProvider.generate = flaky
        try:
            pipe.run(project.id, max_shots=3)
        finally:
            mock_mod.MockImageProvider.generate = original
