"""安全层测试。

覆盖的是「一旦写错就会造成真实安全事故」的那些路径：
口令哈希、会话吊销、API Key 存储方式、CSRF、密钥加密、日志脱敏。
"""

from __future__ import annotations

import time

import pytest

from reelagent_cloud.config import Settings
from reelagent_cloud.observability import redact_value
from reelagent_cloud.security import (
    PasswordPolicyError,
    TokenError,
    csrf_matches,
    decrypt_byok_map,
    decrypt_secret,
    encrypt_byok_map,
    encrypt_secret,
    generate_api_key,
    hash_api_key,
    hash_password,
    issue_session,
    issue_share_token,
    mask_api_key,
    needs_rehash,
    normalize_email,
    normalize_slug,
    parse_api_key,
    read_session,
    read_share_token,
    validate_password_strength,
    verify_password,
)


# --------------------------------------------------------------------------- #
# 口令
# --------------------------------------------------------------------------- #
class TestPasswords:
    def test_hash_is_not_plaintext(self):
        h = hash_password("Str0ng-Passw0rd!23")
        assert "Str0ng" not in h
        assert h.startswith("$argon2id$"), "必须使用 Argon2id"

    def test_same_password_different_hash(self):
        """加盐：同一口令两次哈希必须不同，否则可被彩虹表攻击。"""
        a = hash_password("Str0ng-Passw0rd!23")
        b = hash_password("Str0ng-Passw0rd!23")
        assert a != b

    def test_verify_roundtrip(self):
        h = hash_password("Str0ng-Passw0rd!23")
        assert verify_password("Str0ng-Passw0rd!23", h) is True
        assert verify_password("wrong-password", h) is False

    def test_verify_survives_corrupted_hash(self):
        """哈希串损坏时必须返回 False，绝不能抛异常导致认证逻辑被绕过或 500。"""
        assert verify_password("x", "not-a-valid-hash") is False
        assert verify_password("x", "") is False

    def test_needs_rehash_detects_param_change(self):
        """当前参数生成的哈希不需要重算；用旧参数生成的则需要。"""
        current = hash_password("Str0ng-Passw0rd!23")
        assert needs_rehash(current) is False

        # 手工构造一个低参数成本的 Argon2id 哈希（模拟算法参数升级前的历史数据）
        from argon2 import PasswordHasher
        from argon2.low_level import Type

        weak = PasswordHasher(
            time_cost=1, memory_cost=8, parallelism=1, hash_len=16, salt_len=8, type=Type.ID
        )
        assert needs_rehash(weak.hash("Str0ng-Passw0rd!23")) is True

    def test_needs_rehash_treats_garbage_as_stale(self):
        """无法解析的哈希一律视为需要重算——宁可多算一次，也不能沿用坏数据。"""
        assert needs_rehash("garbage") is True
        assert needs_rehash("") is True

    @pytest.mark.parametrize(
        "password,reason",
        [
            ("short1!A", "太短"),
            ("password", "常见弱口令"),
            ("alllowercaseletters", "字符类别不足"),
            ("aaaaaaaaaaaaaa1A!", "重复字符过多"),
        ],
    )
    def test_policy_rejects_weak(self, password, reason):
        with pytest.raises(PasswordPolicyError):
            validate_password_strength(password, min_length=10)

    def test_policy_rejects_email_local_part(self):
        with pytest.raises(PasswordPolicyError, match="邮箱"):
            validate_password_strength("alice-Secure!23", min_length=10, email="alice@example.com")

    def test_policy_accepts_strong(self):
        validate_password_strength("Str0ng-Passw0rd!23", min_length=10, email="a@b.com")


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #
class TestSessions:
    def test_roundtrip(self, settings: Settings):
        token = issue_session(settings, user_id="usr_1", session_version=3)
        data = read_session(settings, token)
        assert data["uid"] == "usr_1"
        assert data["sv"] == 3

    def test_tampered_token_rejected(self, settings: Settings):
        token = issue_session(settings, user_id="usr_1", session_version=1)
        tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
        with pytest.raises(TokenError):
            read_session(settings, tampered)

    def test_token_signed_with_other_key_rejected(self, settings: Settings):
        from tests.conftest import build_settings

        other = build_settings(settings.workspace_dir / "other")
        # 必须真的换掉密钥——build_settings 默认给所有实例同一个测试密钥
        other.security.secret_key = type(other.security.secret_key)("a-different-signing-key")
        token = issue_session(other, user_id="usr_1", session_version=1)
        with pytest.raises(TokenError):
            read_session(settings, token)

    def test_expired_token_rejected(self, settings: Settings):
        # itsdangerous 的时间戳按整秒计算，因此必须跨过 2 秒才能稳定触发过期
        settings.security.session_max_age_seconds = 1
        token = issue_session(settings, user_id="usr_1", session_version=1)
        time.sleep(2.2)
        with pytest.raises(TokenError, match="过期"):
            read_session(settings, token)

    def test_empty_token_rejected(self, settings: Settings):
        with pytest.raises(TokenError):
            read_session(settings, "")


# --------------------------------------------------------------------------- #
# API Key
# --------------------------------------------------------------------------- #
class TestApiKeys:
    def test_generated_format_and_hash(self, settings: Settings):
        gen = generate_api_key(settings)
        assert gen.full_key.startswith("reel_")
        assert parse_api_key(gen.full_key) == gen.prefix
        # 只存哈希
        assert gen.key_hash == hash_api_key(settings, gen.full_key)
        assert gen.full_key not in gen.key_hash

    def test_keys_are_unique(self, settings: Settings):
        keys = {generate_api_key(settings).key_hash for _ in range(50)}
        assert len(keys) == 50

    def test_pepper_isolates_hashes(self, settings: Settings):
        from tests.conftest import build_settings

        other = build_settings(settings.workspace_dir / "other2")
        other.security.api_key_pepper = type(other.security.api_key_pepper)("different-pepper")
        gen = generate_api_key(settings)
        assert hash_api_key(settings, gen.full_key) != hash_api_key(other, gen.full_key)

    @pytest.mark.parametrize(
        "bad", ["", "reel_", "reel_short_x", "nope_12345678_abc", "reel_12345678", "reel_1234567_abc"]
    )
    def test_malformed_keys_rejected(self, bad):
        assert parse_api_key(bad) is None

    def test_masking_hides_secret(self):
        masked = mask_api_key("abcd1234")
        assert "abcd1234" in masked
        assert "****" in masked


# --------------------------------------------------------------------------- #
# CSRF
# --------------------------------------------------------------------------- #
class TestCsrf:
    def test_match_requires_both(self):
        assert csrf_matches("abc", "abc") is True
        assert csrf_matches("abc", "abd") is False
        assert csrf_matches("", "") is False
        assert csrf_matches("abc", "") is False
        assert csrf_matches("", "abc") is False


# --------------------------------------------------------------------------- #
# 分享 token
# --------------------------------------------------------------------------- #
class TestShareTokens:
    def test_roundtrip(self, settings: Settings):
        token = issue_share_token(settings, artifact_id="art_1", expires_at=int(time.time()) + 60)
        assert read_share_token(settings, token)["aid"] == "art_1"

    def test_expired_rejected(self, settings: Settings):
        token = issue_share_token(settings, artifact_id="art_1", expires_at=int(time.time()) - 1)
        with pytest.raises(TokenError, match="过期"):
            read_share_token(settings, token)

    def test_forged_rejected(self, settings: Settings):
        with pytest.raises(TokenError):
            read_share_token(settings, "totally-made-up-token")


# --------------------------------------------------------------------------- #
# BYOK 加密
# --------------------------------------------------------------------------- #
class TestSecretEncryption:
    def test_roundtrip(self, settings: Settings):
        plain = "sk-live-abcdefghijklmnop"
        cipher = encrypt_secret(settings, plain)
        assert plain not in cipher, "密文里不能出现明文"
        assert decrypt_secret(settings, cipher) == plain

    def test_empty_passthrough(self, settings: Settings):
        assert encrypt_secret(settings, "") == ""
        assert decrypt_secret(settings, "") == ""

    def test_wrong_key_fails_loudly(self, settings: Settings):
        from tests.conftest import build_settings

        other = build_settings(settings.workspace_dir / "other3")
        other.security.secret_key = type(other.security.secret_key)("a-completely-different-key")
        cipher = encrypt_secret(settings, "sk-secret")
        with pytest.raises(TokenError):
            decrypt_secret(other, cipher)

    def test_map_roundtrip_skips_undecryptable(self, settings: Settings):
        encrypted = encrypt_byok_map(settings, {"video": "sk-v", "image": "sk-i", "llm": ""})
        assert "llm" not in encrypted
        decrypted = decrypt_byok_map(settings, {**encrypted, "broken": "not-a-fernet-token"})
        assert decrypted == {"video": "sk-v", "image": "sk-i"}


# --------------------------------------------------------------------------- #
# 日志脱敏
# --------------------------------------------------------------------------- #
class TestRedaction:
    def test_sensitive_keys_redacted(self):
        out = redact_value(
            {
                "api_key": "reel_abc_xyz",
                "password": "p",
                "Authorization": "Bearer tok",
                "webhook_secret": "whsec_1",
                "user": "alice",
            }
        )
        assert out["api_key"] == "***REDACTED***"
        assert out["password"] == "***REDACTED***"
        assert out["Authorization"] == "***REDACTED***"
        assert out["webhook_secret"] == "***REDACTED***"
        assert out["user"] == "alice", "非敏感字段不能被误伤"

    def test_nested_and_list(self):
        out = redact_value({"a": {"b": {"token": "x"}}, "list": [{"secret": "y"}]})
        assert out["a"]["b"]["token"] == "***REDACTED***"
        assert out["list"][0]["secret"] == "***REDACTED***"

    def test_bare_secret_values_scrubbed(self):
        out = redact_value({"note": "key is sk-live-abcdefghijklmnop and Bearer abcdefghijklmn"})
        assert "sk-live" not in out["note"]
        assert "Bearer abcdefghijklmn" not in out["note"]

    def test_depth_limit_prevents_runaway(self):
        deep: dict = {}
        node = deep
        for _ in range(20):
            node["next"] = {}
            node = node["next"]
        node["api_key"] = "should-not-matter"
        redact_value(deep)  # 不应抛异常或无限递归


# --------------------------------------------------------------------------- #
# 归一化
# --------------------------------------------------------------------------- #
class TestNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("  Alice@Example.COM ", "alice@example.com"),
            ("BOB@TEST.IO", "bob@test.io"),
        ],
    )
    def test_email(self, raw, expected):
        assert normalize_email(raw) == expected

    def test_slug_is_ascii_url_safe(self):
        """中文名必须退化为空（由调用方补随机后缀），不能原样进 URL。"""
        assert normalize_slug("光影工作室") == ""
        assert normalize_slug("My Cool Org!") == "my-cool-org"
        assert normalize_slug("a---b") == "a-b"
        assert normalize_slug("Hello 世界 World") == "hello-world"
        assert len(normalize_slug("x" * 200)) <= 48
        assert all(c.isascii() for c in normalize_slug("中文 English 123"))

    def test_slug_of_pure_symbols_is_empty(self):
        assert normalize_slug("!!!###") == ""
        assert normalize_slug("   ") == ""
