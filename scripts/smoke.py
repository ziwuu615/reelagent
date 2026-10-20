"""开发期冒烟脚本：注册 → 账本 → 任务 → 领取 → 结算 → 对账。

用法：python scripts/smoke.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("REEL_ENV", "dev")

from reelagent_cloud.config import DatabaseSettings, get_settings
from reelagent_cloud.db import create_all, dispose_engine, init_engine
from reelagent_cloud.db.session import session_scope
from reelagent_cloud.domain.enums import JobKind, JobStatus
from reelagent_cloud.services import accounts, credits, jobs
from reelagent_cloud.services import storage as storage_svc


async def main() -> int:
    db_path = Path(tempfile.gettempdir()) / "reel_smoke_cloud.db"
    if db_path.exists():
        db_path.unlink()

    settings = get_settings()
    init_engine(DatabaseSettings(url=f"sqlite+aiosqlite:///{db_path.as_posix()}"))
    await create_all()
    storage_svc.reset_storage()

    failures: list[str] = []

    def check(label: str, ok: bool, extra: str = "") -> None:
        mark = "✓" if ok else "✗"
        print(f"  {mark} {label}{(' — ' + extra) if extra else ''}")
        if not ok:
            failures.append(label)

    print("== 1. 注册 ==")
    async with session_scope() as session:
        user, org, membership = await accounts.signup(
            session,
            settings,
            email="Founder@Example.COM",
            password="Str0ng-Passw0rd!",
            display_name="创始人",
            org_name="光影工作室",
        )
        user_id, org_id = user.id, org.id
        check("邮箱归一化为小写", user.email == "founder@example.com", user.email)
        check("组织创建", bool(org.slug), org.slug)
        check("成员角色为 owner", membership.role.value == "owner")

    print("== 2. 注册赠送额度与账本 ==")
    async with session_scope() as session:
        balance = await credits.get_balance(session, org_id)
        check("赠送额度到账", balance == 500, f"balance={balance}")
        recon = await credits.reconcile(session, org_id)
        check("账本与余额一致", recon["consistent"], str(recon))

    print("== 3. 登录（含失败与锁定计数） ==")
    async with session_scope() as session:
        authed = await accounts.authenticate(
            session, settings, email="founder@example.com", password="Str0ng-Passw0rd!"
        )
        check("正确口令可登录", authed.id == user_id)
    async with session_scope() as session:
        from reelagent_cloud.domain.errors import Unauthorized

        try:
            await accounts.authenticate(
                session, settings, email="founder@example.com", password="wrong-password"
            )
            check("错误口令被拒绝", False)
        except Unauthorized:
            check("错误口令被拒绝", True)

    print("== 4. 重复邮箱注册被拒 ==")
    async with session_scope() as session:
        from reelagent_cloud.domain.errors import AlreadyExists

        try:
            await accounts.signup(
                session, settings, email="founder@example.com", password="Another-Str0ng!23"
            )
            check("重复邮箱被拒", False)
        except AlreadyExists:
            check("重复邮箱被拒", True)

    print("== 5. 弱口令被拒 ==")
    async with session_scope() as session:
        from reelagent_cloud.security import PasswordPolicyError

        try:
            await accounts.signup(session, settings, email="weak@example.com", password="12345678")
            check("弱口令被拒", False)
        except PasswordPolicyError as exc:
            check("弱口令被拒", True, str(exc))

    print("== 6. 额度扣减与幂等 ==")
    async with session_scope() as session:
        entry1 = await credits.consume(
            session,
            org_id=org_id,
            amount=120,
            description="生成消耗",
            idempotency_key="job:alpha",
        )
        balance = await credits.get_balance(session, org_id)
        check("扣减生效", balance == 380, f"balance={balance}")
    async with session_scope() as session:
        entry2 = await credits.consume(
            session,
            org_id=org_id,
            amount=120,
            description="生成消耗（重复请求）",
            idempotency_key="job:alpha",
        )
        balance = await credits.get_balance(session, org_id)
        check("幂等键阻止重复扣费", entry2.id == entry1.id and balance == 380, f"balance={balance}")

    print("== 7. 余额不足被拦截 ==")
    async with session_scope() as session:
        from reelagent_cloud.domain.errors import PaymentRequired

        try:
            await credits.consume(session, org_id=org_id, amount=99_999)
            check("超额扣减被拒", False)
        except PaymentRequired as exc:
            check("超额扣减被拒", True, f"required={exc.details.get('required')}")

    print("== 8. 项目与任务入队 ==")
    async with session_scope() as session:
        project = await _make_project(session, settings, org_id=org_id, user_id=user_id)
        project_id = project.id
        quote = credits.quote_project(settings, shots=6, allow_premium=False)
        check("额度预估", quote.credits > 0, f"{quote.credits} 点 / ${quote.provider_cost_usd}")
        job = await jobs.enqueue(
            session,
            settings,
            org_id=org_id,
            kind=JobKind.GENERATE_PROJECT,
            project_id=project_id,
            created_by=user_id,
            payload={"max_shots": 6},
            credits_reserved=quote.credits,
            idempotency_key="req-001",
        )
        job_id = job.id
        check("任务已入队", job.status is JobStatus.PENDING)
    async with session_scope() as session:
        dup = await jobs.enqueue(
            session,
            settings,
            org_id=org_id,
            kind=JobKind.GENERATE_PROJECT,
            project_id=project_id,
            created_by=user_id,
            idempotency_key="req-001",
        )
        check("入队幂等", dup.id == job_id)

    print("== 9. 领取与租约 ==")
    async with session_scope() as session:
        claimed = await jobs.claim(session, settings, worker_id="worker-test")
        check("成功领取", claimed is not None and claimed.id == job_id)
        assert claimed is not None
        check("状态置为 running", claimed.status is JobStatus.RUNNING)
        check("租约已设置", claimed.lease_expires_at is not None)
        check("尝试次数自增", claimed.attempts == 1)
        fresh = await jobs.heartbeat(session, settings, job_id=job_id, worker_id="worker-test")
        check("心跳续约成功", fresh is True)
    async with session_scope() as session:
        rival = await jobs.claim(session, settings, worker_id="worker-rival")
        check("被占用时其它 worker 抢不到", rival is None)

    print("== 10. 结算与退还 ==")
    async with session_scope() as session:
        settle = await jobs.settle(session, settings, job_id=job_id, actual_provider_cost_usd=0.30)
        check("按实际用量结算", "refunded" in settle or "extra_charged" in settle, str(settle))
        await jobs.complete(session, job_id=job_id, result={"ok": True}, provider_cost_usd=0.30)
    async with session_scope() as session:
        done = await jobs.get(session, job_id)
        check("任务完成", done.status is JobStatus.SUCCEEDED)
        recon = await credits.reconcile(session, org_id)
        check("结算后账本仍一致", recon["consistent"], str(recon))

    print("== 11. 失败退还 ==")
    async with session_scope() as session:
        before = await credits.get_balance(session, org_id)
        job2 = await jobs.enqueue(
            session,
            settings,
            org_id=org_id,
            kind=JobKind.GENERATE_PROJECT,
            project_id=project_id,
            credits_reserved=100,
            idempotency_key="req-002",
        )
        await credits.consume(
            session,
            org_id=org_id,
            amount=100,
            description="预扣",
            idempotency_key=f"reserve:{job2.id}",
        )
        job2_id = job2.id
    async with session_scope() as session:
        await jobs.fail(session, job_id=job2_id, error="供应商 500", error_code="provider_failure")
    async with session_scope() as session:
        after = await credits.get_balance(session, org_id)
        check("失败任务退还预扣", after == before, f"{before} -> {after}")

    print("== 12. 租约过期回收 ==")
    async with session_scope() as session:
        job3 = await jobs.enqueue(
            session,
            settings,
            org_id=org_id,
            kind=JobKind.GENERATE_PROJECT,
            project_id=project_id,
            idempotency_key="req-003",
        )
        job3_id = job3.id
    async with session_scope() as session:
        await jobs.claim(session, settings, worker_id="worker-doomed")
    async with session_scope() as session:
        from datetime import timedelta

        from reelagent_cloud.db.base import utcnow
        from reelagent_cloud.db.models import Job

        stale = await session.get(Job, job3_id)
        assert stale is not None
        stale.lease_expires_at = utcnow() - timedelta(seconds=5)
    async with session_scope() as session:
        reaped = await jobs.reap_expired_leases(session)
        check("回收过期租约", reaped >= 1, f"reaped={reaped}")
    async with session_scope() as session:
        reclaimed = await jobs.claim(session, settings, worker_id="worker-new")
        check("崩溃任务可被重新领取", reclaimed is not None and reclaimed.id == job3_id)

    print("== 13. 存储与租户隔离 ==")
    storage = storage_svc.get_storage(settings)
    key = storage_svc.build_key(
        org_id=org_id, project_id=project_id, category="keyframes", filename="shot 1?.png"
    )
    obj = await storage.put_bytes(key, b"hello-world", content_type="image/png")
    check("写入对象", obj.size_bytes == 11, key)
    check("读取一致", await storage.get_bytes(key) == b"hello-world")
    check("路径被清洗", "?" not in key and " " not in key, key)
    check("租户前缀校验通过", storage_svc.key_belongs_to_org(key, org_id))
    check("越权 key 被识别", not storage_svc.key_belongs_to_org(key, "org_other"))

    print("== 14. 密钥脱敏 ==")
    from reelagent_cloud.observability import redact_value

    red = redact_value(
        {
            "api_key": "reel_abcd1234_supersecretvalue",
            "nested": {"password": "p@ss", "note": "Bearer sk-live-abcdefghijklmnop"},
        }
    )
    check("字段名脱敏", red["api_key"] == "***REDACTED***")
    check("嵌套脱敏", red["nested"]["password"] == "***REDACTED***")
    check("裸值脱敏", "sk-live" not in red["nested"]["note"], red["nested"]["note"])

    await dispose_engine()

    print()
    if failures:
        print(f"❌ {len(failures)} 项失败：")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


async def _make_project(session, settings, *, org_id: str, user_id: str):
    from reelagent_cloud.db.models import Project
    from reelagent_cloud.domain.enums import ProjectStatus
    from reelagent_cloud.domain.ids import PREFIX, new_id

    project = Project(
        id=new_id(PREFIX["project"]),
        org_id=org_id,
        created_by=user_id,
        title="深夜便利店",
        seed_text="深夜的便利店只剩下最后一盏灯。",
        style=settings.style,
        aspect_ratio=settings.aspect_ratio,
        status=ProjectStatus.DRAFT,
    )
    session.add(project)
    await session.flush()
    return project


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
