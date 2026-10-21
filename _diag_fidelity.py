import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from reelagent.config import Settings
from reelagent.harness.state import Store

st = Settings.load()
pid = "proj_e2092083e7"
store = Store(st.workspace / "reelagent.db")
proj = store.load_project(pid)
print("标题:", proj.script.title, "| 总时长:", proj.script.total_duration_s)
print("线索:")
for c in proj.clues:
    print(f"  - {c.name} [{c.kind}] appearance='{(c.appearance or '')[:40]}' base_image={bool(c.base_image)}")

print("\n镜头与 appearance 注入:")
for sc in proj.script.scenes:
    for s in sc.shots:
        prompt = s.image_prompt or ""
        bound = [c for c in proj.clues if c.id in (s.clue_ids or [])]
        print(f"\n--- {s.id} [{s.shot_size}/{s.camera}] status={s.status} {s.provider_used}/{s.model_used} ${s.cost_usd:.5f}")
        print(f"    clue_ids={s.clue_ids} -> {[(c.name, c.kind) for c in bound]}")
        print(f"    image_prompt({len(prompt)}字): {prompt[:300]}")
        for c in bound:
            app = (c.appearance or "").strip()
            if not app:
                print(f"    !! {c.name}: appearance 为空")
                continue
            head = app[:12]
            print(f"    !! {c.name}: appearance[:12]='{head}' -> 命中={head in prompt}")
