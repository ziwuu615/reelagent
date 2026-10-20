"""列出火山方舟账号下可用的模型。

方舟的 `/api/v3/models` 会返回该账号可见的模型清单，带 `status` 字段。
`Shutdown` 表示已下线不可用；其余状态（如 `Available`/空）可尝试调用。

这一步能直接回答「我的 Key 到底能用哪些模型」，比猜模型名高效得多。
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://ark.cn-beijing.volces.com/api/v3"

#: 关键词 -> 能力归类
KIND_HINTS = [
    ("seedance", "视频"),
    ("seedream", "图像"),
    ("wan", "视频/图像"),
    ("kling", "视频"),
    ("doubao", "文本/多模态"),
    ("deepseek", "文本"),
    ("qwen", "文本"),
    ("glm", "文本"),
    ("moonshot", "文本"),
    ("embedding", "向量"),
]

#: 明确属于「屏蔽/不可用」的状态
DEAD_STATUS = {"shutdown", "retired", "offline"}


def load_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("REEL_PROVIDERS__LLM__API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("找不到 Key")


def kind_of(model_id: str) -> str:
    low = model_id.lower()
    for hint, label in KIND_HINTS:
        if hint in low:
            return label
    return "其它"


def main() -> int:
    key = load_key()
    resp = httpx.get(f"{BASE}/models", headers={"Authorization": f"Bearer {key}"}, timeout=60)
    resp.raise_for_status()
    items = resp.json().get("data") or []

    alive: dict[str, list[tuple[str, str]]] = defaultdict(list)
    dead_count = 0

    for item in items:
        model_id = str(item.get("id") or "")
        status = str(item.get("status") or "")
        if status.lower() in DEAD_STATUS:
            dead_count += 1
            continue
        alive[kind_of(model_id)].append((model_id, status))

    print("=" * 74)
    print(f"账号可用模型清单（共 {len(items)} 条，已排除 {dead_count} 个下线模型）")
    print("=" * 74)

    if not alive:
        print("\n⚠ 该账号可见的模型全部处于下线状态 —— 需要到「模型广场」申请开通。")
        return 1

    for kind in ("视频", "图像", "视频/图像", "文本/多模态", "文本", "向量", "其它"):
        rows = alive.get(kind)
        if not rows:
            continue
        print(f"\n【{kind}】{len(rows)} 个")
        for model_id, status in sorted(rows):
            tag = f"  [{status}]" if status else ""
            print(f"  {model_id}{tag}")

    print("\n" + "=" * 74)
    print("下一步")
    print("=" * 74)
    video = alive.get("视频", []) + alive.get("视频/图像", [])
    image = alive.get("图像", []) + alive.get("视频/图像", [])
    if video:
        print(f"  推荐视频模型：{video[0][0]}")
    else:
        print("  ✗ 没有可用视频模型 → 去方舟「模型广场」申请开通 Seedance")
    if image:
        print(f"  推荐图像模型：{image[0][0]}")
    else:
        print("  ✗ 没有可用图像模型 → 去方舟「模型广场」申请开通 Seedream")
    print("\n  （注意：清单里出现 ≠ 已开通。能否调用要以实际请求为准）")

    dump = ROOT / "workspace" / "ark_models.json"
    dump.parent.mkdir(parents=True, exist_ok=True)
    dump.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完整清单已存到 {dump.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
