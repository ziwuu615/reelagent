"""审计代码里所有「声称代表真实成本」的硬编码数字。

规则：任何影响预算、额度预扣或对外成本展示的常量，
要么写明**官方来源**，要么标明它是**从实跑数据推导**的，要么承认它是
**产品决策**（没有外部真值）。三者之外就是来路不明的数，需要处理。

分类动机：早期版本把「限流参数、分页大小、音频采样率」和「真实价格」
混在一起报成「26 项来源不明」——**这个结论本身就是不诚实的**，
因为它把无害的数和危险的钱混为一谈，导致报告失去可操作性。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 可能代表真实价格/成本的标识符特征
PRICE_HINTS = ("cost", "price", "usd", "cny", "credit", "unit", "per_", "budget", "rate")

#: 类别 1：已核对官方文档 —— 有外部真值，可对外声称是真实价格
VERIFIED: dict[str, str] = {
    "VIDEO_PRICE_PER_S": (
        "百炼 wan3.0-video 定价页：480P $0.041256/s、720P $0.082513/s、1080P $0.165025/s（华北2 北京）"
    ),
    "IMAGE_UNIT_COST": "百炼 wan2.5-t2i-preview 定价页：0.2 元/张（华北2 北京）",
    "VIDEO_UNIT_COST_PER_S": "同 VIDEO_PRICE_PER_S 的 480P 档",
    "MODEL_PRICES": (
        "qwen-plus 已核对百炼定价页（北京：输入 $0.115/M、输出 $0.287/M）；"
        "其余模型为估算，代码注释里已逐条标「未核对」"
    ),
}

#: 类别 2：从真实运行数据推导 —— 有内部证据，但依赖样本
DERIVED: dict[str, str] = {
    "AVG_SHOT_DURATION_S": "3 镜实跑：视频总时长 10.0 秒 / 3 镜 ≈ 3.33 秒，取 3.5",
    "AVG_LLM_TOKENS_PER_CALL": "3 镜实跑：5 次调用共 4836 token ≈ 970/次，取 1000",
    "AVG_TTS_CHARS_PER_SHOT": "3 镜实跑：40 字符 / 3 镜 ≈ 13，取 15",
    "IMAGE_UNIT_COST": "同官方定价页",
    "TTS_UNIT_COST_PER_1K_CHAR": "百炼 qwen3-tts-flash 定价页：0.8 元/万字符",
}

#: 类别 3：产品决策 —— 没有外部真值，只有内部一致性要求
PRODUCT_DECISION: dict[str, str] = {
    "credits_per_usd": "产品定价：1 美元 = 100 点数（毛利率决策，非外部事实）",
    "job_budget_usd": "单片预算上限，用于保护用户不被单个任务烧穿",
    "budget_usd": "引擎侧单片预算上限",
    "QUOTE_SAFETY_FACTOR": "预估安全系数：宁可多预留，不可中途断粮",
    "premium_uplift": "premium 档的额度上浮（约等于 720P/480P 的 2 倍价差，留少量余量）",
    "NATIVE_AUDIO_BED_GAIN": "原生音轨作背景的音量系数（-18dB 声学惯例）",
    "THINKING_OUTPUT_MULTIPLIER": "思考模式输出价约为非思考的 4 倍（qwen-plus 北京实测定价）",
    "max_shots_per_project": "套餐限制",
    "max_base_images": "成本控制：限制线索基准图数量",
    "CNY_PER_USD": "汇率假设（非实时汇率）。误差约 ±3%，见 reelagent/providers/base.py 注释",
}

#: 类别 4：不是价格 —— 纯技术参数，不该出现在成本审计里
NOT_A_PRICE: set[str] = {
    "per_page",
    "anonymous_per_minute",
    "user_per_minute",
    "project_creates_per_hour",
    "api_key_per_minute",
    "sample_rate",
    "rate_limit_base_delay",
    "rate_limit_max_delay",
    "quota_base_delay",
    "quota_max_delay",
    "context_budget",
    "credits_reserved",
    "provider_cost_usd",
    "cost_usd",
    "usd",
    "total_cost_usd",
    "budget",
    "chars_per_sec",
    "record_cost",
    "enqueue",
    "complete",
    "__init__",
}


def classify(name: str) -> str:
    """把标识符归入四类之一。"""
    base = name.split("(")[0]
    if base in NOT_A_PRICE:
        return "not_a_price"
    if base in VERIFIED:
        return "verified"
    if base in DERIVED:
        return "derived"
    if base in PRODUCT_DECISION:
        return "decision"
    return "unknown"


def scan(path: Path) -> list[tuple[int, str, str]]:
    """返回 [(行号, 变量名, 值字面量)]。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    found: list[tuple[int, str, str]] = []

    def interesting(name: str) -> bool:
        return any(h in name.lower() for h in PRICE_HINTS)

    def literal_of(node: ast.expr) -> str | None:
        """标量与字典都要能取出来 —— 价格表通常是字典。"""
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return repr(node.value)
        if isinstance(node, ast.Dict):
            pairs = [
                f"{k.value}={v.value}"
                for k, v in zip(node.keys, node.values, strict=False)
                if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)
            ]
            return "{" + ", ".join(pairs) + "}" if pairs else None
        return None

    for node in ast.walk(tree):
        # 带类型注解的赋值（`X: dict[str, float] = {...}`）是 AnnAssign，
        # 不是 Assign —— 只扫 Assign 会把价格表这类最关键的常量整个漏掉。
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if value is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and interesting(target.id):
                    lit = literal_of(value)
                    if lit is not None:
                        found.append((node.lineno, target.id, lit))

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 位置参数的默认值从**右边**对齐（有前置无默认参数时 zip 会错位）
            args, defaults = node.args.args, node.args.defaults
            pairs = list(zip(args[len(args) - len(defaults) :], defaults, strict=False))
            # `*` 之后的仅关键字参数在 kwonlyargs 里，默认值在 kw_defaults 里 ——
            # credits.quote_project(..., *, avg_provider_cost_per_shot_usd=0.14)
            # 就是这种，只扫 args 会漏掉。
            pairs += [
                (a, d)
                for a, d in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=False)
                if d is not None
            ]
            for arg, default in pairs:
                if interesting(arg.arg):
                    lit = literal_of(default)
                    if lit is not None:
                        found.append((default.lineno, f"{node.name}({arg.arg})", lit))
    return found


def main() -> None:
    targets = sorted((ROOT / "reelagent").rglob("*.py")) + sorted((ROOT / "reelagent_cloud").rglob("*.py"))
    buckets: dict[str, list[tuple[str, int, str, str]]] = {
        "verified": [],
        "derived": [],
        "decision": [],
        "not_a_price": [],
        "unknown": [],
    }
    for path in targets:
        for lineno, name, value in scan(path):
            buckets[classify(name)].append((path.relative_to(ROOT).as_posix(), lineno, name, value))

    print("=" * 96)
    print("成本/价格类硬编码常量审计")
    print("=" * 96)
    print()
    print("  四类分开看，因为它们的「真实性」要求不同：")
    print("    [官方]  有外部真值，可对外声称是真实价格")
    print("    [推导]  从实跑数据算出，有内部证据但依赖样本")
    print("    [决策]  产品定价/预算，没有外部真值，只要求内部一致")
    print("    [非价格] 限流、分页、采样率等技术参数")

    def dump(key: str, title: str, sources: dict[str, str] | None = None) -> None:
        items = buckets[key]
        print()
        print(f"【{title}】{len(items)} 项")
        print("-" * 96)
        for rel, lineno, name, value in items:
            print(f"  {rel}:{lineno}   {name} = {value}")
            if sources:
                note = sources.get(name.split("(")[0])
                if note:
                    print(f"      说明: {note[:88]}")

    dump("verified", "官方定价（已核对）", VERIFIED)
    dump("derived", "从实跑数据推导", DERIVED)
    dump("decision", "产品决策（无外部真值）", PRODUCT_DECISION)
    dump("not_a_price", "非价格（技术参数）")

    unknown = buckets["unknown"]
    print()
    print(f"【来路不明】{len(unknown)} 项  ← 需要处理的就是这些")
    print("-" * 96)
    for rel, lineno, name, value in unknown:
        print(f"  {rel}:{lineno}   {name} = {value}")
    if not unknown:
        print("  （无）")

    total = sum(len(v) for v in buckets.values())
    print()
    print("=" * 96)
    print(
        f"合计 {total} 项：官方 {len(buckets['verified'])}、推导 {len(buckets['derived'])}、"
        f"决策 {len(buckets['decision'])}、非价格 {len(buckets['not_a_price'])}、"
        f"**不明 {len(unknown)}**"
    )
    print("=" * 96)


if __name__ == "__main__":
    main()
