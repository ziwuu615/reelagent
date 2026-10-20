"""生成服务器的生产 .env。

原则：
  * 所有密钥**在本地随机生成**，不打印到终端、不写进对话
  * 百炼 Key 从本地 .env 读取后原样写入（同一个账户）
  * 生成的文件放在 _deploy/ 下（仓库之外），上传后立即删除本地副本
"""

from __future__ import annotations

import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 本脚本位于 <repo>/scripts/ 下，因此 ROOT 就是仓库根目录。
REPO = ROOT
# _deploy 放在工作区顶层（<工作区>/_deploy），仓库之外，避免密钥被误提交。
# 从 <工作区>/AI视频生成/reel-agent 往上两级即 <工作区>。
OUT = ROOT.parent.parent / "_deploy" / ".env.server"

SERVER_IP = "120.26.16.164"


def read_local_key(name: str) -> str:
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        key, _, value = line.strip().partition("=")
        if key == name and value.strip():
            return value.strip()
    raise SystemExit(f"本地 .env 里找不到 {name}")


def main() -> None:
    dashscope_key = read_local_key("REEL_PROVIDERS__VIDEO__API_KEY")

    # 所有密钥独立生成 —— 复用同一个随机串是常见的安全疏忽
    secret_key = secrets.token_urlsafe(48)
    api_key_pepper = secrets.token_urlsafe(48)
    invite_code = secrets.token_urlsafe(18)
    postgres_password = secrets.token_urlsafe(24)
    redis_password = secrets.token_urlsafe(24)

    content = f"""# ============================================================================
# ReelAgent 生产部署配置（由 scripts/gen_server_env.py 生成）
#
# 环境：staging
#   为什么不是 prod：prod 会强制要求 https:// 的 BASE_URL 与显式 ALLOWED_HOSTS，
#   而当前还没有域名和证书。staging 跳过这些校验，但**其它校验仍然生效**
#   （SECRET_KEY / API_KEY_PEPPER 缺失时照样拒绝启动）。
#   拿到域名后：改 REEL_ENV=prod、REEL_BASE_URL=https://域名、
#   REEL_SECURITY__HTTPS_ONLY=true，并挂上反向代理即可。
# ============================================================================

REEL_ENV=staging
REEL_PRODUCT_NAME=ReelAgent
REEL_BASE_URL=http://{SERVER_IP}:8000
REEL_BIND_ADDR=0.0.0.0
REEL_HTTP_PORT=8000

# ---------------------------------------------------------------------------
# 安全
# ---------------------------------------------------------------------------
REEL_SECURITY__SECRET_KEY={secret_key}
REEL_SECURITY__API_KEY_PEPPER={api_key_pepper}
REEL_SECURITY__HTTPS_ONLY=false
# 必须把 127.0.0.1 / localhost 也列进来！
# Dockerfile 的 HEALTHCHECK 打的是 http://127.0.0.1:8000/livez，
# 反向代理（Caddy/Nginx）的健康探测也来自本机。只写公网 IP 会让
# 健康检查收到 400 "Invalid host header"，容器被判定为 unhealthy ——
# 而业务本身是正常的，这种「假红」极难排查。
REEL_SECURITY__ALLOWED_HOSTS=["{SERVER_IP}","localhost","127.0.0.1"]

# 开放注册，但**必须**持邀请码。
# 原因：注册赠送 500 点 ≈ 1.67 美元供应商成本（约 12 元）。
# 若公网可自由注册，爬虫注册一批账号就能把你的百炼余额烧光。
REEL_SECURITY__ALLOW_SIGNUP=true
REEL_SECURITY__SIGNUP_INVITE_CODE={invite_code}

# ---------------------------------------------------------------------------
# 数据库 / Redis（口令由 compose 注入到连接串，此处只供 postgres 容器初始化）
# ---------------------------------------------------------------------------
POSTGRES_USER=reelagent
POSTGRES_PASSWORD={postgres_password}
POSTGRES_DB=reelagent
REDIS_PASSWORD={redis_password}

# ---------------------------------------------------------------------------
# 供应商：一把百炼 Key 覆盖 文本/图像/视频/语音（已验证）
# ---------------------------------------------------------------------------
REEL_PROVIDERS__LLM__PROVIDER=dashscope
REEL_PROVIDERS__LLM__MODEL=qwen-plus
REEL_PROVIDERS__LLM__API_KEY={dashscope_key}
REEL_PROVIDERS__LLM__BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

REEL_PROVIDERS__IMAGE__PROVIDER=dashscope
REEL_PROVIDERS__IMAGE__MODEL=wan2.5-t2i-preview
REEL_PROVIDERS__IMAGE__API_KEY={dashscope_key}
REEL_PROVIDERS__IMAGE__BASE_URL=https://dashscope.aliyuncs.com/api/v1

REEL_PROVIDERS__VIDEO__PROVIDER=dashscope
REEL_PROVIDERS__VIDEO__MODEL=wan3.0-video
REEL_PROVIDERS__VIDEO__API_KEY={dashscope_key}
REEL_PROVIDERS__VIDEO__BASE_URL=https://dashscope.aliyuncs.com/api/v1
REEL_PROVIDERS__VIDEO__TIMEOUT_SECONDS=600
REEL_PROVIDERS__VIDEO__MAX_CONCURRENCY=1

REEL_PROVIDERS__TTS__PROVIDER=dashscope
REEL_PROVIDERS__TTS__MODEL=qwen3-tts-flash
REEL_PROVIDERS__TTS__API_KEY={dashscope_key}
REEL_PROVIDERS__TTS__BASE_URL=https://dashscope.aliyuncs.com/api/v1

# BYOK 关闭：不让用户填自己的 Key，统一走我们的账
REEL_PROVIDERS__ALLOW_BYOK=false

# ---------------------------------------------------------------------------
# 存储 / 可观测性 / worker
# ---------------------------------------------------------------------------
REEL_STORAGE__BACKEND=local
REEL_STORAGE__LOCAL_DIR=/app/workspace/storage
REEL_STORAGE__MAX_UPLOAD_MB=32

REEL_OBSERVABILITY__LOG_FORMAT=json
REEL_OBSERVABILITY__LOG_LEVEL=INFO
REEL_OBSERVABILITY__METRICS_ENABLED=true

# worker 并发压到 1：单机 3.5G 内存，视频生成本身就吃内存
REEL_WORKER__CONCURRENCY=1
REEL_WORKER__SHOT_CONCURRENCY=2
REEL_WORKER__LEASE_SECONDS=900
REEL_WORKER__HEARTBEAT_SECONDS=20
REEL_WORKER__JOB_TIMEOUT_SECONDS=3600
REEL_WORKER__MAX_ATTEMPTS=3
REEL_WORKER__SHUTDOWN_GRACE_SECONDS=60

# 限流保持开启
REEL_RATE_LIMIT__ENABLED=true
REEL_RATE_LIMIT__ANONYMOUS_PER_MINUTE=30
REEL_RATE_LIMIT__USER_PER_MINUTE=240
REEL_RATE_LIMIT__PROJECT_CREATES_PER_HOUR=20

# ---------------------------------------------------------------------------
# 计费：Stripe 未接入
# ---------------------------------------------------------------------------
REEL_BILLING__ENABLED=false

# 镜像名
REEL_IMAGE=reelagent
REEL_IMAGE_TAG=local
"""

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(content, encoding="utf-8")
    OUT.chmod(0o600)

    # 只报告"生成了什么"，不打印任何密钥值
    print(f"  已写入 {OUT}")
    print(f"  行数 {len(content.splitlines())}")
    print()
    print("  已生成的密钥（值不显示，仅确认非空且长度合理）：")
    for name, value in (
        ("SECRET_KEY", secret_key),
        ("API_KEY_PEPPER", api_key_pepper),
        ("SIGNUP_INVITE_CODE", invite_code),
        ("POSTGRES_PASSWORD", postgres_password),
        ("REDIS_PASSWORD", redis_password),
    ):
        print(f"    {name:<20} 长度 {len(value)}")
    print()
    print("  SECRET_KEY 与 API_KEY_PEPPER 是否不同:", secret_key != api_key_pepper)
    print()
    print(f"  >>> 邀请码（你需要记下来，注册时要用）：{invite_code}")


if __name__ == "__main__":
    main()
