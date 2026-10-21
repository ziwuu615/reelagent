"""零成本验证：把 _enforce_clue_fidelity 作用到真实项目 proj_e2092083e7，
用真实的 eval 指标重新计算保真度，确认门禁会从 0.3333 变成 1.0。

不需要重新调用任何模型 —— 直接复用已落盘的 prompt / 线索。
"""

import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from reelagent.config import Settings
from reelagent.agents.director import Director
from reelagent.eval.consistency import prompt_clue_fidelity
from reelagent.harness.state import Store
from reelagent.harness.tracing import Tracer


class _StubRT:
    def __init__(self):
        self.tracer = Tracer(None, "sim")


st = Settings.load()
store = Store(st.workspace / "reelagent.db")
proj = store.load_project("proj_e2092083e7")
shots = proj.script.shots
clues = {c.id: c for c in proj.clues}

before, misses_before = prompt_clue_fidelity(shots, clues)
print(f"修复前 prompt_clue_fidelity = {before}")
for m in misses_before:
    print("   缺:", m)

director = Director.__new__(Director)
director.rt = _StubRT()
director.verbose = False

for s in shots:
    bound = [clues[cid] for cid in s.clue_ids if cid in clues]
    old = s.image_prompt
    new = director._enforce_clue_fidelity(s, bound)
    if new != old:
        added = new[len(old):].strip("；")
        print(f"\n[{s.id}] 补齐 {len(added)} 字 -> {added[:110]}…")
        s.image_prompt = new

after, misses_after = prompt_clue_fidelity(shots, clues)
print(f"\n修复后 prompt_clue_fidelity = {after}   miss={misses_after}")
print("门禁阈值 0.95 ->", "通过" if (after or 0) >= 0.95 else "未通过")
