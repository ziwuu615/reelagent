"""`usage_records` 落库测试。

这张表曾经「有表、有模型、有 4 个索引、有文档、有运维 SQL」，
但**没有任何代码往里写一行** —— 于是 OPERATIONS.md 里的失败率/延迟查询
永远返回空集。这类「接口齐全但不产出数据」的缺陷最难发现，
因为它不会报错，页面上也看不出异常。

测试重点：
  1. 金额来自 `costs`（且镜头归因要带过来）
  2. 失败与延迟来自 `spans` —— **只取 costs 会漏掉这两个维度**
  3. 上游未上报 provider 时用配置归属，并打上 `cfg:` 前缀标明来源
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from reelagent.harness.state import Store
from reelagent.harness.tracing import Tracer
from reelagent_cloud.db.models import UsageRecord
from reelagent_cloud.db.session import session_scope
from reelagent_cloud.services.pipeline import _capability_of, _persist_usage_records, _short_error_code

pytestmark = pytest.mark.usefixtures("db")

ORG = "org_test_usage"
PROJECT = "proj_test_usage"
ENGINE_PROJECT = "proj_engine_01"


def _seed_engine_db(workspace: Path) -> None:
    """用引擎自己的写入器造数据，保证格式与真实运行一致。"""
    store = Store(workspace / "reelagent.db")
    try:
        # 成功的图像调用（带镜头归因）
        store.record_cost(
            project_id=ENGINE_PROJECT,
            category="image",
            provider="dashscope",
            model="wan2.5-t2i-preview",
            unit="image",
            quantity=1.0,
            usd=0.03,
            meta={"shot_id": "sc01_sh01"},
        )
        # 成功的视频调用
        store.record_cost(
            project_id=ENGINE_PROJECT,
            category="video",
            provider="dashscope",
            model="wan3.0-video",
            unit="second",
            quantity=4.5,
            usd=0.18,
            meta={"shot_id": "sc01_sh02"},
        )
        # LLM 调用没有镜头归属，只有 task
        store.record_cost(
            project_id=ENGINE_PROJECT,
            category="llm",
            provider="dashscope",
            model="qwen-plus",
            unit="token",
            quantity=1200.0,
            usd=0.002,
            meta={"task": "plan_script"},
        )

        # 一次失败的视频调用 —— costs 里不会有它，只存在于 spans
        tracer = Tracer(store, ENGINE_PROJECT)
        with tracer.span("tool.generate_clip", kind="tool", attrs={"shot_id": "sc01_sh02"}) as sp:
            sp.fail("ProviderError: 智谱 HTTP 429 [code 1305：模型当前访问量过大]")

        # 一次成功的调用（不应被计入失败）
        with tracer.span("tool.generate_keyframe", kind="tool") as sp:
            sp.set(shot_id="sc01_sh01", provider="dashscope", model="wan2.5-t2i-preview")

        # 非 tool 类的 span 不是供应商调用，必须被排除
        with tracer.span("stage.generate", kind="stage") as sp:
            sp.fail("RuntimeError: 某个阶段失败")
    finally:
        store.close()


async def _rows() -> list[UsageRecord]:
    async with session_scope() as session:
        result = await session.execute(select(UsageRecord).where(UsageRecord.project_id == PROJECT))
        return list(result.scalars())


class TestUsageRecordPersistence:
    async def test_amounts_come_from_costs(self, tmp_workspace: Path) -> None:
        _seed_engine_db(tmp_workspace)
        count = await _persist_usage_records(
            job_id="job_test",
            org_id=ORG,
            project_id=PROJECT,
            workspace=tmp_workspace,
            engine_project_id=ENGINE_PROJECT,
        )
        assert count > 0

        rows = await _rows()
        money = [r for r in rows if r.success]
        assert len(money) == 3, "三条成本记录应各产生一行"
        assert {r.capability for r in money} == {"image", "video", "llm"}
        assert sum(r.provider_cost_usd for r in money) == pytest.approx(0.212)

    async def test_shot_attribution_is_carried_over(self, tmp_workspace: Path) -> None:
        _seed_engine_db(tmp_workspace)
        await _persist_usage_records(
            job_id="job_test",
            org_id=ORG,
            project_id=PROJECT,
            workspace=tmp_workspace,
            engine_project_id=ENGINE_PROJECT,
        )
        rows = await _rows()
        by_cap = {r.capability: r for r in rows if r.success}
        assert by_cap["image"].shot_key == "sc01_sh01"
        assert by_cap["video"].shot_key == "sc01_sh02"
        # LLM 调用没有镜头归属，应留空而不是硬塞假值
        assert by_cap["llm"].shot_key == ""

    async def test_failures_come_from_spans_not_costs(self, tmp_workspace: Path) -> None:
        """这是本功能的核心：只取 costs 会得到「失败率恒为 0」。"""
        _seed_engine_db(tmp_workspace)
        await _persist_usage_records(
            job_id="job_test",
            org_id=ORG,
            project_id=PROJECT,
            workspace=tmp_workspace,
            engine_project_id=ENGINE_PROJECT,
        )
        rows = await _rows()
        failures = [r for r in rows if not r.success]
        assert len(failures) == 1, "应恰好记录一次失败调用"
        f = failures[0]
        assert f.capability == "video"
        assert f.provider_cost_usd == 0.0
        assert f.latency_ms is not None, "失败行必须带延迟，否则排障查询拿不到数据"
        assert "1305" in f.error_code

    async def test_non_provider_spans_are_excluded(self, tmp_workspace: Path) -> None:
        """stage/agent 类 span 是编排结构，不是供应商调用，不该进用量表。"""
        _seed_engine_db(tmp_workspace)
        await _persist_usage_records(
            job_id="job_test",
            org_id=ORG,
            project_id=PROJECT,
            workspace=tmp_workspace,
            engine_project_id=ENGINE_PROJECT,
        )
        rows = await _rows()
        assert not any(r.capability == "llm" and not r.success for r in rows)

    async def test_missing_provider_is_marked_as_inferred(self, tmp_workspace: Path) -> None:
        """失败 span 不带 provider 时用配置归属，并标明是推断而非上游上报。"""
        _seed_engine_db(tmp_workspace)
        await _persist_usage_records(
            job_id="job_test",
            org_id=ORG,
            project_id=PROJECT,
            workspace=tmp_workspace,
            engine_project_id=ENGINE_PROJECT,
            configured_providers={"video": "dashscope"},
        )
        failures = [r for r in await _rows() if not r.success]
        assert failures[0].provider == "dashscope"
        assert failures[0].error_code.startswith("cfg:"), "推断出来的归属必须可识别"

    async def test_missing_engine_db_is_not_fatal(self, tmp_workspace: Path) -> None:
        """工作区已被清理时不该抛错 —— 缺分析数据不应让整个任务失败。"""
        count = await _persist_usage_records(
            job_id="job_test",
            org_id=ORG,
            project_id=PROJECT,
            workspace=tmp_workspace / "nowhere",
            engine_project_id=ENGINE_PROJECT,
        )
        assert count == 0


class TestCapabilityMapping:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("tool.generate_keyframe", "image"),
            ("tool.generate_clip", "video"),
            ("tool.synthesize_speech", "tts"),
            ("director.clip", "llm"),
            ("", "llm"),
        ],
    )
    def test_capability_of(self, name: str, expected: str) -> None:
        assert _capability_of(name) == expected


class TestErrorCodeExtraction:
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            ('ProviderError: 原始返回：{"error":{"code":"1305","message":"..."}}', "1305"),
            ("ProviderError: 智谱 HTTP 429 [code 1302：您的账户已达到速率限制]", "1302"),
            ("ProviderError: HTTP 500 内部错误", "500"),
            ("ProviderError: 没有任何数字的错误", "ProviderError"),
            ("", "unknown"),
        ],
    )
    def test_short_error_code(self, error: str, expected: str) -> None:
        assert _short_error_code(error) == expected
