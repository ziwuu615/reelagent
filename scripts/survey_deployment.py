"""部署就绪度调查：仓库里到底有哪些部署相关的东西。

只报告实际存在与否，不做「应该有」的假设。
每一项都给出证据（文件路径 / 配置字段 / 代码位置）。
"""

from __future__ import annotations

import pathlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def exists(rel: str) -> str:
    p = ROOT / rel
    if p.is_file():
        return f"有  ({p.stat().st_size // 1024} KB)"
    if p.is_dir():
        n = len(list(p.iterdir()))
        return f"有  (目录，{n} 项)"
    return "缺"


print("=" * 82)
print("1. 部署产物")
print("=" * 82)
for rel in (
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "deploy",
    "docs/DEPLOYMENT.md",
    "docs/OPERATIONS.md",
    "docs/ARCHITECTURE.md",
    "scripts/smoke.py",
    "scripts/smoke_http.py",
    "scripts/wait_for_db.py",
    "scripts/worker_healthcheck.py",
    "alembic.ini",
    "reelagent_cloud/db/migrations",
):
    print(f"  {rel:<44} {exists(rel)}")

print()
print("=" * 82)
print("2. 生产环境依赖（数据库 / 存储 / 缓存）")
print("=" * 82)
from reelagent_cloud.config import Settings  # noqa: E402  # noqa: E402

s = Settings()
print(f"  数据库 URL    : {s.database.url}")
print(f"  连接池        : {getattr(s.database, 'pool_size', '?')}")
print(f"  存储后端      : {s.storage.backend}")
print(f"  存储根目录    : {getattr(s.storage, 'root', '?')}")
print(f"  Redis         : {getattr(s.redis, 'url', '(未配置)')}")
print(f"  环境          : {s.env}")

print()
print("  数据库驱动是否支持 Postgres:")
try:
    import asyncpg  # noqa: F401

    print("    asyncpg 已安装 -> 可用 Postgres")
except ImportError:
    print("    asyncpg 未安装 -> 当前只能 SQLite（生产需要装）")

print()
print("=" * 82)
print("3. 安全与运维配置项")
print("=" * 82)
sec = s.security
for field in (
    "secret_key",
    "session_cookie",
    "csrf_cookie",
    "secure_cookies",
    "allowed_hosts",
    "trusted_proxies",
):
    if hasattr(sec, field):
        value = getattr(sec, field)
        shown = "(已设置)" if "secret" in field and value else value
        print(f"  security.{field:<22} = {shown}")

print()
print("=" * 82)
print("4. 健康检查与可观测性")
print("=" * 82)
router_src = pathlib.Path(ROOT / "reelagent_cloud/api/routes/api.py")
if router_src.exists():
    text = router_src.read_text(encoding="utf-8")
    for needle in ("/healthz", "/readyz", "/metrics", "/version"):
        print(f"  {needle:<12} {'有' if needle.strip('/') in text else '缺'}")
print(f"  结构化日志    : {'有' if (ROOT / 'reelagent_cloud/observability').exists() else '缺'}")
print(
    f"  Prometheus    : {'有' if 'prometheus' in (ROOT / 'pyproject.toml').read_text(encoding='utf-8') else '缺'}"
)
