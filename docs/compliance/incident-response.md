# 应急处置预案

> 用于备案材料与值班交接。按「症状 → 判断 → 动作 → 验证」组织，
> 不按部门职责组织 —— 出事时没人会去读组织架构。

## 一、分级与响应时限

| 级别 | 定义 | 示例 | 响应 |
|---|---|---|---|
| **P0** | 资金安全或合规失控 | 账本漂移、违规内容已交付、审计链断裂 | 立即，冻结相关入口 |
| **P1** | 核心功能不可用 | 无存活 worker、任务成功率 < 80%、全部供应商熔断 | 30 分钟内 |
| **P2** | 局部劣化或成本异常 | 单供应商熔断、队列积压、成本尖峰 | 2 小时内 |
| **P3** | 体验问题 | 单个接口 5xx 升高、限流误伤 | 次工作日 |

## 二、五个高概率场景

### 1. 账本漂移（P0）

**症状**：`LedgerDriftDetected` 告警；或 `/api/v1/audit` 里出现对账异常记录。

**判断**：`SUM(ledger_entries.amount) != organizations.credits_balance`
（实现：`credits.reconcile_all_orgs`）。漂移方向决定性质：
- **正漂移**（余额 > 账本）→ 少记了一笔扣费，用户在免费使用
- **负漂移**（余额 < 账本）→ 多扣了用户的钱，必须退还

**动作**：
1. 立即对该组织开启人工审核（`organizations.is_suspended`），阻断继续消费
2. 用 `credits.reconcile(org_id)` 拿单机构明细，与 `usage_records` 对时间线
3. 定位到具体写入路径（并发？幂等键缺失？结算漏写？）
4. 用 `credits.adjust()` 人工补正，**必须带 `idempotency_key`**，金额与原因写进 `meta`
5. 补一条审计，说明这是一次人工对账修正

**验证**：再次巡检，`drifted_count == 0`。

### 2. 违规内容已交付（P0）

**症状**：用户投诉、监管转发、或审核日志显示 `allowed` 但内容明显违规。

**动作**：
1. 立即下架：把该产物 `artifacts.deleted_at` 置位并删对象
   （`retention.purge_project_artifacts`）
2. 冻结该组织入口
3. 回溯同一 `scope` 的全部审核记录，判断是词库缺口还是链路绕过
4. 补词库或修审核点，**并在告警规则里加一条针对性告警**
5. 按监管要求上报（时限以当地要求为准）

### 3. 供应商全部故障 / 熔断全开（P1）

**症状**：`ProviderCircuitOpen` 多供应商同时告警；任务成功率骤降。

**判断**：看 `reelagent_provider_circuit_open` 是否所有 `provider/capability` 都为 1。

**动作**：
1. 确认是供应商侧还是自己侧（对照供应商状态页）
2. 熔断打开时请求**已自动降级到本地合成**，成片仍能交付（画质下降但不中断）
3. 若本地合成也不可用，暂停接单：把 api 侧的创建入口摘流
4. 恢复后断路器会冷却后自动半开试探，**不需要手工重置**

### 4. 存储成本异常增长（P2）

**症状**：`GcNotRunning` 或对象存储用量曲线陡增。

**动作**：
1. 确认 GC 是否在跑：`increase(reelagent_artifact_gc_total[6h])`
2. 若 GC 挂：查 worker 日志 `artifact_gc_failed`，多半是存储权限或网络
3. 若 GC 正常但量仍涨：查是否有大量未设置 `expires_at` 的产物
   （`expires_at IS NULL` 的记录永远不会被回收）
4. 处置存量：手工把过期记录的 `expires_at` 前移，让下一轮 GC 收走

### 5. 审计链校验失败（P0）

**症状**：`GET /api/v1/audit/verify` 返回 `ok=false`。

**判断**：`status` 决定性质，两者处置完全不同：
- `hash_mismatch` → 内容被改，**真篡改**，按安全事件处理
- `broken_link` → 记录缺失或并发分叉，可能是误删也可能是掩盖

**动作**：
1. 记录 `broken_at` 的时间点，与该时间段的部署、运维操作对照
2. 检查是否有直接的数据库写入（绕过应用层）
3. 若确认篡改：保全证据（导出涉及组织的全链 + 数据库快照），走安全事件流程
4. 已知限制：并发写入可能产生分叉（见 `services/audit.py` 文档），
   分叉时两条各自自洽，需结合写入日志判断是否为分叉而非篡改

## 三、通用动作清单

| 动作 | 命令 / 位置 |
|---|---|
| 看队列与租约 | `SELECT status, count(*) FROM jobs GROUP BY status` |
| 看活跃 worker | `reelagent_worker_active` 指标 |
| 看单项目阶段与成本 | `python -m reelagent status <engine_project_id>` |
| 看耗时归因 | `python -m reelagent spans <engine_project_id>` |
| 手工触发对账 | `credits.reconcile_all_orgs(session)` |
| 手工触发 GC | `retention.gc_expired_artifacts(session, settings)` |
| 冻结组织 | `UPDATE organizations SET is_suspended = true, suspended_reason = ...` |
| 回滚版本 | 见 `docs/DEPLOYMENT.md` |

## 四、演练要求

**没演练过的预案等于没有预案。** 上线前至少完成：

- [ ] 备份恢复演练（从备份起一套可用环境）
- [ ] 熔断演练（人为把供应商 Key 改错，确认降级生效且成片仍可交付）
- [ ] 账本漂移演练（手工改一个机构的 balance，确认一小时内告警）
- [ ] GC 演练（手工把一条产物 `expires_at` 改到过去，确认 10 分钟内被回收）
- [ ] 审核拦截演练（构造违规文本，确认任务失败且没有产出成片）
