# 内容安全机制说明

> 用于生成式 AI 服务备案与内部审计。每一项都指到代码位置。
> 语气刻意保持技术性而非合规套话 —— 一份写满"我们高度重视"但指不出实现的文档，
> 在监管核查时没有任何价值。

## 一、审核点在哪里

内容不是只在入口审一次。系统里大量文字是**模型自己生成的**，
所以审核点放在流水线内部三处（实现：`reelagent/agents/director.py`）：

| 顺序 | 审核对象 | 触发时机 | 方法 |
|---|---|---|---|
| 1.5 | 画面提示词 `image_prompt` | 拼装完成后、调用图像模型**之前** | `_guard_text(scope="image_prompt")` |
| 1.5 | 视频提示词 `video_prompt` | 同上 | `_guard_text(scope="video_prompt")` |
| 2 | 关键帧图像 | 图像生成返回**之后**、落盘登记之前 | `_guard_image` |
| 4 | 台词 / 旁白 | 合成语音**之前** | `_guard_text(scope="speech")` |

**为什么必须审提示词而不是只审用户输入**：画面提示词是逐镜拼出来的，
Planner 阶段还不存在。只审用户输入等于审了一个中间产物。

## 二、判定与处置

审核结论有三种（`ModerationVerdict.action`）：`allow` / `block` / `review`。
只有 `allowed` 字段用于分支 —— 调用方不该自己解释 action。

- **命中即拦截**：该镜头标记失败
- **整条任务失败，而不是跳过该镜**：交付一条缺镜头的成片，用户看到的是
  「少了一段」而不是「内容不合规」，会反复重试。所以任一镜头被拦，
  `Director.run()` 抛 `ModerationError`，任务落 `failed`，预扣额度全额退还。

**违规图必须从磁盘删除**：留在工作区会被断点恢复当成「已完成的关键帧」复用。
实现见 `Director._guard_image` 的 `path.unlink()`。

## 三、失败策略：fail-closed

审核服务自身故障（超时、5xx、词库文件损坏）时，默认**拒绝生成**。
配置项：`settings.moderation_fail_closed`（默认 `True`）。

理由：对外提供生成式服务时，「因为审核挂了所以放出一条违规内容」不可接受；
而「因为审核挂了所以这次失败」用户重试即可，钱会退。

代价明确写在这里：审核可用性直接等于生成可用性。这是一个有意的取舍。

## 四、审核能力边界（如实披露）

当前实现是**关键词审核 + 图像启发式检测**：

| 能力 | 现状 | 局限 |
|---|---|---|
| 文本审核 | 内置词表 + 外挂词库（`REEL_MODERATION_BLOCKLIST_FILE`） | 只能查词面。已做归一化（NFKC、去空白/标点/零宽字符）挡住「色 情」这类低成本绕过，但**挡不住同音、拆字、拼音等刻意变体** |
| 图像审核 | 启发式：纯色、过小、不可解码 | **不是语义审核**。涉黄/涉暴/涉政识别必须接云端内容安全或自建视觉模型 |

**上线前必须完成**：接入云端内容安全做语义级复核，形成「本地快筛 + 云端复核」
双层结构。替换方式：实现 `ImageModerationProvider` 协议并改
`moderation.build_moderation()` 的装配，调用方代码不动。

## 五、AI 生成内容标识

依据《人工智能生成合成内容标识办法》（2025-09-01 施行），显式与隐式标识同时实施。

| 类型 | 载体 | 实现 |
|---|---|---|
| 显式 | 成片画面右下角半透明角标 | `render/watermark.py`，ffmpeg overlay 烤入 |
| 隐式 | `manifest.json` 的 `aigc` 块 | `AIGC / AIGC-Provider / AIGC-Standard / AIGC-Content-Id` |
| 隐式 | MP4 容器元数据 | 同上字段写入容器 |

**标识失败不阻断交付**（只记 warning）—— 成片已经花钱生成，因为水印失败丢掉
产物是最差结果。但这条策略有合规风险，已列入 `PRODUCTION_READINESS.md`
的待办：上线前应改为阻断。

## 六、日志与追溯

每次审核都写 span（`moderation.text` / `moderation.image`），带
`scope`、`categories`、`action`、`shot_id`。命中记录进审计日志。
指标：`reelagent_moderation_blocked_total{scope,category}`，
告警规则见 `deploy/monitoring/prometheus-rules.yml::ModerationBlockSpike`。

拦截量突增有两种成因——恶意刷量或词库误杀——靠 `scope` 与 `category`
的分布区分：集中在单一 scope 多为攻击，分散在多个 category 多为误杀。
