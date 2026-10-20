"""`reelagent-server` 入口：启动 HTTP 服务。"""

from __future__ import annotations

import argparse
import sys

from .api.app import create_app
from .config import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="reelagent-server",
        description="ReelAgent HTTP 服务",
    )
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - 容器内需要监听全网卡
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="开发模式：代码变更自动重载")
    parser.add_argument("--workers", type=int, default=1, help="worker 进程数（生产建议 CPU 核数）")
    args = parser.parse_args()

    settings = get_settings()
    app = create_app(settings)

    import uvicorn

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=1 if args.reload else args.workers,
        log_config=None,  # 交给 structlog 统一处理
        access_log=False,  # 我们自己记录访问日志（已带 request_id 与脱敏）
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
