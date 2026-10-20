"""用真实存储服务验证中文文件名修复（不是单测，走完整的 put/get 路径）。

单测能证明 `sanitize_filename` 函数正确，但证明不了「服务层真的按这个
规则落盘并取回」。这里用真实的 LocalStorage 做一次往返：
    写入中文名 → 检查磁盘上的实际文件名 → 按 key 读回 → 校验内容一致
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reelagent_cloud.config import Settings  # noqa: E402
from reelagent_cloud.services.storage import build_key, get_storage, reset_storage  # noqa: E402

SAMPLES = [
    ("assets", "clue_便利店霓虹灯_6cd5.png"),
    ("assets", "clue_雨夜积水路面_5590.png"),
    ("final", "雨夜便利店_成片.mp4"),
    ("keyframes", "sc01_sh01_女孩特写.png"),
]


async def main() -> int:
    settings = Settings()
    reset_storage()
    storage = get_storage(settings)
    root = settings.storage.local_dir

    print(f"存储后端 : {type(storage).__name__}")
    print(f"存储根   : {root}\n")

    failures = 0
    for category, filename in SAMPLES:
        key = build_key(
            org_id="org_DEMO123",
            project_id="proj_DEMO456",
            category=category,
            filename=filename,
        )
        payload = f"内容-{filename}".encode()

        await storage.put_bytes(key, payload, content_type="application/octet-stream")
        on_disk = root / key
        back = await storage.get_bytes(key)

        readable = filename in key
        persisted = on_disk.exists()
        identical = back == payload

        ok = readable and persisted and identical
        failures += not ok
        print(f"  {'✓' if ok else '✗'} {filename}")
        print(f"      key        = {key}")
        print(f"      磁盘文件   = {on_disk.name}")
        print(f"      可读性保留 = {readable}   落盘 = {persisted}   往返一致 = {identical}")

        # 清理
        await storage.delete(key)

    print()
    print("=" * 62)
    print("全部通过：中文文件名在真实存储链路上完整保留" if not failures else f"失败 {failures} 项")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
