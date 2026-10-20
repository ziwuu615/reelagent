"""公开页面：落地页、定价、服务条款、隐私政策、状态页。

这些页面承担产品的「门面」职责，同时是 SEO 与转化入口，
因此它们对未登录用户开放，且不依赖任何鉴权。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...deps import AppSettings, DbSession
from ...domain.plans import all_plans, paid_plans
from ...observability import get_logger
from ...security import OptionalPrincipal
from ...services import jobs as jobs_svc
from ..templating import get_templates

log = get_logger(__name__)
router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing(request: Request, settings: AppSettings, principal: OptionalPrincipal):
    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "pages/landing.html",
        {
            "principal": principal,
            "paid_plans": paid_plans(),
            "styles": _style_showcase(),
            "faqs": _faqs(settings),
        },
    )


@router.get("/pricing", response_class=HTMLResponse, include_in_schema=False)
async def pricing(request: Request, settings: AppSettings, principal: OptionalPrincipal):
    return get_templates().TemplateResponse(
        request,
        "pages/pricing.html",
        {
            "principal": principal,
            "all_plans": all_plans(),
            "faqs": _faqs(settings),
        },
    )


@router.get("/terms", response_class=HTMLResponse, include_in_schema=False)
async def terms(request: Request, settings: AppSettings, principal: OptionalPrincipal):
    return get_templates().TemplateResponse(request, "pages/terms.html", {"principal": principal})


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy(request: Request, settings: AppSettings, principal: OptionalPrincipal):
    return get_templates().TemplateResponse(request, "pages/privacy.html", {"principal": principal})


@router.get("/status", response_class=HTMLResponse, include_in_schema=False)
async def status_page(request: Request, settings: AppSettings, session: DbSession):
    """状态页：把队列深度与依赖健康度公开出来，让用户知道系统是否正常。"""
    from .system import _run_checks

    checks = await _run_checks(settings)
    depths = await jobs_svc.queue_depths(session)
    overall = "ok" if all(c["ok"] for c in checks.values() if c.get("critical")) else "degraded"
    return get_templates().TemplateResponse(
        request,
        "pages/status.html",
        {"checks": checks, "depths": depths, "overall": overall, "principal": None},
    )


# --------------------------------------------------------------------------- #
# 内容
# --------------------------------------------------------------------------- #
def _style_showcase() -> list[dict[str, str]]:
    return [
        {"key": "cinematic", "name": "电影感", "desc": "戏剧性侧逆光、胶片颗粒、浅景深"},
        {"key": "anime", "name": "日式动画", "desc": "赛璐璐上色、干净线稿、高明度"},
        {"key": "cyberpunk", "name": "赛博朋克", "desc": "霓虹反射、湿地面高光、强对比"},
        {"key": "ink", "name": "水墨", "desc": "宣纸质感、墨韵晕染、大量留白"},
        {"key": "realistic", "name": "写实记录", "desc": "自然光、真实皮肤纹理、手持感"},
    ]


def _faqs(settings) -> list[dict[str, str]]:
    return [
        {
            "q": "生成一条成片要多久、多少点数？",
            "a": "默认 6-8 个镜头，通常 3-8 分钟完成。消耗约 300-500 点，"
            "具体取决于镜头数量与档位；生成失败会全额退还。",
        },
        {
            "q": "什么叫「跨镜头一致性」？为什么重要？",
            "a": "长视频最容易出的问题是角色「变脸」、场景漂移。"
            "我们会先为每个角色/场景生成一张基准设定图，之后所有分镜都以它为参考，"
            "并在每个镜头的提示词里嵌入固定的外观描述，从机制上避免漂移。",
        },
        {
            "q": "生成的片子可以商用吗？",
            "a": "付费套餐的成片可商用且无水印；免费版带水印，仅供体验。"
            "请同时遵守你所使用模型供应商的内容政策。",
        },
        {
            "q": "可以接自己的模型 Key 吗？",
            "a": "可以。专业版与工作室版支持自带密钥（BYOK），"
            "这样你可以直接使用自己的供应商额度，平台只收取编排与评测费用。",
        },
        {
            "q": "额度用不完会累积吗？",
            "a": "订阅套餐的月度额度在周期结束时重置，不结转到下个周期；单独购买的点数包不设过期时间。",
        },
        {
            "q": "有 API 吗？怎么联系你们？",
            "a": f"专业版及以上开放 REST API 与 API Key。商务与合作请联系 {settings.support_email}。",
        },
    ]
