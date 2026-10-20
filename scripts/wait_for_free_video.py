"""等待免费视频模型恢复可用。

背景：智谱的 CogVideoX-Flash 是官方免费模型，高峰期会返回
`429 / code 1305 该模型当前访问量过大`。这是**瞬时过载**，不是模型不可用。
但「什么时候恢复」无法预知，人工反复重试很浪费精力。

这个脚本周期性探测，一旦模型接受任务就退出（0=可用，1=超时）。

    注意探测方式：免费模型没有「查询可用性」的接口，唯一可靠的判据是真的提交
    一个生成任务。因此**每次探测成功都会创建一个用不上的免费任务**。
    默认间隔设为 300 秒就是为了避免无谓消耗；调小间隔请自行权衡。

为什么值得单独写：
    免费模型的 429 很容易被误判成「模型不能用」，进而促使人去用付费模型 ——
    那会在无感知的情况下产生费用。把等待自动化，就不会有人因为不耐烦而掏钱。

用法：
    python scripts/wait_for_free_video.py                  # 默认等 30 分钟
    python scripts/wait_for_free_video.py --minutes 120    # 等 2 小时
    python scripts/wait_for_free_video.py --interval 30    # 每 30 秒试一次
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://open.bigmodel.cn/api/paas/v4"
MODEL = "cogvideox-flash"


def load_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("REEL_PROVIDERS__LLM__API_KEY=") and line.split("=", 1)[1].strip():
            return line.split("=", 1)[1].strip()
    raise SystemExit("找不到 Key")


def probe(key: str) -> tuple[bool, str]:
    """提交一个最小任务。返回 (是否可用, 说明)。

    免费模型提交不产生费用，所以直接试提交是最可靠的可用性判据 ——
    比任何间接指标都准。
    """
    payload = json.dumps({"model": MODEL, "prompt": "可用性探测", "with_audio": False}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/videos/generations",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return True, str(body.get("id") or body.get("task_id") or "已接受")
    except urllib.error.HTTPError as exc:
        detail = ""
        # 读取错误体只为给出更清晰的提示，失败也不该掩盖状态码
        with contextlib.suppress(Exception):
            detail = exc.read().decode("utf-8", "ignore")[:180]
        if exc.code == 429:
            return False, "429 过载"
        return False, f"HTTP {exc.code} {detail}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="等待免费视频模型恢复")
    parser.add_argument("--minutes", type=float, default=60.0, help="最长等待分钟数")
    parser.add_argument("--interval", type=float, default=300.0, help="探测间隔秒数")
    args = parser.parse_args()

    key = load_key()
    deadline = time.time() + args.minutes * 60
    attempt = 0

    print(f"等待 {MODEL} 恢复（最长 {args.minutes:g} 分钟，每 {args.interval:g} 秒探测一次）")
    print("提示：免费模型的 429 是瞬时过载，不是不可用；请勿因此改用付费模型。\n")

    while time.time() < deadline:
        attempt += 1
        ok, note = probe(key)
        stamp = time.strftime("%H:%M:%S")
        if ok:
            print(f"[{stamp}] 第 {attempt} 次：✓ 已恢复（task={note}）")
            print("\n可以重新生成视频片段了。")
            return 0
        remaining = int(deadline - time.time())
        print(f"[{stamp}] 第 {attempt} 次：{note}，剩余 {remaining // 60} 分 {remaining % 60} 秒")
        time.sleep(args.interval)

    print(f"\n✗ 等待超时（{args.minutes:g} 分钟）。免费模型仍在过载。")
    print("  选择：继续等（可加大 --minutes），或改用付费模型 cogvideox-3（约 1.5 元/5 秒）。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
