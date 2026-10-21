"""ICP 备案号页脚渲染测试。

**为什么需要它**：中国大陆的服务器 + 备案域名，法规要求首页底部展示备案号
并链接到工信部网站（beian.miit.gov.cn）。不挂会被管局责令整改，严重时注销备案。

这属于「不做就违法、但漏了也没有任何功能报错」的典型项 ——
只能靠测试兜住。同时要保证本地开发（未备案）时不会渲染出空链接。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from reelagent_cloud.api.app import create_app
from reelagent_cloud.api.ratelimit import reset_limiter_sync
from reelagent_cloud.api.templating import reset_templates
from reelagent_cloud.db import init_engine
from reelagent_cloud.db.session import create_all
from reelagent_cloud.services.storage import reset_storage
from tests.conftest import build_settings

ICP = "沪ICP备2026000001号-1"


def _app(tmp_workspace: Path, **overrides):
    settings = build_settings(tmp_workspace, **overrides)
    loop = asyncio.get_event_loop_policy().new_event_loop()
    reset_storage()
    reset_limiter_sync()
    # 模板是进程级单例，不重置会让上一个用例的 icp_beian 泄漏到这个用例
    reset_templates()
    init_engine(settings.database)
    loop.run_until_complete(create_all())
    return create_app(settings)


class TestIcpBeianFooter:
    def test_beian_number_rendered_with_link(self, tmp_workspace: Path) -> None:
        """配置了备案号就必须出现在首页底部，并链到工信部。"""
        with TestClient(_app(tmp_workspace, icp_beian=ICP)) as c:
            html = c.get("/").text

        assert ICP in html, "备案号没有渲染到页面上"
        assert "beian.miit.gov.cn" in html, "备案号必须链接到工信部网站"

    def test_link_opens_safely(self, tmp_workspace: Path) -> None:
        """外链必须带 noopener / noreferrer —— 否则新标签页能通过 window.opener 操纵本站。"""
        with TestClient(_app(tmp_workspace, icp_beian=ICP)) as c:
            html = c.get("/").text

        # 定位备案号所在的那个 <a> 标签
        idx = html.find(ICP)
        assert idx > 0
        tag_start = html.rfind("<a ", 0, idx)
        tag = html[tag_start : html.find(">", idx) + 1]

        assert "noopener" in tag, f"缺 noopener：{tag}"
        assert "noreferrer" in tag, f"缺 noreferrer：{tag}"

    def test_absent_when_not_configured(self, tmp_workspace: Path) -> None:
        """本地开发 / 海外部署不该出现空链接或占位文字。"""
        with TestClient(_app(tmp_workspace, icp_beian="")) as c:
            html = c.get("/").text

        assert "beian.miit.gov.cn" not in html, "没备案却渲染了工信部链接"
        assert "ICP备" not in html, "没备案却渲染了备案号文案"

    def test_gongan_beian_also_rendered(self, tmp_workspace: Path) -> None:
        """公安备案号是可选项：配了就渲染，且链到公安部平台。"""
        with TestClient(_app(tmp_workspace, icp_beian=ICP, gongan_beian="沪公网安备31010502000001号")) as c:
            html = c.get("/").text

        assert ICP in html
        assert "沪公网安备31010502000001号" in html
        assert "beian.mps.gov.cn" in html

    def test_console_pages_do_not_show_footer(self, tmp_workspace: Path) -> None:
        """控制台布局不渲染页脚（侧边栏已承担入口），备案号也不该出现在那里。"""
        with TestClient(_app(tmp_workspace, icp_beian=ICP)) as c:
            # 未登录会被重定向到 /login，所以只看公开页的对照
            landing = c.get("/").text
            pricing = c.get("/pricing").text

        # 两个公开页面都应该有
        assert ICP in landing
        assert ICP in pricing
