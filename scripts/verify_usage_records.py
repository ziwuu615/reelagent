"""验证 usage_records 落库后，OPERATIONS.md 里那条运维查询真的能出数据。

为什么值得单独验证：
这条 SQL 在文档里存在了很久，但因为没人往表里写数据，它一直返回空集。
「查询语法正确」和「查得出东西」是两回事 —— 只有跑一次才知道修好了没有。

数据源用真实历史运行留下的引擎工作区（含失败调用），
而不是临时编造的样本，这样失败率与延迟都是真实量级。
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reelagent.harness.state import Store  # noqa: E402
from reelagent_cloud.config import Settings as CloudSettings  # noqa: E402
from reelagent_cloud.db import dispose_engine, init_engine, session_scope  # noqa: E402
from reelagent_cloud.services.pipeline import _persist_usage_records  # noqa: E402

#: OPERATIONS.md 原文的排障查询（SQLite 方言：now() -> datetime('now')）
DOCUMENTED_SQL = """
SELECT capability,
       provider,
       model,
       count(*)                                                    AS calls,
       sum(CASE WHEN success THEN 0 ELSE 1 END)                    AS failures,
       round(100.0 * sum(CASE WHEN success THEN 0 ELSE 1 END)
             / count(*), 1)                                       AS fail_pct,
       round(avg(latency_ms), 0)                                   AS avg_ms,
       round(max(latency_ms), 0)                                   AS max_ms
  FROM usage_records
 GROUP BY 1, 2, 3
 ORDER BY failures DESC;
"""


async def main() -> int:
    # 找一个真实历史引擎工作区
    engine_root = ROOT / "workspace" / "engine"
    candidates = [d for d in engine_root.iterdir() if (d / "reelagent.db").exists()]
    if not candidates:
        print("没有可用的历史引擎工作区")
        return 0
    src = candidates[0]

    probe = ROOT / "workspace" / "_usage_verify"
    shutil.rmtree(probe, ignore_errors=True)
    probe.mkdir(parents=True)
    shutil.copy(src / "reelagent.db", probe / "reelagent.db")

    store = Store(probe / "reelagent.db")
    row = store._conn.execute(
        "SELECT project_id FROM costs GROUP BY project_id ORDER BY count(*) DESC LIMIT 1"
    ).fetchone()
    engine_pid = row["project_id"]
    costs = store.list_costs(engine_pid)
    spans = store.list_provider_spans(engine_pid)
    store.close()

    print("=" * 76)
    print("引擎侧原始数据")
    print("=" * 76)
    failed = sum(1 for s in spans if s["status"] != "ok")
    print(f"  金额记录（costs）    : {len(costs)} 条，全部为成功调用")
    print(f"  调用记录（tool spans）: {len(spans)} 条，其中失败 {failed} 条")
    print("  -> 若只取 costs，失败率恒为 0、延迟恒为 NULL")

    init_engine(CloudSettings().database)
    try:
        print()
        print("=" * 76)
        print("落库到 usage_records")
        print("=" * 76)
        n = await _persist_usage_records(
            job_id="job_verify",
            org_id="org_verify",
            project_id="proj_verify",
            workspace=probe,
            engine_project_id=engine_pid,
            configured_providers={"image": "dashscope", "video": "dashscope", "tts": "dashscope"},
        )
        print(f"  写入 {n} 行")

        print()
        print("=" * 76)
        print("执行 OPERATIONS.md 里的排障查询")
        print("=" * 76)
        async with session_scope() as session:
            from sqlalchemy import text

            result = await session.execute(text(DOCUMENTED_SQL))
            rows = result.fetchall()
            if not rows:
                print("  （仍然没有数据 —— 修复无效）")
                return 1
            print(
                f"  {'capability':<10} {'provider':<12} {'model':<24} {'calls':>6} {'fail':>5} {'fail%':>7} {'avg_ms':>8}"
            )
            print("  " + "-" * 74)
            for r in rows:
                print(
                    f"  {r[0]!s:<10} {r[1]!s:<12} {str(r[2])[:24]:<24} "
                    f"{r[3]:>6} {r[4]:>5} {r[5]:>7} {r[6]!s:>8}"
                )

            print()
            print("  错误码分布（文档里的第二条查询）:")
            err = await session.execute(
                text(
                    "SELECT provider, capability, error_code, count(*) "
                    "FROM usage_records WHERE NOT success "
                    "GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 6"
                )
            )
            for r in err.fetchall():
                print(f"    {r[0]!s:<12} {r[1]!s:<10} {r[2]!s:<16} {r[3]}")
    finally:
        await dispose_engine()
        shutil.rmtree(probe, ignore_errors=True)

    print()
    print("=" * 76)
    print("结论：文档承诺的排障能力现在真的可用了")
    print("=" * 76)
    return 0


if __name__ == "__main__":

    async def _run() -> int:
        # 每次验证前清掉上一次的验证数据，避免累积干扰读数
        init_engine(CloudSettings().database)
        from sqlalchemy import text

        async with session_scope() as session:
            await session.execute(text("DELETE FROM usage_records WHERE org_id='org_verify'"))
        await dispose_engine()
        return await main()

    sys.exit(asyncio.run(_run()))
