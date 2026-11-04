"""内容审核合规校验：生产环境不允许只有启发式审核。

生成式 AI 服务备案要求具备内容安全能力。启发式也只能发现「纯色/过小/不可解码」，
对涉黄涉暴涉政完全无感 —— 把它当合规审核是自欺。所以生产环境这条是硬门槛。
"""

from __future__ import annotations

import unittest

from reelagent_cloud.config import Settings
from tests.support import cleanup_tmp, make_tmp


def _prod(tmp, *, moderation: str = "dashscope", llm_key: str = "sk-llm"):
    return {
        "env": "prod",
        "base_url": "https://reelagent.example.com",
        "workspace_dir": tmp,
        "database": {"url": "postgresql+asyncpg://u:p@db:5432/reel"},
        "security": {
            "secret_key": "k",
            "api_key_pepper": "p",
            "https_only": True,
            "allowed_hosts": ["reelagent.example.com"],
        },
        "providers": {
            "video": {"provider": "ark", "model": "m", "api_key": "sk-real"},
            "llm": {"provider": "dashscope", "model": "qwen-plus", "api_key": llm_key},
        },
        "observability": {"log_external_retention": True},
        "compliance": {"image_moderation": moderation},
    }


class TestImageModerationGate(unittest.TestCase):
    def test_heuristic_is_rejected_in_production(self) -> None:
        tmp = make_tmp("cgate")
        try:
            with self.assertRaises(ValueError) as exc:
                Settings(**_prod(tmp, moderation="heuristic"))
            msg = str(exc.exception)
            self.assertIn("语义审核", msg)
            self.assertIn("IMAGE_MODERATION", msg)
        finally:
            cleanup_tmp(tmp)

    def test_semantic_moderation_accepted(self) -> None:
        tmp = make_tmp("cgate2")
        try:
            Settings(**_prod(tmp))
        finally:
            cleanup_tmp(tmp)

    def test_semantic_moderation_requires_llm_key(self) -> None:
        tmp = make_tmp("cgate3")
        try:
            with self.assertRaises(ValueError) as exc:
                Settings(**_prod(tmp, llm_key=""))
            self.assertIn("LLM__API_KEY", str(exc.exception))
        finally:
            cleanup_tmp(tmp)

    def test_default_is_semantic(self) -> None:
        """默认值必须是合规的那一侧，而不是离线的那一侧。"""
        tmp = make_tmp("cgate4")
        try:
            s = Settings(**_prod(tmp))
            self.assertEqual(s.compliance.image_moderation, "dashscope")
        finally:
            cleanup_tmp(tmp)


if __name__ == "__main__":
    unittest.main()
