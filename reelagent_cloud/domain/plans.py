"""套餐定义。

单一事实来源：定价页、额度校验、Stripe Price 映射都读这里，
避免「官网写一个价、后端校验另一个价」这类经典事故。

## 点数与月费的关系（改价前必读）

扣费侧（`services/credits.py::usd_to_credits`）：

    credits = ceil(供应商成本USD × markup_multiplier × credits_per_usd)

默认 3.0 × 100 = 300，即 **$1 供应商成本扣 300 点**。

于是「3 倍毛利」等价于：300 点的售价 ≥ 3 × $1 × 7.2 元 = 21.6 元。
两边同除，markup 被约掉，得到一个**与倍率无关**的下限：

    元/点 ≥ CNY_PER_USD / credits_per_usd = 7.2 / 100 = 0.072

本文件各套餐按 `monthly_credits × 0.072` 定价（216 / 1080 / 4320 元）。
额度阶梯本身就是 ×1 / ×5 / ×20，价格阶梯与之同比，因此三档毛利率一致。

⚠️ 历史坑：原定价 49 / 199 / 699 对应 0.0163 / 0.0133 / 0.0117 元/点，
只能收回供应商成本的 23%~49%，**而且套餐越大亏得越多**（越贵反而越便宜）。
实测算例：一条 7 镜 premium 成片供应商成本 $2.68 ≈ 19.3 元，
创作者版按点数只卖 13.13 元 —— 卖一条亏 6 元。
`tests/test_plan_pricing_margin.py` 现在把这条不变量钉死了。

对外只展示「点数」，不暴露供应商真实成本结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .enums import PlanTier


@dataclass(frozen=True)
class Plan:
    tier: PlanTier
    name: str
    tagline: str
    price_cny: int  # 每月价格（元），0 表示免费
    monthly_credits: int  # 每月发放额度
    max_shots_per_project: int
    max_concurrent_jobs: int
    max_projects: int  # -1 表示不限
    max_org_members: int
    support: str
    highlights: list[str] = field(default_factory=list)
    #: 是否可使用高保真档位（premium）
    allow_premium_tier: bool = True
    #: 优先级（数字越大越优先调度）
    priority: int = 0
    #: 是否可商用
    commercial_use: bool = True

    @property
    def is_free(self) -> bool:
        return self.price_cny == 0

    @property
    def credits_per_cny(self) -> float:
        return 0.0 if self.is_free else self.monthly_credits / self.price_cny


PLANS: dict[PlanTier, Plan] = {
    PlanTier.FREE: Plan(
        tier=PlanTier.FREE,
        name="体验版",
        tagline="先跑通一条完整成片，再决定要不要付费",
        price_cny=0,
        monthly_credits=500,
        max_shots_per_project=6,
        max_concurrent_jobs=1,
        max_projects=3,
        max_org_members=1,
        support="社区支持",
        highlights=[
            "每月 500 点数",
            "单条成片最多 6 个镜头",
            "同时 1 个生成任务",
            "保留 3 个项目",
            "成片带水印",
        ],
        allow_premium_tier=False,
        commercial_use=False,
    ),
    PlanTier.STARTER: Plan(
        tier=PlanTier.STARTER,
        name="创作者版",
        tagline="给做号的人：稳定出片，去掉水印",
        price_cny=216,
        monthly_credits=3_000,
        max_shots_per_project=8,
        max_concurrent_jobs=1,
        max_projects=20,
        max_org_members=2,
        support="邮件支持（48h）",
        highlights=[
            "每月 3,000 点数",
            "单条成片最多 8 个镜头",
            "去除水印，可商用",
            "保留 20 个项目",
            "高保真档位（Premium）",
        ],
        priority=1,
    ),
    PlanTier.PRO: Plan(
        tier=PlanTier.PRO,
        name="专业版",
        tagline="给小团队：并发、批量、可试用 API",
        price_cny=1080,
        monthly_credits=15_000,
        max_shots_per_project=12,
        max_concurrent_jobs=3,
        max_projects=-1,
        max_org_members=5,
        support="邮件支持（24h）+ API 文档",
        highlights=[
            "每月 15,000 点数",
            "单条成片最多 12 个镜头",
            "同时 3 个生成任务",
            "项目数不限",
            "开放 REST API + API Key",
            "品牌风格预设",
        ],
        priority=2,
    ),
    PlanTier.STUDIO: Plan(
        tier=PlanTier.STUDIO,
        name="工作室版",
        tagline="给工作室：高并发与优先调度",
        price_cny=4320,
        monthly_credits=60_000,
        max_shots_per_project=12,
        max_concurrent_jobs=8,
        max_projects=-1,
        max_org_members=20,
        support="专属支持渠道（4h）",
        highlights=[
            "每月 60,000 点数",
            "同时 8 个生成任务（优先调度）",
            "团队成员 20 人",
            "自定义风格与基准图上传",
            "用量报表导出",
            "专属支持渠道",
        ],
        priority=3,
    ),
}

#: 注册赠送额度
SIGNUP_GRANT_CREDITS = PLANS[PlanTier.FREE].monthly_credits

#: 失败任务的自动退还比例（1.0 = 全额退还）
FAILURE_REFUND_RATIO = 1.0


def get_plan(tier: PlanTier | str) -> Plan:
    if isinstance(tier, str):
        tier = PlanTier(tier)
    return PLANS[tier]


def all_plans() -> list[Plan]:
    return [PLANS[t] for t in (PlanTier.FREE, PlanTier.STARTER, PlanTier.PRO, PlanTier.STUDIO)]


def paid_plans() -> list[Plan]:
    return [p for p in all_plans() if not p.is_free]
