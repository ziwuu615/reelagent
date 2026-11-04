"""账号注销。

注销是不可逆操作，测试必须覆盖三类风险：
  1. 认证 —— 光有有效会话不能注销，必须重新输密码
  2. 数据真的被清 —— 产物对象、项目、API Key，而不只是「登录不上了」
  3. 共享组织保护 —— 组织还有别的成员时不能一人删掉全组数据
"""

from __future__ import annotations

import pytest

from reelagent_cloud.config import Settings
from reelagent_cloud.db.models import ApiKey, Job, Membership, Organization, Project, User
from reelagent_cloud.db.session import session_scope
from reelagent_cloud.domain.enums import (
    JobKind,
    JobStatus,
    MembershipRole,
    ProjectStatus,
)
from reelagent_cloud.domain.errors import Forbidden, ValidationFailed
from reelagent_cloud.domain.ids import PREFIX, new_id
from reelagent_cloud.security import passwords
from reelagent_cloud.services import account as account_svc

pytestmark = pytest.mark.usefixtures("db")


async def _setup(*, password: str = "CorrectHorse1!", extra_member: bool = False):
    user_id = new_id(PREFIX["user"])
    org_id = new_id(PREFIX["org"])
    project_id = new_id(PREFIX["project"])
    job_id = new_id(PREFIX["job"])
    key_id = new_id(PREFIX["api_key"])

    async with session_scope() as session:
        session.add(
            User(
                id=user_id,
                email=f"u-{user_id[-6:]}@example.com",
                password_hash=passwords.hash_password(password),
                display_name="张三",
            )
        )
        session.add(Organization(id=org_id, name="o", slug=f"o-{org_id[-6:]}", credits_balance=100))
        session.add(
            Membership(
                id=new_id(PREFIX["membership"]), user_id=user_id, org_id=org_id, role=MembershipRole.OWNER
            )
        )
        session.add(
            Project(
                id=project_id,
                org_id=org_id,
                title="p",
                seed_text="x",
                style="cinematic",
                aspect_ratio="9:16",
                status=ProjectStatus.READY,
            )
        )
        session.add(
            Job(
                id=job_id,
                org_id=org_id,
                project_id=project_id,
                kind=JobKind.GENERATE_PROJECT,
                status=JobStatus.RUNNING,
            )
        )
        session.add(
            ApiKey(
                id=key_id,
                org_id=org_id,
                created_by=user_id,
                name="ci",
                prefix="reel_abc",
                key_hash="0" * 64,
            )
        )
        if extra_member:
            other = new_id(PREFIX["user"])
            session.add(
                User(
                    id=other,
                    email=f"o-{other[-6:]}@example.com",
                    password_hash=passwords.hash_password("Xxxxxxxx1!"),
                )
            )
            session.add(
                Membership(
                    id=new_id(PREFIX["membership"]), user_id=other, org_id=org_id, role=MembershipRole.MEMBER
                )
            )
        await session.flush()

    return user_id, org_id, project_id, job_id, key_id


async def test_wrong_password_is_rejected(settings: Settings) -> None:
    user_id, org_id, *_ = await _setup(password="CorrectHorse1!")

    async with session_scope() as session:
        user = await session.get(User, user_id)
        assert user is not None
        with pytest.raises(Forbidden):
            await account_svc.delete_account(
                session, settings, user=user, org_id=org_id, password="wrong-password"
            )


async def test_deletion_clears_data_and_blocks_access(settings: Settings) -> None:
    user_id, org_id, project_id, job_id, key_id = await _setup()

    async with session_scope() as session:
        user = await session.get(User, user_id)
        assert user is not None
        receipt = await account_svc.delete_account(
            session, settings, user=user, org_id=org_id, password="CorrectHorse1!"
        )

    assert receipt["cancelled_jobs"] == 1

    async with session_scope() as session:
        user = await session.get(User, user_id)
        org = await session.get(Organization, org_id)
        project = await session.get(Project, project_id)
        job = await session.get(Job, job_id)
        api_key = await session.get(ApiKey, key_id)

        assert user is not None and not user.is_active
        assert user.email.endswith("@deleted.invalid"), "邮箱必须匿名化以释放唯一约束"
        assert user.display_name == "已注销用户"

        assert org is not None and org.is_suspended, "组织必须停用以阻断入口"
        assert org.suspended_reason == "账号已注销"

        assert project is not None and project.deleted_at is not None
        assert project.status == ProjectStatus.ARCHIVED

        assert job is not None and job.status == JobStatus.CANCELLED, "活跃任务必须取消"

        assert api_key is not None and api_key.revoked_at is not None, "API Key 必须吊销"


async def test_session_invalidated_after_deletion(settings: Settings) -> None:
    """会话代际 +1，让已签发的 Cookie 立即失效。"""
    user_id, org_id, *_ = await _setup()

    async with session_scope() as session:
        user = await session.get(User, user_id)
        assert user is not None
        before = user.session_version
        await account_svc.delete_account(
            session, settings, user=user, org_id=org_id, password="CorrectHorse1!"
        )
        assert user.session_version == before + 1


async def test_multi_member_org_cannot_self_delete(settings: Settings) -> None:
    user_id, org_id, *_ = await _setup(extra_member=True)

    async with session_scope() as session:
        user = await session.get(User, user_id)
        assert user is not None
        with pytest.raises(ValidationFailed):
            await account_svc.assert_can_delete(session, user=user, org_id=org_id)


async def test_single_member_org_can_self_delete(settings: Settings) -> None:
    user_id, org_id, *_ = await _setup()

    async with session_scope() as session:
        user = await session.get(User, user_id)
        assert user is not None
        await account_svc.assert_can_delete(session, user=user, org_id=org_id)
