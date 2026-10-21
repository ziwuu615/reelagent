"""回归：跨镜头相似度「没有可度量对象」时必须返回 None，不能返回 1.0。

真实事故（proj_ee0f7be519，7 镜 live）：
规划出来的每个角色都只出现在 1 个镜头里（林晚 1 镜、老人 1 镜），
于是 all_vals 为空，函数返回 1.0，CLI 打印「同线索跨镜头相似度 1.0000」、
综合分被抬到 1.0000 —— 而实际上**一次测量都没做**。

这与同模块 clue_reference_coverage 的处理自相矛盾（那里明确返回 None，
注释写着「"没有可度量的对象"与"全部达标"是两回事」），
也是 README 反复强调的「无数据不等于满分」。
"""

from __future__ import annotations

import unittest

import numpy as np
from PIL import Image

from reelagent.eval.consistency import cross_shot_clue_similarity
from reelagent.schema import Clue, Shot
from tests.support import cleanup_tmp, make_tmp


def _keyframe(path, shift: int = 0, invert: bool = False) -> None:
    """造一张非均匀的关键帧。

    必须是梯度而不是纯色：metrics 内部用「减均值除标准差」做 NCC，
    纯色图 std=0 会被直接判为不可用，正对照会误判成失败。
    """
    row = np.linspace(0, 255, 64, dtype=np.uint8)
    row = np.roll(row, shift)
    if invert:
        row = 255 - row
    arr = np.tile(row, (64, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr, mode="L").convert("RGB").save(path)


def _clue(cid: str, name: str, kind: str = "character") -> Clue:
    return Clue(id=cid, kind=kind, name=name, description="", appearance="外貌描述文本")


class TestSimilarityNotMeasured(unittest.TestCase):
    def test_no_shared_character_returns_none(self) -> None:
        """两个角色各只出现在一个镜头 -> 无可比对象 -> None（而不是 1.0）。"""
        tmp = make_tmp("sim_na")
        try:
            lin_wan = _clue("c_lin", "林晚")
            old_man = _clue("c_old", "老人")
            _keyframe(tmp / "kf1.png", shift=0)
            _keyframe(tmp / "kf2.png", shift=0, invert=True)
            shots = [
                Shot(id="s1", scene_id="sc", index=0, clue_ids=[lin_wan.id], keyframe_path="kf1.png"),
                Shot(id="s2", scene_id="sc", index=1, clue_ids=[old_man.id], keyframe_path="kf2.png"),
            ]
            clues = {lin_wan.id: lin_wan, old_man.id: old_man}

            score, per_clue = cross_shot_clue_similarity(shots, clues, tmp)

            self.assertIsNone(score, "没有可度量对象时必须返回 None，1.0 会把综合分抬成满分假象")
            self.assertEqual(per_clue, {})
        finally:
            cleanup_tmp(tmp)

    def test_same_character_in_two_shots_is_measured(self) -> None:
        """同一角色跨两个镜头 -> 真的测出一个数值（对照组，防止修过头）。"""
        tmp = make_tmp("sim_ok")
        try:
            lin_wan = _clue("c_lin", "林晚")
            _keyframe(tmp / "kf1.png", shift=0)
            _keyframe(tmp / "kf2.png", shift=3)
            shots = [
                Shot(id="s1", scene_id="sc", index=0, clue_ids=[lin_wan.id], keyframe_path="kf1.png"),
                Shot(id="s2", scene_id="sc", index=1, clue_ids=[lin_wan.id], keyframe_path="kf2.png"),
            ]

            score, per_clue = cross_shot_clue_similarity(shots, {lin_wan.id: lin_wan}, tmp)

            self.assertIsNotNone(score)
            assert score is not None
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)
            self.assertIn("林晚", per_clue)
            self.assertEqual(per_clue["林晚"]["shots"], 2)
        finally:
            cleanup_tmp(tmp)


if __name__ == "__main__":
    unittest.main()
