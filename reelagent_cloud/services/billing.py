"""Stripe 计费。

覆盖三条链路：
  1. **订阅**：Checkout 结账 -> Webhook 回填订阅状态 -> 周期发放额度
  2. **续费与失败**：invoice.paid 发放额度；invoice.payment_failed 标记 past_due
  3. **客户门户**：让用户自己在 Stripe 侧改卡/取消，避免我们实现一套账单 UI

**Webhook 必须幂等**：Stripe 会重复投递同一事件（超时重试、网络抖动）。
`webhook_events(provider, event_id)` 唯一约束保证只处理一次；
额度发放本身也带 `idempotency_key`，双重保险。

未配置 Stripe 时（`billing.enabled=false`），产品降级为**纯额度模式**：
注册赠送额度可正常使用，但没有充值入口。这样自托管部署无需依赖 Stripe。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.base import utcnow
from ..db.models import Organization, Subscription, WebhookEvent
from ..domain.enums import (
    AuditAction,
    LedgerKind,
    PlanTier,
    SubscriptionStatus,
    WebhookStatus,
)
from ..domain.errors import NotFound, UnsupportedOperation, ValidationFailed
from ..domain.ids import PREFIX, new_id
from ..domain.plans import get_plan
from ..observability import get_logger
from . import audit, credits

log = get_logger(__name__)


def _stripe(settings: Settings):
    if not settings.billing.enabled:
        raise UnsupportedOperation("当前部署未开启在线支付，请联系管理员手动充值")
    try:
        import stripe
    except ImportError as exc:  # pragma: no cover
        raise UnsupportedOperation("未安装 stripe 依赖") from exc
    stripe.api_key = settings.billing.secret_key.get_secret_value()
    return stripe


def price_id_for(settings: Settings, tier: PlanTier) -> str:
    mapping = {
        PlanTier.STARTER: settings.billing.price_starter,
        PlanTier.PRO: settings.billing.price_pro,
        PlanTier.STUDIO: settings.billing.price_studio,
    }
    price_id = mapping.get(tier, "")
    if not price_id:
        raise ValidationFailed(f"套餐 {tier.value} 尚未在 Stripe 配置价格，请联系支持")
    return price_id


def tier_for_price(settings: Settings, price_id: str) -> PlanTier | None:
    mapping = {
        settings.billing.price_starter: PlanTier.STARTER,
        settings.billing.price_pro: PlanTier.PRO,
        settings.billing.price_studio: PlanTier.STUDIO,
    }
    return mapping.get(price_id)


# --------------------------------------------------------------------------- #
# 客户与结账
# --------------------------------------------------------------------------- #
async def ensure_customer(session: AsyncSession, settings: Settings, org: Organization) -> str:
    sub = await _get_or_create_subscription(session, org)
    if sub.stripe_customer_id:
        return sub.stripe_customer_id

    stripe = _stripe(settings)
    import asyncio

    customer = await asyncio.to_thread(
        stripe.Customer.create,
        name=org.name,
        metadata={"org_id": org.id, "org_slug": org.slug},
    )
    sub.stripe_customer_id = customer["id"]
    await session.flush()
    log.info("stripe_customer_created", org_id=org.id, customer=customer["id"])
    return customer["id"]


async def create_checkout_session(
    session: AsyncSession,
    settings: Settings,
    *,
    org: Organization,
    tier: PlanTier,
    success_url: str,
    cancel_url: str,
    user_email: str = "",
    user_id: str | None = None,
) -> str:
    stripe = _stripe(settings)
    plan = get_plan(tier)
    if plan.is_free:
        raise ValidationFailed("免费版无需结账")

    customer_id = await ensure_customer(session, settings, org)
    price_id = price_id_for(settings, tier)
    import asyncio

    params: dict[str, Any] = {
        "mode": "subscription",
        "customer": customer_id,
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "allow_promotion_codes": True,
        "client_reference_id": org.id,
        "metadata": {"org_id": org.id, "tier": tier.value, "user_id": user_id or ""},
        "subscription_data": {"metadata": {"org_id": org.id, "tier": tier.value}},
    }
    if user_email:
        params["customer_email"] = user_email

    checkout = await asyncio.to_thread(stripe.checkout.Session.create, **params)
    log.info("checkout_session_created", org_id=org.id, tier=tier.value, session=checkout["id"])
    return str(checkout["url"])


async def create_portal_session(
    session: AsyncSession, settings: Settings, *, org: Organization, return_url: str
) -> str:
    stripe = _stripe(settings)
    customer_id = await ensure_customer(session, settings, org)
    import asyncio

    portal = await asyncio.to_thread(
        stripe.billing_portal.Session.create, customer=customer_id, return_url=return_url
    )
    return str(portal["url"])


# --------------------------------------------------------------------------- #
# Webhook
# --------------------------------------------------------------------------- #
def verify_and_parse(settings: Settings, payload: bytes, signature: str) -> dict[str, Any]:
    stripe = _stripe(settings)
    secret = settings.billing.webhook_secret.get_secret_value()
    if not secret:
        raise UnsupportedOperation("未配置 Stripe webhook secret")
    try:
        event = stripe.Webhook.construct_event(payload, signature, secret)
    except Exception as exc:
        raise ValidationFailed(f"Webhook 签名校验失败：{exc}") from exc
    return dict(event)


async def record_event(
    session: AsyncSession, *, provider: str, event: dict[str, Any]
) -> tuple[WebhookEvent, bool]:
    """登记事件。返回 (记录, 是否首次)。"""
    event_id = str(event.get("id") or "")
    if not event_id:
        raise ValidationFailed("Webhook 缺少事件 ID")

    existing = (
        await session.execute(
            select(WebhookEvent).where(WebhookEvent.provider == provider, WebhookEvent.event_id == event_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        log.info("webhook_duplicate", provider=provider, event_id=event_id, status=existing.status.value)
        return existing, False

    row = WebhookEvent(
        id=new_id(PREFIX["webhook"]),
        provider=provider,
        event_id=event_id,
        event_type=str(event.get("type") or ""),
        status=WebhookStatus.RECEIVED,
        payload=_truncate_payload(event),
    )
    session.add(row)
    await session.flush()
    return row, True


def _truncate_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Webhook 原文可能很大，只保留排障需要的关键字段。"""
    obj = event.get("data", {}).get("object", {}) or {}
    keep = {
        "id",
        "object",
        "status",
        "customer",
        "subscription",
        "amount_paid",
        "amount_due",
        "currency",
        "created",
        "current_period_start",
        "current_period_end",
        "cancel_at_period_end",
        "client_reference_id",
        "metadata",
        "billing_reason",
    }
    slim = {k: v for k, v in obj.items() if k in keep}
    return {"id": event.get("id"), "type": event.get("type"), "data": {"object": slim}}


async def process_event(session: AsyncSession, settings: Settings, event: dict[str, Any]) -> str:
    """分发处理。返回处理结果描述。"""
    row, first = await record_event(session, provider="stripe", event=event)
    if not first:
        return "duplicate"

    event_type = str(event.get("type") or "")
    obj = (event.get("data") or {}).get("object") or {}

    try:
        handler = _HANDLERS.get(event_type)
        if handler is None:
            row.status = WebhookStatus.IGNORED
            row.processed_at = utcnow()
            log.info("webhook_ignored", event_type=event_type)
            return "ignored"

        message = await handler(session, settings, obj)
        row.status = WebhookStatus.PROCESSED
        row.processed_at = utcnow()
        row.error = ""
        log.info("webhook_processed", event_type=event_type, message=message)
        return message
    except Exception as exc:
        row.status = WebhookStatus.FAILED
        row.error = f"{type(exc).__name__}: {exc}"[:1000]
        log.exception("webhook_failed", event_type=event_type, error=str(exc))
        raise


# --------------------------------------------------------------------------- #
# 事件处理
# --------------------------------------------------------------------------- #
async def _handle_checkout_completed(session, settings, obj) -> str:
    org_id = str(obj.get("client_reference_id") or (obj.get("metadata") or {}).get("org_id") or "")
    if not org_id:
        return "missing org reference"
    subscription_id = str(obj.get("subscription") or "")
    tier = PlanTier((obj.get("metadata") or {}).get("tier") or PlanTier.STARTER.value)

    sync = await _sync_subscription(
        session, settings, org_id=org_id, subscription_id=subscription_id, tier=tier
    )
    await audit.record(
        session,
        action=AuditAction.SUBSCRIPTION_CHANGED,
        org_id=org_id,
        actor_type="system",
        target_type="subscription",
        target_id=sync.id,
        meta={"tier": tier.value, "source": "checkout.completed"},
    )
    return f"activated {tier.value}"


async def _handle_subscription_updated(session, settings, obj) -> str:
    subscription_id = str(obj.get("id") or "")
    metadata = obj.get("metadata") or {}
    org_id = str(metadata.get("org_id") or "")
    if not org_id:
        found = await _find_org_by_subscription(session, subscription_id)
        org_id = found.id if found else ""
    if not org_id:
        return "unknown subscription"

    price_id = _extract_price_id(obj)
    tier = tier_for_price(settings, price_id) or PlanTier(metadata.get("tier") or PlanTier.STARTER.value)
    sync = await _sync_subscription(
        session, settings, org_id=org_id, subscription_id=subscription_id, tier=tier
    )
    await audit.record(
        session,
        action=AuditAction.SUBSCRIPTION_CHANGED,
        org_id=org_id,
        actor_type="system",
        target_type="subscription",
        target_id=sync.id,
        meta={"tier": tier.value, "status": sync.status.value, "source": "subscription.updated"},
    )
    return f"updated {tier.value}/{sync.status.value}"


async def _handle_subscription_deleted(session, settings, obj) -> str:
    subscription_id = str(obj.get("id") or "")
    sub = (
        await session.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
        )
    ).scalar_one_or_none()
    if sub is None:
        return "unknown subscription"

    sub.status = SubscriptionStatus.CANCELED
    sub.canceled_at = utcnow()
    sub.cancel_at_period_end = False

    org = await session.get(Organization, sub.org_id)
    if org is not None:
        org.plan = PlanTier.FREE
        org.watermark = True
    log.info("subscription_canceled", org_id=sub.org_id)
    return "canceled -> downgraded to free"


async def _handle_invoice_paid(session, settings, obj) -> str:
    subscription_id = _extract_subscription_id(obj)
    invoice_id = str(obj.get("id") or "")
    if not subscription_id:
        return "not a subscription invoice"

    sub = (
        await session.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
        )
    ).scalar_one_or_none()
    if sub is None:
        return "unknown subscription"

    org = await session.get(Organization, sub.org_id)
    if org is None:
        return "unknown org"

    plan = get_plan(sub.plan)
    amount = int(obj.get("amount_paid") or 0)
    is_renewal = str(obj.get("billing_reason") or "") == "subscription_cycle"

    if is_renewal:
        # 续费：按订阅计划发放新周期额度。幂等键用 invoice id，重复投递不会重复发。
        await credits.grant(
            session,
            org_id=org.id,
            amount=plan.monthly_credits,
            kind=LedgerKind.PLAN_GRANT,
            description=f"{plan.name} 周期额度（{invoice_id}）",
            idempotency_key=f"invoice:{invoice_id}",
            external_ref=invoice_id,
            meta={"plan": plan.value if hasattr(plan, "value") else plan.tier.value},
        )
        org.credits_granted_this_period = plan.monthly_credits
        org.period_started_at = utcnow()
        await session.flush()
        log.info("plan_credits_granted", org_id=org.id, credits=plan.monthly_credits, invoice=invoice_id)
        return f"granted {plan.monthly_credits} credits"

    # 首次支付：额度在 activate 时已发过，这里只补一次“首购加成”之外的记录
    return f"paid {amount}"


async def _handle_invoice_failed(session, settings, obj) -> str:
    subscription_id = _extract_subscription_id(obj)
    if not subscription_id:
        return "not a subscription invoice"
    sub = (
        await session.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
        )
    ).scalar_one_or_none()
    if sub is None:
        return "unknown subscription"
    sub.status = SubscriptionStatus.PAST_DUE
    log.warning("subscription_past_due", org_id=sub.org_id, invoice=obj.get("id"))
    return "marked past_due"


_HANDLERS = {
    "checkout.session.completed": _handle_checkout_completed,
    "customer.subscription.created": _handle_subscription_updated,
    "customer.subscription.updated": _handle_subscription_updated,
    "customer.subscription.deleted": _handle_subscription_deleted,
    "invoice.paid": _handle_invoice_paid,
    "invoice.payment_failed": _handle_invoice_failed,
}


# --------------------------------------------------------------------------- #
# 内部
# --------------------------------------------------------------------------- #
def _extract_price_id(obj: dict[str, Any]) -> str:
    items = (obj.get("items") or {}).get("data") or []
    if items:
        price = items[0].get("price") or {}
        return str(price.get("id") or "")
    return str(((obj.get("plan") or {}).get("id")) or "")


def _extract_subscription_id(obj: dict[str, Any]) -> str:
    direct = obj.get("subscription")
    if isinstance(direct, str):
        return direct
    parent = (obj.get("parent") or {}).get("subscription_details") or {}
    if isinstance(parent.get("subscription"), str):
        return parent["subscription"]
    return ""


async def _find_org_by_subscription(session: AsyncSession, subscription_id: str) -> Organization | None:
    if not subscription_id:
        return None
    sub = (
        await session.execute(
            select(Subscription).where(Subscription.stripe_subscription_id == subscription_id)
        )
    ).scalar_one_or_none()
    if sub is None:
        return None
    return await session.get(Organization, sub.org_id)


async def _get_or_create_subscription(session: AsyncSession, org: Organization) -> Subscription:
    sub = (
        await session.execute(select(Subscription).where(Subscription.org_id == org.id))
    ).scalar_one_or_none()
    if sub is None:
        sub = Subscription(
            id=new_id(PREFIX["subscription"]),
            org_id=org.id,
            plan=org.plan,
            status=SubscriptionStatus.ACTIVE,
            current_period_start=utcnow(),
            current_period_end=utcnow() + timedelta(days=30),
        )
        session.add(sub)
        await session.flush()
    return sub


async def _sync_subscription(
    session: AsyncSession,
    settings: Settings,
    *,
    org_id: str,
    subscription_id: str,
    tier: PlanTier,
) -> Subscription:
    """回填订阅状态并发放首期额度（幂等）。"""
    org = await session.get(Organization, org_id)
    if org is None:
        raise NotFound("组织不存在")

    sub = await _get_or_create_subscription(session, org)
    plan = get_plan(tier)
    previous_tier = org.plan

    sub.plan = tier
    sub.status = SubscriptionStatus.ACTIVE
    sub.stripe_subscription_id = subscription_id

    stripe = _stripe(settings)
    import asyncio

    try:
        remote = await asyncio.to_thread(stripe.Subscription.retrieve, subscription_id)
        sub.stripe_price_id = _extract_price_id(remote)
        sub.current_period_start = _from_ts(remote.get("current_period_start"))
        sub.current_period_end = _from_ts(remote.get("current_period_end"))
        sub.cancel_at_period_end = bool(remote.get("cancel_at_period_end"))
        sub.stripe_customer_id = str(remote.get("customer") or sub.stripe_customer_id)
        sub.status = _parse_status(remote.get("status"))
    except Exception as exc:  # 拉取失败不阻塞激活，Webhook 后续会纠正
        log.warning("stripe_subscription_fetch_failed", subscription_id=subscription_id, error=str(exc))

    org.plan = tier
    org.watermark = plan.is_free
    org.period_started_at = utcnow()
    await session.flush()

    # 首期额度：幂等键绑订阅 id，确保「升级」重复投递也不会重复发放
    if plan.monthly_credits > 0:
        await credits.grant(
            session,
            org_id=org.id,
            amount=plan.monthly_credits,
            kind=LedgerKind.PLAN_GRANT,
            description=f"{plan.name} 首期额度",
            idempotency_key=f"sub-activate:{subscription_id}",
            external_ref=subscription_id,
            meta={"from": previous_tier.value, "to": tier.value},
        )
        org.credits_granted_this_period = plan.monthly_credits
        await session.flush()

    log.info(
        "subscription_synced",
        org_id=org.id,
        tier=tier.value,
        from_tier=previous_tier.value,
        credits=plan.monthly_credits,
    )
    return sub


def _from_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError):
        return None


def _parse_status(value: Any) -> SubscriptionStatus:
    """Stripe 的 status 枚举与我们的不完全一致，未知值一律按 active 处理（避免误降级）。"""
    try:
        return SubscriptionStatus(str(value))
    except ValueError:
        log.warning("unknown_stripe_status", status=str(value))
        return SubscriptionStatus.ACTIVE


# --------------------------------------------------------------------------- #
# 展示用
# --------------------------------------------------------------------------- #
async def subscription_for(session: AsyncSession, org_id: str) -> Subscription | None:
    return (
        await session.execute(select(Subscription).where(Subscription.org_id == org_id))
    ).scalar_one_or_none()


def current_plan_summary(org: Organization, sub: Subscription | None) -> dict[str, Any]:
    plan = get_plan(org.plan)
    return {
        "tier": org.plan.value,
        "name": plan.name,
        "price_cny": plan.price_cny,
        "monthly_credits": plan.monthly_credits,
        "status": sub.status.value if sub else SubscriptionStatus.ACTIVE.value,
        "current_period_end": sub.current_period_end.isoformat() if sub and sub.current_period_end else None,
        "cancel_at_period_end": bool(sub.cancel_at_period_end) if sub else False,
        "watermark": org.watermark,
    }
