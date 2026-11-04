# 生产就绪清单

> 这份文档回答一个问题：**现在能不能对外收钱上线？**
> 每一项都写清「做到什么程度」「代码在哪」「怎么验证」「还差什么」。
> 没有做到的直接写没做到 —— 一份把未完成项藏起来的清单，比没有清单更危险。

## 一、怎么读

- **已就绪**：代码在仓库里，有测试，可直接上线
- **代码就绪 / 流程待办**：工程侧做完了，还缺法务或资质流程
- **未开始**：还没做，附上为什么它重要

## 二、上线 Gate

### P0：阻断级，不满足不能上线

| 项 | 状态 | 实现 / 位置 | 验证 |
|---|---|---|---|
| AIGC 显式标识（画面水印） | ✅ 已就绪 | `reelagent/render/watermark.py`，`composer.compose()` 末尾统一烤入 | `tests/test_aigc_label.py` |
| AIGC 隐式标识（元数据） | ✅ 已就绪 | 同上，写进 `manifest.json` 的 `aigc` 块；MP4 走 ffmpeg 容器元数据 | 同上 |
| 输入内容审核（提示词） | ✅ 已就绪 | `reelagent/moderation.py`；`Director._guard_text` 在进模型前审 | `tests/test_moderation.py`、`tests/test_director_moderation.py` |
| 输出内容审核（关键帧） | ✅ 已就绪 | `DashScopeVisionModeration`（多模态语义审核）+ 启发式兜底；生产环境强制要求语义审核，配 `REEL_COMPLIANCE__IMAGE_MODERATION=heuristic` 会**启动失败** | `test_vision_moderation.py`(12)、`test_compliance_gate.py`(4) |
| 审核失败策略 fail-closed | ✅ 已就绪 | `Settings.moderation_fail_closed`，默认 True | `test_moderation.py::TestFailPolicy` |
| 违规内容不交付 | ✅ 已就绪 | 任一镜头被拦 → 整条任务失败（而非跳过该镜），避免交付缺镜头的片子 | `Director.run()` 的 `_blocked` 分支 |
| 产物 TTL 与 GC | ✅ 已就绪 | `reelagent_cloud/services/retention.py`；worker 每 10 分钟回收 | `tests/test_retention.py` |
| 账本漂移巡检 | ✅ 已就绪 | `credits.reconcile_all_orgs()`；worker 每小时全量巡检并打指标 | `tests/test_reconcile_all.py` |
| 账本漂移告警 | ✅ 已就绪 | `deploy/monitoring/prometheus-rules.yml::LedgerDriftDetected`（P0，无 `for` 抑制） | `promtool check rules` |
| 对账差异工单 | ✅ 已就绪 | `reconciliation_diffs` 表 + `credits.record_diffs`：持续漂移去重累加、金额变化另起一行、漂移消失自动关闭；迁移 `0003` | `tests/test_reconciliation_diffs.py`（5 项） |
| 生成式 AI 双备案 | ⏳ 材料齐备 / 待法务提交 | 材料清单与仓库对应关系见 `docs/compliance/filing-checklist.md`；仍需法务提交 | 法务 / 网信办 |

### P1：上线首月必须完成

| 项 | 状态 | 实现 / 位置 | 说明 |
|---|---|---|---|
| 供应商熔断 | ✅ 已就绪 | `reelagent/harness/breaker.py`；接在 `Director` 的片段生成路径 | 熔断打开后直接走本地合成，把「3 次重试 × 超时」的分钟级白等压到毫秒级 |
| 核心告警规则 | ✅ 已就绪 | `deploy/monitoring/prometheus-rules.yml`（12 条，按 P0/P1/P2 分级） | 每条都写了「先看什么、再做什么」 |
| 资金/生命周期指标 | ✅ 已就绪 | `observability/metrics.py` 新增 drift / gc / moderation 三组 | 这三类问题的共同点是「不报错、只会安静地错」 |
| SLI / SLO | ✅ 已定义 | 见本文第三节 | 需要接看板才算落地 |
| 账号注销真实生效 | ✅ 已就绪 | `services/account.py`（清产物→软删项目→吊销 Key→匿名化→停用组织→审计）+ `DELETE /api/v1/me` | `tests/test_account_deletion.py`（5 项） |
| 主动退款流程 | ✅ 已就绪 | `services/refunds.py`（按实际消耗退、幂等、7 天窗口）+ `POST /projects/{id}/refund` | `tests/test_refunds.py`（7 项） |
| 租户配额（并发 + 每日） | ✅ 已就绪 | `services/quotas.py`，接在 `create_project` 幂等短路之后 | `tests/test_quotas.py`（5 项） |
| 审计日志防篡改 | ✅ 已就绪 | `services/audit.py` 链式哈希（prev/entry hash + seq）+ `GET /audit/verify` + 迁移 `0002` | `tests/test_audit_chain.py`（5 项） |
| 备份恢复演练 | ⏳ 文档就绪 | `docs/DEPLOYMENT.md` 有备份说明，**未做过恢复演练** | 没演练过就等于没有 |

### P2：三个月内

| 项 | 状态 | 说明 |
|---|---|---|
| 图像语义审核接入云端 | ❌ | 替换 `HeuristicImageModeration` 即可，调用方不用改 |
| 日志留存 ≥ 6 个月 | ✅ 已就绪 | `log_sink=file/both` 按天轮转保留 180 天；prod 必须落盘或显式声明外部留存，否则启动失败 | `test_log_retention.py`(10) |
| 用户实名（手机号） | ❌ | 生成式 AI 服务需真实身份信息认证 |
| 月度账单 / 发票 | ❌ | 现有页内用量，无账单 PDF |
| 灰度发布与回滚 | ❌ | CI 有（`.github/workflows/`），缺流量切分 |
| 等保二级 | ❌ | 有客户会问 |
| 2FA / SSO | ❌ | 企业客户会问 |

## 三、SLI / SLO

| SLI | 定义 | SLO | 错误预算（30 天） |
|---|---|---|---|
| 成片成功率 | `succeeded / (succeeded + failed + dead)` | ≥ 95% | 5% ≈ 36 次失败 |
| 端到端时延 | 从任务入队到成片落库的 P95 | ≤ 15 分钟（7 镜） | 超出即视为消耗预算 |
| 账本一致性 | `drifted_count == 0` | 100% | 0 —— 这不是「预算」而是红线 |
| 内容合规 | 审核拦截全部留痕且不交付 | 100% | 0 |

**错误预算怎么用**：一个周期内失败率超标 → 冻结新功能发布，先修稳定性。
这条规则的目的是把「要不要发版」从争论变成算术。

## 四、几个关键设计决策

### 1. 为什么审核放在流水线里，而不是只在 API 层

系统里大量文字是**模型自己生成的**（剧本、提示词、评审意见），它们不经过
API 日志、也没有人看过就进了图像/视频模型。只在 API 层审用户输入，等于把
模型编出来的东西整段放行。

### 2. 为什么标识用「后处理一步」而不是塞进合成滤镜

合成函数已有四种音频分支与一条 `-vf` 链，插 overlay 要改写成 `filter_complex`
并照顾每条分支的 `-map`，回归风险高。标识是产物级后处理：合成逻辑零改动、
可以单独重跑、新后端自动获得标识能力。代价是多一次转码。

### 3. 为什么 GC 要跳过「运行中任务」的产物

`expires_at` 在创建时算好。若一条任务跑了很久（重试 + 修复轮），早期片段可能
已到 TTL，删掉会让正在生成的片子突然缺片段。所以 GC 显式排除未终结任务名下的产物。

### 4. 为什么熔断按 `provider:capability` 分开计数

视频挂了不该把图像也停掉。一个供应商抖动导致整站停摆，是比故障本身更大的事故。

### 5. 为什么对账用一条 SQL 而不是逐机构循环

1000 个机构就是 1001 次查询。对账是低频任务，但它跑的时候不该把数据库拖住。
LEFT JOIN + HAVING 让数据库一次算完，只把漂移的行带回来。

## 五、上线前检查清单

- [ ] `pytest` 与 `unittest` 全绿（引擎 88 项 + 云层全量）
- [ ] `ruff check` / `ruff format --check` / `mypy` 全过
- [ ] `promtool check rules deploy/monitoring/prometheus-rules.yml`
- [ ] 手工跑一条真实项目，确认成片带角标、`manifest.aigc` 齐全
- [ ] 手工构造一条违规文本，确认被拦且**没有产生任何成片**
- [ ] 手工把某机构余额改错，确认一小时内告警触发
- [ ] 手工改一条产物的 `expires_at` 为过去，确认 GC 删对象并置 `deleted_at`
- [ ] 断网跑一次，确认熔断打开后任务仍能以本地合成完成
- [ ] 备份恢复演练一次（这是唯一「没做过就不能说会」的项）
- [ ] 备案材料齐备（算法 + 服务，两套）

## 六、还没做的事，以及我为什么如实写出来

当前仍未完成：用户实名认证、月度账单/发票、灰度发布、等保、2FA。图像语义审核与日志留存已从「缺口」转为「已就绪」。
它们都不难，但都属于「要么有、要么没有」的合规/资金能力 —— 做一半比不做更危险，
因为会让人误以为已经受控。

把这份清单摆在面试官或投资人面前，比说「我的项目很完整」有说服力得多：
**它证明我知道「能跑」和「能上线」之间隔着什么。**
