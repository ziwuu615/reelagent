# ADR 0001：任务队列使用 Postgres `FOR UPDATE SKIP LOCKED` 而非 Celery

- **状态**：已接受
- **日期**：1.0.0 发布前
- **决策者**：后端 / 基础设施
- **相关文件**：`reelagent_cloud/services/jobs.py`、`reelagent_cloud/db/models.py`（`Job` 类）、`reelagent_cloud/workers/runner.py`

---

## 背景

ReelAgent 的每一次生成都是**长任务**：跑一条成片需要几分钟到十几分钟，
期间要调用 LLM、图像、视频、TTS 四类供应商，成本可达数元人民币。
这类任务对队列有五个硬性要求：

1. **与业务写入同事务**。用户在页面上点「开始生成」时，同一个事务里要完成：
   校验套餐限制、预扣额度、建项目、入队任务。如果队列在外部 broker 里，
   「业务提交了但入队失败」或「入队了但业务回滚」都需要额外的
   transactional outbox 模式来兜底——这是额外的一整套机制。
2. **崩溃后可自动恢复**。worker 可能被 OOM Killer 杀掉、被滚动更新中止、
   被宿主机故障吞掉。恢复过程不能依赖人工介入。
3. **幂等**。客户端网络超时后会重试，同一次点击不能创建两个任务、扣两次钱。
4. **支持延迟执行**。失败重试需要退避（30s → 600s）。
5. **自托管友好**。产品定位是「可一键部署的生产级系统」，
   每多一个外部组件就多一份部署与运维负担。

此外还有一个业务约束：**吞吐量并不高**。一条成片跑几分钟，
目标规模下同时在线任务数在几十到几百量级，而不是每秒上千。

## 决策

**用业务数据库自己的表作为任务队列**，具体做法：

- 任务存在 `jobs` 表里，与 `organizations` / `projects` / `ledger_entries` 同库同事务。
- 领取用一条带 `FOR UPDATE SKIP LOCKED` 的 `SELECT ... LIMIT 1`：

  ```python
  stmt = (
      select(Job)
      .where(
          Job.status.in_([JobStatus.PENDING, JobStatus.FAILED]),
          Job.run_after <= now,
          or_(Job.locked_by.is_(None), Job.lease_expires_at < now),
      )
      .order_by(Job.priority.desc(), Job.run_after.asc())
      .limit(1)
      .with_for_update(skip_locked=True)
  )
  ```

- 配套三个可靠性机制：
  - **租约（lease）**：领取时写 `lease_expires_at = now + lease_seconds`（默认 900s），
    worker 每 `heartbeat_seconds`（默认 20s）续约。租约过期即可被重新领取——
    **不需要额外的故障检测服务**。
  - **回收（reap）**：worker 每 120 秒执行一次 `reap_expired_leases()`，
    把过期租约的 `RUNNING` 任务打回 `FAILED`（`error_code='lease_expired'`）。
  - **幂等键**：`uq_job_org_idempotency(org_id, idempotency_key)`。
- 性能靠一个专门的复合索引保证：
  `ix_jobs_claim(status, priority, run_after)`。
- 供应商配置、并发度等参数由 `WorkerSettings` 暴露。

## 后果

### 正面

- **事务一致性天然成立**。预扣额度、建项目、入队任务在 `session_scope()` 的
  同一个事务里提交或回滚，不存在「扣了钱没建项目」这类脏状态。
- **少一个组件**。自托管部署只需要 Postgres（+ 可选 Redis 用于限流），
  不需要 Redis/RabbitMQ 常驻做 broker。
- **`SKIP LOCKED` 天然并发**。多个 worker 无锁竞争，拿不到锁的直接跳过这条
  去抢下一条，而不是排队等待；加 worker 就是加副本，不需要重新分片。
- **运维可见性极好**。队列深度就是一条 `GROUP BY status`，
  排障就是查 `jobs` 表的 `locked_by` / `heartbeat_at` / `lease_expires_at`。
  不需要去 broker 的管理界面里找消息。
- **优先级调度免费得到**。`ORDER BY priority DESC, run_after ASC`，
  套餐的优先级（免费 0 / 创作者 1 / 专业 2 / 工作室 3）与单镜重跑（5）
  直接是字段值。

### 负面 / 代价

- **吞吐受数据库限制**。所有 worker 的领取操作都打到同一个数据库，
  Postgres 的行锁与 `SKIP LOCKED` 扫描在高并发下会成为瓶颈。
  代码注释里明确写了这个代价：「若将来要到每秒上千任务，再换成专用 broker 即可
  （接口已抽象在上述函数里）。」
- **长任务期间仍要持有租约**，因此数据库不可用会直接影响任务续约，
  进而导致租约过期、任务被重跑、供应商成本被重复支付。
  （这个风险由「任务执行期间不持有数据库连接」的设计部分缓解：
  只有心跳的短事务会碰到数据库。）
- **SQLite 方言会忽略 `FOR UPDATE`**。本地开发与测试用 SQLite 时，
  `SKIP LOCKED` 不起作用。代码注释说明这在「本地单 worker 场景仍然正确」，
  但多 worker + SQLite 不是受支持的组合。
- **`jobs` 表会随业务增长**，需要定期归档终态任务（当前**没有**自动归档策略）。

### 需要长期注意

- 队列与业务表共用连接池。`pool_size × (API 进程数 + worker 进程数)`
  必须小于 Postgres 的 `max_connections`。
- 领取逻辑的原子性依赖 `with_for_update(skip_locked=True)` 与
  `ORDER BY ... LIMIT 1` 的组合；改写这条语句时要格外小心。

## 备选方案

### 方案 A：Celery + Redis/RabbitMQ

- **优点**：成熟生态、可视化工具（Flower）、任务路由与限流开箱即用、
  水平扩展路径清晰。
- **否决理由**：
  1. 需要 transactional outbox 才能保证「业务提交 ⇔ 任务入队」的原子性，
     这是额外的表、额外的中继进程、额外的一致性风险。
  2. 多一个必须高可用的有状态组件（broker），自托管部署成本上升。
  3. Celery 的可靠性语义（`acks_late`、visibility timeout、worker 丢失任务）
     需要仔细配置，配错的后果比自建租约更隐蔽。
  4. 本项目的吞吐量用不上 Celery 的分布式能力。

### 方案 B：RQ（Redis Queue）

- 比 Celery 简单，但同样引入 Redis 作为必需组件，且同样缺少与业务事务的原子性。
- 否决理由同 A 的第 1、2 条。

### 方案 C：Kafka / NATS JetStream

- **优点**：真正的流式平台，天然支持高吞吐、事件溯源、多消费者组。
- **否决理由**：运维复杂度与学习成本远超需求。用消息队列做「几分钟一条」的
  任务调度是明显的过度设计。

### 方案 D：云厂商托管队列（SQS / Cloud Tasks）

- **优点**：零运维，天然与业务数据库解耦。
- **否决理由**：把产品锁死在某个云上，与「可一键部署、自托管友好」的定位冲突。
  同时仍然缺少与业务事务的原子性。

### 方案 E：数据库队列但不加租约（只用 `status` 字段）

- 简单，但 worker 崩溃后任务会永久卡在 `running`，需要人工 UPDATE。
- 否决理由：这是**必然会发生**的运维事故（滚动更新、OOM、宿主机故障），
  把它设计成需要人工介入是不可接受的。租约是这个方案能成立的前提。

---

## 参考

- 运维处置：[OPERATIONS.md](../OPERATIONS.md)（「任务卡在 running 不动」「队列积压」两节）
- 架构细节：[ARCHITECTURE.md](../ARCHITECTURE.md) 第 6 节
- 代码注释：`reelagent_cloud/services/jobs.py` 模块 docstring、
  `reelagent_cloud/db/models.py::Job` docstring
