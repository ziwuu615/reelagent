# ADR 0003：额度采用「只追加账本 + 余额快照」双写

- **状态**：已接受
- **日期**：1.0.0 发布前
- **决策者**：后端 / 计费
- **相关文件**：`reelagent_cloud/services/credits.py`、`reelagent_cloud/db/models.py`（`Organization`、`LedgerEntry`）、`reelagent_cloud/services/jobs.py`、`reelagent_cloud/services/billing.py`

---

## 背景

额度（对外称「点数」）是这个产品里**唯一直接对应钱的东西**。
它的实现要同时满足六个要求：

1. **可审计**。用户问「我的 5000 点怎么用掉的」，必须能逐条回答，
   而不是只给一个余额数字。
2. **可对账**。某个时刻余额与历史记录必须能互相验证，
   否则 bug 会以「余额莫名少了几百点」的形式沉默地积累。
3. **并发安全**。同一组织的多个任务可能同时结算，
   不能出现丢失更新（lost update）。
4. **幂等**。Stripe 会重复投递 webhook；用户会重复点击生成按钮；
   任务失败后重试会重新结算。这些都不能重复扣费或重复发放。
5. **余额守卫**。不能出现负余额（用户花超）。
6. **可人工干预**。客服需要能给用户补额度，而且这个动作本身也要留痕。

此外，余额的读取极其频繁：每次 `GET /api/v1/me`、每个页面渲染、
每次创建项目前的额度预检都要读它。如果每次都去做一次
`SUM(ledger_entries.amount)`，随着账本增长会越来越慢。

## 决策

**双写：一个只追加的事实账本 + 一个可快速读取的余额快照。**

### 事实侧：`ledger_entries`（只追加）

- 任何额度变动都必须写入一条，正数入账、负数出账。
- 每条记录带 **`balance_after`**（变动后的余额快照），
  因此任意历史时点的余额都能单条读出，无需回溯求和。
- 带 `kind`（7 种）、`description`、`project_id` / `job_id`（关联，**不建外键**）、
  `external_ref`（Stripe 单号）、`idempotency_key`、`created_by`、`meta`。
- `uq_ledger_org_idempotency(org_id, idempotency_key)` 保证同一外部单号只入账一次。
- **没有 UPDATE、没有 DELETE**。修正错误的方式是写一条反向的
  `ADJUSTMENT` 条目，而不是改历史。

### 快照侧：`organizations.credits_balance`

- `BigInteger`，带 CHECK 约束 `ck_org_credits_non_negative`（`credits_balance >= 0`）。
- 存在的唯一理由是**避免每次请求都做聚合**（`db/models.py` 的注释原话：
  「真值以 ledger_entries 为准，这里是为了避免每次请求都做聚合」）。

### 两者在同一事务内更新，写入路径唯一

所有变动汇聚到一个私有函数 `_apply()`：

```python
async def _apply(session, *, org_id, kind, amount, ...):
    # 1. 幂等短路
    existing = await _find_idempotent(session, org_id, idempotency_key)
    if existing is not None:
        return existing

    # 2. 行级排他锁：并发下不丢更新
    org = await _lock_org(session, org_id)        # SELECT ... FOR UPDATE
    new_balance = int(org.credits_balance) + int(amount)

    # 3. 余额守卫
    if new_balance < 0 and not allow_negative:
        raise PaymentRequired(..., required=abs(amount), available=int(org.credits_balance))

    # 4. 写账本 + 更新快照（同一事务）
    session.add(LedgerEntry(..., balance_after=new_balance))
    org.credits_balance = new_balance

    # 5. 显式 flush（会话是 autoflush=False）
    await session.flush()
    return entry
```

对外只暴露语义化接口：`grant()` / `consume()` / `refund()` / `adjust()`，
全部走 `_apply()`。

### 对账是一等公民

```python
async def reconcile(session, org_id) -> dict:
    ledger_sum = SELECT COALESCE(SUM(amount), 0) FROM ledger_entries WHERE org_id = ?
    balance    = SELECT credits_balance FROM organizations WHERE id = ?
    return {
        "ledger_sum": ledger_sum,
        "balance": balance,
        "consistent": ledger_sum == balance,
        "drift": balance - ledger_sum,
    }
```

控制台页面 `/app/usage` 直接渲染这个结果。`drift != 0` 即存在 bug，
应当立即告警。

## 后果

### 正面

- **审计体验完整**。`GET /api/v1/credits` 返回带 `balance_after` 的流水，
  用户能自己看懂「第 3 笔扣了 420 点，扣完剩 1580 点」。
- **对账是自动的、可反复执行的断言**。不需要人工比对，
  一条 SQL 等式就能证明账本与余额自洽。
- **金额 bug 会立刻暴露**。任何绕过 `_apply()` 的直接 UPDATE 都会让
  `reconcile()` 报 `drift != 0`——这让「静默的金额错误」变成
  「可检测的告警」。
- **人工干预可审计**。`adjust()` 强制写 `description = f"人工调整：{reason}"`
  并记 `created_by`，因此客服补偿也能追溯。
- **余额读取是 O(1)**。几十万条账本不影响页面渲染速度。

### 负面 / 代价

- **两处必须同步更新，存在不一致的可能**。缓解手段是「写入路径唯一 +
  对账兜底」，但代码里只要出现一次绕过 `_apply()` 的直接赋值就会破坏等式。
  （代码中确实存在这样的位置：`services/billing.py` 里
  `org.credits_granted_this_period = plan.monthly_credits` 与
  `org.period_started_at = utcnow()` 是对快照字段的直接赋值，
  但它们不是 `credits_balance`，因此不影响等式。）
- **`autoflush=False` 是一个真实的坑**。会话配置为不自动 flush，
  因此 `_apply()` 末尾那句 `await session.flush()` 是**必需的**。
  漏掉它会导致同一会话内后续的 `get_balance()` 读到数据库旧值
  （账本已是新值、余额还是旧值）。代码里有明确注释说明这一点，
  因为这是个「不看注释就会踩」的设计。
- **行级锁带来串行化**。同一组织的所有额度变动串行执行。
  对于「几分钟一条成片」的业务量完全够用，但如果某天要支持
  高频小额扣费（例如按 token 计费），这个锁会成为热点。
- **账本无限增长**。当前**没有**归档或分区策略。
  `ix_ledger_org_created(org_id, created_at)` 让按组织的分页查询保持高效，
  但全表规模仍会持续上升。
- **`LedgerKind.EXPIRE` 是空定义**。枚举里有这个值，
  但代码里没有任何地方产生它；且 `sign` 属性把除 `CONSUME` 之外的所有类型
  都算作正号，因此即便产生也会被记成入账。若将来要做额度过期，
  必须先修正 `sign` 语义或显式传负 `amount`。

### 需要长期注意

- **禁止 `UPDATE organizations SET credits_balance = ...`**。
  修正必须走 `adjust()`。运维文档里对此有明确警告。
- **新增额度变动场景时必须同时给出幂等键的取值约定**，
  否则重试会造成重复扣费。

## 备选方案

### 方案 A：只存余额（一个 `credits_balance` 字段）

- **优点**：最简单，一次读写。
- **否决理由**：**完全无法审计**。用户投诉「我的点数少了」时无据可查，
  也无法定位是哪个 bug 扣的。「额度直接对应钱」这个属性决定了
  可审计性不是可选项。否决。

### 方案 B：只存账本，余额用视图 / 实时聚合

- **优点**：没有任何不一致的可能，单一事实来源。
- **否决理由**：每次读余额都要 `SUM(amount)`。账本到百万行时，
  即使有 `ix_ledger_org_created` 索引，这个聚合也会变慢，
  而余额的读取频率远高于写入（每次页面渲染、每次预检）。
  另外，`credits_balance >= 0` 这个数据库级 CHECK 约束在这种方案下
  无法实现（CHECK 不能跨表）。**这个约束本身就是一个决策理由**：
  它是余额不会变负的最后一道防线。

### 方案 C：账本 + 定期物化余额（异步对账任务）

- 介于 A 与 B 之间：余额由一个后台任务定期重算。
- **否决理由**：读取会读到陈旧值（用户充值后额度到账有延迟），
  对一个「付钱就要立刻能用」的产品是不可接受的体验。
  而且这个方案仍然需要 `credits_balance` 字段来做预检，
  等于回到了双写，只是把一致性保证从「同事务」降级为「最终一致」。

### 方案 D：用 `SELECT SUM(amount) FOR UPDATE` 会计数器表

- 保留一个 `org_balances(org_id, total)` 表，用 `UPDATE ... SET total = total + ?`
  的原子自增代替「读-改-写」。
- **优点**：省掉 `SELECT ... FOR UPDATE`，并发性能更好。
- **否决理由**：`balance_after` 就无法可靠地写出（并发下拿不到
  「本次变动后的余额」这个精确值），而这个字段是审计体验的核心。
  另外 `UPDATE ... SET total = total + ?` 配合 CHECK 约束时，
  约束失败的错误信息不如显式守卫友好。
  **注意**：如果将来额度变动频率真的成为瓶颈，这是首选的重构方向，
  代价是放弃 `balance_after` 的精确性（可改为记录 `amount` 与时间戳，
  由对账任务补 `balance_after`）。

### 方案 E：把额度交给 Stripe 的 meter / credit grant 管理

- **优点**：不用自己实现账本与对账。
- **否决理由**：Stripe 的计费模型面向「按订阅发放、按用量计费」，
  与本产品的「预扣 → 结算 → 多退少补」两段式不完全契合；
  且强绑定单一支付服务商，与「未配置 Stripe 时降级为纯额度模式」
  的部署定位冲突（`billing.enabled=false` 时产品必须仍能正常扣额度）。

---

## 参考

- 对账与修复流程：[OPERATIONS.md](../OPERATIONS.md)（「额度对不上账」一节）
- 双记账设计细节：[ARCHITECTURE.md](../ARCHITECTURE.md) 第 7 节
- 代码：`reelagent_cloud/services/credits.py`
