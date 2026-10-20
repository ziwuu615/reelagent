"""通用工具。

`stable_seed` 的存在是有原因的：Python 对 str 的内置 `hash()` 默认按进程随机化
（PYTHONHASHSEED），用它当随机种子会导致**同一份输入两次运行得到不同画面**。
这会直接摧毁"评测可回归 / CI 门禁"的前提——回归对比失去意义。
所以凡是需要确定性种子的地方，一律用这里的实现。
"""

from __future__ import annotations

import hashlib


def stable_seed(text: str, salt: str = "", mod: int = 99991) -> int:
    """跨进程、跨平台稳定的确定性种子。"""
    payload = f"{text}|{salt}".encode()
    return int(hashlib.md5(payload, usedforsecurity=False).hexdigest()[:8], 16) % mod
