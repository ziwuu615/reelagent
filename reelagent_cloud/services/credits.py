"""额度账本。

**这是全项目最需要严谨的一个模块**——它直接对应钱。

设计：
  1. `organizations.credits_balance` 是**快照**，`ledger_entries` 是**事实**（只追加）。
     两者在同一事务内更新，任何额度变动都留下一条带 `balance_after` 的记录。
  2. 更新前对 org 行加 `SELECT ... FOR UPDATE` 排他锁，保证并发下不会丢更新。
  3. 所有入账/出账都支持 `idempotency_key`：同一外部单号（如 Stripe invoice）
     重复投递只生效一次。
  4. `reconcile()` 校验「账本求和 == 余额快照」，这是发现 bug 的兜底断言。

额度口径：`credits = ceil(provider_cost_usd × markup_multiplier × credits_per_usd)`
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from reelagent.llm.base import price_for
from reelagent.providers.base import (
    IMAGE_UNIT_COST,
    estimate_tts_cost,
    estimate_video_cost,
)
from reelagent.routing import TIER_PREMIUM, TIER_STANDARD, tier_spec

from ..config import Settings
from ..db.base import utcnow
from ..db.models import LedgerEntry, Organization
from ..domain.enums import LedgerKind
from ..domain.errors import NotFound, PaymentRequired, ValidationFailed
from ..domain.ids import PREFIX, new_id
from ..observability import get_logger

log = get_logger(__name__)


# --------------------------------------------------------------------------- #
# 计价
# --------------------------------------------------------------------------- #
def usd_to_credits(settings: Settings, usd: float) -> int:
    """把供应商成本换算成向用户扣的点数。向上取整，避免出现 0 点消耗。"""
    if usd <= 0:
        return 0
    rate = settings.billing.markup_multiplier * settings.billing.credits_per_usd
    return max(1, math.ceil(usd * rate))


def credits_to_usd(settings: Settings, credits: int) -> float:
    rate = settings.billing.markup_multiplier * settings.billing.credits_per_usd
    return round(credits / rate, 6) if rate else 0.0


@dataclass(frozen=True)
class CreditQuote:
    credits: int
    provider_cost_usd: float
    breakdown: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "estimated_credits": self.credits,
            "breakdown": self.breakdown,
        }


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #
async def get_balance(session: AsyncSession, org_id: str) -> int:
    value = await session.scalar(select(Organization.credits_balance).where(Organization.id == org_id))
    if value is None:
        raise NotFound("组织不存在")
    return int(value)


async def history(
    session: AsyncSession, org_id: str, *, limit: int = 50, offset: int = 0
) -> list[LedgerEntry]:
    rows = await session.execute(
        select(LedgerEntry)
        .where(LedgerEntry.org_id == org_id)
        .order_by(LedgerEntry.created_at.desc())
        .limit(min(limit, 200))
        .offset(offset)
    )
    return list(rows.scalars())


async def reconcile(session: AsyncSession, org_id: str) -> dict[str, Any]:
    """对账：账本求和必须等于余额快照。不等即存在 bug，应当立即告警。"""
    ledger_sum = await session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(LedgerEntry.org_id == org_id)
    )
    balance = await get_balance(session, org_id)
    ledger_sum = int(ledger_sum or 0)
    return {
        "org_id": org_id,
        "ledger_sum": ledger_sum,
        "balance": balance,
        "consistent": ledger_sum == balance,
        "drift": balance - ledger_sum,
    }


# --------------------------------------------------------------------------- #
# 写入
# --------------------------------------------------------------------------- #


async def _find_idempotent(
    session: AsyncSession, org_id: str, idempotency_key: str | None
) -> LedgerEntry | None:
    if not idempotency_key:
        return None
    return (
        await session.execute(
            select(LedgerEntry).where(
                LedgerEntry.org_id == org_id,
                LedgerEntry.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()


async def _apply(
    session: AsyncSession,
    *,
    org_id: str,
    kind: LedgerKind,
    amount: int,
    description: str = "",
    project_id: str | None = None,
    job_id: str | None = None,
    external_ref: str = "",
    idempotency_key: str | None = None,
    created_by: str | None = None,
    meta: dict[str, Any] | None = None,
    allow_negative: bool = False,
) -> LedgerEntry:
    """账本变更的唯一实现。所有公开函数最终都走这里。

    **并发正确性靠单条原子 UPDATE，而不是锁**：
    「先 SELECT 读余额，再 UPDATE 写回」是经典的 read-modify-write 竞态。
    即使加进程内锁也不安全——锁会在事务提交前释放，后一个协程仍可能读到
    未提交的旧值（这个坑在并发测试里被真实抓到）。

    改成 `SET credits_balance = credits_balance + :amount ... RETURNING`：
    数据库自己保证「加法 + 读取新值」不可分割，PostgreSQL 与 SQLite(≥3.35)
    都支持，因此既不需要行锁，也不需要按方言分叉。
    """
    existing = await _find_idempotent(session, org_id, idempotency_key)
    if existing is not None:
        log.info(
            "ledger_idempotent_hit",
            org_id=org_id,
            idempotency_key=idempotency_key,
            entry_id=existing.id,
        )
        return existing

    stmt = (
        update(Organization)
        .where(Organization.id == org_id)
        .values(
            credits_balance=Organization.credits_balance + amount,
            updated_at=utcnow(),
        )
        .returning(Organization.credits_balance)
    )
    if amount < 0 and not allow_negative:
        # 余额不足时该 UPDATE 匹配不到行，从而在数据库层面拒绝透支
        stmt = stmt.where(Organization.credits_balance + amount >= 0)

    row = (await session.execute(stmt)).first()
    if row is None:
        # 区分「组织不存在」与「余额不足」——两者的处理方式完全不同
        org = await session.get(Organization, org_id)
        if org is None:
            raise NotFound("组织不存在")
        raise PaymentRequired(
            f"额度不足：本次需要 {abs(amount)} 点，当前可用 {org.credits_balance} 点",
            required=abs(amount),
            available=int(org.credits_balance),
        )

    new_balance = int(row[0])

    entry = LedgerEntry(
        id=new_id(PREFIX["ledger"]),
        org_id=org_id,
        kind=kind,
        amount=int(amount),
        balance_after=new_balance,
        description=description[:255],
        project_id=project_id,
        job_id=job_id,
        external_ref=external_ref[:128],
        idempotency_key=idempotency_key,
        created_by=created_by,
        meta=meta or {},
    )
    session.add(entry)

    # 显式 flush：会话配置为 autoflush=False，否则同一会话内后续的
    # `get_balance()` 会读到数据库里的旧值（账本已是新值，余额还是旧值 -> 不一致）。
    await session.flush()

    log.info(
        "ledger_applied",
        org_id=org_id,
        kind=kind.value,
        amount=amount,
        balance_after=new_balance,
        description=description[:60],
    )
    return entry


# --------------------------------------------------------------------------- #
# 对外语义化接口
# --------------------------------------------------------------------------- #
async def grant(
    session: AsyncSession,
    *,
    org_id: str,
    amount: int,
    kind: LedgerKind = LedgerKind.PLAN_GRANT,
    description: str = "",
    idempotency_key: str | None = None,
    external_ref: str = "",
    created_by: str | None = None,
    meta: dict[str, Any] | None = None,
) -> LedgerEntry:
    if amount <= 0:
        raise ValueError("发放额度必须为正数")
    return await _apply(
        session,
        org_id=org_id,
        kind=kind,
        amount=amount,
        description=description or f"发放 {amount} 点",
        idempotency_key=idempotency_key,
        external_ref=external_ref,
        created_by=created_by,
        meta=meta,
    )


async def consume(
    session: AsyncSession,
    *,
    org_id: str,
    amount: int,
    description: str = "",
    project_id: str | None = None,
    job_id: str | None = None,
    idempotency_key: str | None = None,
    meta: dict[str, Any] | None = None,
) -> LedgerEntry:
    if amount <= 0:
        raise ValueError("扣减额度必须为正数，实际扣减量请传正数")
    return await _apply(
        session,
        org_id=org_id,
        kind=LedgerKind.CONSUME,
        amount=-int(amount),
        description=description or f"生成消耗 {amount} 点",
        project_id=project_id,
        job_id=job_id,
        idempotency_key=idempotency_key,
        meta=meta,
    )


async def refund(
    session: AsyncSession,
    *,
    org_id: str,
    amount: int,
    description: str = "",
    project_id: str | None = None,
    job_id: str | None = None,
    idempotency_key: str | None = None,
) -> LedgerEntry:
    if amount <= 0:
        return await _apply(session, org_id=org_id, kind=LedgerKind.REFUND, amount=0, description="无需退还")
    return await _apply(
        session,
        org_id=org_id,
        kind=LedgerKind.REFUND,
        amount=int(amount),
        description=description or f"失败退还 {amount} 点",
        project_id=project_id,
        job_id=job_id,
        idempotency_key=idempotency_key,
    )


async def adjust(
    session: AsyncSession,
    *,
    org_id: str,
    amount: int,
    reason: str,
    created_by: str,
) -> LedgerEntry:
    """人工调整（客服补偿、扣罚）。允许扣成负数需要显式确认。"""
    return await _apply(
        session,
        org_id=org_id,
        kind=LedgerKind.ADJUSTMENT,
        amount=int(amount),
        description=f"人工调整：{reason}",
        created_by=created_by,
        allow_negative=False,
    )


# --------------------------------------------------------------------------- #
# 校验与预估
# --------------------------------------------------------------------------- #
async def ensure_sufficient(session: AsyncSession, org_id: str, required: int) -> None:
    if required <= 0:
        return
    balance = await get_balance(session, org_id)
    if balance < required:
        raise PaymentRequired(required=required, available=balance)


#: 单镜平均时长（秒）。来自真实运行观测：3 镜项目的视频总时长 10.0 秒，
#: 即 3.33 秒/镜。取 3.5 略微保守。实际时长由剧本决定，这里只用于预估。
AVG_SHOT_DURATION_S = 3.5

#: 引擎预算相对预扣额的余量系数。
#:
#: 预扣额本身已是「估算 × 1.15」，这里再给一点余量，目的是保证
#: **引擎允许花的钱不少于我们向用户收的钱**。
#:
#: 踩过的坑：引擎预算原本是固定常量 `job_budget_usd = 2.0`，与镜头数、
#: 档位都无关。5 镜 premium 的实际成本约 $2.2，于是第 5 镜被路由**静默
#: 降级为标准档** —— 用户按 premium 付费，最后一镜却拿到标准画质。
#: 计费和交付必须由同一个模型推导，否则一定对不上。
BUDGET_HEADROOM = 1.4

#: 单个项目允许的最高成本（USD）。这是**产品决策**，不是外部事实。
#:
#: 超过此值在**创建项目时直接拒绝**，而不是先收钱、再在生成中途降级。
#: 让用户明确知道"太贵了做不了"，比悄悄降低质量诚实。
#:
#: 取 8.0 的依据：平台最大 12 镜 × premium 的估算约 $4.5，
#: 留出余量后仍可承接最贵的合法请求。
MAX_PROJECT_COST_USD = 8.0

#: LLM 每次调用的平均 token 量。来自 3 镜实跑：5 次调用共 4836 token
#: （≈970 token/次）。planner 一次、每镜导演一次、评审一次。
AVG_LLM_TOKENS_PER_CALL = 1000

#: 每镜 TTS 字符数。来自 3 镜实跑：40 字符 / 3 镜 ≈ 13 字符。
AVG_TTS_CHARS_PER_SHOT = 15

#: 预估安全系数：实际用量会因剧本、重试、线索数而波动，
#: 预扣必须略高于期望值，否则用户会在跑到一半时被判定额度不足。
QUOTE_SAFETY_FACTOR = 1.15


def quote_project(
    settings: Settings,
    *,
    shots: int,
    allow_premium: bool,
    tier: str | None = None,
) -> CreditQuote:
    """按镜头数预估消耗，用于「开始生成前」的额度预检与界面提示。

    ## 为什么不再用一个「每镜均价」常量

    早期实现是 `avg_provider_cost_per_shot_usd: float = 0.14` —— 一个凭印象填的数。
    用真实运行数据核对后发现它错得很远：

        standard 单镜真实成本  $0.31   （0.14 低估 121%）
        premium  单镜真实成本  $0.56   （0.14 低估 298%）

    之所以没出事，是因为上面还叠了 `× 1.8` 的上浮和 `× 1.15` 安全系数 ——
    **是巧合掩盖了错误，不是估对了。**

    现在改为**按已验证的官方单价逐项计算**：视频用
    `estimate_video_cost(时长, 分辨率)`（分辨率由档位决定，价差 2 倍），
    图像/LLM/TTS 各按单价与观测量计算。这样单价一改，预估自动跟上，
    不会再出现「常量与价格表脱节」。

    `tier` 是用户实际选择的档位：
      - `express`  -> 720P + 优速版模型（同画质，快 63%，贵 54%）
      - `premium`  -> 720P
      - `standard` -> 480P
      - `draft`    -> 不调用视频模型，视频成本为 0
      - `None`（auto）-> 按套餐允许的最高档估算。因为路由可能把难镜头
        升到 premium，宁可多预留也不要让用户跑到一半断粮。

    **档位到参数的映射只查 `routing.TIERS` 一处**，不在这里重写 if/elif。
    这里曾经手写过一份 `if tier == TIER_EXPRESS: ... elif tier == TIER_PREMIUM: ...`
    的分支，等于把档位知识复制了第二份 —— 引擎加档位时计价侧不会自动跟上。
    """
    # auto（None）按套餐允许的最高档估；其余按用户实际选的档位。
    spec = tier_spec(tier, default=TIER_PREMIUM if allow_premium else TIER_STANDARD)

    # 视频：按真实分辨率与平均时长计价；draft 不调用模型，成本为 0
    video_cost = (
        0.0
        if not spec.use_video_model
        else shots * estimate_video_cost(AVG_SHOT_DURATION_S, spec.resolution, fast=spec.use_fast_model)
    )

    # 图像：每镜一张关键帧 + 项目级的线索基准图。
    #
    # 基准图数量**不随镜头数线性增长** —— 它取决于 planner 找到几条线索。
    # 实测：3 镜用了 4 张基准图，1 镜也用了 3–4 张。
    # 早期写法 `max(2, shots // 2)` 会算成 2 张，系统性低估 2 张左右的成本。
    base_images = min(4, settings.max_base_images)
    image_cost = (shots + base_images) * IMAGE_UNIT_COST

    # LLM：planner（1 次）+ 每镜导演（1 次）+ 评审（1 次）
    llm_calls = shots + 2
    llm_model = settings.providers.llm.model or "qwen-plus"
    price_in, price_out = price_for(llm_model)
    # 输入/输出各占一半的粗略假设；量级极小，不影响总额判断
    llm_cost = llm_calls * AVG_LLM_TOKENS_PER_CALL * (price_in + price_out) / 2 / 1_000_000

    # TTS：按字符计价，量极小但仍如实计入
    tts_cost = estimate_tts_cost("字" * (shots * AVG_TTS_CHARS_PER_SHOT))

    total_usd = (video_cost + image_cost + llm_cost + tts_cost) * QUOTE_SAFETY_FACTOR

    return CreditQuote(
        credits=usd_to_credits(settings, total_usd),
        provider_cost_usd=round(total_usd, 4),
        breakdown={
            "image": usd_to_credits(settings, image_cost * QUOTE_SAFETY_FACTOR),
            "video": usd_to_credits(settings, video_cost * QUOTE_SAFETY_FACTOR),
            "llm": usd_to_credits(settings, llm_cost * QUOTE_SAFETY_FACTOR),
            "tts": usd_to_credits(settings, tts_cost * QUOTE_SAFETY_FACTOR),
        },
    )


def budget_for(settings: Settings, quote: CreditQuote) -> float:
    """推导本次任务应给引擎的预算上限。

    为什么必须由预扣额推导，而不是用固定常量：
    引擎在预算不足时会把 premium **静默降级**为标准档。如果预算与预扣额
    脱钩，就会出现「收了 premium 的钱、给了 standard 的货」。

    规则：
      - 至少等于预扣额 × `BUDGET_HEADROOM`（引擎允许花的不低于我们收的）
      - 不低于配置里的 `job_budget_usd`（保留原来的下限保护）
    """
    derived = quote.provider_cost_usd * BUDGET_HEADROOM
    return max(float(settings.job_budget_usd), round(derived, 4))


def assert_affordable(settings: Settings, quote: CreditQuote) -> None:
    """在**创建项目时**判断成本是否可接受。

    不可接受就当场拒绝，而不是先扣钱、再在生成中途降级画质。
    让用户清楚知道「这个配置太贵，做不了」，比悄悄降质诚实。
    """
    limit = min(MAX_PROJECT_COST_USD, float(settings.job_budget_usd) * 4)
    if quote.provider_cost_usd > limit:
        raise ValidationFailed(
            f"该配置的预估成本 ${quote.provider_cost_usd:.2f} 超出单项目上限 "
            f"${limit:.2f}，请减少镜头数或降低档位"
        )
