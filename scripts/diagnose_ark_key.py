"""火山方舟 Key 有效性对照诊断。

为什么要做对照实验：
    只用「一个模型调用失败」无法区分三种原因 ——
      (a) Key 本身无效
      (b) Key 有效但模型没开通
      (c) 模型名写错了
    这三种的处理方式完全不同。所以这里用一个**故意写错的 Key** 打同样的接口，
    比较两者的错误码，就能确定到底是哪一类问题。
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://ark.cn-beijing.volces.com/api/v3"


def load_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("REEL_PROVIDERS__LLM__API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("找不到 Key")


def call(client: httpx.Client, key: str, model: str) -> tuple[int, str]:
    try:
        resp = client.post(
            f"{BASE}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 4},
            timeout=45,
        )
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"

    code = ""
    try:
        body = resp.json()
        code = str((body.get("error") or {}).get("code") or "")
    except Exception:
        code = resp.text[:120]
    return resp.status_code, code


def main() -> int:
    real_key = load_key()
    bogus_key = "ark-00000000-0000-0000-0000-000000000000-deadbeef"

    with httpx.Client() as client:
        print("=" * 70)
        print("对照实验：用错误 Key 与真实 Key 打同一个接口")
        print("=" * 70)

        for label, key in (("错误 Key", bogus_key), ("你的 Key", real_key)):
            status, code = call(client, key, "doubao-seed-1-6-250615")
            print(f"\n{label}（{key[:14]}…）：HTTP {status}  code={code or '（无）'}")

        print("\n" + "=" * 70)
        print("结论判读")
        print("=" * 70)
        real_status, real_code = call(client, real_key, "doubao-seed-1-6-250615")
        bogus_status, _bogus_code = call(client, bogus_key, "doubao-seed-1-6-250615")

        if real_status == 401 or "Authentication" in real_code:
            print("→ Key 无效或格式不对。请回到方舟控制台重新复制。")
        elif real_status == 404 and bogus_status == 401:
            print("→ **Key 是有效的**（错误 Key 得到 401，你的 Key 得到 404）。")
            print("  问题在于：账号下没有任何已开通的模型，或者模型名/端点 ID 不对。")
            print("  下一步：到方舟控制台确认「模型广场」里哪些模型显示为『已开通』，")
            print("  并直接复制控制台给出的模型 ID（不是展示名）。")
        elif real_status == 404 and bogus_status == 404:
            print("→ 两种 Key 都返回 404，说明这个接口路径或模型名整体不对，")
            print("  或者该 Key 属于**自定义推理接入点**（需要用 ep-xxxx 形式的端点 ID）。")
        elif real_status == 403:
            print("→ Key 有效但无权限：模型未开通，或开通时选错了分组。")
        elif real_status == 200:
            print("→ 成功（此前失败可能是网络抖动）。")
        else:
            print(f"→ 需要人工判断：status={real_status} code={real_code}")

        # 尝试列出模型（方舟部分区域支持）
        print("\n=== 尝试列出可用模型 ===")
        for path in ("/models", "/foundation_models"):
            try:
                r = client.get(f"{BASE}{path}", headers={"Authorization": f"Bearer {real_key}"}, timeout=30)
                print(f"  GET {path} -> HTTP {r.status_code} {r.text[:200]}")
            except Exception as exc:
                print(f"  GET {path} -> {type(exc).__name__}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
