"""生产配置自检。

## 为什么需要这个脚本

配置错误有两种，代价完全不同：

    **启动就失败**  改完重启，日志里立刻看到。痛一次，但不会带病跑。
    **启动能过、跑起来才发现**  会话随机掉登录、日志没留、审核形同虚设。
                                 这类问题往往在客户投诉或监管检查时才暴露。

`Settings` 的校验器只负责第一类。这个脚本做两件事：
1. 把第一类的报错**整理成人话**（把 pydantic 的堆栈变成一条一条可行动的项）
2. 把第二类里「能静态判断」的部分列出来提醒（比如限流关着、备份未配置）

用法：

    python scripts/check_production_config.py                  # 读 .env
    python scripts/check_production_config.py --env-file .env  # 指定文件
    REEL_ENV=prod python scripts/check_production_config.py    # 读环境变量

退出码：0 = 可以启动；1 = 有会拦住启动的问题；2 = 能启动但有风险项。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _mask(value: str, keep: int = 4) -> str:
    if not value:
        return "(空)"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return value[:keep] + "…" + value[-keep:]


def main() -> int:
    parser = argparse.ArgumentParser(description="生产配置自检")
    parser.add_argument("--env-file", default=".env", help="要检查的 env 文件（默认 .env）")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    if not env_path.is_absolute():
        env_path = ROOT / env_path
    if not env_path.exists():
        print(f"[×] 找不到配置文件：{env_path}")
        print("    从模板复制：cp .env.production.example .env")
        return 1

    # pydantic-settings 在实例化时读环境变量 + env_file；这里显式指定文件，
    # 避免「检查了 .env、实际部署用的是另一份」的错位。
    try:
        from reelagent_cloud.config import Settings
    except Exception as exc:  # pragma: no cover
        print(f"[×] 导入配置模块失败：{exc}")
        return 1

    try:
        settings = Settings(_env_file=str(env_path))  # type: ignore[call-arg]
    except Exception as exc:
        print("=" * 72)
        print(" 配置校验未通过 —— 服务会拒绝启动")
        print("=" * 72)
        text = str(exc)
        # pydantic 的报错里有一段 "配置校验未通过（环境=prod）：\n  - a\n  - b"
        # 直接把它打出来，比堆栈好读得多。
        for line in text.splitlines():
            stripped = line.strip()
            # pydantic 会在最后一条后面追加 [type=value_error, input_value={...}]，
            # 那是给调试看的，对运维没有价值，截掉
            if " [type=" in stripped:
                stripped = stripped.split(" [type=")[0]
            if stripped.startswith("- ") or "配置校验" in stripped:
                print(f"  {stripped}")
        if "配置校验" not in text:
            print(f"  {text[:800]}")
        print()
        print("逐条修完再跑一次本脚本。模板参考：.env.production.example")
        return 1

    env = settings.env.value
    print("=" * 72)
    print(f" 配置校验通过（环境={env}）")
    print("=" * 72)

    o = settings.observability
    print("关键项：")
    print(f"  Secret Key        {_mask(settings.security.secret_key.get_secret_value())}")
    print(f"  API Key Pepper    {_mask(settings.security.api_key_pepper.get_secret_value())}")
    print(f"  数据库             {'SQLite（非生产）' if settings.database.is_sqlite else 'PostgreSQL'}")
    print(f"  视频供应商 Key      {'已配置' if settings.providers.video.configured else '未配置'}")
    print(f"  LLM 供应商 Key      {'已配置' if settings.providers.llm.configured else '未配置'}")
    print(f"  图像审核           {settings.compliance.image_moderation}")
    print(f"  日志输出           {o.log_sink} → {o.log_file or '(stdout)'}")
    print(f"  日志保留           {o.log_retention_days} 天")
    print(f"  产物保留（成片/片段） {settings.retention.final_video_days}/{settings.retention.video_days} 天")
    print(f"  限流               {'开启' if settings.rate_limit.enabled else '关闭'}")
    print(f"  Stripe 计费         {'开启' if settings.billing.enabled else '关闭（额度模式）'}")

    # ---- 第二类：能启动但有风险 ----
    warnings: list[str] = []
    if not settings.security.secret_key.get_secret_value():
        warnings.append(
            "Secret Key 为空：多副本部署会导致会话互不通用、用户随机掉登录。"
            "（生产校验本应拦住它，若走到这里说明校验逻辑被改过，请人工确认）"
        )
    if not o.log_external_retention and o.log_sink == "stdout":
        warnings.append("日志只输出 stdout 且未声明外部留存：无法自证已留存 180 天")
    if o.log_retention_days < 180:
        warnings.append(f"日志保留 {o.log_retention_days} 天 < 180 天（网络安全法要求六个月）")
    if settings.compliance.image_moderation != "dashscope":
        warnings.append("图像审核未启用语义审核：涉黄涉暴涉政无法识别")
    if not settings.rate_limit.enabled:
        warnings.append("限流已关闭：接口无任何请求级保护")
    if settings.rate_limit.enabled and not settings.redis.enabled:
        warnings.append("限流开启但 Redis 关闭：多副本下限流退化为进程内计数，实际不生效")
    if settings.retention.final_video_days == 0:
        warnings.append("成片 TTL=0（不回收）：对象存储会无限增长")
    if settings.env.is_production and not settings.providers.tts.configured:
        warnings.append("未配置 TTS Key：成片将没有配音（会降级为静音/本地合成音）")

    print()
    if warnings:
        print(f"风险项（不会拦住启动，{len(warnings)} 条）：")
        for w in warnings:
            print(f"  ! {w}")
        print()
        print("结论：可以启动，但上线前建议逐条确认。")
        return 2

    print()
    print("结论：未发现风险项，可以上线。")
    print()
    print("下一步建议：")
    print("  1. 启动后立刻验证 /readyz 与 /metrics 可达")
    print("  2. 跑一条真实项目，确认成片带 AIGC 角标、manifest 有 aigc 块")
    print("  3. 构造一条违规文本，确认被拦且没有产出成片")
    return 0


if __name__ == "__main__":
    sys.exit(main())
