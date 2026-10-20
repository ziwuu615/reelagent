"""口令哈希。

用 **Argon2id**（当前密码哈希的推荐算法，抗 GPU/ASIC 暴力破解）。
参数取 OWASP 建议的「约 50ms / 次」档位：安全与登录延迟的平衡点。

注意：Argon2 只用于**低熵**秘密（人类密码）。
API Key 是 256 位随机串，用带 pepper 的 SHA-256 即可，
对它跑 Argon2 只会白白拖慢每个 API 请求。
"""

from __future__ import annotations

import re
import unicodedata

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

from ..domain.errors import ValidationFailed

#: OWASP 推荐档：m=19MiB, t=2, p=1
_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

#: 常见弱口令（生产环境应替换为更大的字典或接入 HIBP）
_COMMON_PASSWORDS = {
    "password",
    "12345678",
    "123456789",
    "qwertyuiop",
    "password1",
    "admin123",
    "letmein123",
    "welcome123",
    "iloveyou",
    "abc123456",
    "111111111",
    "passw0rd",
    "reelagent",
    "reelagent123",
    "changeme",
    "1qaz2wsx3edc",
}


class PasswordPolicyError(ValidationFailed):
    """口令不符合策略。消息可直接展示给用户。

    **必须继承 AppError（经 ValidationFailed）而不是裸 ValueError**：
    否则 `except AppError` 接不住它，用户输入弱口令会拿到 500 而不是友好提示。
    """

    code = "password_policy_violation"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    except Exception:  # pragma: no cover - 防御性兜底，绝不因哈希异常放行
        return False


def needs_rehash(password_hash: str) -> bool:
    """判断已有哈希是否需要按当前参数重算（登录成功后调用）。

    无法解析的哈希一律返回 True：它显然不是当前格式，就该重算。
    实际调用点只在口令校验通过之后，因此这个分支正常永不触发；
    但语义上必须是 True —— 返回 False 等于「沿用一份读不懂的哈希」。
    """
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:
        return True


# --------------------------------------------------------------------------- #
# 强度校验
# --------------------------------------------------------------------------- #
def _classes_count(password: str) -> int:
    checks = [
        re.search(r"[a-z]", password),
        re.search(r"[A-Z]", password),
        re.search(r"\d", password),
        re.search(r"[^\w\s]", password),
    ]
    return sum(1 for c in checks if c)


def validate_password_strength(password: str, *, min_length: int = 10, email: str = "") -> None:
    """校验口令强度，不合格抛 PasswordPolicyError。"""
    normalized = unicodedata.normalize("NFKC", password or "")

    if len(normalized) < min_length:
        raise PasswordPolicyError(f"密码至少需要 {min_length} 个字符")
    if len(normalized) > 256:
        raise PasswordPolicyError("密码过长（上限 256 字符）")
    if normalized.lower() in _COMMON_PASSWORDS:
        raise PasswordPolicyError("这个密码太常见了，请换一个")
    if _classes_count(normalized) < 3:
        raise PasswordPolicyError("密码需至少包含大写字母、小写字母、数字、符号中的三类")
    if email:
        local = email.split("@", 1)[0].lower()
        if local and len(local) >= 4 and local in normalized.lower():
            raise PasswordPolicyError("密码不能包含你的邮箱名")
    if len(set(normalized)) < 5:
        raise PasswordPolicyError("密码重复字符过多，请增加变化")
