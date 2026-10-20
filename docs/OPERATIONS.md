# 运维 Runbook（OPERATIONS.md）

> **面向对象**：值班同学 / on-call 工程师。
> **组织方式**：按**症状**，不按系统模块。值班时你看到的是一个现象，
> 不是一张架构图。
>
> **使用前提**：本文假设你已经能访问数据库、Redis（如启用）、
> 服务器 shell 与日志系统。每个场景都给出可直接执行的 SQL 与命令。
>
> ⚠️ 所有 `UPDATE` / `DELETE` 语句在**生产执行前必须先 `SELECT` 确认影响行数**。
> 改数据库是最后手段，先考虑走服务层或接口。

---

## 目录

- [0. 五分钟摸底](#0-五分钟摸底)
- [1. 任务卡在 running 不动](#1-任务卡在-running-不动)
- [2. 队列积压](#2-队列积压)
- [3. 供应商大面积失败](#3-供应商大面积失败)
- [4. 额度对不上账](#4-额度对不上账)
- [5. Stripe Webhook 没生效](#5-stripe-webhook-没生效)
- [6. 数据库连接池打满](#6-数据库连接池打满)
- [7. 磁盘被产物写满](#7-磁盘被产物写满)
- [8. 附录：应急预案速查表](#8-附录应急预案速查表)

---

## 0. 五分钟摸底

任何告警进来，先跑这三步。多数问题在这一步就能定位到方向。

### 0.1 探针与健康

```bash
# 进程活着吗（不碰数据库）
curl -s http://127.0.0.1:8000/livez

# 能接流量吗（数据库 + 存储真实读写）
curl -s http://127.0.0.1:8000/readyz | jq

# 人类可读汇总（含版本、环境、各依赖检查明细）
curl -s http://127.0.0.1:8000/health | jq
```

`/readyz` 与 `/health` 的 `checks` 里有四项：

| check | critical | 含义 |
|---|---|---|
| `database` | ✅ | `SELECT 1` 是否成功 |
| `storage` | ✅ | 写一个 `_healthcheck/probe.txt` 再删除（真实读写） |
| `providers` | ❌ | 各供应商是否配了 Key（没配也能起服务） |
| `billing` | ❌ | `billing.enabled` |

**只有 critical 检查失败才会让 `/readyz` 返回 503。**
`providers` 显示 `ok: false` 是正常的（未配置 Key 时），
除非你正在处理「生成不了片子」的问题。

### 0.2 队列与配置快照

```bash
curl -s http://127.0.0.1:8000/api/v1/system/status | jq
```

返回：

```json
{
  "version": "1.0.0",
  "env": "prod",
  "queues": {
    "pending": 12, "running": 6, "succeeded": 1843,
    "failed": 3, "dead": 2, "cancelled": 7
  },
  "config": { ... 密钥已打码的配置快照 ... }
}
```

> ⚠️ 这个端点当前**没有鉴权**，任何能访问服务的人都能读到。
> 生产环境请用反向代理限制来源 IP。

### 0.3 关键指标

```bash
curl -s http://127.0.0.1:8000/metrics | grep -E '^reelagent_(job|provider|worker|auth|rate)'
```

| 指标 | 异常信号 |
|---|---|
| `reelagent_job_queue_depth{status="pending"}` | 持续上升 → 队列积压（第 2 节） |
| `reelagent_job_queue_depth{status="running"}` | 高于 `worker 副本数 × concurrency` → 有僵尸任务（第 1 节） |
| `reelagent_job_lease_expired_total` | 持续增长 → worker 在崩溃（第 1 节） |
| `reelagent_worker_active` | 掉到 0 → 所有 worker 都挂了（第 2 节） |
| `reelagent_provider_calls_total{status="failed"}` | 突增 → 供应商故障（第 3 节） |
| `reelagent_provider_circuit_open` = 1 | 熔断已打开（第 3 节） |
| `reelagent_auth_failures_total` | 突增 → 撞库或凭证泄漏排查 |
| `reelagent_rate_limit_hits_total` | 突增 → 有人在刷，或限额配置不合理 |

> 注意：`reelagent_job_queue_depth` 只在 worker 的 housekeeping 循环里更新
> （每 120 秒一次），因此它反映的是**最近一次扫描**的状态，不是实时值。
> 需要实时值就直接查数据库。

### 0.4 数据库连接速查

```bash
# 用 psql 直连（把连接串里的 +asyncpg 去掉）
psql "postgresql://user:pass@host:5432/reelagent"
```

```sql
-- 当前所有 reelagent 连接
SELECT application_name, state, count(*)
  FROM pg_stat_activity
 WHERE application_name = 'reelagent'
 GROUP BY 1, 2 ORDER BY 3 DESC;
```

`application_name='reelagent'` 是连接层设置的（见
`reelagent_cloud/db/session.py` 的 `server_settings`），可直接用来过滤。

---

## 1. 任务卡在 running 不动

### 1.1 症状

- 用户在页面上看到「生成中」十几分钟甚至几小时不动。
- `GET /api/v1/jobs/{job_id}` 返回 `status: "running"`，
  `stage` 一直停在某一阶段，`progress` 不变。
- `reelagent_job_queue_depth{status="running"}` 明显高于
  `worker 副本数 × REEL_WORKER__CONCURRENCY`。

### 1.2 机制回顾（先理解，再动手）

任务不是靠「进程是否存活」来判断的，而是靠**租约**：

| 参数 | 默认值 | 含义 |
|---|---|---|
| `REEL_WORKER__LEASE_SECONDS` | 900（15 分钟） | 租约时长。超过未续约视为 worker 崩溃 |
| `REEL_WORKER__HEARTBEAT_SECONDS` | 20 | 心跳间隔，每次心跳续约 |

- 领取任务时写 `lease_expires_at = now() + 900s`。
- worker 每 20 秒调 `jobs.heartbeat()` 续约一次。
- worker 每 120 秒（`REAP_INTERVAL_SECONDS`）执行一次
  `reap_expired_leases()`，把**租约已过期**的 `running` 任务打回
  `failed`（`error_code='lease_expired'`），于是它就能被重新领取。

**结论：如果只是 worker 崩了，任务会在最多
「租约剩余时间 + 120 秒」内自动恢复，不需要人工干预。**
所以「卡住超过 20 分钟」才是真正需要排查的信号。

### 1.3 排查 SQL

**第一步：找出所有 running 任务及其心跳新鲜度**

```sql
SELECT id,
       kind,
       project_id,
       locked_by,
       attempts,
       max_attempts,
       stage,
       round(progress::numeric, 2)                       AS progress,
       started_at,
       heartbeat_at,
       lease_expires_at,
       now() - heartbeat_at                              AS since_heartbeat,
       lease_expires_at - now()                          AS lease_remaining
  FROM jobs
 WHERE status = 'running'
 ORDER BY heartbeat_at NULLS FIRST;
```

判读：

| 现象 | 结论 |
|---|---|
| `since_heartbeat` < 60s | 任务**是活的**，只是慢。看第 3 节或查引擎日志 |
| `since_heartbeat` > 60s 且 `lease_remaining > 0` | worker 可能卡在某个同步调用上（心跳循环是独立 asyncio task，一般不会卡）。查 worker 进程 |
| `lease_remaining < 0`（已过期）但状态仍是 `running` | **没有任何 worker 在跑回收循环** → 所有 worker 都挂了。看第 1.4 节 |
| `locked_by` 为 NULL 但 `status = 'running'` | 数据异常，可能是手工 UPDATE 造成。走第 1.5 节的手动重置 |

**第二步：按 `locked_by` 归组，看是不是某个 worker 整体失联**

```sql
SELECT locked_by,
       count(*)                                          AS running_jobs,
       min(heartbeat_at)                                 AS oldest_heartbeat,
       max(heartbeat_at)                                 AS newest_heartbeat
  FROM jobs
 WHERE status = 'running'
 GROUP BY locked_by
 ORDER BY oldest_heartbeat;
```

如果某个 `locked_by` 下的所有任务心跳都很旧，那个 worker 进程已经死了；
它留在队列里的 `running` 任务会随租约到期自动回收。

**第三步：查是否有任务已经卡到不合理的时间**

服务里有一个专门的方法对应这个查询
（`jobs.find_stale(session, older_than_seconds=7200)`）：

```sql
-- 心跳陈旧超过 2 小时的 running 任务
SELECT id, kind, project_id, locked_by, attempts, started_at, heartbeat_at
  FROM jobs
 WHERE status = 'running'
   AND (heartbeat_at IS NULL OR heartbeat_at < now() - interval '2 hours')
 ORDER BY started_at;
```

**第四步：看历史失败分布（判断是偶发还是系统性）**

```sql
SELECT error_code,
       count(*)          AS n,
       min(finished_at)  AS first_seen,
       max(finished_at)  AS last_seen
  FROM jobs
 WHERE status IN ('failed', 'dead')
   AND finished_at > now() - interval '24 hours'
 GROUP BY error_code
 ORDER BY n DESC;
```

`lease_expired` 的条数直接告诉你「过去 24 小时有多少任务因为 worker 掉线被回收」。

### 1.4 判定 worker 进程状态

**看日志关键字**（worker 输出的结构化日志）：

```bash
# JSON 日志（生产默认格式）
journalctl -u reelagent-worker --since "30 min ago" \
  | jq -r 'select(.event != null) | "\(.timestamp) \(.event) \(.job_id // "") \(.error // "")"' \
  | tail -100
```

或者直接用 grep：

```bash
kubectl logs -l app=reelagent-worker --tail=200 | grep -E \
  'worker_starting|worker_stopping|worker_stopped|signal_received|worker_draining|worker_force_cancel|job_task_crashed|worker_loop_error|job_lease_reaped'
```

| 日志 | 含义 |
|---|---|
| `worker_starting` | 进程启动，含 `worker_id` / `concurrency` / `env` |
| `signal_received` | 收到 SIGINT/SIGTERM，开始优雅停机 |
| `worker_draining` | 正在等在跑的任务收尾（含 `running` 数与宽限期） |
| `worker_force_cancel` | 超过 `shutdown_grace_seconds`，强退剩余任务 |
| `worker_stopped` | 已完全退出 |
| `worker_loop_error` | 主循环单次异常（**不会导致退出**，会自动退避重试） |
| `job_task_crashed` | 单个任务协程抛异常（已被 `_on_done` 捕获记录） |
| `job_lease_reaped` | 回收了 N 个过期租约任务 |
| `job_heartbeat_lost` | 心跳失败（任务被接管或收到取消请求） |
| `job_claimed` | 领取了任务 |

**检查进程与副本数**：

```bash
# systemd
systemctl status reelagent-worker

# Kubernetes
kubectl get pods -l app=reelagent-worker
kubectl describe pod <pod> | grep -A5 'Last State'   # 看是不是被 OOMKilled
```

如果是 `OOMKilled`：单个任务的引擎工作区在磁盘上（不是内存），
但 **PIL 渲染多帧 GIF 会有明显内存峰值**。处置：
降低 `REEL_WORKER__CONCURRENCY` 或提高容器内存 limit。

### 1.5 手动干预

**优先级从高到低，不要跳步。**

#### 干预 1：确认 worker 在跑回收循环

只要**至少有一个** worker 存活，它每 120 秒就会回收一次过期租约。
所以最省事的处置是：**确认有 worker 活着，然后等**。

```bash
# 看有几个 worker 在工作（这个指标只在 worker 的 housekeeping 里更新）
curl -s http://127.0.0.1:8000/metrics | grep reelagent_worker_active
```

如果 `reelagent_worker_active == 0`，先把 worker 拉起来：

```bash
kubectl scale deployment/reelagent-worker --replicas=2
# 或
systemctl restart reelagent-worker
```

#### 干预 2：手动触发一次租约回收（不改 SQL）

**没有提供单独的回收接口**。回收逻辑内嵌在 worker 主循环里。
如果你的目的是「立刻回收而不等 120 秒」，最干净的做法是重启一个 worker——
它在启动后的第一次 housekeeping 就会回收。

#### 干预 3：通过接口取消任务（推荐，会正确退款）

```bash
# JSON API（需要会话 Cookie 或 API Key；注意 API Key 也可取消任务，
# 因为它属于写项目操作）
curl -X POST https://your-domain/api/v1/jobs/<job_id>/cancel \
  -H "Authorization: Bearer reel_xxxxxxxx_yyyyyyyy" | jq
```

或页面上的「取消」按钮（`POST /app/projects/{id}/cancel`，
会取消该项目下所有 `pending` / `running` 的任务）。

**取消会退还预扣额度**（幂等键 `cancel-refund:<job_id>`）。

⚠️ **`running` 任务的取消不是立即的**：`cancel()` 只置
`cancel_requested = true`，实际停止依赖 worker 的下一次心跳发现该标志
（最多 20 秒），然后引擎在下一个**阶段边界或镜头边界**抛出
`PipelineCancelled`。一个正在进行的供应商 HTTP 请求会跑完才退出。
从「点取消」到「任务真的停」可能是几十秒。

如果任务已经完全没有 worker 在管（僵尸），
`cancel_requested` 永远不会被读到——见干预 4。

#### 干预 4：直接重置僵尸任务（最后手段）

**必须先确认没有任何 worker 在跑这个任务。**

```sql
-- ① 记录要改动的行（先 SELECT！）
SELECT id, status, stage, locked_by, lease_expires_at, attempts, credits_reserved,
       cancel_requested, heartbeat_at, error_code
  FROM jobs
 WHERE status = 'running'
   AND lease_expires_at < now()
 ORDER BY lease_expires_at;
```

正常情况下这些行应该在 120 秒内被自动回收，所以**执行下面的 UPDATE 前
先确认 worker 确实全挂了**。复现回收逻辑的效果：

```sql
-- ② 干跑：只统计会影响多少行
SELECT count(*) FROM jobs
 WHERE status = 'running'
   AND lease_expires_at IS NOT NULL
   AND lease_expires_at < now();
-- 预期与 ① 结果一致

-- ③ 执行（与 reap_expired_leases() 完全等价）
BEGIN;

UPDATE jobs
   SET status         = 'failed',
       error          = 'worker 租约过期（进程可能崩溃），任务已被重新排队',
       error_code     = 'lease_expired',
       locked_by      = NULL,
       locked_at      = NULL,
       lease_expires_at = NULL
 WHERE status = 'running'
   AND lease_expires_at IS NOT NULL
   AND lease_expires_at < now()
 RETURNING id, project_id;

-- ④ 检查返回的行数符合预期后提交
COMMIT;
```

**这个 UPDATE 会把任务打回可领取状态，因此它会被重新执行——
意味着供应商成本会被重新支付一次。**
如果任务其实已经跑完只是状态没更新，重新执行是纯浪费。
所以**优先用干预 1/2/3**。

#### 干预 5：把任务彻底作废（不再重跑）

如果任务对应的工作已经无意义（项目被删、用户退款走人），
直接终结它，不要让它重新排队：

```sql
BEGIN;

-- 先确认退款状态：如果 credits_reserved > 0，说明预扣额度还没退
SELECT id, org_id, project_id, credits_reserved, status
  FROM jobs
 WHERE id = '<job_id>';

UPDATE jobs
   SET status           = 'cancelled',
       stage            = 'queued',
       finished_at      = now(),
       locked_by        = NULL,
       lease_expires_at = NULL,
       cancel_requested = true
 WHERE id = '<job_id>'
 RETURNING *;

-- ⚠️ 如果上面看到 credits_reserved > 0，必须手工退额度！
-- 不要直接 UPDATE organizations.credits_balance（会破坏对账等式）。
-- 正确做法见第 4.5 节，用 credits.adjust() 写一条反向账本条目。

COMMIT;
```

同时把项目状态改回可操作状态：

```sql
-- 项目状态机：draft / queued / running / ready / failed / archived
UPDATE projects SET status = 'failed' WHERE id = '<project_id>' AND status = 'running';
```

### 1.6 事后确认

```sql
-- 确认没有遗留的僵尸 running
SELECT count(*) FROM jobs WHERE status = 'running' AND lease_expires_at < now();
-- 预期 0（或很快被回收）

-- 对账任务的额度是否已经正确处理
SELECT org_id, ledger_sum, balance, drift FROM (
  SELECT o.id AS org_id,
         COALESCE((SELECT SUM(amount) FROM ledger_entries l WHERE l.org_id = o.id), 0) AS ledger_sum,
         o.credits_balance AS balance,
         o.credits_balance - COALESCE((SELECT SUM(amount) FROM ledger_entries l WHERE l.org_id = o.id), 0) AS drift
    FROM organizations o WHERE o.id = '<org_id>'
) t;
-- drift 必须是 0
```

### 1.7 预防

- **给 worker 配好优雅停机**：K8s 里设置
  `terminationGracePeriodSeconds` **大于**
  `REEL_WORKER__SHUTDOWN_GRACE_SECONDS`（默认 60s），
  否则 SIGTERM 后 pod 会在任务收尾前被强制杀死，产生僵尸任务。
- **监控 `reelagent_job_lease_expired_total` 的增长速率**。
  它增长意味着 worker 在非正常死亡，应该先修根因（OOM、节点故障），
  而不是依赖租约回收兜底。
- **不要让 worker 跑在会被频繁抢占的实例上**（如 spot 实例），
  除非你能接受任务被重跑带来的成本。

---

## 2. 队列积压

### 2.1 症状

- 用户反馈「排队很久还没开始」。
- `reelagent_job_queue_depth{status="pending"}` 持续上升。
- `GET /api/v1/jobs?active_only=true` 返回大量 `pending`。

### 2.2 先分清两种情况

**队列积压**（任务进得来，出得慢）与**调度阻塞**
（任务根本不应该现在跑）是两个完全不同的问题。先查：

```sql
SELECT status,
       count(*)                                  AS n,
       min(created_at)                           AS oldest_created,
       min(run_after)                            AS earliest_run_after,
       max(run_after)                            AS latest_run_after
  FROM jobs
 WHERE status IN ('pending', 'failed')
 GROUP BY status;
```

| 现象 | 含义 | 处置 |
|---|---|---|
| `pending` 大量且 `earliest_run_after <= now()` | **真的积压**：worker 产能不足 | 第 2.3 节扩容 |
| `pending` 大量但 `earliest_run_after > now()` | 任务都在**退避等待**（失败重试） | 第 3 节，看供应商 |
| `failed` 大量 | 失败任务在等退避重试，会反复占用 worker | 第 3 节，看 `error_code` |

**按退避时间分布看是否有集中等待**：

```sql
SELECT date_trunc('minute', run_after) AS run_at, count(*)
  FROM jobs
 WHERE status IN ('pending', 'failed') AND run_after > now()
 GROUP BY 1 ORDER BY 1;
```

失败退避的公式是 `min(30 × 2^(attempts-1), 600)` 秒，
即 30 / 60 / 120 / 240 / 480 / 600 秒。看到这些整数值就说明是退避。

### 2.3 扩容 worker

队列的领取是 `FOR UPDATE SKIP LOCKED`，**加副本就是加吞吐，不需要重新分片**。

```bash
# Kubernetes
kubectl scale deployment/reelagent-worker --replicas=8

# 单机多进程（不推荐，优先用容器编排）
reelagent-worker --concurrency 4 &
reelagent-worker --concurrency 4 &
```

**两个并发参数必须分清**：

| 参数 | 含义 | 默认 |
|---|---|---|
| `REEL_WORKER__CONCURRENCY` | **单个 worker 进程**同时执行的 **Job 数** | 2 |
| `REEL_WORKER__SHOT_CONCURRENCY` | **单个 Job 内**并发生成的**镜头数** | 3 |

因此在跑的供应商调用数上限约为
`worker 进程数 × CONCURRENCY × SHOT_CONCURRENCY`。

**扩容前必须算清三笔账**：

1. **供应商并发上限**。每个供应商配置里有
   `max_concurrency`（默认 4）用于避免触发厂商限流。
   如果扩容后总并发超过供应商的配额，你会看到
   `provider_rate_limited` 错误——**扩容反而降低了有效吞吐**。
2. **数据库连接池**。
   `REEL_DATABASE__POOL_SIZE`(10) + `MAX_OVERFLOW`(20) = 单进程最多 30 条连接。
   `30 × (API 进程数 + worker 进程数)` 必须小于 Postgres 的
   `max_connections`。**这是扩容最常见的踩坑点**，详见第 6 节。
3. **内存**。PIL 渲染 GIF 与 ffmpeg 子进程都有内存峰值。
   按 `CONCURRENCY` 线性估算，留 50% 余量。

推荐扩容顺序：

```
① 先加 worker 副本数（提高 Job 级并行）
② 观察 reelagent_provider_calls_total{status="failed"} 是否上升
③ 如果没有上升，再考虑提高 REEL_WORKER__CONCURRENCY
④ 最后才考虑提高 REEL_WORKER__SHOT_CONCURRENCY（它直接放大供应商压力）
```

`--concurrency` CLI 参数可以覆盖 `REEL_WORKER__CONCURRENCY`，
适合临时做实验：

```bash
reelagent-worker --concurrency 6 --worker-id worker-canary
```

### 2.4 优先级调度现状

领取顺序是 `ORDER BY priority DESC, run_after ASC`：

| 来源 | `priority` |
|---|---|
| 体验版（free） | 0 |
| 创作者版（starter） | 1 |
| 专业版（pro） | 2 |
| 工作室版（studio） | 3 |
| 单镜重跑（`/app/projects/{id}/regenerate`） | **5** |

创建项目时，优先级取 `plan.priority`（体验版 0 → 工作室版 3），
无论走控制台表单还是 `POST /api/v1/projects`，都经由
`projects.start_generation()` 里的 `jobs.enqueue(..., priority=plan.priority)`。

**单镜重跑优先级最高（5）是有意的**：重跑一个镜头成本低、用户预期快，
让它排在整条生成前面能显著改善感知延迟。

> ℹ️ 另有一处需要留意：`POST /api/v1/projects` 会读取 `Idempotency-Key`
> 请求头（未提供时用内容指纹兜底）并在响应里回显，
> 但 `projects.create_project()` **没有接收这个键**——
> 任务的幂等键仍然是 `gen:<project_id>`，而 `project_id` 每次都是新生成的。
> 因此**同一素材重复提交会创建两个项目、各扣一次预扣额度**。
> 详见 [CHANGELOG 已知问题](../CHANGELOG.md#影响云层断点恢复能力的限制)。
> 排障时如果遇到「用户说只点了一次却被扣两次」，
> 先按 `projects.seed_text` + `created_at` 找同一素材的重复项目：

```sql
SELECT id, org_id, title, left(seed_text, 40) AS seed, status,
       credits_spent, created_at
  FROM projects
 WHERE org_id = '<org_id>'
   AND seed_text = '<完整素材文本>'
   AND created_at > now() - interval '1 hour'
 ORDER BY created_at;
```

**如果排队时间仍然过长**，可以临时调高某类任务的优先级：

```sql
-- ⚠️ 治标措施。会让低优先级的任务更慢，先确认业务上可接受。
BEGIN;
SELECT count(*) FROM jobs WHERE status = 'pending' AND priority < 3;  -- 先看影响面
UPDATE jobs SET priority = 3
 WHERE status = 'pending' AND priority < 3 AND created_at < now() - interval '30 minutes';
COMMIT;
```

### 2.5 队列深度监控

指标：`reelagent_job_queue_depth{status="pending"}`

**注意更新时机**：这个 Gauge 只在 worker 的 housekeeping 里更新
（每 `REAP_INTERVAL_SECONDS = 120` 秒一次），
所以它反映的是「最近一次扫描」的状态。worker 全挂时它会**冻结**，
而不是归零或跳变——这本身就是一个告警信号（配合
`reelagent_worker_active`）。

建议告警规则（**示例阈值，需按你的实际产能校准**）：

```yaml
- alert: ReelAgentQueueBacklog
  expr: reelagent_job_queue_depth{status="pending"} > 50
  for: 10m
  annotations:
    summary: "任务队列积压超过 50 个"

- alert: ReelAgentNoWorker
  expr: reelagent_worker_active < 1
  for: 3m
  annotations:
    summary: "没有任何活跃 worker"

- alert: ReelAgentLeaseReaping
  expr: increase(reelagent_job_lease_expired_total[15m]) > 3
  annotations:
    summary: "15 分钟内超过 3 个任务因租约过期被回收，worker 可能在不稳定"
```

### 2.6 如果积压是因为「任务本身永远跑不完」

检查是不是有任务的 `max_attempts` 被设得过大，导致它反复占用 worker：

```sql
SELECT id, kind, project_id, attempts, max_attempts, error_code, run_after
  FROM jobs
 WHERE status IN ('pending', 'failed') AND attempts >= max_attempts - 1
 ORDER BY attempts DESC;
```

把这类任务提前判死，不要再浪费 worker 与供应商成本：

```sql
UPDATE jobs
   SET status = 'dead',
       error = '人工判定：反复失败，标记为 dead 以释放队列容量',
       finished_at = now()
 WHERE status = 'failed'
   AND attempts >= max_attempts - 1
   AND error_code IN ('provider_failure', 'provider_timeout')
 RETURNING id;
```

> ⚠️ 置为 `dead` **不会自动退额度**（`fail()` 才做退款）。
> 这些任务的 `credits_reserved` 在之前每次 `fail()` 时已被退到 0
> （退款是幂等的），所以通常无需额外处理。
> 但执行前请确认：

```sql
SELECT id, credits_reserved FROM jobs
 WHERE status = 'dead' AND credits_reserved > 0;
-- 预期为空。若有值，按第 4.5 节手工退额度。
```

---

## 3. 供应商大面积失败

### 3.1 症状

- 用户反馈「生成失败」激增。
- `reelagent_provider_calls_total{status="failed"}` 突增。
- `reelagent_provider_circuit_open` = 1。
- `jobs.error_code` 集中在 `provider_*` 系列。

### 3.2 定位

**先看错误码分布**：

```sql
SELECT error_code,
       count(*)                                          AS n,
       count(DISTINCT project_id)                        AS projects,
       min(finished_at)                                  AS first_seen,
       max(finished_at)                                  AS last_seen
  FROM jobs
 WHERE status IN ('failed', 'dead')
   AND finished_at > now() - interval '2 hours'
 GROUP BY error_code
 ORDER BY n DESC;
```

| `error_code` | 含义 | 可重试 |
|---|---|---|
| `provider_failure` | 上游供应商调用失败（HTTP 5xx / 返回异常） | ✅ |
| `provider_timeout` | 供应商超时（默认 `REEL_PROVIDERS__*__TIMEOUT_SECONDS=180`） | ✅ |
| `provider_rate_limited` | 触发厂商限流 | ✅ |
| `service_unavailable` | 服务暂时不可用 | ✅ |
| `lease_expired` | worker 崩溃导致的租约过期 | ✅ |
| `budget_exceeded` | 超出预算上限（402） | ❌ |
| `unsupported_operation` | 功能在当前部署下不可用（400） | ❌ |
| `validation_failed` | 请求参数不合法（422） | ❌ |
| `unexpected_error` | 未预期异常（默认按可重试处理） | ✅ |

**再看是哪个供应商 / 能力在失败**：

```sql
-- 从原始用量表看成功率与延迟（这里有 provider / model / capability 维度）
SELECT provider,
       capability,
       model,
       count(*)                                                    AS calls,
       sum(CASE WHEN success THEN 0 ELSE 1 END)                    AS failures,
       round(100.0 * sum(CASE WHEN success THEN 0 ELSE 1 END) / count(*), 1) AS fail_pct,
       round(avg(latency_ms)::numeric, 0)                          AS avg_ms,
       round(max(latency_ms)::numeric, 0)                          AS max_ms
  FROM usage_records
 WHERE created_at > now() - interval '1 hour'
 GROUP BY 1, 2, 3
 ORDER BY failures DESC;
```

```sql
-- 错误码维度
SELECT provider, capability, error_code, count(*)
  FROM usage_records
 WHERE created_at > now() - interval '1 hour' AND NOT success
 GROUP BY 1, 2, 3 ORDER BY 4 DESC;
```

**看熔断状态**：

```bash
curl -s http://127.0.0.1:8000/metrics | grep provider_circuit_open
```

熔断配置：

| 参数 | 默认 | 含义 |
|---|---|---|
| `REEL_PROVIDERS__<KIND>__CIRCUIT_BREAKER_THRESHOLD` | 5 | 连续失败 N 次后打开断路器 |
| `REEL_PROVIDERS__<KIND>__CIRCUIT_BREAKER_RESET_SECONDS` | 60 | 打开多少秒后尝试半开 |
| `REEL_PROVIDERS__<KIND>__MAX_RETRIES` | 3 | 失败重试次数 |
| `REEL_PROVIDERS__<KIND>__MAX_CONCURRENCY` | 4 | 单供应商并发上限 |
| `REEL_PROVIDERS__<KIND>__TIMEOUT_SECONDS` | 180 | 单次调用超时 |

其中 `<KIND>` ∈ `LLM` / `IMAGE` / `VIDEO` / `TTS`，
例如 `REEL_PROVIDERS__VIDEO__CIRCUIT_BREAKER_THRESHOLD`。

断路器打开时，请求会立即抛出
`ProviderCircuitOpen`（错误码 `provider_circuit_open`，HTTP 503），
响应体带 `details.retry_after_seconds`，并设置 `Retry-After` 响应头。

**如果熔断误伤**（供应商其实好了，但断路器还在冷却期），
最直接的处置是**重启 worker / API 让断路器状态归零**
（断路器是进程内状态，没有持久化）：

```bash
kubectl rollout restart deployment/reelagent-worker
kubectl rollout restart deployment/reelagent-server
```

### 3.3 降级路径

系统有三层天然的降级，理解它们能帮你判断「是否需要人工介入」：

#### 降级 1：引擎内的模型路由降级

当任务预算耗尽（`settings.budget_usd`，来自
`payload.budget_usd` 或 `REEL_JOB_BUDGET_USD`，默认 2.0 USD）时，
`ModelRouter` 会把档位强制降为 `draft`：

```
draft 档 = 跳过视频模型，用本地运动合成 → 视频供应商成本为 0
```

另外，「剩余预算不足以支撑多镜 premium」时会把 `premium` 降为 `standard`。

这是**自动的、无需干预的**降级。代价是成片质量下降，
但任务仍然会完成并交付。

#### 降级 2：引擎内的单镜失败隔离

单个镜头失败不会拖垮整条流水线：`Director` 用
`cf.as_completed` 收集结果，单镜异常只把该镜头标记为 `failed`，
其余镜头照常生成。因此**「部分镜头失败但成片仍交付」是预期行为**，
不是 bug。

查看某个项目有多少镜头失败：

```sql
SELECT shot_key, status, error
  FROM shots
 WHERE project_id = '<project_id>' AND status = 'failed';
```

#### 降级 3：引擎的 mock 供应商（**生产环境不可用**）

引擎支持 mock 供应商（离线产出真实媒体文件），
但**云层在生产环境刻意拒绝使用它**：

```python
def resolve_engine_mode(settings, org) -> str:
    """生产环境若没有可用 Key 则直接拒绝——宁可不接单，也不能交付假成片。"""
    has_video_key = bool(byok.get("video")) or settings.providers.video.configured
    if has_video_key:
        return "live"
    if settings.is_production:
        raise UnsupportedOperation(
            "当前部署未配置视频生成供应商，暂时无法生成成片。请联系支持。"
        )
    return "mock"
```

也就是说：**如果平台视频 Key 配置被误删，所有生成请求会立刻变成
`unsupported_operation`（HTTP 400）而不是产出占位图。**
这是刻意的产品红线（「让用户花钱买到占位图是最坏的产品事故」）。

因此如果你看到大量 `unsupported_operation`，**第一件事是检查视频 Key 配置**：

```bash
curl -s http://127.0.0.1:8000/health | jq '.checks.providers'
# 期望看到 video 不是「未配置」
```

### 3.4 临时止血与停服

**停 worker（停止消耗供应商成本与新任务）**：

```bash
# 方式 A：缩容到 0（推荐，进程会优雅停机）
kubectl scale deployment/reelagent-worker --replicas=0

# 方式 B：systemd
systemctl stop reelagent-worker
```

⚠️ **缩容到 0 的后果**：
- 队列里的 `pending` 任务会堆积（**不会**被扣钱——
  额度是任务成功结算时才最终确定的，预扣在创建时已发生且失败会退）。
- **没有 worker 就没有租约回收**，如果有 `running` 的僵尸任务，
  它们会停留在 `running` 直到有新 worker 启动。
- 邮件发件箱不再被投递。

**如果只是要停止「新任务的产生」而不停 worker**：

```sql
-- 停用某个租户（最常用）
UPDATE organizations
   SET is_suspended = true,
       suspended_reason = 'incident-2025-xx-xx: 供应商故障期间暂停'
 WHERE id = '<org_id>'
 RETURNING id, name, slug, is_suspended;
```

被停用的组织：API Key 与请求会收到 `AccountSuspended`
（HTTP 403，「该组织已被停用，请联系支持」）。

> ⚠️ **`is_suspended` 只在 `_principal_from_api_key` 与
> `_resolve_org`（默认组织路径）里检查。**
> 当用户通过 `X-Org-Id` 头显式指定组织时，
> `_resolve_org` 的 `requested` 分支**不检查 `is_suspended`**
> （只在遍历结束后命中了才返回，检查写在默认分支之后）。
> 这是一个需要留意的行为差异；停用高风控租户时建议同时吊销其 API Key：

```sql
UPDATE api_keys SET revoked_at = now()
 WHERE org_id = '<org_id>' AND revoked_at IS NULL
 RETURNING prefix;
```

**恢复服务**：

```bash
kubectl scale deployment/reelagent-worker --replicas=3
```

```sql
UPDATE organizations SET is_suspended = false, suspended_reason = ''
 WHERE id = '<org_id>';
```

### 3.5 退还额度

失败任务的额度退还**是自动的**（`jobs.fail()` 里
`credits.refund(..., idempotency_key=f"refund:{job.id}")`）。
只有当自动退还也失败（数据库故障、进程被强杀在退款前）时才需要手工处理。

**先找出可能欠退的任务**：

```sql
-- 终态（failed/dead/cancelled）但仍有预扣未结清的任务
SELECT id, org_id, project_id, status, error_code,
       credits_reserved, credits_charged, provider_cost_usd, finished_at
  FROM jobs
 WHERE status IN ('failed', 'dead', 'cancelled')
   AND credits_reserved > 0
 ORDER BY finished_at DESC;
```

`credits_reserved > 0` 且任务是终态 → 这笔预扣没有被退还。

**退还的正确方式**（**不要**直接 UPDATE `credits_balance`）：

`credits.refund()` 需要 `org_id` 与 `job_id`。有两种做法：

**做法 A（推荐）：写一个一次性的 Python 脚本**

```python
# scripts/refund_stuck.py  ——  一次性补偿脚本
import asyncio
from reelagent_cloud.config import get_settings
from reelagent_cloud.db.session import session_scope
from reelagent_cloud.services import credits

STUCK_JOB_IDS = ["job_01ABC...", "job_01DEF..."]   # ← 从上面的查询结果填入

async def main() -> None:
    settings = get_settings()
    for job_id in STUCK_JOB_IDS:
        async with session_scope() as session:
            # refund 的幂等键固定为 refund:<job_id>，重复执行是安全的
            entry = await credits.refund(
                session,
                org_id="<org_id>",              # ← 与 job 的 org_id 一致
                amount=<credits_reserved>,      # ← 与 job 的 credits_reserved 一致
                description=f"运维补偿：任务 {job_id} 预扣未退还",
                project_id="<project_id>",
                job_id=job_id,
                idempotency_key=f"ops-refund:{job_id}",
            )
            print(f"{job_id} -> ledger {entry.id} amount={entry.amount}")
    # 顺手把 jobs.credits_reserved 清零，避免下次扫描又把它列出来
    async with session_scope() as session:
        from sqlalchemy import update
        from reelagent_cloud.db.models import Job
        await session.execute(
            update(Job).where(Job.id.in_(STUCK_JOB_IDS)).values(credits_reserved=0)
        )
    print("done")

asyncio.run(main())
```

运行：

```bash
python scripts/refund_stuck.py
```

**做法 B（无 Python 环境时的等效 SQL）**

必须满足两个条件：条目符号正确（`kind='refund'`，`amount` 为正）、
`balance_after` 与 `organizations.credits_balance` 在**同一事务内**更新。

```sql
BEGIN;
-- 变量（按实际填入）
-- org        = '<org_id>'
-- job        = '<job_id>'
-- amt        = <credits_reserved>
-- idem       = 'ops-refund:<job_id>'

-- ① 加锁读，避免并发下丢失更新
SELECT credits_balance FROM organizations WHERE id = '<org_id>' FOR UPDATE;

-- ② 写账本（balance_after 用当前余额 + amt）
INSERT INTO ledger_entries
  (id, org_id, kind, amount, balance_after, description,
   project_id, job_id, external_ref, idempotency_key, created_by, meta, created_at)
VALUES
  ('led_' || upper(substr(md5(random()::text), 1, 26)),   -- 生产环境请用 ULID 生成器
   '<org_id>', 'refund', <amt>,
   (SELECT credits_balance FROM organizations WHERE id = '<org_id>') + <amt>,
   '运维补偿：任务 <job_id> 预扣未退还',
   '<project_id>', '<job_id>', '', 'ops-refund:<job_id>', NULL, '{}'::jsonb, now());

-- ③ 更新快照
UPDATE organizations
   SET credits_balance = credits_balance + <amt>, updated_at = now()
 WHERE id = '<org_id>';

-- ④ 清零 job 的预扣标记
UPDATE jobs SET credits_reserved = 0 WHERE id = '<job_id>';

-- ⑤ 提交前自查等式（必须 drift = 0，且 consistent = true）
SELECT ledger_sum, balance, balance - ledger_sum AS drift FROM (
  SELECT COALESCE(SUM(amount), 0) AS ledger_sum FROM ledger_entries WHERE org_id = '<org_id>'
) s, (SELECT credits_balance AS balance FROM organizations WHERE id = '<org_id>') o;

COMMIT;
```

> ⚠️ **`led_` 后面的 26 位必须是合法 ULID**（Crockford Base32 时间戳前缀），
> 因为 `parse_ulid_timestamp()` 会从 ID 反解创建时间。
> 用上面那个 `md5` 的写法会导致该函数报错。
> **生产环境请用做法 A**（脚本），它调用 `new_id(PREFIX["ledger"])` 生成合法 ULID。
> 这里的 SQL 只用于理解语义。

### 3.6 长期措施

- **给供应商配置合理的并发上限**（`max_concurrency`），
  宁可在队列里排队，也不要去触发厂商限流。
- **接入供应商的状态页**，在厂商公告故障时提前停 worker。
- **按供应商区分监控**：`reelagent_provider_calls_total{provider="ark"}` 的
  失败率应该独立告警，而不是和整体混在一起。
- **考虑多供应商兜底**：当前 `ProvidersSettings` 每个能力只有一个供应商配置
  （`llm` / `image` / `video` / `tts` 各一个）。真正的多供应商
  故障转移需要扩展配置结构（当前未实现）。

---

## 4. 额度对不上账

这是最需要谨慎处理的一类问题，因为它**直接对应钱**。

### 4.1 机制回顾

| | 角色 | 表 / 字段 |
|---|---|---|
| **事实** | 只追加的账本 | `ledger_entries`（每次变动一条，带 `balance_after`） |
| **快照** | 快速读取的余额 | `organizations.credits_balance` |

**核心不变量**：

```
organizations.credits_balance == SUM(ledger_entries.amount) WHERE org_id = ?
```

两者在**同一事务内**更新。`credits.reconcile()` 就是这个等式的实现，
控制台页面 `/app/usage` 会直接渲染它的结果。

### 4.2 第一步：确认是否真的不一致

**通过接口（推荐，等价于页面看到的结果）**：

```bash
# 注意：当前没有独立的 reconcile API 端点。
# 页面 /app/usage 会调用 credits.reconcile()。
# 用 SQL 复现同一个计算：
```

```sql
SELECT o.id                                   AS org_id,
       o.name,
       o.slug,
       o.credits_balance                      AS balance,
       COALESCE(SUM(l.amount), 0)             AS ledger_sum,
       o.credits_balance - COALESCE(SUM(l.amount), 0) AS drift,
       (o.credits_balance = COALESCE(SUM(l.amount), 0)) AS consistent
  FROM organizations o
  LEFT JOIN ledger_entries l ON l.org_id = o.id
 WHERE o.id = '<org_id>'
 GROUP BY o.id, o.name, o.slug, o.credits_balance;
```

**全量扫描，找出所有不一致的组织**（先跑这个，判断是单点还是系统性问题）：

```sql
SELECT o.id,
       o.name,
       o.credits_balance                      AS balance,
       COALESCE(SUM(l.amount), 0)             AS ledger_sum,
       o.credits_balance - COALESCE(SUM(l.amount), 0) AS drift,
       count(l.id)                            AS entries
  FROM organizations o
  LEFT JOIN ledger_entries l ON l.org_id = o.id
 GROUP BY o.id, o.name, o.credits_balance
HAVING o.credits_balance <> COALESCE(SUM(l.amount), 0)
 ORDER BY abs(o.credits_balance - COALESCE(SUM(l.amount), 0)) DESC;
```

> `drift > 0`：**余额比账本多**（少扣了，或账本丢了入账记录）。
> `drift < 0`：**余额比账本少**（多扣了，或账本多了不存在的记录）。
>
> 用户视角：`drift < 0` 是「我的点数被多扣了」，投诉优先级最高。

### 4.3 第二步：定位断裂点

`balance_after` 是为了这个用途设计的。把账本按时间排序，
检查每条的 `balance_after` 是否等于「上一条的 `balance_after` + 本条 `amount`」：

```sql
WITH ordered AS (
  SELECT id, created_at, kind, amount, balance_after, description,
         lag(balance_after) OVER (PARTITION BY org_id ORDER BY created_at, id) AS prev_balance
    FROM ledger_entries
   WHERE org_id = '<org_id>'
)
SELECT id, created_at, kind, amount, prev_balance, balance_after,
       prev_balance + amount AS expected_balance,
       balance_after - (prev_balance + amount) AS gap
  FROM ordered
 WHERE prev_balance IS NOT NULL
   AND balance_after <> prev_balance + amount
 ORDER BY created_at;
```

**第一条断裂的位置就是问题发生的时间点。** 那条记录前后发生了什么，
答案通常就在那里。

**按类型汇总，看是哪个环节在出问题**：

```sql
SELECT kind,
       count(*)                     AS n,
       sum(amount)                  AS net,
       sum(CASE WHEN amount > 0 THEN amount ELSE 0 END) AS credited,
       sum(CASE WHEN amount < 0 THEN amount ELSE 0 END) AS debited,
       min(created_at)              AS first_at,
       max(created_at)              AS last_at
  FROM ledger_entries
 WHERE org_id = '<org_id>'
 GROUP BY kind
 ORDER BY net DESC;
```

**检查是否有任务结算与账本对不上**：

```sql
-- 已终态任务：预扣 + 实扣 与账本里该 job 的净额是否一致
SELECT j.id                                   AS job_id,
       j.status,
       j.credits_reserved,
       j.credits_charged,
       COALESCE(SUM(l.amount), 0)             AS ledger_net_for_job,
       -j.credits_charged + COALESCE(SUM(l.amount), 0) AS expected_delta
  FROM jobs j
  LEFT JOIN ledger_entries l ON l.job_id = j.id
 WHERE j.org_id = '<org_id>'
   AND j.status IN ('succeeded', 'failed', 'dead', 'cancelled')
 GROUP BY j.id, j.status, j.credits_reserved, j.credits_charged
HAVING -j.credits_charged + COALESCE(SUM(l.amount), 0) <> 0
 ORDER BY j.id DESC
 LIMIT 50;
```

> 说明：对成功任务，账本里应有
> `consume(reserve)` 的负数 + 可能的 `refund(settle-refund)` 正数，
> 净额应为 `-credits_charged`。这个查询找的就是净额不符的任务。

**检查幂等键是否有重复（唯一约束应该阻止，但如果有绕过路径就要查）**：

```sql
SELECT org_id, idempotency_key, count(*)
  FROM ledger_entries
 WHERE idempotency_key IS NOT NULL
 GROUP BY 1, 2
HAVING count(*) > 1;
-- 预期为空（uq_ledger_org_idempotency 保证）
```

### 4.4 常见原因清单

按实际发生概率排序：

| # | 原因 | 如何确认 |
|---|---|---|
| 1 | **有人直接用 SQL 改了 `organizations.credits_balance`** | 查数据库审计日志（`pg_stat_statements` 或云厂商的审计日志）找 `UPDATE organizations SET credits_balance` |
| 2 | **绕过了 `credits` 服务层直接 INSERT 账本** | 检查 `ledger_entries.id` 是否符合 ULID 格式；非法 ID 说明是手工插入 |
| 3 | **`settle()` 的补扣失败被记为欠账** | 查 `jobs.result->'settlement'->>'extra_uncollected'`：`job.credits_charged` 已记为实际值，但账本里少了这笔补扣 → `drift > 0` |
| 4 | **`_apply()` 的 `flush()` 缺失导致同会话读到旧值** | 代码层面已有注释警告，但仍需检查是否有新代码路径漏了 flush。表现是「同一事务内连续两次变动，第二条的 `balance_after` 基于旧值」 |
| 5 | **组织行不存在时账本被写入** | 查 `ledger_entries` 里是否有 `org_id` 不存在于 `organizations` 的记录（外键应阻止，确认约束是否真的生效） |
| 6 | **并发下的丢失更新** | 只可能发生在绕过 `_lock_org()` 的 `SELECT ... FOR UPDATE` 的路径上。检查是否有代码直接 `org.credits_balance += x` |
| 7 | **手工 SQL 调整时算错** | 查 `kind='adjustment'` 的记录，看 `balance_after` 是否连续 |
| 8 | **`CHECK` 约束被临时禁用后未恢复** | 查 `pg_constraint` 里 `ck_org_credits_non_negative` 是否 still valid |

针对第 3 条的具体查询：

```sql
SELECT id, org_id, status, credits_charged, credits_reserved, provider_cost_usd,
       result->'settlement' AS settlement
  FROM jobs
 WHERE result->'settlement'->>'extra_uncollected' IS NOT NULL
 ORDER BY finished_at DESC
 LIMIT 50;
```

针对第 8 条：

```sql
SELECT conname, convalidated
  FROM pg_constraint
 WHERE conname = 'ck_org_credits_non_negative';
-- convalidated 必须是 true
```

### 4.5 修复方式

**唯一正确的修复方向：用账本条目补齐差额，让等式重新成立。**

**绝对不要**直接 `UPDATE organizations SET credits_balance = <账本求和>`
——那只是把症状掩盖掉，丢失了「发生了什么」的信息，
而且下次对账还是会发现别的问题。

#### 修复原则

| `drift` | 含义 | 修复动作 |
|---|---|---|
| `drift > 0`（余额 > 账本） | 余额偏高，用户占了便宜 | 写一条 **负数** 的 `adjustment`（扣回差额） |
| `drift < 0`（余额 < 账本） | 余额偏低，用户被多扣 | 写一条 **正数** 的 `adjustment`（补回差额） |

两种情况下都 **不改历史条目**，只追加。

#### 修复实现

`credits.adjust()` 是为此设计的：

```python
async def adjust(session, *, org_id, amount: int, reason: str, created_by: str) -> LedgerEntry:
    """人工调整（客服补偿、扣罚）。允许扣成负数需要显式确认。"""
    return await _apply(
        session,
        org_id=org_id,
        kind=LedgerKind.ADJUSTMENT,
        amount=int(amount),
        description=f"人工调整：{reason}",
        created_by=created_by,
        allow_negative=False,     # 不能把余额扣成负数
    )
```

**注意 `allow_negative=False`**：如果 `drift > 0` 且差额大于当前余额
（理论上不可能，因为余额本身就是 drift 的来源），扣回会失败。
实际上 `amount = -drift` 时 `new_balance = balance - drift = ledger_sum >= 0`，
因此**只要 `ledger_sum >= 0` 就一定成功**。

**修复脚本**：

```python
# scripts/reconcile_fix.py  ——  对账修复脚本
#
# 用法：
#   python scripts/reconcile_fix.py --org <org_id> --dry-run
#   python scripts/reconcile_fix.py --org <org_id> --apply --reason "INC-1234 对账修复"
import argparse
import asyncio

from reelagent_cloud.config import get_settings
from reelagent_cloud.db.session import session_scope
from reelagent_cloud.services import credits


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--org", required=True, help="组织 ID")
    parser.add_argument("--apply", action="store_true", help="真正执行（默认只干跑）")
    parser.add_argument("--reason", default="对账修复", help="调整原因，会写进账本")
    parser.add_argument("--created-by", default="ops", help="操作者标识")
    args = parser.parse_args()

    get_settings()   # 触发配置校验

    async with session_scope() as session:
        result = await credits.reconcile(session, args.org)

    print(f"org_id     = {result['org_id']}")
    print(f"ledger_sum = {result['ledger_sum']}")
    print(f"balance    = {result['balance']}")
    print(f"drift      = {result['drift']}")
    print(f"consistent = {result['consistent']}")

    if result["consistent"]:
        print("账本与余额一致，无需修复。")
        return 0

    # drift > 0 → 余额偏高 → 扣回（传负数）
    # drift < 0 → 余额偏低 → 补回（传正数）
    amount = -result["drift"]

    if not args.apply:
        print(f"\n[干跑] 将写入一条 adjustment：amount = {amount}")
        print("      确认无误后加 --apply 执行。")
        return 0

    async with session_scope() as session:
        entry = await credits.adjust(
            session,
            org_id=args.org,
            amount=amount,
            reason=args.reason,
            created_by=args.created_by,
        )
        print(f"\n已写入账本条目 {entry.id}：amount={entry.amount} balance_after={entry.balance_after}")

    async with session_scope() as session:
        after = await credits.reconcile(session, args.org)
    print(f"修复后：ledger_sum={after['ledger_sum']} balance={after['balance']} "
          f"drift={after['drift']} consistent={after['consistent']}")

    return 0 if after["consistent"] else 1


raise SystemExit(asyncio.run(main()))
```

执行：

```bash
python scripts/reconcile_fix.py --org org_01M4Z76RW2WNQXHJE6Y49XNMNA --dry-run
python scripts/reconcile_fix.py --org org_01M4Z76RW2WNQXHJE6Y49XNMNA --apply --reason "INC-1234"
```

**修复后必须做的两件事**：

1. **复查全量对账**（跑 4.2 的全量查询），确认没有其它组织也不一致。
2. **通知用户**（如果是 `drift < 0`，即用户被多扣了）。
   账本条目里 `description` 会写「人工调整：<reason>」，
   用户能在 `/app/usage` 页面上看到，所以 `reason` 要写得让人看得懂。

### 4.6 用户侧的自证途径

用户可以在 `/app/usage` 页面看到完整的流水与对账结果，
也可以通过 API：

```bash
curl -s "https://your-domain/api/v1/credits?limit=100" \
  -H "Authorization: Bearer reel_xxxxxxxx_yyyyyyyy" | jq
```

返回每条流水含 `id` / `kind` / `amount` / `balance_after` /
`description` / `project_id` / `job_id` / `created_at`。
**因为有 `balance_after`，用户能自己验证「上一笔余额 + 本笔金额 = 本笔余额」**——
这是把对账能力交给用户的设计。

---

## 5. Stripe Webhook 没生效

### 5.1 症状

- 用户付了钱但套餐没变（`organizations.plan` 还是 `free`）。
- 订阅到期续费后额度没有增加。
- 用户取消了订阅但仍然能继续用付费能力。
- Stripe Dashboard 的 Webhook 页面显示投递失败。

### 5.2 机制回顾

```
Stripe ──POST /webhooks/stripe──▶ 验证签名（原始 body + Stripe-Signature 头）
                                      │
                                      ▼
                               record_event()：写 webhook_events
                               UNIQUE(provider, event_id) 去重
                                      │
                                      ├─ 已存在 ─▶ 返回 "duplicate"（200，不重复处理）
                                      │
                                      ▼
                               process_event()：按 event_type 分发
                                      │
                                      ├─ 不在 _HANDLERS ─▶ status=IGNORED（200）
                                      ├─ 处理成功 ────────▶ status=PROCESSED（200）
                                      └─ 抛异常 ──────────▶ status=FAILED（500 → Stripe 会重试）
```

**关键设计**：

| 设计 | 理由 |
|---|---|
| **验签用原始请求体** | 任何中间件提前消费/改写 body 都会让签名对不上。路由不挂任何 body 解析依赖 |
| **先落库再处理** | 重复投递被唯一约束挡下，不会重复发额度 |
| **失败返回 5xx** | 让 Stripe 重试。**吞异常返回 200 会导致用户付了钱没到账** |
| **双重幂等** | `webhook_events` 去重 + 额度发放的 `idempotency_key` |

`billing.enabled=false` 时，`/webhooks/stripe` 直接返回
`{"ignored": "billing disabled"}`（200）。

### 5.3 排查 SQL

**第一步：查这个事件有没有到过我们这里**

```sql
SELECT id,
       provider,
       event_id,
       event_type,
       status,          -- received / processed / failed / ignored
       attempts,
       left(error, 300) AS error,
       processed_at,
       created_at,
       payload
  FROM webhook_events
 WHERE provider = 'stripe'
 ORDER BY created_at DESC
 LIMIT 50;
```

**第二步：按事件 ID 精确查（从 Stripe Dashboard 复制 `evt_...`）**

```sql
SELECT * FROM webhook_events
 WHERE provider = 'stripe' AND event_id = 'evt_1ABC...';
```

**第三步：按类型与状态汇总（判断是全面失效还是个别事件）**

```sql
SELECT event_type,
       status,
       count(*)          AS n,
       max(created_at)   AS last_at
  FROM webhook_events
 WHERE provider = 'stripe'
   AND created_at > now() - interval '24 hours'
 GROUP BY 1, 2
 ORDER BY 1, 2;
```

**第四步：找失败的事件**

```sql
SELECT event_id, event_type, attempts, error, created_at
  FROM webhook_events
 WHERE provider = 'stripe' AND status = 'failed'
 ORDER BY created_at DESC
 LIMIT 30;
```

### 5.4 判因表（最重要的一张表）

| 现象 | 结论 | 处置 |
|---|---|---|
| **表里完全没有该 `event_id`** | 请求没到应用层，或验签失败后被 400 拒绝，或 `billing.enabled=false` | 见 5.5 |
| `status = 'ignored'` | 事件类型不在 `_HANDLERS` 里 | 正常行为，除非你期望它被处理 → 见 5.6 |
| `status = 'failed'`，`error` 有值 | 处理器内部抛异常 | 看 `error` 内容，见 5.7 |
| `status = 'received'` 且 `processed_at IS NULL` | 落库了但处理中断（进程被强杀） | 这个状态会一直保留，需要重放，见 5.8 |
| `status = 'processed'` 但业务没生效 | 处理器执行了但逻辑没改到该改的行 | 见 5.9 |

### 5.5 表里完全没有该事件

**按可能性排序检查**：

**① `billing.enabled` 是不是 false**

```bash
curl -s http://127.0.0.1:8000/health | jq '.checks.billing'
```

日志关键字：`stripe_webhook_disabled`。

**② 验签失败（400）**

日志关键字：`stripe_webhook_verify_failed`

```bash
kubectl logs -l app=reelagent-server --tail=500 | grep stripe_webhook_verify_failed
```

常见原因：

| 原因 | 确认方式 |
|---|---|
| `REEL_BILLING__WEBHOOK_SECRET` 与 Stripe Dashboard 里的签名密钥不一致 | 在 Stripe Dashboard → Developers → Webhooks → 选中端点 → Reveal signing secret，与部署配置比对 |
| 反向代理 / 中间件改写了请求体 | 检查 Nginx / Ingress 配置是否有 `proxy_set_header` 改动 body，或有 WAF 重写 |
| 代理加了额外的 body 处理 | 确认没有任何中间件在路由前 `await request.json()` |
| 请求被别的路由接管 | 确认 `/webhooks/stripe` 在路由表里；`billing_routes.router` 应该在最靠前挂载 |

**③ 请求根本没到达**

```bash
# 在服务器上看有没有 POST /webhooks/stripe 的访问日志
kubectl logs -l app=reelagent-server --tail=1000 | grep 'webhooks/stripe'
```

> ⚠️ **注意**：Stripe webhook 的路由是
> `APIRouter(prefix="/webhooks", tags=["webhooks"], include_in_schema=False)`，
> 它**不在 OpenAPI 文档里**。因此不要用 `/docs` 来确认它是否存在。

**④ 网络层阻断**

Stripe 的 webhook 源 IP 段是公开的。检查：
- 云厂商的安全组 / 网络 ACL 是否放行了入站的 443。
- 反向代理是否只允许特定路径。
- 域名是否可被公网解析（Stripe 无法访问内网地址）。

用 Stripe CLI 从外部验证连通性：

```bash
stripe listen --forward-to https://your-domain/webhooks/stripe
```

### 5.6 `status = 'ignored'`

当前注册的 6 个事件处理器（`services/billing.py::_HANDLERS`）：

| 事件类型 | 动作 |
|---|---|
| `checkout.session.completed` | 激活订阅：回填订阅状态 + 发放首期额度（幂等键 `sub-activate:<sub_id>`） |
| `customer.subscription.created` | 同 `_handle_subscription_updated` |
| `customer.subscription.updated` | 同步套餐 / 状态 / 周期，发首期额度 |
| `customer.subscription.deleted` | 订阅置 `canceled`，组织降级到 `free` 并恢复水印。**不改账本** |
| `invoice.paid` | 若是续费（`billing_reason == 'subscription_cycle'`）→ 发放周期额度（幂等键 `invoice:<invoice_id>`）；首购只返回 `paid <amount>` |
| `invoice.payment_failed` | 订阅置 `past_due`。**不改账本** |

**其它事件一律 `ignored`**。因此如果某个你期望的事件被 ignored，
说明它不在上表里——需要在 Stripe Dashboard 里取消订阅它（减少噪音），
或者提交 PR 添加处理器。

### 5.7 `status = 'failed'`

看 `error` 字段。它记录了 `f"{type(exc).__name__}: {exc}"`（截断到 1000 字符）。

日志里搜同一个事件：

```bash
kubectl logs -l app=reelagent-server --tail=2000 \
  | grep -A2 'stripe_webhook_processing_failed' | grep -B1 -A2 'evt_'
```

**常见失败原因**：

| `error` 内容 | 原因 | 处置 |
|---|---|---|
| `NotFound: 组织不存在` | `client_reference_id` / `metadata.org_id` 指向的组织不存在 | 确认 Checkout 是用哪个组织的会话创建的；必要时手工修正订阅归属 |
| `UnsupportedOperation: 未配置 Stripe webhook secret` | `REEL_BILLING__WEBHOOK_SECRET` 为空 | 配置它 |
| `UnsupportedOperation: 当前部署未开启在线支付` | `billing.enabled=false` | 配置 `REEL_BILLING__ENABLED=true` |
| Stripe SDK 的网络错误 | 调 `stripe.Subscription.retrieve` 时网络不通 | 检查出站网络 / 代理配置。**注意 `_sync_subscription` 内部对 retrieve 失败是容错的**（只记 `stripe_subscription_fetch_failed` 警告），因此这个错误通常来自别处 |
| `ValidationFailed: Webhook 缺少事件 ID` | 事件结构异常 | 检查是不是有人在手工 POST 假事件 |

### 5.8 重放（replay）

**优先用 Stripe 侧的重放**，因为 Stripe 会正确地重新签名。

#### 方式 A：Stripe Dashboard 重发（推荐）

1. Stripe Dashboard → Developers → Webhooks → 选中你的端点。
2. 在「Events」列表里找到失败的事件。
3. 点击事件 → 「Resend」（重发）。

**注意幂等性**：因为我们先落 `webhook_events` 再处理，
重复投递会被识别为 `duplicate` 而**跳过处理**，直接返回 200。

因此**如果那条记录的 `status = 'failed'`，重发不会重新处理**——
`record_event()` 返回 `existing`，`process_event()` 立刻返回 `"duplicate"`。

**正确做法：先把那条记录的状态改回可重放状态，再让 Stripe 重发。**

有两种选择：

**选择 1：改状态（保留记录，推荐）**

```sql
BEGIN;
-- ① 先看当前状态
SELECT event_id, event_type, status, error, attempts
  FROM webhook_events
 WHERE provider = 'stripe' AND event_id = 'evt_1ABC...';

-- ② 把状态改为 received 并累计 attempts
--    注意：process_event() 除了 status 还依赖 record_event() 的返回值，
--    而 record_event() 只按 (provider, event_id) 查记录是否存在。
--    只要记录存在，就会返回 first=False → "duplicate"。
--    ⚠️ 因此改 status 不足以触发重放！必须删除记录（见选择 2）。
COMMIT;
```

⚠️ **重要修正**：看 `record_event()` 的实现：

```python
existing = (await session.execute(
    select(WebhookEvent).where(
        WebhookEvent.provider == provider, WebhookEvent.event_id == event_id
    )
)).scalar_one_or_none()
if existing is not None:
    log.info("webhook_duplicate", ...)
    return existing, False          # ← first = False
```

`process_event()` 拿到 `first=False` 就直接返回 `"duplicate"`。
**因此只改 `status` 字段不会触发重新处理——必须删除那一行。**

**选择 2：先备份再删除记录（触发重放的正确做法）**

```sql
BEGIN;

-- ① 备份（务必！这是排障证据）
CREATE TABLE IF NOT EXISTS webhook_events_replay_backup (
  LIKE webhook_events INCLUDING ALL
);
INSERT INTO webhook_events_replay_backup
SELECT * FROM webhook_events
 WHERE provider = 'stripe' AND event_id = 'evt_1ABC...';

-- ② 确认备份成功
SELECT count(*) FROM webhook_events_replay_backup
 WHERE event_id = 'evt_1ABC...';   -- 必须 = 1

-- ③ 删除原记录
DELETE FROM webhook_events
 WHERE provider = 'stripe' AND event_id = 'evt_1ABC...'
 RETURNING event_id, event_type, status;

COMMIT;
```

然后**在 Stripe Dashboard 里 Resend 那个事件**。

**为什么删记录是安全的**：额度发放本身有独立幂等键
（`sub-activate:<subscription_id>`、`invoice:<invoice_id>`），
受 `uq_ledger_org_idempotency` 保护。因此即使重放，
**额度不会重复发放**。这是「双重保险」设计的价值所在。

#### 方式 B：Stripe CLI 重放

```bash
# 拉取指定事件并转发到本地/远端端点
stripe events resend evt_1ABC... --webhook-endpoint we_1XYZ...

# 或持续监听并转发（用于调试，不适合生产修复）
stripe listen --forward-to https://your-domain/webhooks/stripe
```

#### 方式 C：手工构造重放（最后手段，需要 webhook secret）

**只在前两种方式都不可用时使用。** 手工重放必须重新计算签名，
否则会被验签拒绝。

```bash
# 需要：事件 JSON 原文（从 webhook_events_replay_backup.payload 取，
#       但它已经被 _truncate_payload 裁剪过，不完整！）
```

> ⚠️ **`webhook_events.payload` 是裁剪过的**（`_truncate_payload()` 只保留
> `id` / `object` / `status` / `customer` / `subscription` / `amount_paid` /
> `amount_due` / `currency` / `created` / `current_period_start` /
> `current_period_end` / `cancel_at_period_end` / `client_reference_id` /
> `metadata` / `billing_reason`）。
> **因此不能拿它当重放的输入。** 重放必须从 Stripe 侧发起（方式 A / B）。
> 这也意味着：如果你在删记录前没有备份，排障证据就永久丢失了 ——
> 所以选择 2 里的备份步骤不可省略。

#### 手工等价修复（当重放不可行时）

如果 Stripe 侧的事件已经过期（Stripe 的事件保留期有限），
可以手工完成等价的业务效果。**必须用服务层函数，不要直接 UPDATE。**

```python
# 手工激活一个订阅（等价于 checkout.session.completed 的效果）
import asyncio
from reelagent_cloud.config import get_settings
from reelagent_cloud.db.session import session_scope
from reelagent_cloud.domain.enums import PlanTier
from reelagent_cloud.services import billing

async def main() -> None:
    settings = get_settings()
    async with session_scope() as session:
        sub = await billing._sync_subscription(
            session,
            settings,
            org_id="org_01M4Z76RW2WNQXHJE6Y49XNMNA",
            subscription_id="sub_1ABC...",          # ← 从 Stripe 复制
            tier=PlanTier.PRO,
        )
        print(f"synced: plan={sub.plan.value} status={sub.status.value}")

asyncio.run(main())
```

> ⚠️ 用的是带下划线的内部函数 `_sync_subscription`。
> 它**是幂等的**（额度发放用 `sub-activate:<sub_id>` 幂等键），
> 所以重复执行是安全的。但如果将来重构成公开 API，请改用公开函数。

**手工发放周期额度**（等价于 `invoice.paid` 的续费分支）：

```python
import asyncio
from reelagent_cloud.config import get_settings
from reelagent_cloud.db.session import session_scope
from reelagent_cloud.domain.enums import LedgerKind
from reelagent_cloud.domain.plans import get_plan
from reelagent_cloud.services import credits

ORG_ID = "org_01M4Z76RW2WNQXHJE6Y49XNMNA"
INVOICE_ID = "in_1ABC..."
TIER = "pro"

async def main() -> None:
    settings = get_settings()
    plan = get_plan(TIER)
    async with session_scope() as session:
        entry = await credits.grant(
            session,
            org_id=ORG_ID,
            amount=plan.monthly_credits,
            kind=LedgerKind.PLAN_GRANT,
            description=f"{plan.name} 周期额度（{INVOICE_ID}）",
            idempotency_key=f"invoice:{INVOICE_ID}",       # ← 与 webhook 处理器一致
            external_ref=INVOICE_ID,
        )
        print(f"granted {entry.amount}, balance_after={entry.balance_after}")

asyncio.run(main())
```

### 5.9 `status = 'processed'` 但业务没生效

处理器跑了但结果不对。逐个检查：

**订阅状态与组织套餐是否一致**：

```sql
SELECT o.id, o.name, o.slug, o.plan AS org_plan, o.watermark, o.is_suspended,
       o.credits_granted_this_period, o.period_started_at,
       s.plan AS sub_plan, s.status AS sub_status,
       s.stripe_customer_id, s.stripe_subscription_id, s.stripe_price_id,
       s.current_period_start, s.current_period_end, s.cancel_at_period_end
  FROM organizations o
  LEFT JOIN subscriptions s ON s.org_id = o.id
 WHERE o.id = '<org_id>';
```

| 不一致 | 原因 |
|---|---|
| `org_plan` 与 `sub_plan` 不同 | `_sync_subscription` 里两者在同一个函数更新，不应该不同。可能是手工改了其中一个 |
| `sub_status = past_due` 但 `org_plan` 还是付费档 | **这是预期行为**：`invoice.payment_failed` 只改订阅状态，不动 `org.plan`（给用户宽限期） |
| `cancel_at_period_end = true` 但还没降级 | **这也是预期行为**：周期性取消要到期末才会收到 `customer.subscription.deleted` |
| `watermark` 与套餐不匹配 | `_sync_subscription` 里 `org.watermark = plan.is_free`；取消订阅时 `_handle_subscription_deleted` 里也设 `watermark = True` |

**检查 Price ID 映射**：

```bash
# 配置里的三个 Price ID
curl -s http://127.0.0.1:8000/api/v1/system/status | jq '.config.billing' 2>/dev/null
# 或直接看配置
echo "starter=$REEL_BILLING__PRICE_STARTER"
echo "pro=$REEL_BILLING__PRICE_PRO"
echo "studio=$REEL_BILLING__PRICE_STUDIO"
```

`tier_for_price()` 依赖这三个配置做反向映射。
如果 Stripe 里的 Price ID 与配置不一致，
`customer.subscription.updated` 会 fallback 到 `metadata.tier`
（`PlanTier(metadata.get("tier") or PlanTier.STARTER.value)`），
可能映射到错误的档位：

```sql
-- 看订阅的实际 price_id 是否在配置的三个里面
SELECT stripe_price_id, count(*)
  FROM subscriptions
 WHERE stripe_price_id <> ''
 GROUP BY 1;
```

`price_id_for()` 在套餐未配置 Price 时会抛
`ValidationFailed(f"套餐 {tier.value} 尚未在 Stripe 配置价格，请联系支持")`，
用户可以自己看到这个错误。

### 5.10 预防

- **在 Stripe Dashboard 里把端点的事件订阅收紧到那 6 个**，
  减少 `ignored` 噪音。
- **监控 `webhook_events` 的 failed 计数**：

  ```sql
  SELECT count(*) FROM webhook_events
   WHERE provider='stripe' AND status='failed'
     AND created_at > now() - interval '1 hour';
  ```

  建议告警阈值 1（账务问题一个都不能放过）。
- **定期核对 `subscriptions` 与 Stripe 侧的一致性**：
  Stripe Dashboard 导出订阅列表，与数据库 `stripe_subscription_id` 比对。
  这个核对**当前没有自动化的工具**。
- **不要删 `webhook_events` 的历史记录**（它同时也是排障证据）。
  如果有清理需求，先归档到 `webhook_events_replay_backup` 之类的地方。

---

## 6. 数据库连接池打满

### 6.1 症状

- 请求大量超时 / 500。
- 日志里出现 `TimeoutError: QueuePool limit of size 10 overflow 20 reached`
  或 asyncpg 的 `too many clients already`。
- 页面加载极慢，但 CPU 不高。

### 6.2 池参数

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `REEL_DATABASE__POOL_SIZE` | 10 | 常驻连接数 |
| `REEL_DATABASE__MAX_OVERFLOW` | 20 | 峰值可临时增加的连接数 |
| `REEL_DATABASE__POOL_TIMEOUT` | 30 | 等待可用连接的秒数，超时抛异常 |
| `REEL_DATABASE__POOL_RECYCLE` | 1800 | 连接最大存活秒数（防中间件掐连接） |
| `REEL_DATABASE__POOL_PRE_PING` | true | 取连接前先 ping（防用到期连接） |
| `REEL_DATABASE__STATEMENT_TIMEOUT_MS` | 30000 | 单语句超时（PG 侧 `statement_timeout`），兜底防长查询 |

**单进程的连接上限 = `POOL_SIZE + MAX_OVERFLOW` = 30。**

**容量公式**：

```
(POOL_SIZE + MAX_OVERFLOW) × (API 进程数 + worker 进程数)
    < postgres max_connections − 保留给运维/迁移的连接数
```

> ⚠️ **这是最经典的踩坑**：把 worker 从 2 个扩到 8 个，
> 连接需求从 60 涨到 240，而 Postgres 的 `max_connections` 默认只有 100。
> **扩容 worker 前必须先算这个数。**

### 6.3 排查

**① 从 PG 侧看连接分布**

```sql
-- 按应用与状态分组
SELECT application_name,
       state,
       count(*)                                        AS conns,
       round(avg(extract(epoch FROM (now() - state_change)))::numeric, 1) AS avg_state_age_s,
       max(extract(epoch FROM (now() - state_change)))::int              AS max_state_age_s
  FROM pg_stat_activity
 WHERE datname = current_database()
 GROUP BY 1, 2
 ORDER BY conns DESC;
```

**② 找出长时间运行或 idle in transaction 的连接（最危险）**

```sql
SELECT pid,
       usename,
       application_name,
       client_addr,
       state,
       wait_event_type,
       wait_event,
       extract(epoch FROM (now() - query_start))::int      AS query_age_s,
       extract(epoch FROM (now() - state_change))::int     AS state_age_s,
       left(query, 200)                                     AS query
  FROM pg_stat_activity
 WHERE datname = current_database()
   AND (state = 'idle in transaction'
        OR (state = 'active' AND query_start < now() - interval '10 seconds'))
 ORDER BY state_age_s DESC;
```

**`idle in transaction` 状态是最需要警惕的**：
它持有着锁，占用着连接，却不做任何事。常见成因：

- 代码里 `async with session_scope()` 的缩进错了，
  把长耗时的操作（调用引擎、调供应商 HTTP）包进了事务里。
- 某个 `await` 卡住了，而它在一个未提交的事务内。

> **本项目的设计刻意避免了这一点**：`services/pipeline.py` 的 docstring
> 明确写着「一条成片要跑几分钟。绝不能把事务开着等它——连接池会被瞬间耗尽。
> 因此：进出各开一个短事务，中间长跑阶段完全不持有连接」。
> 如果出现 `idle in transaction` 持续几分钟，**首先怀疑有代码违反了这个约定**。

**③ 看等待情况（是不是被锁住了）**

```sql
-- 谁在等谁
SELECT w.pid                               AS waiting_pid,
       w.application_name                  AS waiting_app,
       l.pid                               AS blocking_pid,
       l.application_name                  AS blocking_app,
       l.state                             AS blocking_state,
       extract(epoch FROM (now() - l.state_change))::int AS blocking_age_s,
       left(l.query, 150)                  AS blocking_query,
       left(w.query, 150)                  AS waiting_query
  FROM pg_stat_activity w
  JOIN LATERAL unnest(pg_blocking_pids(w.pid)) AS bp(pid) ON true
  JOIN pg_stat_activity l ON l.pid = bp.pid
 WHERE w.datname = current_database();
```

如果 `blocking_pid` 是 `idle in transaction`，那就是它把别人堵死了。

**④ 看连接总数与上限**

```sql
SELECT (SELECT setting::int FROM pg_settings WHERE name = 'max_connections') AS max_conn,
       count(*)                                                              AS total_conn,
       count(*) FILTER (WHERE application_name = 'reelagent')                AS reelagent_conn,
       count(*) FILTER (WHERE state = 'idle in transaction')                 AS idle_in_tx
  FROM pg_stat_activity
 WHERE datname = current_database();
```

**⑤ 从应用侧看池的等待**

```bash
# 慢请求（超过 REEL_OBSERVABILITY__SLOW_REQUEST_MS，默认 3000ms）会被打上慢标记
kubectl logs -l app=reelagent-server --tail=1000 \
  | jq -r 'select(.slow == true) | "\(.duration_ms)ms \(.method) \(.path) \(.status)"' \
  | sort -rn | head -20
```

### 6.4 处置

#### 应急 1：终止僵死的后端连接

**先确认是僵死的**（`idle in transaction` 且 age 很大）。

```sql
-- ① 列出候选（age > 5 分钟的 idle in transaction）
SELECT pid,
       application_name,
       extract(epoch FROM (now() - state_change))::int AS age_s,
       left(query, 200) AS query
  FROM pg_stat_activity
 WHERE datname = current_database()
   AND state = 'idle in transaction'
   AND state_change < now() - interval '5 minutes'
 ORDER BY age_s DESC;

-- ② 逐个终止（用 pg_terminate_backend，不要用 pg_cancel_backend——
--    后者只取消当前查询，对 idle in transaction 无效）
SELECT pid,
       application_name,
       pg_terminate_backend(pid) AS terminated
  FROM pg_stat_activity
 WHERE datname = current_database()
   AND state = 'idle in transaction'
   AND state_change < now() - interval '5 minutes';
```

> ⚠️ **批量终止前务必看清 `query` 字段**。终止一个正在做
> `UPDATE ledger_entries` 或 `COMMIT` 的连接会造成事务回滚。
> 对额度相关的连接要格外小心——回滚是安全的（事务语义保证），
> 但会让用户的操作失败。

#### 应急 2：降低并发，释放连接

```bash
# 减少 worker 副本（每个副本都占一个池）
kubectl scale deployment/reelagent-worker --replicas=2

# 或降低单进程并发
kubectl set env deployment/reelagent-worker REEL_WORKER__CONCURRENCY=1
```

> 注意：`REEL_WORKER__CONCURRENCY` 控制**同时执行的 Job 数**。
> 每个 Job 在执行期间**不持有连接**（只有心跳的短事务会碰数据库），
> 因此降低它**对连接数的帮助有限**。
> 真正占连接的是「API 进程数 × 池大小」。
> **如果是 API 侧打满，要减 API 副本或调小池。**

#### 应急 3：调大池（**必须同时确认 PG 侧有余量**）

```bash
# ① 先确认 PG 还有多少余量
#    （用上面的 ④ 查询：max_conn - total_conn）
```

```bash
# ② 如果有余量，调大池
kubectl set env deployment/reelagent-server \
  REEL_DATABASE__POOL_SIZE=20 \
  REEL_DATABASE__MAX_OVERFLOW=30
```

```sql
-- ③ 如果 PG 侧也不够，先调整 PG（需要重启，属于计划内变更）
ALTER SYSTEM SET max_connections = 300;
-- 然后重启 PG
```

> ⚠️ 提高 `max_connections` 会按连接分配共享内存，
> 每个连接约 5-10MB。300 连接约需 1.5-3GB 的额外内存。
> 更合适的方向通常是**引入 PgBouncer** 做连接池复用
> （应用侧连 PgBouncer，PgBouncer 连 PG）。
> 注意 PgBouncer 的 transaction 模式与 `FOR UPDATE SKIP LOCKED` 兼容，
> 但 session 模式才支持 prepared statements——
> 这需要在接入时验证（当前项目**未使用 PgBouncer**）。

#### 应急 4：启用 `statement_timeout` 兜底

配置已经是默认开启的（`REEL_DATABASE__STATEMENT_TIMEOUT_MS=30000`）。
它通过连接的 `server_settings` 下发：

```python
connect_args={"server_settings": {
    "application_name": "reelagent",
    "statement_timeout": str(settings.statement_timeout_ms),
}}
```

因此任何卡住的语句最多 30 秒就被 PG 主动取消。
**如果这个兜底没有生效，检查 PG 侧是否允许客户端设置 `statement_timeout`**
（某些托管服务会覆盖它）。

### 6.5 根因排查方向

| 现象 | 可能根因 | 验证方式 |
|---|---|---|
| `idle in transaction` 长时间存在 | 长耗时操作被包在事务里（违反 pipeline 的设计约定） | 看 `query` 字段；对比代码里 `session_scope()` 的范围 |
| 连接数突然翻倍 | worker 或 API 被扩容了 | `kubectl get pods` 看副本数与启动时间 |
| 连接数缓慢上升但不降 | 连接泄漏（某个 `session` 没被关闭） | `POOL_RECYCLE=1800` 会回收旧连接，如果泄漏快于回收速度就会持续上升。检查代码里是否有 `factory()` 直接调用而没走 `session_scope()` |
| `/readyz` 探测过频 | 探针 `periodSeconds` 太小（默认建议 ≥10s） | `kubectl describe pod` 看探针配置 |
| 慢查询 | 全表扫描 | 用下面的查询找 |

**找出最慢的查询**（需要 `pg_stat_statements` 扩展）：

```sql
SELECT round(total_exec_time::numeric, 0)      AS total_ms,
       calls,
       round(mean_exec_time::numeric, 1)       AS mean_ms,
       round((100 * total_exec_time / sum(total_exec_time) OVER ())::numeric, 1) AS pct,
       left(query, 120)                        AS query
  FROM pg_stat_statements
 WHERE query NOT LIKE '%pg_stat_statements%'
 ORDER BY total_exec_time DESC
 LIMIT 20;
```

关注 `pct` 高的查询。如果是 `jobs` 表的领取查询慢，
检查 `ix_jobs_claim(status, priority, run_after)` 是否存在：

```sql
SELECT indexname, indexdef
  FROM pg_indexes
 WHERE tablename = 'jobs';
```

**必须存在的索引**（来自模型定义）：

| 索引 | 定义 |
|---|---|
| `ix_jobs_claim` | `(status, priority, run_after)` |
| `ix_jobs_lease` | `(status, lease_expires_at)` |
| `ix_jobs_org_created` | `(org_id, created_at)` |
| `ix_job_...` | `status` / `kind` / `priority` / `org_id` / `project_id` / `run_after` / `lease_expires_at` / `locked_by` 单列索引 |

如果索引缺失（例如用手写的 SQL 建表而没走 `create_all()` 或迁移），
查询计划会退化成顺序扫描：

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM jobs
 WHERE status IN ('pending', 'failed')
   AND run_after <= now()
   AND (locked_by IS NULL OR lease_expires_at < now())
 ORDER BY priority DESC, run_after ASC
 LIMIT 1;
-- 期望看到 Index Scan / Bitmap Index Scan using ix_jobs_claim
-- 如果看到 Seq Scan on jobs，就是索引问题
```

**补索引**（如果确实缺失）：

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_jobs_claim
  ON jobs (status, priority, run_after);
-- CONCURRENTLY 不阻塞写入，但耗时更长，且不能在事务块里执行
```

> ⚠️ **注意**：模型定义里的索引名是 SQLAlchemy 声明的名字。
> 生产环境应当通过迁移创建它们。
> 当前仓库**没有 Alembic 迁移目录**（见 [ARCHITECTURE.md](ARCHITECTURE.md) 第 10.1 节），
> 因此生产初始化 schema 时需要特别注意索引是否完整。

### 6.6 长期措施

- **接入 PgBouncer**（transaction 模式），把应用侧的连接需求与 PG 的连接数解耦。
  必须验证 `FOR UPDATE SKIP LOCKED` 在 transaction 模式下的行为。
- **把连接池容量纳入部署检查清单**：
  `POOL_SIZE + MAX_OVERFLOW` × 进程数 < PG 余量。
- **给 `reelagent_http_request_duration_seconds` 加告警**，
  在连接池打满导致用户可感知之前就发现。
- **定期检查 `idle in transaction`**，把它作为代码质量信号
  （每次出现都意味着有人违反了「不在事务里做长耗时操作」的约定）。

---

## 7. 磁盘被产物写满

### 7.1 症状

- 写入失败（存储探测在 `/readyz` 里变红）。
- ffmpeg / PIL 报磁盘空间不足。
- 数据库写入失败（PG 的数据目录也在同一块盘上时更严重）。

### 7.2 先搞清楚产物落在哪里

**两个完全不同位置的目录，用不同的环境变量控制：**

| 位置 | 环境变量 | 默认值 | 内容 | 生命周期 |
|---|---|---|---|---|
| **对象存储（正式产物）** | `REEL_STORAGE__LOCAL_DIR` | `<项目根>/workspace/storage` | `artifact.storage_key` 指向的文件，key 形如 `org/{org_id}/projects/{project_id}/{category}/{filename}` | **长期保留**，由 `Artifact.deleted_at` 软删（**软删不删磁盘文件**） |
| **引擎临时工作区** | `REEL_WORKSPACE_DIR` / `settings.engine_workspace` | `<项目根>/workspace/engine/<job_id>` | 引擎运行时的 `projects/<engine_project_id>/{keyframes,assets,clips,audio,final}` + `reelagent.db` | **任务结束后由 `cleanup_workspace()` 删除** |

> ⚠️ **注意别搞混**：
> - `REEL_WORKSPACE`（**引擎**读，扁平变量）是引擎自己的 workspace 根目录，
>   默认 `workspace`，引擎的 SQLite 在 `workspace/reelagent.db`。
> - `REEL_WORKSPACE_DIR`（**cloud** 读，顶层字段 `workspace_dir`）
>   是云层的 workspace，`engine_workspace` 属性 = `workspace_dir / "engine"`。
> 两者默认都指向项目根的 `workspace/`，因此实际上会混在一起。
> **生产部署应当把它们指到不同的卷，并且把临时工作区放在快速本地盘上。**

**如果 `REEL_STORAGE__BACKEND=s3`**，正式产物不在本地盘上，
只有临时工作区占本地空间——这种情况下磁盘问题通常不会发生。

### 7.3 定位

**① 找出占用大户（Linux）**

```bash
# 整体用量
df -h

# 按目录层层下钻
du -sh /var/lib/reelagent/workspace/* 2>/dev/null | sort -h
du -sh /var/lib/reelagent/workspace/storage/org/* | sort -h | tail -20
du -sh /var/lib/reelagent/workspace/engine/* | sort -h | tail -20

# 找出最大的单个文件
find /var/lib/reelagent/workspace -type f -size +50M -printf '%s\t%p\n' \
  | sort -rn | head -30 | awk '{printf "%.1f MB\t%s\n", $1/1048576, $2}'
```

**Windows / PowerShell 等价命令**：

```powershell
# 各子目录用量
Get-ChildItem $env:REEL_STORAGE__LOCAL_DIR -Directory |
  ForEach-Object {
    $size = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue |
             Measure-Object -Property Length -Sum).Sum
    [PSCustomObject]@{ Name = $_.Name; MB = [math]::Round($size / 1MB, 1) }
  } | Sort-Object MB -Descending

# 最大的单个文件
Get-ChildItem $env:REEL_STORAGE__LOCAL_DIR -Recurse -File |
  Sort-Object Length -Descending | Select-Object -First 30 FullName,
  @{N='MB';E={[math]::Round($_.Length / 1MB, 1)}}
```

**② 从数据库侧看哪个租户 / 项目最占空间**（比翻文件系统快得多）

```sql
-- 按组织汇总
SELECT a.org_id,
       o.name,
       o.plan,
       count(*)                                        AS files,
       pg_size_pretty(sum(a.size_bytes))                AS total,
       round(sum(a.size_bytes) / 1024.0 / 1024 / 1024, 2) AS gb
  FROM artifacts a
  LEFT JOIN organizations o ON o.id = a.org_id
 WHERE a.deleted_at IS NULL
 GROUP BY a.org_id, o.name, o.plan
 ORDER BY sum(a.size_bytes) DESC
 LIMIT 20;
```

```sql
-- 按项目汇总（找出最占空间的项目）
SELECT a.project_id,
       p.title,
       a.org_id,
       count(*)                         AS files,
       pg_size_pretty(sum(a.size_bytes)) AS total
  FROM artifacts a
  LEFT JOIN projects p ON p.id = a.project_id
 WHERE a.deleted_at IS NULL
 GROUP BY a.project_id, p.title, a.org_id
 ORDER BY sum(a.size_bytes) DESC
 LIMIT 20;
```

```sql
-- 按产物类型汇总（正常情况下视频与 GIF 占大头）
SELECT kind,
       count(*)                          AS files,
       pg_size_pretty(sum(a.size_bytes)) AS total,
       pg_size_pretty(avg(a.size_bytes)::bigint) AS avg_size
  FROM artifacts a
 WHERE deleted_at IS NULL
 GROUP BY kind
 ORDER BY sum(size_bytes) DESC;
```

**③ 找出「软删了但磁盘文件还在」的产物**

这是**最常见的原因**：项目删除只是软删（`projects.deleted_at`），
产物登记也可能被软删（`artifacts.deleted_at`），
但**没有任何代码会去删磁盘上的文件**。

```sql
-- 软删的产物登记（占着数据库记录，磁盘文件通常还在）
SELECT count(*)                       AS soft_deleted_rows,
       pg_size_pretty(sum(size_bytes)) AS potentially_reclaimable
  FROM artifacts
 WHERE deleted_at IS NOT NULL;

-- 已软删项目的所有产物
SELECT a.org_id, a.project_id, p.title, p.deleted_at,
       count(*) AS files,
       pg_size_pretty(sum(a.size_bytes)) AS total
  FROM artifacts a
  JOIN projects p ON p.id = a.project_id
 WHERE p.deleted_at IS NOT NULL
 GROUP BY a.org_id, a.project_id, p.title, p.deleted_at
 ORDER BY sum(a.size_bytes) DESC
 LIMIT 20;
```

**④ 找出过期的分享产物**

`Artifact.expires_at` 字段存在，但目前**没有清理逻辑使用它**：

```sql
SELECT count(*) AS expired_uncleaned,
       pg_size_pretty(sum(size_bytes)) AS total
  FROM artifacts
 WHERE expires_at IS NOT NULL AND expires_at < now() AND deleted_at IS NULL;
```

**⑤ 检查孤儿临时工作区**

任务结束后 `cleanup_workspace()` 会删 `engine_workspace / job_id`，
但如果 worker 被强杀（比如超了 `shutdown_grace_seconds` 被 SIGKILL、
或 OOM），清理不会执行，目录会留下来：

```bash
# 列出 engine 工作区及其大小与修改时间
ls -la --time-style=long-iso "$REEL_WORKSPACE_DIR/engine/" 2>/dev/null | head -50

# 找出超过 1 天没被修改的（很可能都是孤儿）
find "$REEL_WORKSPACE_DIR/engine" -maxdepth 1 -mindepth 1 -type d \
     -mtime +1 -printf '%TY-%Tm-%Td %p\n' | sort | head -50
```

**交叉验证：工作区对应的 job 是否已终态**

```sql
SELECT id, status, kind, started_at, finished_at, error_code
  FROM jobs
 WHERE id IN ('<job_id_1>', '<job_id_2>')   -- 从上面的目录名填
 ORDER BY started_at;
```

如果 `status` 是 `succeeded` / `failed` / `dead` / `cancelled`，
那这个目录就是**确定可以删的孤儿**。

### 7.4 处置

#### 应急 1：清理孤儿临时工作区（最安全，收益通常最大）

**先确认目录名对应的 job 已终态**（上面的交叉验证），再删。

```bash
# 干跑：列出将要删除的目录与总大小
find "$REEL_WORKSPACE_DIR/engine" -maxdepth 1 -mindepth 1 -type d -mtime +1 \
  -exec du -sh {} \; | sort -h | tail -30

# 确认无误后删除（-mtime +1 = 超过 1 天未修改）
find "$REEL_WORKSPACE_DIR/engine" -maxdepth 1 -mindepth 1 -type d -mtime +1 \
  -exec rm -rf {} +
```

> ⚠️ **`-mtime +1` 是必要的保护**。正在跑的任务的工作区会被持续写入，
> 修改时间很新。只删「超过 1 天没动过」的目录，
> 误删正在运行的任务的概率极低。
> 但如果某个任务真的卡了很久没写文件（例如供应商超时 180 秒 × 多次重试），
> 仍有可能被误删——**最严谨的做法是按照「对应 job 已终态」来白名单删除**。

更严谨的做法（只删终态 job 的目录）：

```bash
#!/usr/bin/env bash
# cleanup_orphan_workspaces.sh  ——  只删终态 job 的工作区
set -euo pipefail

WS="${REEL_WORKSPACE_DIR:?REEL_WORKSPACE_DIR 未设置}/engine"
PSQL="${PSQL:-psql -At -d \"$REEL_DATABASE__URL\"}"

total=0
for dir in "$WS"/*/; do
  job_id="$(basename "$dir")"

  # 跳过非 job 目录名（job id 形如 job_<26位 ULID>）
  [[ "$job_id" =~ ^job_[0-9A-HJKMNP-TV-Z]{26}$ ]] || { echo "跳过（非 job 目录）: $job_id"; continue; }

  status=$(psql -At -c "SELECT status FROM jobs WHERE id = '$job_id'" 2>/dev/null || echo "")

  case "$status" in
    succeeded|failed|dead|cancelled)
      size=$(du -sk "$dir" | cut -f1)
      total=$((total + size))
      echo "删除 $job_id (status=$status, ${size}KB)"
      rm -rf "$dir"
      ;;
    "")
      echo "跳过 $job_id：数据库里没有这个 job（可能是已归档的历史任务）"
      ;;
    *)
      echo "跳过 $job_id：status=$status（仍在进行中）"
      ;;
  esac
done

echo "共释放 $((total / 1024)) MB"
```

#### 应急 2：清理已软删项目对应的产物文件

**这一步需要谨慎**：软删的目的是「保留一段时间以便误删恢复」。
如果磁盘吃紧，可以按时间阈值清理「软删超过 N 天」的产物。

```sql
-- 先看清将要处理的范围（软删超过 30 天的项目与其产物）
SELECT p.id AS project_id, p.title, p.deleted_at,
       count(a.id)                        AS files,
       pg_size_pretty(sum(a.size_bytes))  AS total
  FROM projects p
  JOIN artifacts a ON a.project_id = p.id
 WHERE p.deleted_at IS NOT NULL
   AND p.deleted_at < now() - interval '30 days'
 GROUP BY p.id, p.title, p.deleted_at
 ORDER BY sum(a.size_bytes) DESC;
```

拿到 `storage_key` 列表：

```sql
SELECT a.storage_key, a.size_bytes
  FROM artifacts a
  JOIN projects p ON p.id = a.project_id
 WHERE p.deleted_at IS NOT NULL
   AND p.deleted_at < now() - interval '30 days'
 ORDER BY a.size_bytes DESC;
```

**如果 `REEL_STORAGE__BACKEND=local`**，`storage_key` 就是相对路径，
拼上 `REEL_STORAGE__LOCAL_DIR` 即可删除：

```bash
#!/usr/bin/env bash
# purge_soft_deleted_artifacts.sh  ——  删除软删超过 30 天的产物文件
# ⚠️ 运行前请先备份数据库，并确认上面的 SELECT 结果符合预期。
set -euo pipefail

STORE="${REEL_STORAGE__LOCAL_DIR:?未设置}"
DAYS=30

psql -At -d "$REEL_DATABASE__URL" -c "
  SELECT a.storage_key
    FROM artifacts a
    JOIN projects p ON p.id = a.project_id
   WHERE p.deleted_at IS NOT NULL
     AND p.deleted_at < now() - interval '${DAYS} days'
" | while read -r key; do
  [ -z "$key" ] && continue
  # 安全校验：key 必须匹配预期的前缀，防路径穿越
  case "$key" in
    org/*) ;;
    *) echo "跳过可疑 key: $key"; continue ;;
  esac
  target="$STORE/$key"
  if [ -f "$target" ]; then
    rm -f "$target"
    echo "已删 $target"
  fi
done

# 清理空目录
find "$STORE" -type d -empty -delete
```

**如果 `REEL_STORAGE__BACKEND=s3`**，用 `aws s3 rm` 或对应的 CLI：

```bash
psql -At -d "$REEL_DATABASE__URL" -c "
  SELECT a.storage_key FROM artifacts a
    JOIN projects p ON p.id = a.project_id
   WHERE p.deleted_at IS NOT NULL
     AND p.deleted_at < now() - interval '30 days'
" | while read -r key; do
  [ -n "$key" ] && aws s3 rm "s3://$REEL_STORAGE__S3_BUCKET/$key"
done
```

> ⚠️ **删除文件后，把数据库登记也标记清楚**。
> 否则 `/api/v1/artifacts/{id}/download` 会返回
> `NotFound("文件不存在或已被清理")`（因为 `_stream()` 里先 `exists()` 检查），
> 这是可接受的降级，但用户会看到 404。
> 建议同时更新 `artifacts.meta` 记录清理原因：

```sql
UPDATE artifacts
   SET meta = meta || jsonb_build_object(
         'purged_at', now()::text,
         'purge_reason', 'disk pressure: soft-deleted >30d')
 WHERE id IN (SELECT a.id FROM artifacts a
                JOIN projects p ON p.id = a.project_id
               WHERE p.deleted_at IS NOT NULL
                 AND p.deleted_at < now() - interval '30 days')
RETURNING id;
```

#### 应急 3：整租户清理（账号注销场景）

`services/storage.py::purge_org_storage()` 已实现但**没有调用方**。
可以手工调用：

```python
import asyncio
from reelagent_cloud.config import get_settings
from reelagent_cloud.services import storage as storage_svc

async def main() -> None:
    settings = get_settings()
    n = await storage_svc.purge_org_storage(settings, "org_01M4Z76RW2WNQXHJE6Y49XNMNA")
    print(f"removed {n} files")

asyncio.run(main())
```

> ⚠️ **只对 `local` 后端有效**。看实现：
> ```python
> root = storage.local_path(...) if isinstance(storage, LocalStorage) else None
> if root and root.exists():
>     ...
> return 0
> ```
> **S3 后端会直接返回 0，什么都不删。** 用 S3 时需要手工按前缀删除：
> ```bash
> aws s3 rm "s3://$BUCKET/org/$ORG_ID/" --recursive
> ```

#### 应急 4：紧急释放空间给数据库（最强硬）

如果 PG 数据目录和产物目录在同一块盘上，且已经影响到数据库写入：

```bash
# ① 立刻停止产生新产物
kubectl scale deployment/reelagent-worker --replicas=0

# ② 清理所有孤儿工作区（不区分时间，因为情况紧急）
#    ⚠️ 必须先确认没有 running 的任务！
```

```sql
-- ② 的前置检查：确认没有 running 任务
SELECT count(*) FROM jobs WHERE status = 'running';
-- 必须是 0。如果不是 0，先按第 1 节处理它们。
```

```bash
# ③ 清空临时工作区（此时已确认无 running 任务）
rm -rf "${REEL_WORKSPACE_DIR:?}/engine"/*
```

```bash
# ④ 清理旧日志
journalctl --vacuum-size=500M
# 或
find /var/log -name '*.log' -mtime +7 -delete
```

```bash
# ⑤ 恢复 worker
kubectl scale deployment/reelagent-worker --replicas=2
```

#### 应急 5：迁移到 S3（根治）

```bash
kubectl set env deployment/reelagent-server REEL_STORAGE__BACKEND=s3
kubectl set env deployment/reelagent-server REEL_STORAGE__S3_BUCKET=reelagent-artifacts
kubectl set env deployment/reelagent-server REEL_STORAGE__S3_REGION=cn-beijing
kubectl set env deployment/reelagent-server REEL_STORAGE__S3_ENDPOINT_URL=
kubectl set env deployment/reelagent-server REEL_STORAGE__S3_ACCESS_KEY_ID=...
kubectl set env deployment/reelagent-server REEL_STORAGE__S3_SECRET_ACCESS_KEY=...
# 可选：CDN / 公网前缀
kubectl set env deployment/reelagent-server REEL_STORAGE__S3_PUBLIC_BASE_URL=https://cdn.example.com
```

> ⚠️ **切换后端不会自动迁移已有产物**。
> 已有 `storage_key` 指向本地文件，但 `S3Storage` 会去 bucket 里找，
> 结果是「文件不存在」。因此切换前必须**先把本地对象同步到 bucket**，
> 且 key 结构必须完全一致（`org/{org_id}/...`）：
> ```bash
> aws s3 sync "$REEL_STORAGE__LOCAL_DIR" "s3://$BUCKET/" --exclude '*.tmp'
> ```
> 同步完成后**再**切换配置并滚动重启。

### 7.5 变更后的验证

```bash
# 探针必须全绿（storage 是 critical 检查，它会真实读写一次）
curl -s http://127.0.0.1:8000/readyz | jq '.checks.storage'

# 确认磁盘回来了
df -h
```

```sql
-- 确认软删产物登记与磁盘状态一致（抽样）
SELECT id, storage_key, size_bytes, deleted_at
  FROM artifacts
 WHERE deleted_at IS NOT NULL
 ORDER BY deleted_at DESC LIMIT 10;
```

### 7.6 长期措施

**必须补的能力（当前缺失）**：

1. **产物 GC 任务**。现在没有任何代码删除对象存储里的文件。
   `Artifact.expires_at` 字段存在但无人使用。
   需要实现一个定期任务：按 `expires_at` / `projects.deleted_at` /
   `artifacts.deleted_at` 清理对象与登记。
2. **孤儿对象清理**。`_collect_artifacts()` 是「先 `put_file` 再
   `add_all(rows)`」，中间失败会留下孤儿对象。
   需要定期比对存储内容与 `artifacts` 表，删掉无登记的对象。
3. **存储用量配额**。按套餐限制单组织可占用的存储空间
   （`StorageSettings.max_upload_mb = 32` 只限制单次上传大小，
   不限制总量）。
4. **磁盘与对象存储用量监控**：
   - 文件系统层面：`node_filesystem_avail_bytes` 告警。
   - 应用层面：定期跑 7.3 的按组织汇总 SQL，把结果作为指标暴露。

**运维习惯**：

- 把临时工作区（`engine_workspace`）放在**独立的、可随时清空的卷**上，
  不要与对象存储或 PG 数据目录共用一块盘。
- 对于批量生成的租户，评估是否需要把他们引导到 BYOK +
  更大的存储套餐。

---

## 8. 附录：应急预案速查表

### 8.1 「我要立刻停止出血」

| 目标 | 命令 |
|---|---|
| 停止所有生成（不再花钱） | `kubectl scale deployment/reelagent-worker --replicas=0` |
| 停止某个租户 | `UPDATE organizations SET is_suspended=true WHERE id='<org>'` + 吊销其 API Key |
| 停止所有新任务但保持 worker | 让 `REEL_PROVIDERS__VIDEO__API_KEY` 失效 → 生产环境会拒绝执行（`unsupported_operation`）。**副作用大，慎用** |
| 停止 Webhook 处理 | `REEL_BILLING__ENABLED=false`（接口会返回 `{"ignored":"billing disabled"}`） |
| 停止全部对外的 API | 在反向代理层返回 503，同时保留 `/livez`（否则编排会重启容器） |

### 8.2 「我要立刻恢复服务」

| 情况 | 命令 |
|---|---|
| worker 全挂 | `kubectl scale deployment/reelagent-worker --replicas=2` |
| 熔断误伤 | `kubectl rollout restart deployment/reelagent-worker deployment/reelagent-server`（断路器状态在进程内，重启即清零） |
| 连接池打满 | 见第 6.4 节；最快的是重启 API 进程释放泄漏连接 + 降并发 |
| 磁盘满 | 见第 7.4 节应急 1（清孤儿工作区），通常能立刻释放大量空间 |
| 数据库连接耗尽 | 终止 `idle in transaction` 的连接（见 6.4 应急 1） |

### 8.3 核心表速查

| 我想知道 | 查这张表 |
|---|---|
| 任务为什么失败 | `jobs.error` / `jobs.error_code` |
| 任务被谁占着、租约何时到期 | `jobs.locked_by` / `jobs.lease_expires_at` / `jobs.heartbeat_at` |
| 队列有多深 | `jobs.status` 分组计数 |
| 某个镜头为什么没出来 | `shots.status` / `shots.error` |
| 产物的存储位置与大小 | `artifacts.storage_key` / `artifacts.size_bytes` |
| 用户的钱去哪了 | `ledger_entries`（含 `balance_after`） |
| 余额是否正确 | `organizations.credits_balance` vs `SUM(ledger_entries.amount)` |
| 供应商调用成功率与延迟 | `usage_records.success` / `latency_ms` |
| 订阅状态 | `subscriptions` |
| Webhook 是否处理过 | `webhook_events.status` / `error` |
| 谁做了什么 | `audit_logs` |
| 邮件发出去了吗 | `email_outbox.status` / `last_error` |

### 8.4 关键环境变量速查

| 变量 | 默认 | 影响 |
|---|---|---|
| `REEL_ENV` | `dev` | `prod` 会启用严格的启动校验 |
| `REEL_WORKER__CONCURRENCY` | 2 | 单进程同时执行的 Job 数 |
| `REEL_WORKER__SHOT_CONCURRENCY` | 3 | 单 Job 内并发镜头数 |
| `REEL_WORKER__LEASE_SECONDS` | 900 | 租约时长 |
| `REEL_WORKER__HEARTBEAT_SECONDS` | 20 | 心跳间隔 |
| `REEL_WORKER__SHUTDOWN_GRACE_SECONDS` | 60 | 优雅停机宽限期 |
| `REEL_WORKER__MAX_ATTEMPTS` | 3 | 任务最大重试次数 |
| `REEL_DATABASE__POOL_SIZE` | 10 | 连接池常驻连接 |
| `REEL_DATABASE__MAX_OVERFLOW` | 20 | 连接池峰值溢出 |
| `REEL_DATABASE__STATEMENT_TIMEOUT_MS` | 30000 | 单语句超时 |
| `REEL_PROVIDERS__<KIND>__TIMEOUT_SECONDS` | 180 | 供应商调用超时 |
| `REEL_PROVIDERS__<KIND>__MAX_CONCURRENCY` | 4 | 单供应商并发上限 |
| `REEL_PROVIDERS__<KIND>__CIRCUIT_BREAKER_THRESHOLD` | 5 | 熔断阈值 |
| `REEL_PROVIDERS__<KIND>__CIRCUIT_BREAKER_RESET_SECONDS` | 60 | 熔断冷却时长 |
| `REEL_RATE_LIMIT__ENABLED` | true | 是否启用限流 |
| `REEL_BILLING__ENABLED` | false | 是否启用 Stripe |
| `REEL_OBSERVABILITY__METRICS_ENABLED` | true | 是否暴露 `/metrics` |
| `REEL_OBSERVABILITY__SLOW_REQUEST_MS` | 3000 | 慢请求阈值 |
| `REEL_STORAGE__BACKEND` | `local` | `local` 或 `s3` |
| `REEL_STORAGE__LOCAL_DIR` | `<root>/workspace/storage` | 本地产物根目录 |

### 8.5 日志关键字速查

| 关键字 | 含义 |
|---|---|
| `app_starting` / `app_ready` / `app_stopping` / `app_stopped` | API 进程生命周期 |
| `worker_starting` / `worker_stopping` / `worker_stopped` | worker 生命周期 |
| `worker_draining` / `worker_force_cancel` | 优雅停机与强制退出 |
| `job_claimed` | 任务被领取 |
| `job_enqueued` / `job_enqueue_idempotent` | 任务入队 / 幂等命中 |
| `job_succeeded` / `job_failed` / `job_dead` | 任务终态 |
| `job_retry_scheduled` | 失败后安排了重试（含退避秒数） |
| `job_lease_reaped` | 回收了过期租约任务 |
| `job_heartbeat_lost` | 心跳失败（被接管或收到取消） |
| `job_cancelled` / `job_cancel_requested` | 取消 |
| `job_settled` | 结算完成（含 reserved/actual/delta） |
| `engine_mock_mode` | 非生产环境退到 mock |
| `engine_stage_failed` | 引擎阶段失败 |
| `artifacts_collected` | 产物归集完成 |
| `ledger_applied` | 额度变动（含 kind/amount/balance_after） |
| `ledger_idempotent_hit` | 账本幂等命中 |
| `stripe_webhook_received` / `_processed` / `_failed` / `_verify_failed` / `_disabled` | Webhook 全链路 |
| `webhook_duplicate` / `webhook_ignored` | 重复投递 / 未识别事件 |
| `subscription_synced` / `subscription_canceled` / `subscription_past_due` | 订阅状态变化 |
| `plan_credits_granted` | 周期额度发放 |
| `cross_tenant_file_access_blocked` | **越权访问尝试**（安全信号） |
| `console_csrf_failed` | 控制台 CSRF 校验失败 |
| `rate_limited` | 触发限流（含 scope 与限额） |
| `auth_unexpected_error` | 认证环节内部错误 |
| `session_rejected` | 会话被拒绝（含原因） |
| `password_rehashed` | Argon2 参数升级导致的重哈希 |
| `account_locked` | 登录失败次数过多导致锁定 |
| `s3_presign_failed` / `workspace_cleanup_failed` / `reap_failed` | 非致命失败 |

---

## 相关文档

- [架构说明](ARCHITECTURE.md) —— 理解机制再看 Runbook 会快很多
- [部署指南](DEPLOYMENT.md) —— 环境变量完整清单、备份恢复、升级回滚
- [REST API 参考](API.md) —— 排障时调用接口的字段说明
- [架构决策记录](adr/) —— 为什么这样设计
