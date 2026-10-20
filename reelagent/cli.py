"""ReelAgent 命令行入口。

python -m reelagent demo                  # 内置样例，一键跑通全流程
python -m reelagent run <文本|文件路径>    # 新建项目并生成
python -m reelagent resume <项目ID>        # 从断点继续
python -m reelagent status <项目ID>        # 阶段/镜头/成本
python -m reelagent spans <项目ID>         # 链路追踪与耗时归因
python -m reelagent list                   # 项目列表
python -m reelagent tools                  # 已注册工具（MCP 兼容结构）
python -m reelagent config                 # 当前生效配置
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings, load_dotenv
from .pipeline import Pipeline

DEMO_STORY = """
深夜的便利店只剩下最后一盏灯。林晚把湿透的伞收在门口，走向货架。
她在最底层找到了一盒过期的牛奶，生产日期是三年前的今天。
收银台后的老人抬起头，说这盒牛奶他等了很久。
林晚打开盒盖，里面没有牛奶，只有一把小小的黄铜钥匙。
钥匙上刻着一串数字，正好是她的生日。
她抬起头，便利店已经空了，只有门外的雨还在下。
"""


# --------------------------------------------------------------------------- #
# 输出辅助
# --------------------------------------------------------------------------- #
def hr(title: str = "") -> None:
    width = 68
    if title:
        print(f"\n{title} ".ljust(width, "─"))
    else:
        print("─" * width)


def kv(pairs: dict, indent: int = 2) -> None:
    pad = " " * indent
    width = max((len(str(k)) for k in pairs), default=0)
    for k, v in pairs.items():
        print(f"{pad}{str(k).ljust(width)} : {v}")


def fmt_duration(ms: float) -> str:
    return f"{ms / 1000:.1f}s" if ms < 60000 else f"{ms / 60000:.1f}min"


# --------------------------------------------------------------------------- #
# 命令实现
# --------------------------------------------------------------------------- #
def cmd_config(args: argparse.Namespace) -> int:
    settings = Settings.load()
    hr("生效配置")
    kv(settings.describe(), indent=0)
    print()
    hr("风格预设")
    from .config import STYLE_PRESETS

    for name, spec in STYLE_PRESETS.items():
        mark = "*" if name == settings.style else " "
        print(f" {mark} {name:<12} {spec['label']}")
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    settings = Settings.load()
    pipe = Pipeline(settings, verbose=False)
    from .runtime import Runtime

    with pipe._store() as store:
        rt = Runtime(settings, "_tools_probe", store=store)
        specs = rt.tools.specs()
        rt.close()
    hr(f"已注册工具（{len(specs)} 个，结构符合 MCP tools/list）")
    for spec in specs:
        print(f"\n  • {spec['name']}")
        print(f"    {spec['description']}")
        props = spec["inputSchema"].get("properties", {})
        required = set(spec["inputSchema"].get("required", []))
        for pname, pspec in props.items():
            flag = "必填" if pname in required else "可选"
            print(f"      - {pname} ({pspec.get('type')}, {flag}): {pspec.get('description', '')}")
    return 0


def _run(seed: str, args: argparse.Namespace) -> int:
    overrides = {}
    if getattr(args, "style", None):
        overrides["style"] = args.style
    if getattr(args, "aspect", None):
        overrides["aspect_ratio"] = args.aspect
    if getattr(args, "budget", None) is not None:
        overrides["budget_usd"] = args.budget
    if getattr(args, "concurrency", None) is not None:
        overrides["max_concurrency"] = args.concurrency
    if getattr(args, "mode", None):
        overrides["mode"] = args.mode

    settings = Settings.load(**overrides)
    pipe = Pipeline(settings)

    hr("运行配置")
    kv(settings.describe(), indent=0)

    project = pipe.create(seed, style=settings.style, aspect_ratio=settings.aspect_ratio)
    print(f"\n项目 ID：{project.id}")

    result = pipe.run(
        project.id,
        from_stage=getattr(args, "from_stage", None),
        force_tier=getattr(args, "tier", None),
        use_judge=not getattr(args, "no_judge", False),
        max_shots=getattr(args, "max_shots", 8),
        max_base_images=getattr(args, "max_base_images", 6),
    )
    _print_result(result, settings)
    return 0 if not any(s["status"] == "failed" for s in result["stages"]) else 1


def cmd_run(args: argparse.Namespace) -> int:
    raw = args.seed
    p = Path(raw)
    if p.exists() and p.is_file():
        seed = p.read_text(encoding="utf-8")
        print(f"已从文件读取素材：{p}（{len(seed)} 字）")
    else:
        seed = raw
    return _run(seed, args)


def cmd_demo(args: argparse.Namespace) -> int:
    print("使用内置样例故事运行完整流水线…")
    return _run(DEMO_STORY, args)


def cmd_resume(args: argparse.Namespace) -> int:
    settings = Settings.load()
    pipe = Pipeline(settings)
    result = pipe.run(
        args.project_id,
        from_stage=args.from_stage,
        force_tier=getattr(args, "tier", None),
        use_judge=not getattr(args, "no_judge", False),
    )
    _print_result(result, settings)
    return 0 if not any(s["status"] == "failed" for s in result["stages"]) else 1


def _print_result(result: dict, settings: Settings) -> None:
    hr("阶段执行")
    for st in result["stages"]:
        icon = {"done": "✓", "skipped": "↷", "failed": "✗"}.get(st["status"], "?")
        line = f"  {icon} {st['stage']:<9} {st['status']:<8} {fmt_duration(st['duration_ms']):>8}"
        if st.get("error"):
            line += f"   {st['error'][:80]}"
        print(line)

    proj = result["project"]
    hr("成片产物")
    outputs = {
        k: v
        for k, v in {
            "MP4": proj.get("final_video"),
            "GIF": proj.get("final_gif"),
            "音轨": proj.get("final_audio"),
        }.items()
        if v
    }
    if outputs:
        for k, v in outputs.items():
            print(f"  {k:<6} {settings.abs(v)}")
    else:
        print("  （无成片输出，请检查 generate 阶段是否成功）")

    ev = proj.get("evaluation") or {}
    if ev:
        m = ev.get("metrics") or {}
        hr("评测指标")
        kv(
            {
                "综合分": fmt_metric(m.get("overall")),
                "色调稳定性": fmt_metric(m.get("palette_stability")),
                "同线索跨镜头相似度": fmt_metric(m.get("cross_shot_clue_similarity")),
                "基准图注入覆盖率": fmt_metric(m.get("clue_reference_coverage")),
                "prompt 线索保真度": fmt_metric(m.get("prompt_clue_fidelity")),
                "一致性策略生效": "是" if m.get("consistency_strategy_active") else "否",
                "LLM 评审总分": fmt_metric((ev.get("judge") or {}).get("overall")),
                "CI 门禁": "通过" if (ev.get("gate") or {}).get("passed") else "未通过",
            },
            indent=2,
        )
        for f in (ev.get("gate") or {}).get("failures", []):
            print(f"    ✗ {f}")

    cost = (result.get("summary") or {}).get("cost") or {}
    hr("成本")
    print(f"  总计：${cost.get('total_usd', 0):.4f}   （预算上限 ${settings.budget_usd:.2f}）")
    for cat, info in (cost.get("by_category") or {}).items():
        print(f"    {cat:<8} ${info['usd']:.4f}")
    print(f"\n查看追踪：python -m reelagent spans {result['project_id']}")
    print(f"继续/重跑：python -m reelagent resume {result['project_id']}")


def fmt_metric(value) -> str:
    """None 表示该维度没有可度量的对象，显示 N/A 而不是伪装成满分。"""
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def cmd_list(args: argparse.Namespace) -> int:
    settings = Settings.load()
    pipe = Pipeline(settings, verbose=False)
    rows = pipe.list_projects(args.limit)
    if not rows:
        print("暂无项目。先跑 `python -m reelagent demo`。")
        return 0
    hr(f"项目列表（最近 {len(rows)} 个）")
    print(f"  {'项目ID':<22} {'状态':<16} {'标题':<20} 素材")
    for r in rows:
        print(f"  {r['id']:<22} {r['status']:<16} {r['title'][:18]:<20} {r['seed'][:24]}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    settings = Settings.load()
    pipe = Pipeline(settings, verbose=False)
    summary = pipe.status(args.project_id)
    hr(f"项目 {args.project_id}")
    kv(
        {
            "镜头总数": summary["shots"]["total"],
            "已完成镜头": summary["shots"]["done"],
            "失败镜头": summary["shots"]["failed"],
            "span 数量": summary["spans"],
        },
        indent=0,
    )
    hr("阶段")
    for st in summary["stages"]:
        icon = {"done": "✓", "running": "▶", "failed": "✗", "pending": "·"}.get(st["status"], "?")
        print(f"  {icon} {st['name']:<9} {st['status']:<8} 尝试 {st['attempts']} 次", end="")
        if st["error"]:
            print(f"  {st['error'][:70]}", end="")
        print()
    hr("成本")
    print(f"  总计 ${summary['cost']['total_usd']:.4f}")
    for cat, info in summary["cost"]["by_category"].items():
        print(f"    {cat:<8} ${info['usd']:.4f}  调用 {sum(i['calls'] for i in info['items'])} 次")
    return 0


def cmd_spans(args: argparse.Namespace) -> int:
    settings = Settings.load()
    pipe = Pipeline(settings, verbose=False)
    spans = pipe.spans(args.project_id)
    if not spans:
        print("该项目暂无 span 记录。")
        return 0
    hr(f"链路追踪（{len(spans)} 个 span）")
    print(f"  {'耗时':>9}  {'类型':<8} {'状态':<7} 名称")
    for sp in spans[: args.limit]:
        print(f"  {sp['duration_ms'] or 0:>8.1f}ms  {sp['kind']:<8} {sp['status']:<7} {sp['name']}")
    hr("按名称聚合（耗时归因）")
    stats = pipe.span_stats(args.project_id)
    print(f"  {'名称':<38} {'次数':>5} {'总耗时':>10} {'均值':>9} {'失败':>5}")
    for name, s in sorted(stats.items(), key=lambda kv_: -kv_[1]["total_ms"]):
        mean = s["total_ms"] / max(s["count"], 1)
        print(
            f"  {name:<38} {int(s['count']):>5} {s['total_ms'] / 1000:>9.1f}s {mean:>8.1f}ms {int(s['failed']):>5}"
        )
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    settings = Settings.load()
    pipe = Pipeline(settings)
    result = pipe.run(args.project_id, from_stage="evaluate", use_judge=not args.no_judge)
    ev = (result.get("project") or {}).get("evaluation") or {}
    hr("评测结果")
    print(json.dumps(ev, ensure_ascii=False, indent=2)[:4000])
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """导出完整评测报告 JSON。"""
    settings = Settings.load()
    pipe = Pipeline(settings, verbose=False)
    project = pipe.load(args.project_id)
    if project is None:
        print(f"找不到项目：{args.project_id}")
        return 1
    out = Path(args.output) if args.output else settings.project_dir(project.id) / "final" / "report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "project": project.to_dict(),
        "cost": pipe._store().__enter__().cost_summary(project.id),
        "span_stats": pipe.span_stats(project.id),
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已写入：{out}")
    return 0


# --------------------------------------------------------------------------- #
# 参数解析
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reelagent",
        description="ReelAgent — 短视频创作 Agent：从一句话到成片",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--style", help="风格预设：cinematic/anime/cyberpunk/ink/realistic")
        p.add_argument("--aspect", help="画幅，如 9:16 / 16:9 / 1:1")
        p.add_argument("--budget", type=float, help="单片预算上限（USD）")
        p.add_argument("--concurrency", type=int, help="并发镜头数")
        p.add_argument("--mode", choices=["mock", "live"], help="运行模式")
        p.add_argument("--tier", choices=["draft", "standard", "premium"], help="强制路由档位")
        p.add_argument("--no-judge", action="store_true", help="跳过 LLM-as-Judge，只算确定性指标")

    p_run = sub.add_parser("run", help="新建项目并生成")
    p_run.add_argument("seed", help="故事文本，或一个 .txt 文件路径")
    p_run.add_argument("--max-shots", type=int, default=8, help="镜头数上限（默认 8）")
    p_run.add_argument("--max-base-images", type=int, default=6, help="基准图上限（默认 6）")
    p_run.add_argument("--from-stage", choices=["plan", "assets", "generate", "evaluate", "render"])
    add_common(p_run)
    p_run.set_defaults(func=cmd_run)

    p_demo = sub.add_parser("demo", help="用内置样例跑通全流程")
    p_demo.add_argument("--max-shots", type=int, default=8)
    p_demo.add_argument("--max-base-images", type=int, default=6)
    p_demo.add_argument("--from-stage", choices=["plan", "assets", "generate", "evaluate", "render"])
    add_common(p_demo)
    p_demo.set_defaults(func=cmd_demo)

    p_res = sub.add_parser("resume", help="从断点继续")
    p_res.add_argument("project_id")
    p_res.add_argument(
        "--from-stage",
        choices=["plan", "assets", "generate", "evaluate", "render"],
        help="从该阶段起强制重跑",
    )
    add_common(p_res)
    p_res.set_defaults(func=cmd_resume)

    p_list = sub.add_parser("list", help="项目列表")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.set_defaults(func=cmd_list)

    p_status = sub.add_parser("status", help="项目状态")
    p_status.add_argument("project_id")
    p_status.set_defaults(func=cmd_status)

    p_spans = sub.add_parser("spans", help="链路追踪")
    p_spans.add_argument("project_id")
    p_spans.add_argument("--limit", type=int, default=60)
    p_spans.set_defaults(func=cmd_spans)

    p_eval = sub.add_parser("eval", help="重新评测")
    p_eval.add_argument("project_id")
    p_eval.add_argument("--no-judge", action="store_true")
    p_eval.set_defaults(func=cmd_eval)

    p_report = sub.add_parser("report", help="导出完整报告 JSON")
    p_report.add_argument("project_id")
    p_report.add_argument("--output")
    p_report.set_defaults(func=cmd_report)

    p_tools = sub.add_parser("tools", help="列出已注册工具")
    p_tools.set_defaults(func=cmd_tools)

    p_config = sub.add_parser("config", help="显示生效配置")
    p_config.set_defaults(func=cmd_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n已中断。用 `python -m reelagent resume <项目ID>` 从断点继续。")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
