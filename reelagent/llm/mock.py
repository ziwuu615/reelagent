"""离线 mock LLM。

用途：让整条流水线在**没有任何 API Key** 的情况下端到端跑通，
产出真实的剧本 JSON / prompt / 评测报告，而不是一堆占位符。

它不是"随机返回"，而是基于输入的确定性启发式实现：
同一份种子文本永远得到同一个剧本，因此可复现、可写测试。
"""

from __future__ import annotations

import json
import re
import time

from ..schema import Usage
from .base import LLMClient, LLMResponse, Message, estimate_tokens

_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")
_NAME_PATTERNS = [
    re.compile(r"[「『\"“']([^」』\"”']{1,6})[」』\"”']"),
    re.compile(r"([\u4e00-\u9fff]{2,3})(?:说|问|答|喊|笑|哭|走|跑|看)"),
]
_PROP_WORDS = ["钥匙", "信", "相机", "手机", "照片", "车票", "戒指", "书", "伞", "行李箱", "咖啡", "琴"]
_SHOT_SIZES = ["全景", "中景", "近景", "特写", "中景", "近景"]
_CAMERAS = ["固定", "推", "固定", "移", "固定", "跟"]
_TIME_WORDS = ["清晨", "午后", "黄昏", "深夜", "正午", "黎明"]


def _split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT.split(text)]
    return [p for p in parts if len(p) >= 2]


def _extract_names(text: str) -> list[str]:
    found: list[str] = []
    for pat in _NAME_PATTERNS:
        for m in pat.finditer(text):
            name = m.group(1).strip()
            if name and name not in found and len(name) <= 6:
                found.append(name)
    return found[:3]


def _extract_props(text: str) -> list[str]:
    return [w for w in _PROP_WORDS if w in text][:2]


class MockLLM(LLMClient):
    """确定性离线 LLM。"""

    def __init__(self, model: str = "mock-llm", *, latency_ms: float = 0.0) -> None:
        self.model = model
        self.latency_ms = latency_ms

    # ------------------------------------------------------------------ #
    def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        json_mode: bool = False,
        task: str = "",
    ) -> LLMResponse:
        started = time.perf_counter()
        user = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        system = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")

        handler = {
            "plan_script": self._plan_script,
            "art_direction": self._art_direction,
            "direct_prompt": self._direct_prompt,
            "judge": self._judge,
            "compress": self._compress,
        }.get(task, self._generic)

        text = handler(user, system)

        usage = Usage(
            prompt_tokens=estimate_tokens(system + user),
            completion_tokens=estimate_tokens(text),
        )
        self.total_usage = self.total_usage + usage
        elapsed = (time.perf_counter() - started) * 1000 + self.latency_ms
        return LLMResponse(text=text, model=self.model, usage=usage, latency_ms=elapsed, cost_usd=0.0)

    # ------------------------------------------------------------------ #
    # 各任务实现
    # ------------------------------------------------------------------ #
    def _plan_script(self, user: str, system: str) -> str:
        seed = self._extract_seed(user)
        max_shots = self._extract_int(user, "镜头总数上限", 8)
        self._extract_line(user, "目标风格") or "cinematic"

        sentences = _split_sentences(seed) or [seed.strip() or "一个没有内容的镜头"]
        names = _extract_names(seed)
        props = _extract_props(seed)

        protagonist = names[0] if names else "主角"
        companion = names[1] if len(names) > 1 else ""

        # 分场：每 2-3 句一场
        per_scene = 2 if len(sentences) > 4 else max(1, len(sentences) // 2 or 1)
        scenes_raw: list[list[str]] = []
        for i in range(0, len(sentences), per_scene):
            scenes_raw.append(sentences[i : i + per_scene])
        if not scenes_raw:
            scenes_raw = [[seed]]

        # 压到 max_shots 以内
        budget = max_shots
        scenes: list[dict] = []
        clues: dict[str, dict] = {}
        shot_global = 0

        for si, group in enumerate(scenes_raw):
            if budget <= 0:
                break
            tod = _TIME_WORDS[si % len(_TIME_WORDS)]
            scene_title = f"第{si + 1}场·{tod}"
            scene_clue = f"{tod}的街景"
            clues.setdefault(
                scene_clue,
                {
                    "name": scene_clue,
                    "kind": "scene",
                    "description": f"{tod}的环境",
                    "appearance": f"{tod}，光线方向一致，环境元素固定不变",
                },
            )
            shots: list[dict] = []
            for sent in group:
                if budget <= 0:
                    break
                shot_global += 1
                budget -= 1
                idx = shot_global
                clue_names = [protagonist, scene_clue]
                if companion and idx % 3 == 0:
                    clue_names.insert(1, companion)
                if props and idx % 2 == 0:
                    clue_names.append(props[idx % len(props)])
                shots.append(
                    {
                        "duration_s": 3.0 if idx % 2 else 2.5,
                        "shot_size": _SHOT_SIZES[(idx - 1) % len(_SHOT_SIZES)],
                        "camera": _CAMERAS[(idx - 1) % len(_CAMERAS)],
                        "action": sent.strip(),
                        "narration": sent.strip() if idx % 2 == 1 else "",
                        "dialogue": ""
                        if idx % 2 == 1
                        else (f"{protagonist}：{sent.strip()[:18]}" if companion or idx % 4 == 0 else ""),
                        "clue_names": clue_names,
                    }
                )
            if shots:
                scenes.append(
                    {
                        "title": scene_title,
                        "summary": "".join(group)[:60],
                        "location": f"{tod}的场景",
                        "time_of_day": tod,
                        "shots": shots,
                    }
                )

        # 主角与道具线索
        if True:
            clues.setdefault(
                protagonist,
                {
                    "name": protagonist,
                    "kind": "character",
                    "description": "短片主角",
                    "appearance": "",
                },
            )
        if companion:
            clues.setdefault(
                companion,
                {"name": companion, "kind": "character", "description": "配角", "appearance": ""},
            )
        for p in props:
            clues.setdefault(p, {"name": p, "kind": "prop", "description": "关键道具", "appearance": ""})

        payload = {
            "title": (sentences[0][:14] if sentences else "无题"),
            "logline": seed.strip().replace("\n", " ")[:60],
            "scenes": scenes,
            "clues": list(clues.values()),
        }
        return json.dumps(payload, ensure_ascii=False)

    def _art_direction(self, user: str, system: str) -> str:
        # 从用户消息里抓出 name 列表
        names = re.findall(r"-\s*([^（(\n]+?)\s*[（(](character|prop|scene)[)）]", user)
        out = []
        for name, kind in names:
            name = name.strip()
            out.append({"name": name, "appearance": self._appearance(name, kind)})
        if not out:
            out = [{"name": "主角", "appearance": self._appearance("主角", "character")}]
        return json.dumps({"clues": out}, ensure_ascii=False)

    @staticmethod
    def _appearance(name: str, kind: str) -> str:
        if kind == "character":
            h = sum(ord(c) for c in name)
            hair = ["黑色短发", "栗色长卷发", "深棕色马尾", "银灰色寸头"][h % 4]
            wear = ["深蓝色立领风衣", "米白色粗针织毛衣", "深灰色连帽卫衣", "卡其色工装夹克"][h % 4]
            return f"成年人类，中等身形，{hair}，单眼皮，鼻梁挺直，{wear}，整体低饱和配色"
        if kind == "prop":
            h = sum(ord(c) for c in name)
            mat = ["黄铜", "深色皮革", "磨砂金属", "原木"][h % 4]
            return f"小型道具，{mat}材质，表面有细微使用痕迹，冷调高光，尺寸恒定"
        h = sum(ord(c) for c in name)
        amb = ["狭窄的老式公寓走廊", "临街的咖啡店室内", "雨后湿润的城市街道", "空旷的天台"][h % 4]
        return f"实景空间，{amb}，固定陈设与配色，光线方向全程一致"

    def _direct_prompt(self, user: str, system: str) -> str:
        action = self._extract_line(user, "画面动作") or "主体在画面中移动"
        shot_size = self._extract_line(user, "景别") or "中景"
        camera = self._extract_line(user, "运镜") or "固定"
        style = self._extract_line(user, "风格") or "cinematic"
        aspect = self._extract_line(user, "画幅") or "9:16"

        clue_block = user.split("本镜头绑定的线索", 1)[-1]
        clue_ap = re.findall(r"：\s*(.+?)(?=\n-\s|\n\n|请输出)", clue_block, re.S)
        clue_desc = "；".join(x.strip().replace("\n", " ") for x in clue_ap if x.strip())[:220]

        base = f"{aspect} 竖幅，{shot_size}，{action}"
        if clue_desc:
            base += f"，画面主体固定特征：{clue_desc}"
        image_prompt = f"{base}，{style} 风格，构图稳定"

        motion = (
            "缓慢推进"
            if camera == "推"
            else "缓慢拉远"
            if camera == "拉"
            else "轻微横移"
            if camera == "移"
            else "跟随主体"
            if camera == "跟"
            else "机位固定"
        )
        video_prompt = f"{motion}，主体持续{action}，运动速度自然，首尾帧与关键帧一致，{style} 风格"
        return json.dumps({"image_prompt": image_prompt, "video_prompt": video_prompt}, ensure_ascii=False)

    def _judge(self, user: str, system: str) -> str:
        rows = [ln for ln in user.splitlines() if ln.strip().startswith("|") or "镜头" in ln]
        n = max(len(rows) - 1, 1)
        has_narration = "旁白" in user
        has_dialogue = "台词" in user
        distinct_sizes = len(set(re.findall(r"特写|近景|中景|全景|远景", user)))

        narrative = min(5, 3 + (1 if has_narration else 0) + (1 if n >= 4 else 0))
        consistency = min(
            5, 3 + (1 if "appearance" in user.lower() or "固定特征" in user else 0) + (1 if n <= 8 else 0)
        )
        rhythm = min(5, 2 + min(distinct_sizes, 3))
        fidelity = min(5, 3 + (1 if "prompt" in user.lower() else 0) + (1 if has_dialogue else 0))
        overall = round((narrative + consistency + rhythm + fidelity) / 4, 2)

        issues = []
        if distinct_sizes < 3:
            issues.append("景别种类偏少，容易造成视觉疲劳")
        if not has_narration:
            issues.append("缺少旁白串联，场景切换可能显得突兀")
        if n > 8:
            issues.append("镜头数偏多，单条短视频内信息密度过高")
        if not issues:
            issues.append("整体结构完整，可进一步打磨转场衔接")

        payload = {
            "narrative_coherence": {"score": narrative, "reason": f"{n} 个镜头，叙事推进基本清晰"},
            "visual_consistency": {"score": consistency, "reason": "线索外观描述已嵌入各镜头提示词"},
            "rhythm": {"score": rhythm, "reason": f"使用了 {distinct_sizes} 种景别"},
            "prompt_fidelity": {"score": fidelity, "reason": "分镜意图与生成提示词对应"},
            "overall": overall,
            "top_issues": issues[:2],
            "suggestions": [
                "为情绪转折处增加一个特写镜头",
                "统一各场次的时段描述以强化时间连续感",
            ],
        }
        return json.dumps(payload, ensure_ascii=False)

    def _compress(self, user: str, system: str) -> str:
        body = user.split("---", 1)[-1].rsplit("---", 1)[0].strip()
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        head = " / ".join(lines[:3])[:80]
        tail = " / ".join(lines[-2:])[:80]
        return f"[压缩摘要] 早期：{head}；最新：{tail}；共 {len(lines)} 条记录已折叠。"

    def _generic(self, user: str, system: str) -> str:
        return json.dumps({"ok": True, "echo": user[:120]}, ensure_ascii=False)

    # ------------------------------------------------------------------ #
    # 解析辅助
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_seed(user: str) -> str:
        m = re.search(r"素材如下：\s*-{3,}\s*(.+?)\s*-{3,}", user, re.S)
        return m.group(1).strip() if m else user.strip()

    @staticmethod
    def _extract_int(user: str, label: str, default: int) -> int:
        m = re.search(rf"{label}：\s*(\d+)", user)
        return int(m.group(1)) if m else default

    @staticmethod
    def _extract_line(user: str, label: str) -> str:
        m = re.search(rf"^{re.escape(label)}：\s*(.*)$", user, re.M)
        return m.group(1).strip() if m else ""
