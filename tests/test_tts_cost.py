"""回归：DashScope TTS 的成本必须按字符估算，不能写死 0。

真实事故（proj_e2092083e7，live）：
3 次语音合成调用共 40 字符，costs 表里 tts 三行全部记成 $0.0，
而同样语义的 ark.py TTS 早就用了 estimate_tts_cost。
结果是成本对账里 tts 一栏恒为 0：单条短片看不出，量大后账本与真实支出对不上。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import reelagent.providers.dashscope as ds
from reelagent.providers.base import MediaResult, estimate_tts_cost
from tests.support import cleanup_tmp, make_tmp


class TestDashScopeTTSCost(unittest.TestCase):
    def _generate(self, text: str) -> MediaResult:
        provider = ds.DashScopeTTSProvider(api_key="sk-unit-test")
        tmp = make_tmp("tts")
        try:
            out = tmp / "a.wav"
            fake_body = {"output": {"audio": {"url": "https://example.invalid/a.wav"}}}
            with (
                patch.object(ds, "_request_json", return_value=fake_body),
                patch.object(ds, "_download", side_effect=lambda _url, p: p.write_bytes(b"RIFF0000WAVE")),
            ):
                return provider.generate(text=text, out_path=out)
        finally:
            cleanup_tmp(tmp)

    def test_cost_scales_with_text_length(self) -> None:
        text = "她用指尖拂过货架最底层的纸盒，停在一盒过期牛奶上。"
        result = self._generate(text)

        self.assertGreater(result.cost_usd, 0.0, "TTS 成本不能是 0")
        self.assertAlmostEqual(result.cost_usd, estimate_tts_cost(text), places=10)

    def test_longer_text_costs_more(self) -> None:
        short = self._generate("一句话旁白。")
        long = self._generate("一句话旁白。" * 20)
        self.assertGreater(long.cost_usd, short.cost_usd)


if __name__ == "__main__":
    unittest.main()
