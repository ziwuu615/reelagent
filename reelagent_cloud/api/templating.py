"""模板单例。

## 为什么需要「覆盖配置」

`create_app(settings)` 明明接受一份配置，但模板层曾经无视它 ——
这里写死了 `build_templates(get_settings())`，用的是全局单例。

后果不只是测试不方便：任何「用一份配置创建 app」的调用方（测试、
未来的多租户/多地域实例）拿到的模板上下文都是**进程级默认值**。
表现为「传了 icp_beian 却没渲染」「改了 product_name 页面还是旧名字」，
而且因为 lru_cache 的存在，即使改对了也会被第一个调用者冻结住。

现在改成：`create_app` 调用 `set_template_settings(settings)` 显式覆盖，
未覆盖时仍回落到 `get_settings()`，保持原有行为不变。
"""

from __future__ import annotations

from functools import lru_cache

from fastapi.templating import Jinja2Templates

from ..config import Settings, get_settings
from ..web import build_templates

#: 显式覆盖的模板配置。为 None 时回落到全局 get_settings()。
_override: Settings | None = None


def set_template_settings(settings: Settings) -> None:
    """指定后续 get_templates() 使用的配置，并清掉已有缓存。

    create_app 每次创建应用时调用 —— 这样同一进程里先后创建的两个 app
    不会互相串用对方的 brand/备案号等展示配置。
    """
    global _override
    _override = settings
    get_templates.cache_clear()


def reset_templates() -> None:
    """清空缓存与覆盖，回到「用全局配置」的初始状态。

    测试之间需要它：否则上一个用例设置的 icp_beian 会泄漏到下一个用例，
    出现「单独跑通过、一起跑失败」的诡异现象。
    """
    global _override
    _override = None
    get_templates.cache_clear()


@lru_cache(maxsize=1)
def get_templates() -> Jinja2Templates:
    return build_templates(_override or get_settings())
