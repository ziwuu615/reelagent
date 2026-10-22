"""逐次调用可观测性的回归测试：失败归因 + 成功延迟。

## 两个真实缺陷

**1. 失败调用没有供应商归属**

工具层原来只在**成功之后**才给 span 写 provider/model：

    res = image_p.generate(...)      # 失败时这里就抛了
    rt.record_media_cost(provider=res.provider, ...)
    sp.set(provider=res.provider)    # 永远执行不到

于是失败的 span 从来没有 provider，`usage_records` 只能记空字符串。
线上表现：image 有 20% 的失败率，但**看不出是哪个供应商在失败** ——
而失败率按供应商对比恰恰是运维最需要的维度。

修法：调用**前**就写下 provider/model。它们在调用前已确定，是事实。
刻意不拿「当前配置」回填 —— 那会产生「看起来有归属、实际是猜的」数据
（`list_provider_spans` 的 docstring 明确禁止过这件事）。

**2. 成功调用的延迟恒为 NULL**

延迟原来只写在 span 上。`usage_records` 的成功行来自 `costs` 表，
而 `costs` 没有延迟列 —— 于是「哪个模型更快」这个问题系统自己答不出来，
只能靠手工实测（express 快 63% 那个数字就是这么来的）。

修法：`costs` 加 `latency_ms` 列，工具把测得的耗时一起写进成本行。
"""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

from reelagent.harness.state import Store

#: 旧版 costs 表（没有 latency_ms）。用来验证迁移能补齐。
_OLD_SCHEMA = """
CREATE TABLE costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    category TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    unit TEXT,
    quantity REAL NOT NULL DEFAULT 0,
    usd REAL NOT NULL DEFAULT 0,
    ts REAL NOT NULL,
    meta TEXT
);
"""


def _make_old_db(path: pathlib.Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_OLD_SCHEMA)
        conn.commit()
    finally:
        conn.close()


class TestCostLatencyMigration:
    """断点续跑会打开之前运行留下的旧库，缺列必须能自动补上。"""

    def test_old_db_gets_latency_column(self, tmp_workspace: pathlib.Path) -> None:
        db = tmp_workspace / "old.db"
        _make_old_db(db)

        store = Store(db)
        try:
            cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(costs)").fetchall()}
        finally:
            store.close()

        assert "latency_ms" in cols, "旧库必须被自动补上 latency_ms 列"

    def test_migration_is_idempotent(self, tmp_workspace: pathlib.Path) -> None:
        """反复打开同一个库不能报错（每次任务都会重新打开）。"""
        db = tmp_workspace / "old.db"
        _make_old_db(db)
        for _ in range(3):
            store = Store(db)
            store.close()

    def test_latency_roundtrip(self, tmp_workspace: pathlib.Path) -> None:
        store = Store(tmp_workspace / "s.db")
        try:
            store.record_cost(
                project_id="p1",
                category="video",
                provider="dashscope",
                model="wan3.0-video",
                unit="second",
                quantity=3.0,
                usd=0.123,
                latency_ms=45678.9,
            )
            rows = store.list_costs("p1")
        finally:
            store.close()

        assert len(rows) == 1
        assert rows[0]["latency_ms"] == pytest.approx(45678.9)

    def test_latency_optional(self, tmp_workspace: pathlib.Path) -> None:
        """不传延迟时存 NULL，而不是 0 —— 0 会被误读成「瞬时完成」。"""
        store = Store(tmp_workspace / "s.db")
        try:
            store.record_cost(project_id="p1", category="llm", quantity=1, usd=0.001)
            rows = store.list_costs("p1")
        finally:
            store.close()

        assert rows[0]["latency_ms"] is None


class TestProviderAttributionBeforeCall:
    """失败调用的供应商归属必须在调用前确定。"""

    def test_span_attrs_written_before_provider_call(self) -> None:
        """检查三个工具都在 span 初始化时就传入 provider/model。

        不能用「跑一次失败再看」来测：那样只能证明某条路径，
        而这里的风险是**新增工具时忘记写**。所以直接检查源码结构。
        """
        src = pathlib.Path("reelagent/tools/media_tools.py").read_text(encoding="utf-8")

        for tool, prov in (
            ("tool.generate_keyframe", "image_p"),
            ("tool.generate_clip", "provider"),
            ("tool.synthesize_speech", "tts_p"),
        ):
            assert tool in src, f"找不到 {tool}"
            start = src.find(tool)
            # 取这个工具 span 之后、调用之后的代码段
            seg = src[start : start + 1600]
            assert f'provider=getattr({prov}, "name"' in seg, (
                f"{tool} 的 span 必须在调用前写入 provider（否则失败时无归属）"
            )
            assert f'model=getattr({prov}, "model"' in seg, f"{tool} 的 span 必须在调用前写入 model"

    def test_no_config_backfill_on_span(self) -> None:
        """span 上的 provider 必须来自 provider 实例，不能来自配置猜测。"""
        src = pathlib.Path("reelagent/tools/media_tools.py").read_text(encoding="utf-8")
        assert "rt.settings.providers" not in src, "工具层不应拿配置回填 span 归属 —— 那是猜的，不是事实"


class TestUsageRecordLatency:
    """成功调用的延迟要能进 usage_records。"""

    def test_success_rows_take_latency_from_costs(self) -> None:
        src = pathlib.Path("reelagent_cloud/services/pipeline.py").read_text(encoding="utf-8")
        i = src.find("for c in costs:")
        assert i > 0
        seg = src[i : i + 1200]
        assert "latency_ms" in seg and 'c.get("latency_ms")' in seg, (
            "成功调用的 usage_records 必须从成本行读取延迟"
        )

    def test_configured_providers_is_actually_passed(self) -> None:
        """回填参数之前从未被传过，导致失败记录的 provider 恒为空。"""
        src = pathlib.Path("reelagent_cloud/services/pipeline.py").read_text(encoding="utf-8")
        calls = [m for m in src.split("_persist_usage_records(") if "configured_providers=" in m]
        assert calls, "必须有人真的传入 configured_providers，否则是死参数"
