"""邮件发件箱。

业务代码只往 `email_outbox` 表里写记录，由 worker 负责真正投递。
这样做的好处：
  - 请求路径不被 SMTP 往返阻塞（SMTP 动辄 1-3 秒，放在请求里是灾难）
  - 投递失败自动重试，且失败原因可查
  - 本地开发不需要 SMTP，邮件落库 + 打日志即可验证流程

未配置 SMTP 时（`email.enabled=false`），邮件会被标记为 delivered 并打印到日志，
这样注册验证、额度预警这类流程在开发环境依然可以完整走通。
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from sqlalchemy import select

from ..config import Settings
from ..db.base import utcnow
from ..db.models import EmailOutbox
from ..db.session import session_scope
from ..domain.ids import PREFIX, new_id
from ..observability import get_logger

log = get_logger(__name__)

MAX_ATTEMPTS = 5


# --------------------------------------------------------------------------- #
# 入队
# --------------------------------------------------------------------------- #
async def enqueue(
    session,
    *,
    to_address: str,
    subject: str,
    body_text: str,
    body_html: str = "",
) -> EmailOutbox:
    row = EmailOutbox(
        id=new_id(PREFIX.get("email", "mail")),
        to_address=to_address[:320],
        subject=subject[:255],
        body_text=body_text,
        body_html=body_html,
        status="pending",
    )
    session.add(row)
    log.info("email_queued", to=to_address, subject=subject[:60])
    return row


# --------------------------------------------------------------------------- #
# 模板
# --------------------------------------------------------------------------- #
def welcome_email(settings: Settings, *, display_name: str, credits: int) -> tuple[str, str, str]:
    subject = f"欢迎加入 {settings.product_name}"
    text = (
        f"你好 {display_name}，\n\n"
        f"你的 {settings.product_name} 账号已创建，并已到账 {credits} 点体验额度。\n"
        f"额度可用于生成完整成片，单条 6 镜短片约消耗 300-400 点。\n\n"
        f"开始创作：{settings.public_url('/app/projects/new')}\n"
        f"使用文档：{settings.public_url('/docs')}\n\n"
        f"如有问题请回复本邮件或联系 {settings.support_email}。\n"
    )
    html = f"""\
<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;line-height:1.7;color:#111">
  <h2 style="margin:0 0 12px">欢迎加入 {settings.product_name}</h2>
  <p>你好 {display_name}，</p>
  <p>你的账号已创建，并已到账 <strong>{credits} 点</strong>体验额度。</p>
  <p>额度可用于生成完整成片，单条 6 镜短片约消耗 300-400 点。</p>
  <p>
    <a href="{settings.public_url("/app/projects/new")}"
       style="display:inline-block;background:#111;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none">
      开始创作
    </a>
  </p>
  <p style="color:#666;font-size:13px">如有问题请联系 {settings.support_email}</p>
</div>"""
    return subject, text, html


def credits_low_email(settings: Settings, *, display_name: str, balance: int) -> tuple[str, str, str]:
    subject = f"你的 {settings.product_name} 额度即将用尽"
    text = (
        f"你好 {display_name}，\n\n"
        f"你当前剩余 {balance} 点额度，可能不足以完成下一次生成。\n"
        f"升级套餐：{settings.public_url('/pricing')}\n"
    )
    html = f"""\
<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;line-height:1.7;color:#111">
  <h2 style="margin:0 0 12px">额度即将用尽</h2>
  <p>你当前剩余 <strong>{balance} 点</strong>，可能不足以完成下一次生成。</p>
  <p><a href="{settings.public_url("/pricing")}">查看套餐与充值</a></p>
</div>"""
    return subject, text, html


def job_failed_email(
    settings: Settings, *, display_name: str, title: str, reason: str
) -> tuple[str, str, str]:
    subject = f"《{title}》生成失败，额度已退还"
    text = (
        f"你好 {display_name}，\n\n"
        f"你的项目《{title}》生成失败，预扣额度已全额退还。\n"
        f"失败原因：{reason}\n\n"
        f"你可以重试：{settings.public_url('/app/projects')}\n"
    )
    html = f"""\
<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;line-height:1.7;color:#111">
  <h2 style="margin:0 0 12px">生成失败</h2>
  <p>你的项目《{title}》生成失败，<strong>预扣额度已全额退还</strong>。</p>
  <p style="color:#666">失败原因：{reason}</p>
  <p><a href="{settings.public_url("/app/projects")}">返回项目列表重试</a></p>
</div>"""
    return subject, text, html


# --------------------------------------------------------------------------- #
# 投递
# --------------------------------------------------------------------------- #
def _send_sync(settings: Settings, row: EmailOutbox) -> None:
    message = EmailMessage()
    message["Subject"] = row.subject
    message["From"] = settings.email.from_address
    message["To"] = row.to_address
    message.set_content(row.body_text or row.subject)
    if row.body_html:
        message.add_alternative(row.body_html, subtype="html")

    with smtplib.SMTP(settings.email.host, settings.email.port, timeout=20) as smtp:
        if settings.email.use_tls:
            smtp.starttls()
        username = settings.email.username
        if username:
            smtp.login(username, settings.email.password.get_secret_value())
        smtp.send_message(message)


async def deliver_pending(settings: Settings, *, limit: int = 20) -> int:
    """投递待发邮件。返回成功条数。"""
    import asyncio

    delivered = 0
    async with session_scope() as session:
        rows = list(
            (
                await session.execute(
                    select(EmailOutbox)
                    .where(EmailOutbox.status == "pending", EmailOutbox.attempts < MAX_ATTEMPTS)
                    .order_by(EmailOutbox.created_at)
                    .limit(limit)
                )
            ).scalars()
        )

        for row in rows:
            row.attempts += 1
            if not settings.email.enabled:
                # 开发模式：不真发，标记为已送达并打日志，保证业务流程可跑通
                row.status = "delivered"
                row.sent_at = utcnow()
                log.info(
                    "email_skipped_no_smtp",
                    to=row.to_address,
                    subject=row.subject[:60],
                    preview=(row.body_text or "")[:160].replace("\n", " "),
                )
                delivered += 1
                continue
            try:
                await asyncio.to_thread(_send_sync, settings, row)
                row.status = "delivered"
                row.sent_at = utcnow()
                row.last_error = ""
                delivered += 1
            except Exception as exc:
                row.last_error = f"{type(exc).__name__}: {exc}"[:500]
                if row.attempts >= MAX_ATTEMPTS:
                    row.status = "failed"
                log.warning(
                    "email_send_failed", to=row.to_address, attempt=row.attempts, error=row.last_error
                )

    return delivered
