"""取消因验证脚本意外入队的任务，并退还预扣的额度。

为什么必须走服务层而不是直接改数据库：
`mark_cancelled(refund=True)` 会同时写入一条反向的额度流水，
保持账本可对账。直接 UPDATE 状态会留下「扣了钱但任务没跑」的悬空账，
用户看到余额变少却找不到原因 —— 这类账目不一致在真实产品里是严重问题。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reelagent_cloud.config import Settings  # noqa: E402
from reelagent_cloud.db import dispose_engine, init_engine, session_scope  # noqa: E402
from reelagent_cloud.services import jobs  # noqa: E402

#: 需要取消的任务：全部来自 verify_tier_e2e.py 的意外入队
CANCEL = [
    ("job_01M525C011W7H9GJDEPKSCNXFY", "档位测试auto（运行中）"),
    ("job_01M525C053EV0MW607M1TWQ1CA", "档位测试draft"),
    ("job_01M525C08T38E656VWF0QN96KV", "档位测试standard"),
]

#: 要保留的：本次真正的端到端验证
KEEP = "job_01M525CBK7QMJ8NV77E61XPVRT"


async def main() -> int:
    init_engine(Settings().database)
    print("取消意外入队的任务（额度将退还）：")
    for job_id, label in CANCEL:
        async with session_scope() as session:
            try:
                before = await jobs.get(session, job_id) if hasattr(jobs, "get") else None
                status_before = before.status if before else "?"
                await jobs.mark_cancelled(session, job_id=job_id, refund=True)
                print(f"  [OK] {label:<24} {job_id}  (原状态 {status_before}) -> canceled，已退款")
            except Exception as exc:
                print(f"  [!!] {label:<24} {job_id}  失败：{type(exc).__name__}: {str(exc)[:120]}")
    await dispose_engine()
    print()
    print(f"保留任务：{KEEP}（省钱档实跑，本次验证用）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
