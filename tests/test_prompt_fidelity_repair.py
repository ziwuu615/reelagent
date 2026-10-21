"""回归：Director 必须把线索 appearance 确定性注入 prompt。

真实事故（proj_e2092083e7，live / dashscope qwen-plus）：
3 个镜头里有 2 个的 `prompt_clue_fidelity` 不达标，指标 0.3333，
CI 门禁直接不通过 —— 原因是 LLM 为了行文通顺改写了 appearance：

    sc02  原文 "纯白硬纸盒，正面印褪色蓝字…"
          LLM 写 "一盒纯白硬纸盒牛奶…纸盒正面印有模糊褪色蓝字…"（插字）
    sc03  原文 "瘦高身形，藏青粗布立领衫…"
          LLM 写 "穿藏青粗布立领衫的老人——瘦高身形…"（倒装）

这两个 prompt 是**真实模型的输出**，直接作为本用例的输入。

修法不是重试（概率游戏且要额外付费），而是确定性后置补齐：
缺哪条补哪条。判定口径与 eval.consistency.prompt_clue_fidelity 绑定，
两边用同一个 12 字规则，避免规则漂移。
"""

from __future__ import annotations

import unittest

from reelagent.agents.director import Director
from reelagent.eval.consistency import prompt_clue_fidelity
from reelagent.harness.tracing import Tracer
from reelagent.schema import Clue, Shot


class _StubRT:
    """只为 `_enforce_clue_fidelity` 提供 tracer 的最小运行时。"""

    def __init__(self) -> None:
        self.tracer = Tracer(None, "unit-test")


def _director() -> Director:
    director = Director.__new__(Director)
    director.rt = _StubRT()
    director.verbose = False
    return director


def _clue(cid: str, name: str, appearance: str, kind: str = "character") -> Clue:
    return Clue(id=cid, kind=kind, name=name, description="", appearance=appearance)


def _shot(sid: str, clue_ids: list[str], image_prompt: str) -> Shot:
    return Shot(id=sid, scene_id="sc01", index=0, clue_ids=clue_ids, image_prompt=image_prompt)


# —— 以下是 qwen-plus 真实产出的 prompt（节选自 proj_e2092083e7）——
REAL_MILK_PROMPT = (
    "近景，林晚的手伸入冷柜底层阴影中，指尖拂过积灰纸箱表面，停在一盒纯白硬纸盒牛奶上；"
    "纸盒正面印有模糊褪色蓝字‘晨曦牧场’，生产日期喷码‘20210715’，盒角轻度受潮发软；"
    "冷柜内部幽暗，戏剧性侧逆光勾勒手部轮廓，35mm胶片颗粒质感，浅景深"
)
REAL_OLD_MAN_PROMPT = (
    "中景，穿藏青粗布立领衫的老人——瘦高身形，银白短发整齐贴服，右眉有一道浅陈旧疤痕，"
    "瞳色极淡——正抬眼直视，皱纹如刻，嘴唇微动；固定机位。"
)

MILK = _clue(
    "clue_过期牛奶_be97",
    "过期牛奶",
    "纯白硬纸盒，正面印褪色蓝字‘晨曦牧场’，生产日期喷码为‘20210715’，盒角受潮发软",
    kind="prop",
)
OLD_MAN = _clue(
    "clue_老人_3d5c",
    "老人",
    "瘦高身形，藏青粗布立领衫，银白短发整齐贴服，右眉有一道浅陈旧疤痕，瞳色极淡",
)


class TestPromptFidelityRepair(unittest.TestCase):
    def test_real_paraphrase_is_repaired_to_full_fidelity(self) -> None:
        """真实 LLM 改写后，补齐必须让门禁指标达到 1.0。"""
        director = _director()
        shot = _shot("sc02_sh01", [MILK.id], REAL_MILK_PROMPT)

        before, _ = prompt_clue_fidelity([shot], {MILK.id: MILK})
        self.assertEqual(before, 0.0, "前提：真实 prompt 里含被改写的 appearance")

        shot.image_prompt = director._enforce_clue_fidelity(shot, [MILK])
        after, misses = prompt_clue_fidelity([shot], {MILK.id: MILK})
        self.assertEqual(after, 1.0)
        self.assertEqual(misses, [])

    def test_reordered_appearance_is_repaired(self) -> None:
        """倒装（12 字前缀不再连续）同样必须被补齐。"""
        director = _director()
        shot = _shot("sc03_sh01", [OLD_MAN.id], REAL_OLD_MAN_PROMPT)

        before, _ = prompt_clue_fidelity([shot], {OLD_MAN.id: OLD_MAN})
        self.assertEqual(before, 0.0)

        shot.image_prompt = director._enforce_clue_fidelity(shot, [OLD_MAN])
        after, _ = prompt_clue_fidelity([shot], {OLD_MAN.id: OLD_MAN})
        self.assertEqual(after, 1.0)

    def test_faithful_prompt_is_left_untouched(self) -> None:
        """已经原样注入的 prompt 不能被二次追加（否则会重复堆约束）。"""
        director = _director()
        prompt = f"特写：{MILK.appearance}；灯光嗡鸣"
        shot = _shot("sc01_sh01", [MILK.id], prompt)

        self.assertEqual(director._enforce_clue_fidelity(shot, [MILK]), prompt)

    def test_multiple_shots_reach_100_percent(self) -> None:
        """三个镜头混合（1 个已达标 + 2 个被改写）时整体保真度为 1.0。"""
        director = _director()
        okay = _shot("sc01_sh01", [MILK.id], f"特写：{MILK.appearance}")
        paraphrased_a = _shot("sc02_sh01", [MILK.id], REAL_MILK_PROMPT)
        paraphrased_b = _shot("sc03_sh01", [OLD_MAN.id], REAL_OLD_MAN_PROMPT)
        shots = [okay, paraphrased_a, paraphrased_b]
        clues = {MILK.id: MILK, OLD_MAN.id: OLD_MAN}

        for shot in shots:
            bound = [clues[cid] for cid in shot.clue_ids]
            shot.image_prompt = director._enforce_clue_fidelity(shot, bound)

        score, misses = prompt_clue_fidelity(shots, clues)
        self.assertEqual(score, 1.0)
        self.assertEqual(misses, [])


if __name__ == "__main__":
    unittest.main()
