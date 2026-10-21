"""「失败被上报成成功」的回归测试。

## 真实事故

百炼账户欠费后，一次生成的实际过程是：

    plan 阶段抛出 LLMError: HTTP 400 / code=Arrearage
    -> 引擎把 StageOutcome 标成 failed，**不向上抛异常**，正常返回整个结果
    -> 云端完全不看 stages 字段，继续往下走
    -> project.status = READY（无条件赋值）
    -> job.status = succeeded
    -> 界面显示「已完成」，0 个镜头，扣费 0，退还全部预扣

用户看到的是一个**没有任何错误提示的空项目**。而线上日志里只有一行
`engine_stage_failed`，既没有异常类型也没有消息，完全无法排查。

这里锁住三件事：
  1. 引擎返回的 failed 阶段必须被识别出来
  2. 供应商错误必须翻译成人能看懂、能行动的话
  3. 生成失败不能被自动重试（欠费重试一万次也还是欠费）
"""

from __future__ import annotations

import pytest

from reelagent_cloud.domain.errors import GenerationFailed, JobFailed
from reelagent_cloud.services.pipeline import (
    STAGE_LABELS,
    first_failed_stage,
    humanize_provider_error,
)

#: 线上抓到的真实响应体（截断）。故意保留完整结构，因为匹配逻辑要能穿透 JSON。
ARREARAGE_RAW = (
    'LLMError: HTTP 400: {"error":{"message":"Access denied, please make sure your '
    "account is in good standing. For details, see: https://help.aliyun.com/zh/"
    'model-studio/error-code#overdue-payment","type":"Arrearage","param":null,'
    '"code":"Arrearage"},"id":"chatcmpl-d48ad62e"}'
)


class TestFirstFailedStage:
    def test_detects_failed_stage(self) -> None:
        result = {
            "stages": [
                {"name": "plan", "status": "failed", "error": ARREARAGE_RAW},
            ]
        }
        found = first_failed_stage(result)
        assert found is not None
        assert found["name"] == "plan"

    def test_detects_failure_after_successful_stages(self) -> None:
        """前面的阶段成功、后面的失败，也要能找出来。"""
        result = {
            "stages": [
                {"name": "plan", "status": "done"},
                {"name": "assets", "status": "done"},
                {"name": "generate", "status": "failed", "error": "boom"},
            ]
        }
        found = first_failed_stage(result)
        assert found is not None
        assert found["name"] == "generate"

    def test_all_done_returns_none(self) -> None:
        result = {
            "stages": [
                {"name": "plan", "status": "done"},
                {"name": "render", "status": "done"},
            ]
        }
        assert first_failed_stage(result) is None

    def test_skipped_stage_is_not_failure(self) -> None:
        """断点续跑时阶段可能是 skipped，那不是失败。"""
        result = {"stages": [{"name": "plan", "status": "skipped"}]}
        assert first_failed_stage(result) is None

    @pytest.mark.parametrize("empty", [None, {}, {"stages": None}, {"stages": []}])
    def test_empty_inputs_are_safe(self, empty) -> None:
        assert first_failed_stage(empty) is None

    def test_tolerates_non_dict_stage(self) -> None:
        """引擎若返回了非 dict 的条目（未来格式变化），不能炸。"""
        result = {"stages": ["something", {"name": "plan", "status": "failed"}]}
        found = first_failed_stage(result)
        assert found is not None and found["name"] == "plan"


class TestHumanizeProviderError:
    def test_arrears_becomes_actionable(self) -> None:
        """这条是事故的直接回归：用户必须被告知「去充值」。"""
        msg = humanize_provider_error(ARREARAGE_RAW)

        assert "余额" in msg or "欠费" in msg, f"没有说清是钱的问题：{msg}"
        assert "充值" in msg, f"没有告诉用户该做什么：{msg}"
        # 原文片段要保留，方便用户报障时粘给供应商
        assert "Arrearage" in msg or "arrearage" in msg.lower()

    @pytest.mark.parametrize(
        ("raw", "expect"),
        [
            ('{"code":"Arrearage"}', "充值"),
            ('{"code":"1113","message":"Insufficient balance"}', "余额"),
            ('{"code":"1302","message":"quota exceeded"}', "配额"),
            ("invalid_api_key", "API Key"),
            ("HTTP 429 Too Many Requests", "限流"),
        ],
    )
    def test_known_patterns_are_translated(self, raw: str, expect: str) -> None:
        assert expect in humanize_provider_error(raw), f"{raw} 未被翻译出「{expect}」"

    def test_unknown_error_passes_through(self) -> None:
        """认不出来时原样返回 —— 错误的信息比没有信息更糟。"""
        raw = "SomeBrandNewError: the widget exploded"
        msg = humanize_provider_error(raw)
        assert "widget exploded" in msg
        assert "充值" not in msg, "不能凭空猜测成欠费"

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_blank_returns_placeholder(self, blank) -> None:
        assert humanize_provider_error(blank) == "未知原因"

    def test_long_error_is_truncated(self) -> None:
        msg = humanize_provider_error("x" * 5000)
        assert len(msg) <= 320


class TestStageLabels:
    def test_every_engine_stage_has_a_label(self) -> None:
        """报错要给中文阶段名，不能把 'plan' 直接抛给用户。"""
        for stage in ("plan", "assets", "generate", "evaluate", "render"):
            assert stage in STAGE_LABELS
            assert STAGE_LABELS[stage], f"{stage} 的中文名为空"


class TestGenerationFailureIsNotRetryable:
    def test_retryable_is_false(self) -> None:
        """欠费/密钥失效这类失败重试没有意义，只会浪费配额。"""
        assert GenerationFailed("x").retryable is False

    def test_worker_reads_retryable_via_getattr(self) -> None:
        """worker 用的是 getattr(exc, "retryable", True)，这里确认它会读到 False。"""
        exc = GenerationFailed("生成失败（剧本拆解）：账户欠费")
        assert getattr(exc, "retryable", True) is False

    def test_still_an_app_error(self) -> None:
        """必须仍然是 AppError，否则 worker 会把它当成未预期异常。"""
        from reelagent_cloud.domain.errors import AppError

        assert isinstance(GenerationFailed("x"), AppError)
        assert isinstance(GenerationFailed("x"), JobFailed)

    def test_has_its_own_code(self) -> None:
        assert GenerationFailed("x").code == "generation_failed"
