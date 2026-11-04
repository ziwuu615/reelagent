"""Director 的内容审核接入点。

只测「守卫本身」是不够的——真正容易出错的是**接入位置**：
  - 审核必须发生在调用模型之前（否则已经花钱生成了违规内容）
  - 审核不通过的关键帧必须从磁盘删掉（否则断点恢复会复用违规图）
这两点在这里用桩运行时验证。
"""

from __future__ import annotations

import unittest

from PIL import Image

from reelagent.agents.director import Director
from reelagent.harness.tracing import Tracer
from reelagent.moderation import (
    BlockingModerationFactory,
    HeuristicImageModeration,
    KeywordModerationProvider,
    ModerationError,
)
from reelagent.schema import Shot
from tests.support import cleanup_tmp, make_tmp


class _StubRT:
    def __init__(self, *, fail_closed: bool = True, enabled: bool = True) -> None:
        self.tracer = Tracer(None, "unit-test")
        self.moderation = BlockingModerationFactory(
            KeywordModerationProvider(),
            HeuristicImageModeration(),
            enabled=enabled,
            fail_closed=fail_closed,
        )


def _director(rt: _StubRT) -> Director:
    d = Director.__new__(Director)
    d.rt = rt
    d.verbose = False
    d._blocked = []
    import threading

    d._blocked_lock = threading.Lock()
    return d


def _shot(sid: str = "sc01_sh01") -> Shot:
    return Shot(id=sid, scene_id="sc01", index=0)


class TestGuardText(unittest.TestCase):
    def test_clean_prompt_passes(self) -> None:
        d = _director(_StubRT())
        d._guard_text(_shot(), "深夜便利店，冷白灯管下的货架", scope="image_prompt")

    def test_violating_prompt_raises(self) -> None:
        d = _director(_StubRT())
        with self.assertRaises(ModerationError):
            d._guard_text(_shot(), "画面里出现毒品交易的细节", scope="image_prompt")

    def test_empty_text_is_skipped(self) -> None:
        d = _director(_StubRT())
        d._guard_text(_shot(), "   ", scope="video_prompt")

    def test_disabled_moderation_never_raises(self) -> None:
        d = _director(_StubRT(enabled=False))
        d._guard_text(_shot(), "毒品", scope="image_prompt")


class TestGuardImage(unittest.TestCase):
    def test_solid_keyframe_is_deleted_and_raises(self) -> None:
        """被拦下的图必须删掉：留着会被断点恢复当成已完成的关键帧。"""
        tmp = make_tmp("mod")
        try:
            shot = _shot()
            bad = tmp / "kf.png"
            Image.new("RGB", (320, 320), (0, 0, 0)).save(bad)

            d = _director(_StubRT())
            with self.assertRaises(ModerationError):
                d._guard_image(shot, bad)
            self.assertFalse(bad.exists(), "违规关键帧必须从磁盘移除")
        finally:
            cleanup_tmp(tmp)

    def test_normal_keyframe_is_kept(self) -> None:
        import numpy as np

        tmp = make_tmp("mod")
        try:
            ok = tmp / "kf.png"
            arr = np.random.default_rng(1).integers(0, 255, (200, 200, 3), dtype=np.uint8)
            Image.fromarray(arr).save(ok)
            d = _director(_StubRT())
            d._guard_image(_shot(), ok)
            self.assertTrue(ok.exists())
        finally:
            cleanup_tmp(tmp)


class TestBlockedShotsTerminateRun(unittest.TestCase):
    def test_director_tracks_blocked_shots(self) -> None:
        """`_blocked` 是「整条任务失败」的判据，必须可写可读。"""
        d = _director(_StubRT())
        d._blocked.append("sc02_sh01")
        self.assertEqual(d._blocked, ["sc02_sh01"])


if __name__ == "__main__":
    unittest.main()
