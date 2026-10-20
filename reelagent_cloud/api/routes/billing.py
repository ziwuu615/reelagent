"""支付回调。

Stripe 的 Webhook 有三条硬性要求，缺一条都会出账务事故：

1. **必须用原始请求体验签**。任何中间件若提前消费/改写 body（例如把 JSON 解析一遍），
   签名就会对不上。因此这里直接 `await request.body()`，且路由不挂任何 body 解析依赖。
2. **必须先落库再处理**。先写 `webhook_events` 再执行业务，重复投递会被唯一约束挡下。
3. **处理失败要返回 5xx**，让 Stripe 重试；吞掉异常返回 200 会导致用户付了钱没到账。
"""

from __future__ import annotations

from fastapi import APIRouter, Header, Request, Response, status
from fastapi.responses import JSONResponse

from ...db.session import session_scope
from ...deps import AppSettings
from ...domain.errors import AppError
from ...observability import get_logger
from ...services import billing

log = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"], include_in_schema=False)


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    settings: AppSettings,
    stripe_signature: str = Header("", alias="Stripe-Signature"),
) -> Response:
    if not settings.billing.enabled:
        log.warning("stripe_webhook_disabled")
        return JSONResponse({"ignored": "billing disabled"}, status_code=status.HTTP_200_OK)

    payload = await request.body()
    if not payload:
        return JSONResponse({"error": "empty payload"}, status_code=status.HTTP_400_BAD_REQUEST)

    try:
        event = billing.verify_and_parse(settings, payload, stripe_signature)
    except AppError as exc:
        # 验签失败通常意味着配置错误或伪造请求，两种都要留下日志
        log.warning("stripe_webhook_verify_failed", error=exc.message)
        return JSONResponse({"error": exc.message}, status_code=status.HTTP_400_BAD_REQUEST)

    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    log.info("stripe_webhook_received", event_id=event_id, event_type=event_type)

    async with session_scope() as session:
        try:
            outcome = await billing.process_event(session, settings, event)
        except Exception as exc:
            # 不吞异常：返回 5xx 让 Stripe 重试
            log.exception("stripe_webhook_processing_failed", event_id=event_id, error=str(exc))
            return JSONResponse(
                {"error": "processing_failed", "event_id": event_id},
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    return JSONResponse({"received": True, "event_id": event_id, "outcome": outcome})
