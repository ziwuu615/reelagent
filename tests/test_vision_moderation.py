"""多模态语义审核（DashScope qwen-vl）。

启发式只能发现「纯色/过小」，挡不住涉黄涉暴。这里验证语义审核这条真实链路：
调用、解析、缓存、失败处理。

不联网：把 `urllib.request.urlopen` 打桩，断言请求形状与结论解析。
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from reelagent.moderation import (
    DashScopeVisionModeration,
    HeuristicImageModeration,
    ModerationVerdict,
    build_moderation,
)
from tests.support import cleanup_tmp, make_tmp


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def _chat_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def _image(path) -> None:
    arr = np.random.default_rng(7).integers(0, 255, (300, 300, 3), dtype=np.uint8)
    Image.fromarray(arr).save(path)


class TestVisionModeration(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = make_tmp("vision")
        self.img = self.tmp / "kf.png"
        _image(self.img)

    def tearDown(self) -> None:
        cleanup_tmp(self.tmp)

    def test_blocked_verdict_is_parsed(self) -> None:
        provider = DashScopeVisionModeration(api_key="sk-test")
        body = _chat_response(
            json.dumps({"allowed": False, "categories": ["暴力血腥"], "reason": "画面含血迹"})
        )
        with patch("urllib.request.urlopen", return_value=_FakeResp(body)):
            verdict = provider.check(self.img, scope="keyframe:sc01")

        self.assertFalse(verdict.allowed)
        self.assertIn("暴力血腥", verdict.categories)
        self.assertEqual(verdict.provider, "dashscope-vision")

    def test_allowed_verdict_is_parsed(self) -> None:
        provider = DashScopeVisionModeration(api_key="sk-test")
        body = _chat_response(json.dumps({"allowed": True, "categories": [], "reason": ""}))
        with patch("urllib.request.urlopen", return_value=_FakeResp(body)):
            verdict = provider.check(self.img)
        self.assertTrue(verdict.allowed)

    def test_markdown_wrapped_json_is_tolerated(self) -> None:
        """模型常把 JSON 包在代码块里，不能因此判成「审核不可用」。"""
        provider = DashScopeVisionModeration(api_key="sk-test")
        body = _chat_response('```json\n{"allowed": false, "categories": ["赌博诈骗"]}\n```')
        with patch("urllib.request.urlopen", return_value=_FakeResp(body)):
            verdict = provider.check(self.img)
        self.assertFalse(verdict.allowed)

    def test_unparseable_output_is_unavailable_not_allowed(self) -> None:
        """解析不出来必须报不可用，绝不能默认成「通过」。"""
        provider = DashScopeVisionModeration(api_key="sk-test")
        body = _chat_response("这张图片我无法判断")
        with patch("urllib.request.urlopen", return_value=_FakeResp(body)):
            verdict = provider.check(self.img)
        self.assertFalse(verdict.allowed)
        self.assertIn("审核不可用", verdict.reason)

    def test_network_error_is_unavailable_and_not_cached(self) -> None:
        provider = DashScopeVisionModeration(api_key="sk-test")
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            first = provider.check(self.img)
        self.assertFalse(first.allowed)
        # 失败不缓存：下一次必须重新尝试
        body = _chat_response(json.dumps({"allowed": True, "categories": []}))
        with patch("urllib.request.urlopen", return_value=_FakeResp(body)) as mock:
            second = provider.check(self.img)
        self.assertTrue(second.allowed)
        self.assertEqual(mock.call_count, 1)

    def test_same_image_is_cached(self) -> None:
        provider = DashScopeVisionModeration(api_key="sk-test")
        body = _chat_response(json.dumps({"allowed": True, "categories": []}))
        with patch("urllib.request.urlopen", return_value=_FakeResp(body)) as mock:
            provider.check(self.img)
            provider.check(self.img)
        self.assertEqual(mock.call_count, 1, "同一张图不应重复消耗审核额度")

    def test_missing_file_is_unavailable(self) -> None:
        provider = DashScopeVisionModeration(api_key="sk-test")
        verdict = provider.check(self.tmp / "nope.png")
        self.assertFalse(verdict.allowed)
        self.assertIn("审核不可用", verdict.reason)


class _StubSettings:
    def __init__(self, *, mode: str, api_key: str = "", enabled: bool = True) -> None:
        self.moderation_enabled = enabled
        self.moderation_fail_closed = True
        self.moderation_blocklist_file = ""
        self.image_moderation = mode
        self.image_moderation_model = "qwen-vl-plus"
        self.image_moderation_timeout = 10.0

        class _LLM:
            pass

        llm = _LLM()
        llm.api_key = api_key
        llm.base_url = "https://example.invalid/v1"
        self.llm = llm


class TestBuildModeration(unittest.TestCase):
    def test_heuristic_by_default(self) -> None:
        factory = build_moderation(_StubSettings(mode="heuristic"))
        self.assertIsInstance(factory.image, HeuristicImageModeration)

    def test_dashscope_when_key_present(self) -> None:
        factory = build_moderation(_StubSettings(mode="dashscope", api_key="sk-x"))
        self.assertIsInstance(factory.image, DashScopeVisionModeration)

    def test_falls_back_when_key_missing(self) -> None:
        """要求语义审核但没 Key -> 退回启发式，而不是让流水线起不来。"""
        factory = build_moderation(_StubSettings(mode="dashscope", api_key=""))
        self.assertIsInstance(factory.image, HeuristicImageModeration)

    def test_unknown_mode_falls_back(self) -> None:
        factory = build_moderation(_StubSettings(mode="whatever"))
        self.assertIsInstance(factory.image, HeuristicImageModeration)

    def test_verdict_type_used_consistently(self) -> None:
        factory = build_moderation(_StubSettings(mode="heuristic"))
        self.assertIsInstance(factory.check_text("正常文本", scope="t"), ModerationVerdict)


if __name__ == "__main__":
    unittest.main()
