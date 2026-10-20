"""模板单例。"""

from __future__ import annotations

from functools import lru_cache

from fastapi.templating import Jinja2Templates

from ..config import get_settings
from ..web import build_templates


@lru_cache(maxsize=1)
def get_templates() -> Jinja2Templates:
    return build_templates(get_settings())
