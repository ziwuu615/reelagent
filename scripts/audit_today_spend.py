"""按官方真实单价，把今天所有产生费用的调用算清楚。

为什么要专门算：用户发现账户少了十几块，而我之前报的是「一次约 $0.39」。
两者对不上，必须逐笔核对，而不是继续用代码里那套写死的估算值。
"""

from __future__ import annotations

#: 官方定价（中国北京，USD/秒）—— 来源：百炼模型价格页
VIDEO_PRICE_PER_S = {
    "480P": 0.041256,
    "720P": 0.082513,
    "1080P": 0.165025,
}

#: 我今天实际提交过的视频调用（时间线顺序）
#: 每项 = (说明, 分辨率, 秒数, 次数)
VIDEO_CALLS = [
    ("探测：视频额度是否恢复（probe_dashscope_quota）", "480P", 5, 1),
    ("探测：size 参数是否被接受（probe_video_size）", "480P", 2, 4),
    ("探测：size 实际是否生效（probe_video_size_result）", "480P", 2, 2),
    ("探测：首帧正确写法（probe_first_frame）", "480P", 2, 1),
    ("完整跑：premium 修复前（403 降级，未产生视频费）", "720P", 5, 0),
    ("完整跑：standard 修复前", "480P", 4, 1),
    ("完整跑：standard 修复后", "480P", 4, 1),
    ("完整跑：premium 修复前（真实视频）", "720P", 5, 1),
    ("完整跑：premium 修复后", "720P", 5, 1),
]


def main() -> None:
    print("=" * 78)
    print("今天所有视频调用（按官方单价）")
    print("=" * 78)
    print(f"  {'用途':<48}{'档':>6}{'秒':>4}{'次':>3}{'小计(USD)':>12}")
    print("  " + "-" * 74)

    probe_total = 0.0
    run_total = 0.0
    for label, res, secs, times in VIDEO_CALLS:
        unit = VIDEO_PRICE_PER_S[res]
        sub = unit * secs * times
        if times == 0:
            print(f"  {label:<48}{res:>6}{secs:>4}{times:>3}{'—':>12}")
        else:
            print(f"  {label:<48}{res:>6}{secs:>4}{times:>3}{sub:>12.4f}")
        if label.startswith("探测"):
            probe_total += sub
        else:
            run_total += sub

    print("  " + "-" * 74)
    print(f"  {'探测小计':<48}{'':>6}{'':>4}{'':>3}{probe_total:>12.4f}")
    print(f"  {'完整跑小计':<48}{'':>6}{'':>4}{'':>3}{run_total:>12.4f}")
    print(f"  {'视频合计':<48}{'':>6}{'':>4}{'':>3}{probe_total + run_total:>12.4f}")

    print()
    print("=" * 78)
    print("加上非视频部分（按引擎记录，这几项单价与官方接近）")
    print("=" * 78)
    # 图像：每次完整跑生成 4 张基准图 + 1 张关键帧 = 5 张
    image_calls = 4 * 5
    image_usd = image_calls * 0.03
    llm_usd = 4 * 0.02
    tts_usd = 4 * 0.005
    print(f"  图像 {image_calls} 张 × $0.03        ${image_usd:.4f}")
    print(f"  LLM 4 次                       ${llm_usd:.4f}")
    print(f"  TTS 4 次                       ${tts_usd:.4f}")
    print(f"  非视频合计                     ${image_usd + llm_usd + tts_usd:.4f}")

    grand = probe_total + run_total + image_usd + llm_usd + tts_usd
    cny = grand * 7.2
    print()
    print("=" * 78)
    print(f"  总计 ≈ ${grand:.2f}   ≈ {cny:.1f} 元人民币")
    print("=" * 78)
    print()
    print("  结论：十几块钱不是「跑一遍」产生的，而是")
    print(f"    · 4 个探测脚本的视频调用   ≈ ${probe_total:.2f}（{probe_total * 7.2:.1f} 元）")
    print(f"    · 4 次完整跑                ≈ ${run_total:.2f}（{run_total * 7.2:.1f} 元）")
    print()
    print("  我此前报的「一次约 $0.39」偏低，原因是 estimate_video_cost()")
    print("  不接受分辨率参数，把 720P 按 480P 的价格算了。")


if __name__ == "__main__":
    main()
