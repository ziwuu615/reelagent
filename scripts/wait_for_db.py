#!/usr/bin/env python
"""等待数据库就绪。

为什么需要它：
    `depends_on: condition: service_healthy` 只保证**容器**起来了，
    而 Postgres 官方镜像在 initdb 阶段会先起一个只监听 unix socket 的
    临时实例，之后才重启成正常监听 5432 的实例。于是存在一个时间窗：
    pg_isready 通过、TCP 连接立刻被拒（"Connection refused" / 57P03）。
    compose 里的 migrate 服务因此需要自己的重试循环，
    否则会以"数据库连不上"这种误导性的错误让整个 `up` 失败。

行为：
    周期性尝试 `SELECT 1`，成功则退出码 0；
    超时则打印最后一次的错误与排查提示，退出码 1。
    连接串取自项目配置（REEL_DATABASE__URL），因此对 asyncpg / aiosqlite 都适用。

用法：
    python scripts/wait_for_db.py                  # 默认 60s 超时
    python scripts/wait_for_db.py --timeout 120 --interval 1.5
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

# 允许从仓库根目录直接执行（scripts/ 不是包，需要把根目录放进 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _mask(url: str) -> str:
    """日志里不能出现口令。只保留 scheme / host / port / db。"""
    if url.startswith("sqlite"):
        # 单文件库本来就没有口令，原样输出即可
        # （urlunsplit 会把 sqlite:///./a.db 压成 sqlite:/./a.db，反而让人以为写错了）
        return url
    try:
        parts = urlsplit(url)
        netloc = parts.hostname or ""
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        if parts.username:
            netloc = f"{parts.username}:***@{netloc}"
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    except Exception:  # pragma: no cover - 兜底，绝不让打日志本身抛异常
        return "<无法解析的连接串>"


def ensure_sqlite_parent(url: str) -> None:
    """SQLite 是单文件库：父目录不存在时只会报一句很难懂的
    "unable to open database file"，这里提前把目录建好。"""
    if not url.startswith("sqlite"):
        return
    marker = "///"
    if marker not in url:
        return
    raw_path = url.split(marker, 1)[1].split("?", 1)[0]
    if not raw_path or raw_path == ":memory:":
        return
    Path(raw_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


async def wait_for_db(url: str, *, timeout_seconds: float, interval: float, verbose: bool = True) -> bool:
    started = time.monotonic()
    deadline = started + timeout_seconds
    attempt = 0
    last_error = ""

    # sqlite 是单文件库，没有"服务未就绪"的概念，直接确认能执行查询即可
    engine = create_async_engine(url, pool_pre_ping=True, future=True)
    try:
        while True:
            attempt += 1
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                elapsed = time.monotonic() - started
                if verbose:
                    print(
                        f"[wait_for_db] 数据库已就绪（第 {attempt} 次尝试，耗时 {elapsed:.1f}s）",
                        flush=True,
                    )
                return True
            except Exception as exc:  # 这里就是要吞掉所有连接类异常
                last_error = f"{type(exc).__name__}: {exc}"
                if time.monotonic() >= deadline:
                    break
                if verbose:
                    print(
                        f"[wait_for_db] 数据库暂不可用（第 {attempt} 次尝试）："
                        f"{last_error.splitlines()[0][:200]}；{interval:.1f}s 后重试",
                        flush=True,
                    )
                await asyncio.sleep(interval)
    finally:
        await engine.dispose()

    print(
        f"[wait_for_db] 等待超时（{timeout_seconds:.0f}s），最后一次错误：{last_error}",
        file=sys.stderr,
    )
    if url.startswith("sqlite"):
        print(
            "  排查建议：\n"
            "    1) 父目录是否存在且可写（SQLite 不会自动建库所在目录）\n"
            "    2) 连接串路径是否写成了容器内看不到的位置",
            file=sys.stderr,
        )
    else:
        print(
            "  排查建议：\n"
            "    1) 数据库容器是否健康：docker compose ps\n"
            "    2) 连接串主机名是否为 compose 服务名（同网络内应为 postgres:5432，而不是 localhost）\n"
            "    3) 口令是否与 POSTGRES_PASSWORD 一致（本脚本不会打印口令）",
            file=sys.stderr,
        )
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="等待数据库就绪后再继续（供 compose 的 migrate 服务使用）")
    parser.add_argument("--timeout", type=float, default=60.0, help="最长等待秒数，默认 60")
    parser.add_argument("--interval", type=float, default=2.0, help="重试间隔秒数，默认 2")
    parser.add_argument("--url", default=None, help="覆盖连接串；默认读项目配置 REEL_DATABASE__URL")
    parser.add_argument("--quiet", action="store_true", help="只在最终结果时输出")
    args = parser.parse_args(argv)

    if args.url:
        url = args.url
    else:
        from reelagent_cloud.config import get_settings

        settings = get_settings()
        url = settings.database.url

    # 提前建好 SQLite 的父目录，否则第一次连接必然失败且错误信息很难懂
    ensure_sqlite_parent(url)

    print(f"[wait_for_db] 目标：{_mask(url)}，超时 {args.timeout:.0f}s", flush=True)
    ok = asyncio.run(
        wait_for_db(url, timeout_seconds=args.timeout, interval=args.interval, verbose=not args.quiet)
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
