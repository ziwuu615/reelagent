"""日志留存：文件落盘 + 按天轮转 + 生产环境硬性校验。

网络安全法要求日志留存不低于六个月。只输出 stdout 时这个要求完全由外部
日志平台兑现，应用侧无法自证；所以这里做两件事：

  1. 提供 `file` / `both` 两种落盘模式，按天轮转、保留 N 天
  2. 生产环境启动时**强制**二选一：要么落盘，要么显式声明由外部平台留存

第 2 点是有意的「不给自己留含糊空间」——声明是可以被追责的，静默通过不是。
"""

from __future__ import annotations

import logging
import unittest

from reelagent_cloud.config import ObservabilitySettings, Settings
from reelagent_cloud.observability.logging import configure_logging
from tests.conftest import build_settings
from tests.support import cleanup_tmp, make_tmp


class TestLogFileSink(unittest.TestCase):
    def tearDown(self) -> None:
        # 还原成 stdout，避免污染同一进程里后续用例的日志输出
        configure_logging(sink="stdout")
        cleanup_tmp(getattr(self, "_tmp", None)) if getattr(self, "_tmp", None) else None

    def test_file_sink_writes_records(self) -> None:
        self._tmp = make_tmp("logfile")
        target = self._tmp / "logs" / "app.log"
        configure_logging(sink="file", log_file=str(target), retention_days=180)

        logging.getLogger("reelagent.test").warning("留痕测试消息")

        self.assertTrue(target.exists(), "file sink 必须真的落盘")
        content = target.read_text(encoding="utf-8")
        self.assertIn("留痕测试消息", content)

    def test_rotation_keeps_retention_days(self) -> None:
        """备份份数应等于保留天数（按天切分，一个备份就是一天）。"""
        from logging.handlers import TimedRotatingFileHandler

        self._tmp = make_tmp("logrot")
        target = self._tmp / "logs" / "app.log"
        configure_logging(sink="file", log_file=str(target), retention_days=200)

        handlers = [h for h in logging.getLogger().handlers if isinstance(h, TimedRotatingFileHandler)]
        self.assertEqual(len(handlers), 1)
        self.assertEqual(handlers[0].backupCount, 200)
        self.assertEqual(handlers[0].when, "MIDNIGHT")

    def test_both_sinks_are_installed(self) -> None:
        from logging.handlers import TimedRotatingFileHandler

        self._tmp = make_tmp("logboth")
        target = self._tmp / "logs" / "app.log"
        configure_logging(sink="both", log_file=str(target))

        handlers = logging.getLogger().handlers
        self.assertTrue(any(isinstance(h, TimedRotatingFileHandler) for h in handlers))
        self.assertTrue(any(isinstance(h, logging.StreamHandler) for h in handlers))

    def test_file_sink_without_path_falls_back_to_stdout(self) -> None:
        """路径没配时宁可只输出 stdout，也不让进程起不来。"""
        configure_logging(sink="file", log_file="")
        self.assertTrue(any(isinstance(h, logging.StreamHandler) for h in logging.getLogger().handlers))


class TestProductionLogGate(unittest.TestCase):
    """生产环境必须能证明「日志留了 6 个月」。"""

    def _prod(self, tmp, **obs):
        # 一份「除日志外全部合规」的生产配置：日志相关的用例只该因为日志被拦，
        # 不该被内容审核或视频 Key 的缺失干扰。
        base = {
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
                "llm": {"provider": "dashscope", "model": "qwen-plus", "api_key": "sk-llm"},
            },
            "observability": obs,
        }
        return base

    def test_rejected_without_any_retention_declaration(self) -> None:
        tmp = make_tmp("prodlog")
        try:
            with self.assertRaises(ValueError) as exc:
                Settings(**self._prod(tmp))
            self.assertIn("日志", str(exc.exception))
        finally:
            cleanup_tmp(tmp)

    def test_rejected_when_file_sink_has_no_path(self) -> None:
        tmp = make_tmp("prodlog2")
        try:
            with self.assertRaises(ValueError) as exc:
                Settings(**self._prod(tmp, log_sink="both", log_file=""))
            self.assertIn("LOG_FILE", str(exc.exception))
        finally:
            cleanup_tmp(tmp)

    def test_accepted_with_file_sink(self) -> None:
        tmp = make_tmp("prodlog3")
        try:
            Settings(**self._prod(tmp, log_sink="both", log_file=str(tmp / "app.log")))
        finally:
            cleanup_tmp(tmp)

    def test_accepted_with_external_retention_declaration(self) -> None:
        tmp = make_tmp("prodlog4")
        try:
            Settings(**self._prod(tmp, log_external_retention=True))
        finally:
            cleanup_tmp(tmp)

    def test_rejected_when_retention_too_short(self) -> None:
        tmp = make_tmp("prodlog5")
        try:
            with self.assertRaises(ValueError) as exc:
                Settings(**self._prod(tmp, log_external_retention=True, log_retention_days=30))
            self.assertIn("180", str(exc.exception))
        finally:
            cleanup_tmp(tmp)

    def test_development_is_not_gated(self) -> None:
        """本地开发不该被日志留存要求卡住。"""
        tmp = make_tmp("devlog")
        try:
            s = build_settings(tmp)
            self.assertIsInstance(s.observability, ObservabilitySettings)
        finally:
            cleanup_tmp(tmp)


if __name__ == "__main__":
    unittest.main()
