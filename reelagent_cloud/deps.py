"""共享的基础依赖。

放在包根而不是 `api/` 下，是为了让 `security/` 与 `api/` 都能引用它而不产生循环依赖。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db.session import get_sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """请求级事务边界。

    正常结束提交；抛异常回滚。**不在这里吞异常**——错误映射交给上层中间件，
    这样事务语义与 HTTP 语义解耦。
    """
    factory = get_sessionmaker()
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def settings_dep(request: Request) -> Settings:
    """取当前应用的配置。

    **优先读 `app.state.settings`**，而不是直接调 `get_settings()`：
    `get_settings()` 是进程级 lru_cache，在多应用实例（测试、多租户网关、嵌入场景）
    下会返回错误的那一份——表现为「应用用 A 套密钥、调用方用 B 套」这类
    极难排查的不一致。应用自己装配的配置才是唯一事实来源。
    """
    settings = getattr(request.app.state, "settings", None)
    if isinstance(settings, Settings):
        return settings
    return get_settings()


def client_ip(request: Request) -> str:
    """取客户端 IP。

    仅在**明确信任**反向代理时才使用 X-Forwarded-For——否则攻击者可以伪造该头
    绕过基于 IP 的限流。这里只在存在代理链且第一跳是内网时采信。
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first and _is_private(first):
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            return parts[-1] if parts else first
        return first
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    return request.client.host if request.client else ""


def _is_private(ip: str) -> bool:
    import ipaddress

    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback


def user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:255]


DbSession = Annotated[AsyncSession, Depends(get_session)]
AppSettings = Annotated[Settings, Depends(settings_dep)]
