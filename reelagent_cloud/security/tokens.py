"""令牌与密钥：会话 Cookie、API Key、CSRF、对外分享 token、密钥加密。

安全设计说明：
  - **会话**：签名 Cookie 携带 `session_version`，改密或「登出所有设备」时服务端 +1，
    所有已签发 Cookie 立即失效。无需服务端 session 表即可批量吊销。
  - **API Key**：256 位随机串，只存「pepper + SHA-256」。高熵秘密不需要 Argon2，
    否则每个 API 请求都要付 ~50ms 的哈希开销。
  - **CSRF**：双提交 Cookie（Double Submit Cookie）。Cookie 非 HttpOnly，
    前端读出后放进请求头，服务端做常数时间比较。
  - **对外分享**：成片分享链接用 HMAC 签名，带过期时间，不需要登录即可访问单条产物。
  - **BYOK 密钥**：用户自带供应商 Key 必须**可逆**存储（要用它去调厂商），
    因此用 Fernet 对称加密，密钥由 SECRET_KEY 派生。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..config import Settings

SESSION_SALT = "reelagent.session.v1"
SHARE_SALT = "reelagent.share.v1"

#: API Key 前缀，便于在日志/仓库里做泄漏扫描
API_KEY_SCHEME = "reel"

#: 会话 Cookie 里放什么：只有用户标识、会话代际与签发时间。
#: 不放角色 / 额度 / 套餐——那些必须每次从库里读，否则权限变更无法即时生效。
SESSION_KEYS = ("uid", "sv", "iat")


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #
def normalize_email(email: str) -> str:
    return unicodedata.normalize("NFKC", (email or "").strip()).lower()


def normalize_slug(value: str) -> str:
    """生成 URL 安全的 slug。

    注意：**必须显式排除非 ASCII**。`str.isalnum()` 对中文返回 True，
    直接用它会让「光影工作室」原样变成 slug，进而在 URL、文件名、
    S3 key 里引入百分号编码与跨平台编码问题。
    非 ASCII 名称统一退化为空，由调用方补随机后缀。
    """
    base = "".join(
        ch if (ch.isascii() and ch.isalnum()) or ch == "-" else "-" for ch in (value or "").strip().lower()
    )
    while "--" in base:
        base = base.replace("--", "-")
    return base.strip("-")[:48]


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #
class TokenError(Exception):
    pass


def _serializer(settings: Settings, salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.security.secret_key.get_secret_value(), salt=salt)


def issue_session(settings: Settings, *, user_id: str, session_version: int) -> str:
    payload = {"uid": user_id, "sv": int(session_version), "iat": int(time.time())}
    return _serializer(settings, SESSION_SALT).dumps(payload)


def read_session(settings: Settings, token: str) -> dict[str, Any]:
    try:
        data = _serializer(settings, SESSION_SALT).loads(
            token, max_age=settings.security.session_max_age_seconds
        )
    except SignatureExpired as exc:
        raise TokenError("会话已过期，请重新登录") from exc
    except BadSignature as exc:
        raise TokenError("会话无效") from exc
    if not isinstance(data, dict) or "uid" not in data or "sv" not in data:
        raise TokenError("会话格式错误")
    return data


# --------------------------------------------------------------------------- #
# CSRF
# --------------------------------------------------------------------------- #
def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_matches(cookie_value: str, header_value: str) -> bool:
    """常数时间比较，避免时序侧信道。"""
    if not cookie_value or not header_value:
        return False
    return hmac.compare_digest(cookie_value, header_value)


# --------------------------------------------------------------------------- #
# API Key
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GeneratedApiKey:
    full_key: str  # 仅此一次返回给用户
    prefix: str  # 用于列表展示与定位
    key_hash: str  # 入库


def _pepper(settings: Settings) -> str:
    pepper = settings.security.api_key_pepper.get_secret_value()
    return pepper or settings.security.secret_key.get_secret_value()


def hash_api_key(settings: Settings, full_key: str) -> str:
    return hashlib.sha256(f"{_pepper(settings)}{full_key}".encode()).hexdigest()


def generate_api_key(settings: Settings) -> GeneratedApiKey:
    """生成 API Key。

    **密钥体必须避免使用 `_`**：Key 的格式是 `reel_{prefix}_{secret}`，
    而 `secrets.token_urlsafe()` 的 base64url 字母表包含 `_` 与 `-`，
    一旦 secret 里出现 `_`，按分隔符解析就会散架，导致该 Key 永远认证失败。
    这里改用十六进制（48 位 = 192 bit 熵），彻底消除分隔符歧义。
    """
    prefix = secrets.token_hex(4)  # 8 位十六进制
    secret = secrets.token_hex(24)  # 48 位十六进制，192 bit 熵
    full_key = f"{API_KEY_SCHEME}_{prefix}_{secret}"
    return GeneratedApiKey(
        full_key=full_key,
        prefix=prefix,
        key_hash=hash_api_key(settings, full_key),
    )


def parse_api_key(full_key: str) -> str | None:
    """从完整 Key 中取出 prefix；格式不合法返回 None。

    用 `split("_", 2)` 而非 `split("_")`：历史上用 token_urlsafe 生成的 Key
    其 secret 段可能含 `_`，这样切分仍能正确取到 prefix，
    老 Key 不至于在升级后全部失效。
    """
    parts = (full_key or "").split("_", 2)
    if len(parts) != 3 or parts[0] != API_KEY_SCHEME or len(parts[1]) != 8:
        return None
    if not parts[2]:
        return None
    return parts[1]


def mask_api_key(prefix: str) -> str:
    return f"{API_KEY_SCHEME}_{prefix}_{'*' * 8}"


# --------------------------------------------------------------------------- #
# 对外分享
# --------------------------------------------------------------------------- #
def issue_share_token(settings: Settings, *, artifact_id: str, expires_at: int) -> str:
    return _serializer(settings, SHARE_SALT).dumps({"aid": artifact_id, "exp": int(expires_at)})


def read_share_token(settings: Settings, token: str) -> dict[str, Any]:
    try:
        data = _serializer(settings, SHARE_SALT).loads(token)
    except (SignatureExpired, BadSignature) as exc:
        raise TokenError("分享链接无效或已过期") from exc
    if int(data.get("exp", 0)) < int(time.time()):
        raise TokenError("分享链接已过期")
    return data


# --------------------------------------------------------------------------- #
# BYOK 密钥加密（可逆）
# --------------------------------------------------------------------------- #
def _fernet(settings: Settings) -> Fernet:
    raw = settings.security.secret_key.get_secret_value().encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))


def encrypt_secret(settings: Settings, plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet(settings).encrypt(plaintext.encode()).decode()


def decrypt_secret(settings: Settings, ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet(settings).decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise TokenError("密钥解密失败；SECRET_KEY 可能已轮换，请重新填写 BYOK 密钥") from exc


def encrypt_byok_map(settings: Settings, values: dict[str, str]) -> dict[str, str]:
    return {k: encrypt_secret(settings, v) for k, v in values.items() if v}


def decrypt_byok_map(settings: Settings, values: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (values or {}).items():
        try:
            out[k] = decrypt_secret(settings, v)
        except TokenError:
            continue  # 单条解不开不影响其它供应商
    return out


# --------------------------------------------------------------------------- #
# 杂项
# --------------------------------------------------------------------------- #
def new_request_id() -> str:
    return secrets.token_hex(8)


def new_worker_id(prefix: str = "worker") -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a or "", b or "")
