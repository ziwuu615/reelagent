#!/usr/bin/env python
"""Worker 存活探针。

为什么单独写一个脚本，而不是塞一行 shell：
    * 镜像里没有 curl / wget / pgrep（slim 基础镜像故意保持精简），
      唯一的"必定存在"的工具就是 Python 自己；
    * worker 不监听端口，没有 HTTP 端点可以探活；
    * 一行式 python -c 想同时处理"扫描不到 /proc"与"没找到进程"两种情况
      可读性极差，而探针写错的代价是"容器被反复误杀"。

判定逻辑（顺序很重要）：
    1. 扫描 /proc/*/cmdline，找到命令行里含 `reelagent-worker` 的进程 → 存活（退出码 0）；
    2. 能扫 /proc 但没找到该进程 → **真的挂了**，退出码 1（让编排系统重启它）；
    3. 根本扫不了 /proc（某些运行时隐藏了 /proc）→ 退化为"1 号进程非僵尸即存活"。

能发现什么 / 不能发现什么：
    能发现：进程崩溃、被 OOM 杀掉、主循环异常退出；
    不能发现：进程活着但卡死（死锁、协程饿死）——这种情况由任务租约兜底：
    超过 lease_seconds 未续约，任务会被其它 worker 重新领取，不会永久丢单。
"""

from __future__ import annotations

import pathlib
import sys

#: 控制台入口名，出现在进程命令行里（/opt/venv/bin/reelagent-worker）
MARKER = b"reelagent-worker"
PROC = pathlib.Path("/proc")


def scan_processes() -> bool | None:
    """返回 True=找到 worker，False=确定没有，None=无法扫描 /proc。"""
    try:
        entries = list(PROC.glob("[0-9]*/cmdline"))
    except OSError:
        return None

    if not entries:
        # /proc 存在但看不到任何进程：多半是运行时隐藏了它，交给调用方降级处理
        return None

    for entry in entries:
        try:
            # 读别的进程的 cmdline 可能被拒绝（不同 uid / 已退出的进程）
            if MARKER in entry.read_bytes():
                return True
        except OSError:
            continue
    return False


def pid1_alive() -> bool:
    """1 号进程存在且不是僵尸。"""
    try:
        raw = (PROC / "1" / "stat").read_text(encoding="utf-8")
    except OSError:
        return False
    # /proc/<pid>/stat 格式：pid (comm) state ...
    # comm 可能含空格甚至括号，所以以**最后一个** ')' 为界切开，后面第一段才是状态
    rparen = raw.rfind(")")
    if rparen == -1:
        return False
    rest = raw[rparen + 1 :].split()
    if not rest:
        return False
    return rest[0] not in {"Z", "X"}


def main() -> int:
    found = scan_processes()
    if found is True:
        return 0
    if found is False:
        print("worker 进程不存在", file=sys.stderr)
        return 1
    # 无法扫描 /proc：降级判断
    if pid1_alive():
        return 0
    print("既找不到 worker 进程，1 号进程也不存活", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
