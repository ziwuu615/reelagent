"""生产配置自检脚本的行为。

这个脚本是上线流程里的第一道闸，所以它自己也要被测：
**报错要报得准、成功要真的干净**。如果它漏报，运维会以为配置没问题；
如果它误报，运维会开始无视它 —— 两种失效方式都会让这道闸失去意义。

用子进程跑真实脚本，而不是 import 后调 main()：脚本会读环境变量与 env 文件，
在测试进程里 import 会与 conftest 注入的配置互相污染。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.support import cleanup_tmp, make_tmp

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_production_config.py"

#: 环境变量里可能与 env 文件冲突的 REEL_* 项，跑脚本前清掉
_STRIP_PREFIX = "REEL_"

_GOOD = """
REEL_ENV=prod
REEL_BASE_URL=https://reelagent.example.com
REEL_SECURITY__SECRET_KEY=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ
REEL_SECURITY__API_KEY_PEPPER=ZYXWVUTSRQPONMLKJIHGFEDCBA9876543210zyxwvu
REEL_SECURITY__HTTPS_ONLY=true
REEL_SECURITY__ALLOWED_HOSTS=["reelagent.example.com"]
REEL_DATABASE__URL=postgresql+asyncpg://u:p@postgres:5432/reelagent
REEL_PROVIDERS__VIDEO__API_KEY=sk-video
REEL_PROVIDERS__LLM__PROVIDER=dashscope
REEL_PROVIDERS__LLM__MODEL=qwen-plus
REEL_PROVIDERS__LLM__API_KEY=sk-llm
REEL_PROVIDERS__TTS__API_KEY=sk-tts
REEL_OBSERVABILITY__LOG_SINK=both
REEL_OBSERVABILITY__LOG_RETENTION_DAYS=180
REEL_COMPLIANCE__IMAGE_MODERATION=dashscope
REEL_RATE_LIMIT__ENABLED=true
REEL_REDIS__ENABLED=true
REEL_REDIS__URL=redis://:p@redis:6379/0
"""


def _run(env_file: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in __import__("os").environ.items() if not k.upper().startswith(_STRIP_PREFIX)}
    # 保留必要项，避免 Windows 上 python 启动缺 PATH
    for keep in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE"):
        if keep in __import__("os").environ:
            env[keep] = __import__("os").environ[keep]
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--env-file", str(env_file)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
    )


def test_complete_config_passes_cleanly() -> None:
    tmp = make_tmp("cfgcheck")
    try:
        env_file = tmp / "good.env"
        env_file.write_text(_GOOD, encoding="utf-8")
        # LOG_FILE 指到临时目录，避免污染仓库 workspace
        env_file.write_text(
            _GOOD + f"REEL_OBSERVABILITY__LOG_FILE={tmp / 'logs' / 'app.log'}\n",
            encoding="utf-8",
        )

        result = _run(env_file)

        assert result.returncode == 0, f"期望干净通过，实际 rc={result.returncode}\n{result.stdout}"
        assert "配置校验通过" in result.stdout
        assert "可以上线" in result.stdout
    finally:
        cleanup_tmp(tmp)


def test_missing_items_are_reported_and_block() -> None:
    tmp = make_tmp("cfgcheck2")
    try:
        env_file = tmp / "bad.env"
        env_file.write_text(
            'REEL_ENV=prod\nREEL_BASE_URL=http://insecure.example.com\nREEL_SECURITY__ALLOWED_HOSTS=["*"]\n',
            encoding="utf-8",
        )

        result = _run(env_file)

        assert result.returncode == 1, "缺配置必须拦住启动"
        out = result.stdout
        assert "配置校验未通过" in out
        # 至少覆盖四类硬门槛，避免「只报了一部分就退出」
        assert "SECRET_KEY" in out
        assert "SQLite" in out
        assert "https" in out
        assert "视频供应商" in out
        assert "日志" in out
    finally:
        cleanup_tmp(tmp)


def test_risk_items_do_not_block_but_warn() -> None:
    """限流关掉之类的问题能启动，但必须被点出来（退出码 2）。"""
    tmp = make_tmp("cfgcheck3")
    try:
        env_file = tmp / "risky.env"
        env_file.write_text(
            _GOOD
            + f"REEL_OBSERVABILITY__LOG_FILE={tmp / 'logs' / 'app.log'}\n"
            + "REEL_RATE_LIMIT__ENABLED=false\n",
            encoding="utf-8",
        )

        result = _run(env_file)

        assert result.returncode == 2, f"期望风险退出码 2，实际 {result.returncode}\n{result.stdout}"
        assert "风险项" in result.stdout
        assert "限流" in result.stdout
    finally:
        cleanup_tmp(tmp)


def test_missing_env_file_is_reported() -> None:
    result = _run(Path("definitely-not-here.env"))
    assert result.returncode == 1
    assert "找不到配置文件" in result.stdout
