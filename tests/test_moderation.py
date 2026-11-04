"""内容审核：归一化、拦截、fail 策略。

这套测试的重点不是「词表里有几个词」，而是三个容易做错的行为：

  1. 归一化 —— 插空格/全角/加符号就能绕过的话，审核形同虚设
  2. fail-closed —— 审核服务挂了要拒绝，不能放行
  3. 可开关 —— 关闭时必须真的全放行（本地开发不该被词表卡住）
"""

from __future__ import annotations

import unittest

from reelagent.moderation import (
    VERDICT_ALLOW,
    VERDICT_BLOCK,
    BlockingModerationFactory,
    HeuristicImageModeration,
    KeywordModerationProvider,
    ModerationError,
    ModerationVerdict,
    load_blocklist,
    normalize,
)
from tests.support import cleanup_tmp, make_tmp


class TestNormalize(unittest.TestCase):
    def test_strips_spaces_and_punctuation(self) -> None:
        """插空格、加连字符、加标点都不能绕过。"""
        self.assertEqual(normalize("色 情"), normalize("色情"))
        self.assertEqual(normalize("色-情"), normalize("色情"))
        self.assertEqual(normalize("色​情"), normalize("色情"))  # 含零宽空格
        self.assertEqual(normalize(" 色　情 "), normalize("色情"))  # 含全角空格

    def test_fullwidth_to_halfwidth(self) -> None:
        self.assertEqual(normalize("ＡＢＣ"), "abc")

    def test_empty(self) -> None:
        self.assertEqual(normalize(""), "")
        self.assertEqual(normalize("   "), "")


class TestKeywordProvider(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = KeywordModerationProvider()

    def test_blocks_and_reports_category(self) -> None:
        verdict = self.provider.check("画面里有人在做毒品交易")
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.action, VERDICT_BLOCK)
        self.assertIn("违禁品", verdict.categories)
        self.assertTrue(verdict.reason)

    def test_bypass_with_spaces_is_still_blocked(self) -> None:
        verdict = self.provider.check("这里 有 赌 博 的 内 容".replace(" ", ""))
        self.assertFalse(verdict.allowed)

    def test_clean_text_passes(self) -> None:
        verdict = self.provider.check("深夜便利店，林晚找到一盒过期牛奶。")
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.action, VERDICT_ALLOW)

    def test_empty_text_passes(self) -> None:
        self.assertTrue(self.provider.check("").allowed)

    def test_extra_blocklist_file(self) -> None:
        tmp = make_tmp("mod")
        try:
            wordlist = tmp / "words.txt"
            wordlist.write_text("# 自定义\n违规品牌:某品牌,另一个品牌\n", encoding="utf-8")
            extra = load_blocklist(wordlist)
            provider = KeywordModerationProvider(extra=extra)
            verdict = provider.check("出现了某品牌的包装")
            self.assertFalse(verdict.allowed)
            self.assertIn("违规品牌", verdict.categories)
        finally:
            cleanup_tmp(tmp)

    def test_missing_blocklist_file_is_ignored(self) -> None:
        self.assertEqual(load_blocklist("/definitely/not/here.txt"), {})
        self.assertEqual(load_blocklist(""), {})


class _BrokenProvider:
    """模拟审核服务不可用。"""

    name = "broken"

    def check(self, text: str, *, scope: str = "") -> ModerationVerdict:
        raise TimeoutError("审核服务超时")


class TestFailPolicy(unittest.TestCase):
    def test_fail_closed_blocks_when_provider_breaks(self) -> None:
        factory = BlockingModerationFactory(_BrokenProvider(), HeuristicImageModeration(), fail_closed=True)
        verdict = factory.check_text("任何内容", scope="test")
        self.assertFalse(verdict.allowed, "fail-closed 下审核服务故障必须拒绝")
        self.assertIn("审核不可用", verdict.reason)

    def test_fail_open_allows_when_provider_breaks(self) -> None:
        factory = BlockingModerationFactory(_BrokenProvider(), HeuristicImageModeration(), fail_closed=False)
        self.assertTrue(factory.check_text("任何内容", scope="test").allowed)

    def test_disabled_allows_everything(self) -> None:
        factory = BlockingModerationFactory(
            KeywordModerationProvider(), HeuristicImageModeration(), enabled=False
        )
        self.assertTrue(factory.check_text("毒品", scope="test").allowed)


class TestHeuristicImage(unittest.TestCase):
    def test_solid_color_is_blocked(self) -> None:
        from PIL import Image

        tmp = make_tmp("img")
        try:
            solid = tmp / "solid.png"
            Image.new("RGB", (320, 320), (0, 0, 0)).save(solid)
            verdict = HeuristicImageModeration().check(solid)
            self.assertFalse(verdict.allowed)
            self.assertIn("图像异常", verdict.categories)
        finally:
            cleanup_tmp(tmp)

    def test_tiny_image_is_blocked(self) -> None:
        from PIL import Image

        tmp = make_tmp("img")
        try:
            tiny = tmp / "tiny.png"
            Image.new("RGB", (16, 16), (10, 20, 30)).save(tiny)
            self.assertFalse(HeuristicImageModeration().check(tiny).allowed)
        finally:
            cleanup_tmp(tmp)

    def test_normal_image_passes(self) -> None:
        import numpy as np
        from PIL import Image

        tmp = make_tmp("img")
        try:
            arr = np.random.default_rng(0).integers(0, 255, (256, 256, 3), dtype=np.uint8)
            ok = tmp / "ok.png"
            Image.fromarray(arr).save(ok)
            self.assertTrue(HeuristicImageModeration().check(ok).allowed)
        finally:
            cleanup_tmp(tmp)

    def test_missing_file_is_unavailable(self) -> None:
        verdict = HeuristicImageModeration().check(make_tmp("img") / "nope.png")
        self.assertFalse(verdict.allowed)
        self.assertIn("审核不可用", verdict.reason)


class TestModerationErrorType(unittest.TestCase):
    def test_is_runtime_error(self) -> None:
        """调用方按类型分支，因此它必须能被 except RuntimeError 兜住。"""
        self.assertTrue(issubclass(ModerationError, RuntimeError))


if __name__ == "__main__":
    unittest.main()
