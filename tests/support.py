"""测试辅助。

注意：测试用的临时工作区放在**仓库内部**（tests/_tmp），而不是系统临时目录。
这样做的原因有两点：
  1. 项目本身的默认 workspace 就在仓库内，测试应当与真实运行路径一致；
  2. 某些受限环境（容器 / 沙箱 / 只读系统盘）不允许在系统临时目录建 sqlite 文件，
     放在仓库内可以避免"测试全挂但代码没问题"的假故障。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

_TMP_ROOT = Path(__file__).resolve().parent / "_tmp"


def make_tmp(prefix: str = "t") -> Path:
    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = _TMP_ROOT / f"{prefix}_{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup_tmp(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def purge_tmp_root() -> None:
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)
