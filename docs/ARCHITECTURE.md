# ReelAgent 架构说明

> 版本：1.0.0 ｜ 适用范围：`reelagent`（引擎）与 `reelagent_cloud`（云端产品层）
>
> 本文描述的是**代码里真实存在的结构**。凡是尚未实现的部分，都会以
> 「⚠️ 当前状态」明确标注，不做乐观陈述。

---

## 目录

- [1. 分层总览](#1-分层总览)
- [2. 为什么引擎与云层严格分离](#2-为什么引擎与云层严格分离)
- [3. 引擎内部结构](#3-引擎内部结构)
- [4. 云层内部结构](#4-云层内部结构)
- [5. 数据模型（14 张表）](#5-数据模型14-张表)
- [6. 任务生命周期与时序](#6-任务生命周期与时序)
- [7. 额度账本的双记账设计](#7-额度账本的双记账设计)
- [8. 跨镜头一致性方案](#8-跨镜头一致性方案)
- [9. 可观测性设计](#9-可观测性设计)
- [10. 已知缺口与后续演进](#10-已知缺口与后续演进)

---

## 1. 分层总览

```
┌──────────────────────────────────────────────────────────────────────────┐
│  浏览器 / 第三方集成                                                       │
│    · HTML 表单页面（/app/*、/login、/pricing …）                           │
│    · REST API（/api/v1/*，Bearer API Key）                                │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ HTTPS
┌───────────────────────────────▼──────────────────────────────────────────┐
│  Web / API 层   reelagent_cloud/api/                                      │
│    app.py        应用工厂 + lifespan（配置 → 引擎 → 迁移检查 → 中间件）      │
│    middleware.py RequestContext（request_id/耗时/指标）+ SecurityHeaders    │
│    errors.py     领域异常 → 统一 JSON 错误体                                │
│    ratelimit.py  Redis 固定窗口 / 进程内滑动窗口（可降级、fail-open）        │
│    routes/       pages · auth · console · api · files · billing · system   │
│    security/     tokens（会话/API Key/CSRF/BYOK）· deps（Principal 与授权）  │
│    services/     用例层：accounts · projects · jobs · credits · billing …  │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────────────┐
│  持久化层   PostgreSQL（生产）/ SQLite（本地与测试）                        │
│    · 业务表（organizations/projects/jobs/artifacts/ledger_entries …）      │
│    · 任务队列：jobs 表 + FOR UPDATE SKIP LOCKED                            │
│    · 对象存储：local 文件系统 / S3 兼容（OSS / R2 / MinIO）                 │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ jobs 表（领取 / 心跳 / 结算）
┌───────────────────────────────▼──────────────────────────────────────────┐
│  Worker 层   reelagent_cloud/workers/runner.py                            │
│    · 并发领取（--concurrency）· 租约续约 · 优雅停机 · 过期租约回收          │
│    · 邮件发件箱投递                                                        │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ services/pipeline.py（引擎桥接）
┌───────────────────────────────▼──────────────────────────────────────────┐
│  引擎层   reelagent/   —— 纯计算，零 Web 依赖                              │
│    五阶段状态机  plan → assets → generate → evaluate → render             │
│    四个 Agent    Planner · ArtDirector · Director · Evaluator             │
│    Harness       Store（SQLite checkpoint）· Tracer · ToolRegistry         │
│                  ContextWindow · run_with_retry                           │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ Provider 抽象
┌───────────────────────────────▼──────────────────────────────────────────┐
│  供应商   LLM（OpenAI 兼容）· 图像（ark / mock）· 视频（ark / mock）        │
│           TTS（dashscope / mock）· ffmpeg（可选，缺失则降级 GIF）           │
└──────────────────────────────────────────────────────────────────────────┘
```

**依赖方向是单向的**：`api/` → `services/` → 引擎；引擎从不反向依赖云层。
唯一的跨界契约是 `reelagent_cloud/services/pipeline.py` 里对引擎的调用，
以及 `reelagent/pipeline/runner.py::run()` 暴露的两个宿主回调
（`on_stage` 上报进度、`should_cancel` 响应取消）。

---

## 2. 为什么引擎与云层严格分离

| 维度 | 引擎 `reelagent/` | 云层 `reelagent_cloud/` |
|---|---|---|
| 依赖 | 仅 `numpy` + `Pillow`（+ 标准库） | FastAPI / SQLAlchemy / Stripe / Redis / Argon2 … |
| 状态 | 单文件 SQLite（`workspace/reelagent.db`） | PostgreSQL（生产） |
| 多租户 | 完全不感知 | 除 `users` 外所有表带 `org_id` |
| 身份 | 无 | 匿名 / 会话 / API Key 三种 Principal |
| 入口 | `python -m reelagent`（CLI） | `reelagent-server` / `reelagent-worker` |

这样切分带来三个实际好处：

1. **引擎可以被单机使用，也可以被服务化。** 引擎不知道数据库、队列与 HTTP
   的存在（[`runner.py`](../reelagent/pipeline/runner.py) 的 docstring 明确写了这一点），
   因此同一个引擎既能跑 `python -m reelagent demo`，也能被 worker 在
   线程池里驱动。切换宿主不需要改 Agent 代码。
2. **引擎的测试不需要数据库、不需要网络、不需要 API Key。** `tests/` 下的
   48 个引擎用例可以在任何机器上 clone 即跑
   （另有 164 个云层用例用 pytest 驱动真实 FastAPI 应用，需要 `[dev]` 依赖）。
3. **失败域隔离。** 云层的计费、鉴权、限流 bug 不会污染生成逻辑；
   引擎的算法改动也不会波及账务。

代价是云层需要一层**桥接代码**来补偿两边的不匹配（见第 4 节）。

---

## 3. 引擎内部结构

### 3.1 五阶段状态机

`reelagent/pipeline/runner.py`：

```python
STAGES = ("plan", "assets", "generate", "evaluate", "render")
```

阶段的完成状态写入 SQLite（`Store`），`run()` 在执行每个阶段前先判断
`store.stage_is_done(project_id, name)`，已完成则跳过并记录 `StageOutcome(status="skipped")`。
阶段失败时项目状态落为 `PROJECT_FAILED` 并中止后续阶段；`resume` 时从第一个
未完成阶段继续。

### 3.2 四个 Agent

| Agent | 文件 | 输入 → 输出 | 关键设计 |
|---|---|---|---|
| Planner | `agents/planner.py` | 种子文本 → `Script`（场景 / 镜头 / 台词 / 旁白 / 线索） | prompt 里把创作约束写成硬规则：镜头 2~5 秒、景别交替、台词与旁白不同镜、强制登记需重复出现的线索 |
| ArtDirector | `agents/artdirector.py` | `list[Clue]` → `appearance` 文本 + `base_image` | `appearance` 只描述**不随镜头变化的特征**；基准图生成优先级 `character > scene > prop`（`_KIND_PRIORITY`），受 `max_base_images` 限制 |
| Director | `agents/director.py` | 每个 `Shot` → 关键帧 + 片段 + 配音 | 线程池并发（`max_concurrency`）、难度路由、参考图注入、重试降级、每步落 checkpoint |
| Evaluator | `agents/evaluator.py` | 成片 → 确定性指标 + LLM Judge + 门禁 | 双轨评测，门禁阈值写在 `_gate()` 里 |

### 3.3 Harness（运行时底座）

`reelagent/harness/`：

| 模块 | 职责 |
|---|---|
| `state.py` | `Store`：SQLite checkpoint。`projects` / `stages` / `shots` / `spans` 等表；线程安全（并发写同一库） |
| `tracing.py` | `Tracer` / `Span`：span 级建模。`with tracer.span(...)` 在退出或显式 `close()` 时落库；`tracer.record(...)` 用于「记完即走」的事实 |
| `registry.py` | `ToolRegistry`：`specs()` 输出 MCP `tools/list` 结构，`call()` 对应 `tools/call`，因此这层能被 MCP Server **零改动**暴露 |
| `context.py` | `ContextWindow`：固定区（系统提示、已确定的线索外观）永不压缩；滚动区超出 token 预算时把最旧的一段折叠成摘要。`ContextStats` 统计压缩次数、节省 token、压缩比 |
| `executor.py` | `run_with_retry` + `RetryPolicy`：指数退避 + 抖动，全过程写 span |

> **一个值得记录的坑**：`run_with_retry` 的 `attrs` 必须走 `attrs=` 关键字传参。
> 早期版本用 `**attrs` 展开，当 `attrs` 里含 `kind` 键时会与 `span(kind=...)`
> 形参冲突抛 `TypeError`，并被外层 `except` 静默吞掉。代码里有注释留痕，
> 测试里也有对应回归用例。

### 3.4 引擎的两个宿主回调

```python
def run(
    self,
    project_id: str,
    *,
    on_stage: Callable[[str, str], None] | None = None,      # (stage, status)
    should_cancel: Callable[[], bool] | None = None,
    ...
) -> dict[str, Any]:
```

- `on_stage(stage, status)`：`status ∈ {running, done, skipped, failed}`，
  云层据此把引擎阶段映射成对外的 `JobStage` 并写进度。
- `should_cancel()`：在**阶段边界**检查。取消请求触发 `PipelineCancelled`，
  云层捕获后落 `GenerationCancelled` 并把任务标记为 `cancelled` 且退还预扣额度。

⚠️ 注意取消的粒度：检查点在阶段边界与镜头循环内，**不是**在供应商 HTTP
请求中途。因此一个正在进行的视频生成请求不会被立即打断，而是等它返回后才退出。

### 3.5 确定性优先

`reelagent/utils.py`：

```python
def stable_seed(text: str, salt: str = "", mod: int = 99991) -> int:
    payload = f"{text}|{salt}".encode("utf-8")
    return int(hashlib.md5(payload).hexdigest()[:8], 16) % mod
```

**刻意不用内置 `hash()`**：后者对 `str` 按进程随机化（`PYTHONHASHSEED`），
会让同一输入两次运行产出不同画面，评测回归就失去意义。所有需要随机种子的
地方（关键帧、片段、基准图）都走 `stable_seed`。

### 3.6 引擎的产物布局

```
<workspace>/projects/<engine_project_id>/
├── assets/       线索基准图（clue_<名称>_<hash>.png）
├── keyframes/    <shot_id>.png
├── clips/        <shot_id>.gif（或有 ffmpeg 时的视频段）
├── audio/        <shot_id>.wav
└── final/
    ├── final.mp4 | final.gif
    ├── audio.wav
    ├── storyboard.png     分镜九宫格（每格底部嵌线索基准图）
    └── manifest.json
```

---

## 4. 云层内部结构

### 4.1 应用启动顺序

`reelagent_cloud/api/app.py::lifespan`，顺序是刻意的：

1. **配置与日志最先**——否则启动期的报错看不到结构化日志。
2. **数据库引擎次之**——后续依赖它的初始化都在此之后。
3. **迁移检查**——`_ensure_schema()`：
   - 必需表集合：`{users, organizations, projects, jobs, ledger_entries}`
   - 生产环境缺表 → 直接 `RuntimeError("…请先执行 alembic upgrade head")`，**只检查不修改**
   - 非生产环境 → 打警告并 `create_all()` 自动建表，降低上手门槛
4. **中间件与路由**——顺序：`GZipMiddleware` → `RequestContextMiddleware`
   → `SecurityHeadersMiddleware`（FastAPI 中后添加的先执行），最后按
   `system → billing(webhook) → auth → api → files → console → pages` 挂载，
   通配的落地页放最后。

关闭顺序相反：`reset_limiter()`（释放 Redis 连接）→ `dispose_engine()`
（释放连接池）。少了这一步，滚动更新时会出现「老进程握着连接不放」
导致连接数打满。

### 4.2 引擎桥接：四个工程问题

`services/pipeline.py` 的 docstring 把这一层的职责写得很清楚，四个问题各有对策：

| 问题 | 对策 |
|---|---|
| 一条成片要跑几分钟，不能长期占用数据库连接 | **进出各开一个短事务**，中间长跑阶段完全不持有连接（`session_scope()` 分段使用） |
| 引擎内部是同步阻塞的（PIL 渲染、urllib 调供应商） | 放进 `asyncio.to_thread()` 执行；工作线程通过 `asyncio.run_coroutine_threadsafe` 回调主循环写进度 |
| 引擎在本地临时目录工作 | 跑完把 `keyframes/ assets/ clips/ audio/ final/` 搬进对象存储，并在 `artifacts` 表登记（含 sha256、size、content_type） |
| 预估额度与实际成本有偏差 | 用引擎汇总的 `cost_summary.total_usd` 去 `jobs.settle()` 结算（多退少补） |

**阶段映射**（`STAGE_TO_JOB_STAGE`）：

| 引擎阶段 | 对外 `JobStage` | 进度 |
|---|---|---|
| `plan` | `planning` | 0.08 |
| `assets` | `assets` | 0.18 |
| `generate` | `generating` | 0.55 |
| `evaluate` | `evaluating` | 0.82 |
| `render` | `rendering` | 0.93 |
| — | `done` | 1.0 |

**产物归集规则**（`ARTIFACT_DIRS` + `final/` 分支）：

| 引擎目录 | 后缀/文件名 | `ArtifactKind` | 默认 content-type |
|---|---|---|---|
| `keyframes/` | 任意 | `keyframe` | `image/png` |
| `assets/` | 任意 | `base_image` | `image/png` |
| `clips/` | 任意 | `video` | `image/gif` |
| `audio/` | 任意 | `audio` | `audio/wav` |
| `final/` | `.mp4` | `video` | 由 `content_type_for()` 推断 |
| `final/` | `.gif` | `gif` | 同上 |
| `final/` | `.wav` | `audio` | 同上 |
| `final/` | `storyboard.png` | `storyboard` | 同上 |
| `final/` | `manifest.json` | `manifest` | 同上 |
| `final/` | `report.json` | `report` | 同上 |

同一 job 重跑时，旧登记会被打上 `deleted_at`（软删），避免产物重复累积。

**一条重要的产品红线**：`resolve_engine_mode()` 在生产环境下若发现
既没有组织 BYOK 视频 Key、也没有平台视频 Key，会直接抛
`UnsupportedOperation("当前部署未配置视频生成供应商…")`，
**拒绝执行**而不是悄悄退到 mock 离线渲染。理由写在注释里：
「让用户花钱买到占位图是最坏的产品事故。」

### 4.3 鉴权模型：三种身份

`security/deps.py` 定义了 `Principal`：

| 身份 | `kind` | `scopes` | 能否管计费/成员 |
|---|---|---|---|
| 匿名 | 无 Principal | — | 否（`get_principal` 抛 401） |
| 浏览器会话 | `session` | `{read, write, admin}` | 是（需 role ≥ admin） |
| API Key | `api_key` | 由 `api_keys.scopes` 决定（默认 `["read","write"]`） | **否**（`require_role` 对非 session 直接 403） |

`require_role()` 的第一行就是：

```python
if self.kind != "session":
    raise Forbidden("该操作需要使用浏览器登录（API Key 不具备管理权限）")
```

理由：API Key 会出现在 CI、脚本、第三方集成里，泄漏概率远高于浏览器会话。
把它限制在「读写项目」范围内，即使泄漏也不会导致账单被改或成员被踢。
详见 [ADR 0004](adr/0004-api-key-cannot-manage-billing.md)。

### 4.4 多租户边界

三道防线：

1. **查询层**：服务函数强制带 `org_id` 过滤。例如
   `projects.get_project(session, *, org_id, project_id)` —— 参数是必填的，
   调用方无法「忘记」传。
2. **组织切换**：`_resolve_org()` 支持 `X-Org-Id` 头切换组织，
   但必须在该用户的 membership 列表里命中，否则 403；默认取
   `order_by(Membership.created_at)` 的第一个。
3. **存储层**（最硬的一道）：对象 key 形式为
   `org/{org_id}/projects/{project_id}/{category}/{filename}`，
   下载路由 `/files/{key:path}` 先调 `key_belongs_to_org(key, principal.org_id)`，
   不匹配则写 `cross_tenant_file_access_blocked` 告警日志并 403。
   `LocalStorage._path()` 还会在拼接后 `resolve()` 再确认仍在根目录内，
   防路径穿越。

---

## 5. 数据模型（14 张表）

表定义在 `reelagent_cloud/db/models.py`。技术约定（`db/base.py`）：

- **JSON 可移植**：PG 上 `JSONB`，其它方言退化为 `JSON`（`JSONType`）。
- **枚举不用 PG 原生 ENUM**：`make_enum()` 生成 `native_enum=False` 的
  `VARCHAR(32)` + CHECK 约束，存的是枚举 `value`。这样加值不需要
  `ALTER TYPE`，迁移更可控。
- **时间统一带时区**：默认值在 Python 侧生成（`utcnow()`），避免 SQLite 丢时区。
- **主键是带业务前缀的 ULID**（`domain/ids.py`）：`proj_01JBQ…`。
  前缀表：`usr / org / mem / key / proj / job / shot / art / led / sub / use / aud / wh / ses / inv`。
  选 ULID 而非 UUID4 的理由写在 docstring 里：单调递增（B-tree 插入局部性好）、
  可读（日志与 URL 里一眼看出类型）、无中心依赖。`parse_ulid_timestamp()`
  能从 ID 直接反解创建时间。

### 5.1 账号与租户

#### `users`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | `String(40)` PK | `usr_…` |
| `email` | `String(320)` **unique** index | 应用层统一 `normalize_email()`（NFKC + lower），因此不需要函数索引 |
| `password_hash` | `String(255)` | Argon2id |
| `display_name` | `String(80)` | |
| `is_active` / `is_verified` / `is_superadmin` | `Boolean` | |
| `last_login_at` | `DateTime(tz)` | |
| `failed_login_count` | `Integer` | 达 `LOCK_THRESHOLD=8` 触发锁定 |
| `locked_until` | `DateTime(tz)` | 锁定 `LOCK_MINUTES=15` 分钟 |
| `session_version` | `Integer` | **会话代际**。改密/登出所有设备时 +1，所有已签发 Cookie 立即失效 |
| `locale` | `String(16)` | 默认 `zh-CN` |
| `meta` | `JSON` | |
| `created_at` / `updated_at` | `DateTime(tz)` index | `TimestampMixin` |

#### `organizations`（租户主体）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | `String(40)` PK | `org_…` |
| `name` / `slug` | `String(120)` / `String(64)` **unique** index | slug 由 `normalize_slug()` 归一化 |
| `plan` | 枚举 `plan_tier` index | `free/starter/pro/studio` |
| `credits_balance` | `BigInteger` | **额度快照**（真值在 `ledger_entries`） |
| `credits_granted_this_period` | `BigInteger` | 本周期已发放，用于防重复发放 |
| `period_started_at` | `DateTime(tz)` | 结算周期锚点 |
| `default_style` | `String(32)` | 默认 `cinematic` |
| `watermark` | `Boolean` | 免费版为 true |
| `is_suspended` / `suspended_reason` | `Boolean` / `String(255)` | 停用开关 |
| `byok` | `JSON` | 组织自带供应商密钥（**Fernet 加密后存储**） |
| `meta` | `JSON` | |

**CHECK 约束**：`ck_org_credits_non_negative`（`credits_balance >= 0`）——
这是账本正确性的最后一道数据库级防线。

#### `memberships`

| 列 | 说明 |
|---|---|
| `id` PK | `mem_…` |
| `user_id` FK → `users.id` ON DELETE CASCADE, index | |
| `org_id` FK → `organizations.id` ON DELETE CASCADE, index | |
| `role` 枚举 `membership_role` | `owner(rank 3) / admin(2) / member(1)` |
| `invited_by` `String(40)` | |
| `last_active_at` | |

**唯一约束**：`uq_membership_user_org(user_id, org_id)` —— 同一用户在同一组织里只能有一条成员关系。

#### `api_keys`

| 列 | 说明 |
|---|---|
| `id` PK | `key_…` |
| `org_id` FK CASCADE, index | |
| `created_by` FK → `users.id` ON DELETE SET NULL | |
| `name` `String(80)` | |
| `prefix` `String(16)` index | 8 位十六进制，**保留明文**便于在列表里辨认 |
| `key_hash` `String(128)` **unique** | `SHA-256(pepper + full_key)`，明文只在创建时返回一次 |
| `scopes` `JSON` | 默认 `["read","write"]` |
| `last_used_at` / `last_used_ip` / `request_count` | 使用信息，写入有 60 秒节流（`_LAST_USED_THROTTLE`） |
| `expires_at` / `revoked_at` | 失效条件 |

`ApiKey.is_active` 属性：`revoked_at is None and not (expires_at and expires_at < now)`。

### 5.2 项目与镜头

#### `projects`

| 列 | 类型 | 说明 |
|---|---|---|
| `id` PK | `String(40)` | `proj_…` |
| `org_id` FK CASCADE, index | | 租户归属 |
| `created_by` FK SET NULL | | |
| `title` `String(160)` / `seed_text` `Text` | | 素材输入 |
| `style` `String(32)` / `aspect_ratio` `String(8)` | | 默认 `cinematic` / `9:16` |
| `status` 枚举 `project_status` index | | `draft/queued/running/ready/failed/archived` |
| `script` `JSON` | | 引擎产出的完整剧本 |
| `clues` `JSON` | | 线索列表（含 `appearance` 与 `base_image`） |
| `evaluation` `JSON` | | 评测结果 |
| `cost_summary` `JSON` | | 成本汇总 |
| `total_shots` / `finished_shots` / `duration_seconds` | `Integer` / `Integer` / `Float` | 冗余汇总，便于列表页直接展示而不用解 JSON |
| `credits_spent` / `provider_cost_usd` | `BigInteger` / `Float` | |
| `engine_project_id` `String(64)` | | 与引擎 workspace 项目的对应关系 |
| `deleted_at` | | **软删除** |

**索引**：`ix_projects_org_created(org_id, created_at)`、
`ix_projects_org_status(org_id, status)` —— 列表页两种主要访问模式。

#### `shots`

| 列 | 说明 |
|---|---|
| `id` PK `String(40)` | `shot_…` |
| `project_id` FK CASCADE index / `org_id` index | |
| `shot_key` `String(64)` | 形如 `sc01_sh01` |
| `scene_key` / `order_index` | 场景与排序 |
| `status` `String(32)` | 默认 `pending` |
| `shot_size` / `camera` / `duration_s` | 景别 / 运镜 / 时长 |
| `action` / `narration` / `dialogue` | 导演意图 |
| `image_prompt` / `video_prompt` | 派生的提示词（Studio 里可手工改写） |
| `clue_ids` `JSON` | 绑定的一致性线索 |
| `tier` / `provider` / `model` | 路由结果 |
| `cost_usd` / `credits` / `attempts` / `error` | 成本与执行 |
| `keyframe_key` / `clip_key` / `audio_key` | 产物存储 key |
| `meta` `JSON` | |

**唯一约束**：`uq_shot_project_key(project_id, shot_key)`；
**索引**：`ix_shots_project_order(project_id, order_index)`。

> **为什么镜头独立成表**：为了支持「单镜重跑」这种局部操作，
> 而不必把整个项目的 JSON 读出来改写再写回——后者会有并发覆盖问题。

### 5.3 任务队列

#### `jobs`

| 分组 | 列 |
|---|---|
| 归属 | `id` PK / `org_id` FK CASCADE index / `project_id` FK CASCADE index / `created_by` FK SET NULL |
| 类型状态 | `kind` 枚举 `job_kind` index / `status` 枚举 `job_status` index / `stage` 枚举 `job_stage` / `progress` `Float` |
| 调度优先级 | `priority` `Integer` index（默认 0，越大越优先） |
| 数据 | `payload` `JSON` / `result` `JSON` / `error` `Text` / `error_code` `String(48)` |
| 重试 | `attempts` `Integer` / `max_attempts` `Integer`（默认 3） |
| 调度与租约 | `run_after` `DateTime(tz)` index / `locked_by` `String(64)` index / `locked_at` / `lease_expires_at` index / `heartbeat_at` / `started_at` / `finished_at` / `cancel_requested` `Boolean` |
| 成本与幂等 | `credits_reserved` `BigInteger` / `credits_charged` `BigInteger` / `provider_cost_usd` `Float` / `idempotency_key` `String(120)` |

**约束与索引**（这是队列性能的关键）：

| 名称 | 定义 | 用途 |
|---|---|---|
| `uq_job_org_idempotency` | UNIQUE `(org_id, idempotency_key)` | 同一请求重复提交只创建一个任务 |
| `ix_jobs_claim` | `(status, priority, run_after)` | **领取任务的核心索引** |
| `ix_jobs_lease` | `(status, lease_expires_at)` | 租约回收扫描 |
| `ix_jobs_org_created` | `(org_id, created_at)` | 组织的任务列表 |

`JobStatus` 语义：

| 值 | 含义 | 是否终态 | 可被领取 |
|---|---|---|---|
| `pending` | 等待执行 | 否 | ✅ |
| `running` | 执行中（持有租约） | 否 | 否 |
| `succeeded` | 成功 | ✅ | 否 |
| `failed` | 失败，**可重试** | ✅ | ✅ |
| `dead` | 超过重试上限，需人工介入 | ✅ | 否 |
| `cancelled` | 已取消 | ✅ | 否 |

### 5.4 产物

#### `artifacts`

| 列 | 说明 |
|---|---|
| `id` PK `String(40)` | `art_…` |
| `org_id` FK CASCADE index / `project_id` FK CASCADE index / `job_id` FK SET NULL | |
| `shot_key` `String(64)` | 目录型产物填相对路径，`final/` 下填空 |
| `kind` 枚举 `artifact_kind` index | `video/gif/audio/keyframe/base_image/storyboard/manifest/report` |
| `storage_key` `String(512)` **unique** | 存储层 key |
| `filename` / `content_type` / `size_bytes` / `sha256` | 供 `ETag` 与展示 |
| `is_public` `Boolean` / `share_token` `String(48)` unique / `download_count` | 分享能力 |
| `expires_at` / `deleted_at` | 生命周期（软删） |
| `meta` `JSON` | |

**索引**：`ix_artifacts_project_kind(project_id, kind)`。

### 5.5 额度账本

#### `ledger_entries`（**只追加**）

| 列 | 说明 |
|---|---|
| `id` PK `String(40)` | `led_…` |
| `org_id` FK CASCADE index | |
| `kind` 枚举 `ledger_kind` index | 见下表 |
| `amount` `BigInteger` | **正数入账、负数出账** |
| `balance_after` `BigInteger` | 本次变动后的余额快照，便于对账与追责 |
| `description` `String(255)` | |
| `project_id` index / `job_id` index | 关联（**无外键**，避免任务删除时牵连账本） |
| `external_ref` `String(128)` | 外部单号（Stripe Invoice / PaymentIntent） |
| `idempotency_key` `String(120)` | |
| `created_by` `String(40)` / `meta` `JSON` | |
| `created_at` `DateTime(tz)` index | |

**唯一约束**：`uq_ledger_org_idempotency(org_id, idempotency_key)`；
**索引**：`ix_ledger_org_created(org_id, created_at)`。

`LedgerKind` 与符号（`sign` 属性）：

| 值 | 方向 | 触发点 |
|---|---|---|
| `signup_grant` | + | 注册赠送（`SIGNUP_GRANT_CREDITS = 500`） |
| `plan_grant` | + | 订阅首期 / 周期续费发放 |
| `purchase` | + | 单独购买点数包 |
| `consume` | **−** | 生成消耗、超出预估补扣 |
| `refund` | + | 任务失败 / 取消 / 按实际用量退还 |
| `adjustment` | ± | 人工调整（客服补偿、扣罚） |
| `expire` | −（按 `sign` 为正，实际未实现扣减路径） | 过期作废 |

> ⚠️ `LedgerKind.sign` 把除 `consume` 之外的所有类型都算作正号。
> 因此 `expire` 目前**不会被记成负数**——该类型在 `enums.py` 里定义了，
> 但代码里没有任何地方产生 `expire` 条目。若将来要做额度过期，
> 必须显式传负 `amount`，或修正 `sign` 语义。

#### `usage_records`

每次供应商调用的**原始用量**，用于成本分析与对账（不参与账本等式）：

| 列 | 说明 |
|---|---|
| `id` PK | `use_…` |
| `org_id` index / `project_id` index / `job_id` index | |
| `shot_key` | |
| `capability` `String(16)` | `image` / `video` / `tts` / `llm` |
| `provider` `String(32)` / `model` `String(64)` | |
| `unit` `String(16)`（默认 `call`）/ `quantity` `Float` | |
| `provider_cost_usd` / `credits_charged` | |
| `latency_ms` `Float` / `success` `Boolean` / `error_code` `String(48)` | |
| `created_at` index | |

**索引**：`ix_usage_org_created(org_id, created_at)`。

### 5.6 订阅与支付

#### `subscriptions`

| 列 | 说明 |
|---|---|
| `id` PK | `sub_…` |
| `org_id` FK CASCADE **unique** | 一个组织一条订阅记录 |
| `plan` 枚举 `sub_plan` | 与 `organizations.plan` 冗余，便于按订阅维度统计 |
| `status` 枚举 `sub_status` | `trialing/active/past_due/canceled/incomplete/unpaid` |
| `stripe_customer_id` `String(64)` index | |
| `stripe_subscription_id` `String(64)` **unique** | |
| `stripe_price_id` `String(64)` | |
| `current_period_start` / `current_period_end` / `canceled_at` / `trial_end` | |
| `cancel_at_period_end` `Boolean` | |
| `meta` `JSON` | |

`Subscription.is_active`：`status ∈ {active, trialing}`。

#### `webhook_events`

| 列 | 说明 |
|---|---|
| `id` PK | `wh_…` |
| `provider` `String(32)` | 目前只有 `stripe` |
| `event_id` `String(128)` | |
| `event_type` `String(64)` | |
| `status` 枚举 `webhook_status` | `received/processed/failed/ignored` |
| `payload` `JSON` | **裁剪后**的原文（`_truncate_payload` 只保留排障所需字段） |
| `error` `Text` / `attempts` `Integer` / `processed_at` | |
| `created_at` index | |

**唯一约束**：`uq_webhook_provider_event(provider, event_id)` ——
Stripe 会重复投递同一事件（超时重试、网络抖动），这个约束保证只处理一次。

### 5.7 审计与通知

#### `audit_logs`

| 列 | 说明 |
|---|---|
| `id` PK | `aud_…` |
| `org_id` index / `actor_user_id` index | 允许为空（系统动作） |
| `actor_type` `String(16)` | `user` / `api_key` / `system` |
| `action` 枚举 `audit_action` index | 见 `domain/enums.py::AuditAction` |
| `target_type` / `target_id` | |
| `ip` / `user_agent` / `request_id` | `request_id` 来自日志上下文，可与日志串联 |
| `meta` `JSON` / `created_at` index | |

**索引**：`ix_audit_org_created(org_id, created_at)`。

> **审计写入不参与业务事务的成败**：业务成功但审计失败时，
> 宁可丢掉一条审计也不能回滚用户的操作。因此 `audit.record()` 单独提交并吞掉异常
> （见 `services/audit.py` 的 docstring）。

#### `email_outbox`

| 列 | 说明 |
|---|---|
| `id` PK | |
| `to_address` `String(320)` index | |
| `subject` `String(255)` / `body_text` `Text` / `body_html` `Text` | |
| `status` `String(16)` index | 默认 `pending` |
| `attempts` `Integer` / `last_error` `Text` / `sent_at` | |
| `created_at` index | |

业务代码只写这张表，由 worker 负责实际投递并重试（`MAX_ATTEMPTS = 5`）。
好处：请求路径不被 SMTP 阻塞；投递失败可重试且可观测；本地开发无 SMTP
时邮件标记为已投递并打日志，流程依然完整可验证。

### 5.8 表清单汇总

| # | 表名 | 职责一句话 |
|---|---|---|
| 1 | `users` | 账号与凭证（含会话代际） |
| 2 | `organizations` | 租户主体：套餐、额度快照、BYOK、停用开关 |
| 3 | `memberships` | 用户 ↔ 组织的成员关系与角色 |
| 4 | `api_keys` | 程序化访问凭证（只存哈希） |
| 5 | `projects` | 一次创作的聚合根：素材、剧本、线索、评测、成本 |
| 6 | `shots` | 镜头级记录，支持单镜重跑 |
| 7 | `jobs` | 任务队列 + 租约 + 幂等 + 成本预留 |
| 8 | `artifacts` | 产物登记与租户隔离的存储 key |
| 9 | `ledger_entries` | 只追加的额度事实账本 |
| 10 | `usage_records` | 每次供应商调用的原始用量 |
| 11 | `subscriptions` | Stripe 订阅状态映射 |
| 12 | `webhook_events` | 外部 Webhook 去重与处理留痕 |
| 13 | `audit_logs` | 关键操作审计 |
| 14 | `email_outbox` | 邮件发件箱（异步投递） |

---

## 6. 任务生命周期与时序

### 6.1 端到端时序

```
用户               API 层                    DB                 Worker            引擎         供应商
 │                   │                       │                    │                │              │
 │─ POST /projects ─▶│                       │                    │                │              │
 │                   │ 校验套餐限制           │                    │                │              │
 │                   │ （镜头数/项目数/并发）  │                    │                │              │
 │                   │─ INSERT projects ─────▶│                    │                │              │
 │                   │─ credits.consume ─────▶│ 预扣额度            │                │              │
 │                   │   （写 ledger_entries） │                    │                │              │
 │                   │─ jobs.enqueue ────────▶│ 幂等：(org,key)     │                │              │
 │◀─ 201 project+job─│  同一事务提交           │                    │                │              │
 │                   │                       │                    │                │              │
 │                   │                       │◀── claim() ────────│                │              │
 │                   │                       │   FOR UPDATE       │                │              │
 │                   │                       │   SKIP LOCKED      │                │              │
 │                   │                       │── RUNNING+租约 ───▶│                │              │
 │                   │                       │                    │─ 启动心跳循环 ──│              │
 │                   │                       │                    │                │              │
 │                   │                       │                    │── plan ───────▶│── LLM ──────▶│
 │                   │                       │◀─ 进度 planning ───│                │              │
 │                   │                       │                    │── assets ─────▶│── 图像 ─────▶│
 │                   │                       │                    │── generate ───▶│── 逐镜并发 ──▶│
 │                   │                       │                    │── evaluate ───▶│── Judge ────▶│
 │                   │                       │                    │── render ─────▶│  ffmpeg      │
 │                   │                       │                    │                │              │
 │                   │                       │◀─ 产物上传 + 登记 ──│                │              │
 │                   │                       │◀─ settle() ────────│  多退少补       │              │
 │                   │                       │◀─ complete() ──────│  READY         │              │
 │◀─ 轮询 /jobs/{id}─│◀─ job + project ──────│                    │                │              │
 │   stage=done      │                       │                    │                │              │
 │   progress=1.0    │                       │                    │                │              │
 │─ GET /final ─────▶│ 返回成片 artifact      │                    │                │              │
```

### 6.2 领取（claim）的原子性

`services/jobs.py::claim()`：

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

- `SKIP LOCKED`：拿不到锁的 worker **直接跳过这条去抢下一条**，而不是排队等待。
  这是多 worker 无锁竞争的关键。
- `or_(locked_by IS NULL, lease_expires_at < now)`：租约过期的任务可以被重新领取，
  因此**不需要额外的故障检测服务**。
- SQLite 方言会忽略 `FOR UPDATE`（代码注释里写明了），本地单 worker 场景仍然正确。

领取成功后同时写入：`status=RUNNING`、`locked_by=worker_id`、
`locked_at=now`、`lease_expires_at=now+lease`、`heartbeat_at=now`、
`started_at`（仅首次）、`attempts += 1`；并把关联 `Project.status` 置为 `RUNNING`。

### 6.3 心跳与取消的合流

`heartbeat()` 每 `heartbeat_seconds`（默认 20s）执行一次续约 UPDATE，
条件是 `id == job_id AND locked_by == worker_id AND status == RUNNING`。
`rowcount == 0` 说明任务已被他人接管（或自身已终态），此时返回 `False`。

Worker 侧 `_heartbeat_loop()` 把「心跳丢失」翻译成协程内可见的取消信号：

```python
if not alive:
    cancel_event.set()      # 要么被取消，要么租约被别的 worker 接管
```

同时 `heartbeat()` 会检查 `job.cancel_requested`，返回 `not cancel_requested`——
**用户的取消请求就是通过这条路径传进引擎的 `should_cancel` 回调的**。
这样设计的好处是：取消不需要额外的通知通道，复用了已有的心跳链路。

### 6.4 失败、退避与重试

`fail()` 的判定：

```python
can_retry = retryable if retryable is not None else error_code in RETRYABLE_CODES
exhausted = job.attempts >= job.max_attempts
```

`RETRYABLE_CODES = {provider_failure, provider_timeout, provider_rate_limited, service_unavailable, lease_expired}`

| 情况 | 结果 |
|---|---|
| 可重试 且未耗尽 | `status=FAILED`（可被重新领取）、`run_after = now + min(30 · 2^(attempts-1), 600)` 秒、`stage=QUEUED` |
| 否则 | `status=DEAD`、项目落 `FAILED`、打 `job_dead` 错误日志 |

**退避序列**（attempts = 1,2,3,4…）：30s → 60s → 120s → 240s → 480s → 600s（封顶）。

无论哪种路径，只要 `credits_reserved > 0` 就**全额退还预扣额度**，
幂等键为 `refund:<job_id>`。注释里的原话：
「钱不能白花，这是产品信誉问题。」

终态映射表：

| `error_code` | HTTP 语义 | 可重试 |
|---|---|---|
| `provider_failure` | 502 | ✅ |
| `provider_timeout` | — | ✅ |
| `provider_rate_limited` | — | ✅ |
| `service_unavailable` | 503 | ✅ |
| `lease_expired` | — | ✅ |
| `budget_exceeded` | 402 | ❌ |
| `unsupported_operation` | 400 | ❌ |
| `validation_failed` | 422 | ❌ |
| `unexpected_error` | 500 | ✅（`retryable` 默认 True） |

### 6.5 租约回收（reap）

Worker 主循环里每 `REAP_INTERVAL_SECONDS = 120` 秒执行一次
`reap_expired_leases()`：

```sql
UPDATE jobs
   SET status = 'failed',
       error = 'worker 租约过期（进程可能崩溃），任务已被重新排队',
       error_code = 'lease_expired',
       locked_by = NULL, locked_at = NULL, lease_expires_at = NULL
 WHERE status = 'running'
   AND lease_expires_at IS NOT NULL
   AND lease_expires_at < now()
```

同时把计数打进 `reelagent_job_lease_expired_total` 指标并打 `job_lease_reaped` 警告日志。
`ix_jobs_lease(status, lease_expires_at)` 就是为这条 UPDATE 服务的。

### 6.6 结算（settle）

```python
actual_credits = credits.usd_to_credits(settings, actual_provider_cost_usd)
delta = actual_credits - int(job.credits_reserved)
```

| `delta` | 行为 | 幂等键 |
|---|---|---|
| `< 0`（实际更省） | `credits.refund(-delta)` | `settle-refund:<job_id>` |
| `= 0` | 不动 | — |
| `> 0`（实际更贵） | `credits.consume(delta)`；**余额不足时不抛错**，记 `extra_uncollected` 并打警告 | `settle-extra:<job_id>` |

`> 0` 时的宽容策略是刻意的：**已生成的成片照常交付**，超出部分记为欠账，
不让用户因为余额差几块钱而拿不到已经花钱生成的片子。

结算后写入 `job.credits_charged = actual_credits`、`job.credits_reserved = 0`、
`job.provider_cost_usd`，并把 `settlement` 明细塞进 `job.result`。

### 6.7 取消的两条路径

```python
async def cancel(session, *, job_id, org_id) -> Job:
    ...
    job.cancel_requested = True
    if job.status == JobStatus.PENDING:
        # 还没开始跑：直接终结
        job.status = CANCELLED; job.finished_at = now()
        预扣额度全额退还（幂等键 cancel-refund:<job_id>）
        项目状态退回 DRAFT
```

| 任务状态 | 取消行为 |
|---|---|
| `PENDING` | **立即**置 `CANCELLED`，退还预扣额度，项目回到 `DRAFT` |
| `RUNNING` | 只置 `cancel_requested=True`，由 worker 的心跳检测到后设 `cancel_event`，引擎在下一个阶段/镜头边界抛 `PipelineCancelled`，worker 捕获后调 `mark_cancelled(refund=True)` |
| 终态 | 抛 `InvalidState("任务已结束，无法取消")`（409） |

**注意**：取消不是立即的。一个正在进行的供应商 HTTP 请求会跑完才退出，
因此从「点取消」到「任务真的停」可能有几十秒延迟。

### 6.8 单镜重跑

`POST /app/projects/{id}/regenerate` 与 `JobKind.REGENERATE_SHOT`：

1. 页面提交 `shot_keys`（逗号分隔），按镜头数 `quote_project()` 预估额度并
   `ensure_sufficient()` + `consume()`（幂等键含时间戳，**刻意不是幂等的**——
   用户重复点击就是重复重跑，应该重复计费）。
2. 入队 `payload={"shot_keys": [...], "max_shots": ...}`，`priority=5`
   （高于任何套餐的 priority：免费 0 / 创作者 1 / 专业 2 / 工作室 3）。
3. `execute_shot_regeneration()` 先把这些镜头的 `Artifact` 打软删、
   清空 `Shot.keyframe_key/clip_key/audio_key` 并置 `status="pending"`，
   再把 `only_shots` 写进 `job.payload`，最后复用整条 `execute_generation()`。
4. 引擎侧 `Director.run(only_shots=[...])` 只处理这些镜头；
   其余已完成镜头因 `Shot.is_finished` 为真被跳过，因此**成本与耗时远低于整条重跑**。

---

## 7. 额度账本的双记账设计

这是全项目最需要严谨的模块（`services/credits.py` 的 docstring 开门见山：
「**这是全项目最需要严谨的一个模块**——它直接对应钱」）。

### 7.1 核心不变量

```
organizations.credits_balance  ==  Σ ledger_entries.amount  (WHERE org_id = ?)
```

- 左侧是**快照**，右侧是**事实**。
- 两者在**同一个事务内**更新。
- 每一次变动都留下一条带 `balance_after` 的记录。

`reconcile()` 就是这个等式的直接实现：

```python
ledger_sum = SELECT COALESCE(SUM(amount), 0) FROM ledger_entries WHERE org_id = ?
balance    = SELECT credits_balance FROM organizations WHERE id = ?
return {"org_id", "ledger_sum", "balance", "consistent": ledger_sum == balance, "drift": balance - ledger_sum}
```

`drift`（`balance - ledger_sum`）非零即意味着存在 bug，应当立即告警。
控制台页面 `/app/usage` 会直接把 `reconcile` 的结果渲染出来。

### 7.2 唯一的写入路径

**所有**额度变动最终都汇聚到 `_apply()`。这是刻意的单点设计：

```python
async def _apply(session, *, org_id, kind, amount, ...) -> LedgerEntry:
    # 1. 幂等短路
    existing = await _find_idempotent(session, org_id, idempotency_key)
    if existing is not None:
        log.info("ledger_idempotent_hit", ...)
        return existing

    # 2. 加排他锁（并发下不丢更新）
    org = await _lock_org(session, org_id)          # SELECT ... FOR UPDATE
    new_balance = int(org.credits_balance) + int(amount)

    # 3. 余额守卫（除非显式 allow_negative）
    if new_balance < 0 and not allow_negative:
        raise PaymentRequired(..., required=abs(amount), available=int(org.credits_balance))

    # 4. 写账本 + 更新快照
    session.add(LedgerEntry(..., balance_after=new_balance))
    org.credits_balance = new_balance

    # 5. 必须显式 flush
    await session.flush()
    return entry
```

**关于第 5 步**：会话被配置为 `autoflush=False`（见 `db/session.py`），
因此不显式 `flush()` 的话，同一会话内后续的 `get_balance()` 会读到数据库里的旧值——
账本已是新值、余额还是旧值，等式当场破裂。代码里为此写了明确注释。

对外语义化接口（都走 `_apply`）：

| 函数 | sign | 幂等键使用方 | 说明 |
|---|---|---|---|
| `grant()` | + | 注册 `signup:<user_id>`、订阅 `sub-activate:<sub_id>`、续费 `invoice:<invoice_id>` | `amount <= 0` 直接 `ValueError` |
| `consume()` | − | 预扣 `reserve:<project_id>:default`、补扣 `settle-extra:<job_id>` | 传正数，内部取负 |
| `refund()` | + | `refund:<job_id>` / `cancel-refund:<job_id>` / `settle-refund:<job_id>` | `amount <= 0` 时记一条 **0 额** 条目「无需退还」，保留审计轨迹 |
| `adjust()` | ± | 无（人工） | `description` 强制为 `人工调整：<reason>`，记 `created_by`；不允许扣成负数 |

### 7.3 幂等键的取值约定

| 场景 | 幂等键 |
|---|---|
| 注册赠送 | `signup:<user_id>` |
| 订阅激活首期额度 | `sub-activate:<stripe_subscription_id>` |
| 周期续费额度 | `invoice:<invoice_id>` |
| 生成预扣 | `reserve:<project_id>:<idempotency_key 或 'default'>` |
| 任务失败退还 | `refund:<job_id>` |
| 取消退还 | `cancel-refund:<job_id>` |
| 结算退还差额 | `settle-refund:<job_id>` |
| 结算补扣 | `settle-extra:<job_id>` |

全部受 `uq_ledger_org_idempotency(org_id, idempotency_key)` 保护。
注意幂等键的作用域是**组织级**，不是全局。

### 7.4 计价口径

```python
def usd_to_credits(settings, usd: float) -> int:
    if usd <= 0:
        return 0
    rate = settings.billing.markup_multiplier * settings.billing.credits_per_usd
    return max(1, math.ceil(usd * rate))
```

- 默认 `markup_multiplier = 3.0`、`credits_per_usd = 100`，
  因此 **1 USD 供应商成本 ≈ 300 点**。
- 向上取整 + `max(1, …)`：避免出现 0 点消耗。
- 反向换算 `credits_to_usd()` 用同一个 `rate`，保证两向一致。
- 对外只展示「点数」，不暴露供应商真实成本结构（`domain/plans.py` 的 docstring）。

**预估**（`quote_project()`，用于生成前的额度预检与界面提示）：

```python
premium_uplift = 1.35 if allow_premium else 1.0
images       = shots                        # 每镜一张关键帧
base_images  = min(max(2, shots // 2), settings.max_base_images)
image_cost   = (images + base_images) * 0.03
video_cost   = shots * avg_provider_cost_per_shot_usd * premium_uplift   # 默认 0.14/镜
tts_cost     = shots * 0.01
total_usd    = (image_cost + video_cost + tts_cost) * 1.15               # 1.15 安全系数
```

刻意略微保守（×1.15），避免实际超支导致用户中途失败。
`CreditQuote.breakdown` 按 `image` / `video` / `tts` 给出分项点数。

### 7.5 预扣 → 结算的两段式

```
创建项目            jobs.enqueue(credits_reserved=quote.credits)
   │                 credits.consume(quote.credits, key=reserve:<project>:default)
   ▼
执行中              （不动账）
   │
   ├─ 成功 ─▶ settle(actual)  ─▶ delta<0 退还 / delta>0 补扣
   │                            job.credits_charged = actual, credits_reserved = 0
   │
   ├─ 失败 ─▶ fail()  ─▶ refund(credits_reserved, key=refund:<job_id>)  ← 全额退
   │
   └─ 取消 ─▶ cancel()/mark_cancelled() ─▶ refund(key=cancel-refund:<job_id>)
```

「先扣钱再干活」是有意选择：如果反过来（先干活再扣钱），
用户余额不足时会陷入「片子已经生成了但收不到钱」的尴尬。
预扣 + 结算的组合把风险放在「多退」这一侧，而多退是可控且对用户友好的。

### 7.6 与 Stripe 的衔接

`services/billing.py` 负责把外部支付事件翻译成账本条目，两条链路都对幂等：

| Stripe 事件 | 账本动作 | 幂等键 |
|---|---|---|
| `checkout.session.completed` | `_sync_subscription()` → `credits.grant(plan.monthly_credits)` | `sub-activate:<sub_id>` |
| `customer.subscription.created/updated` | 同上（同步套餐、状态、周期） | `sub-activate:<sub_id>` |
| `customer.subscription.deleted` | 不改账本；`org.plan = FREE`、`org.watermark = True` | — |
| `invoice.paid`（`billing_reason = subscription_cycle`） | `credits.grant(plan.monthly_credits)` | `invoice:<invoice_id>` |
| `invoice.paid`（首购） | 只返回 `paid <amount>`，额度已在激活时发过 | — |
| `invoice.payment_failed` | 只把订阅置 `PAST_DUE`，**不动额度** | — |
| 其它事件 | `WebhookStatus.IGNORED` | — |

双保险：`webhook_events` 的唯一约束去重 + 账本自身的 `idempotency_key`。
即使 `webhook_events` 那行被误删，重复投递也不会重复发放额度。

---

## 8. 跨镜头一致性方案

长视频最常见的失败模式是「角色变脸 / 场景漂移」。方案分两步，
并且**度量它是否真的被执行**。

### 8.1 第一步：基准图（ArtDirector）

为每条线索（`Clue.kind ∈ {character, prop, scene}`）：

1. 用 LLM 生成 `appearance` 文本，**只描述不随镜头变化的特征**
   （发型 / 五官 / 服装 / 材质），禁止写动作与光线——这是跨镜头一致的前提。
   prompt 见 `reelagent/llm/prompts.py::ART_SYSTEM`。
2. 据此生成一张基准图（角色设定图 / 场景设定图 / 道具设定图），
   种子走 `stable_seed(clue.id, salt="base_image")`。
3. LLM 失败时有确定性兜底：`appearance = description or f"{name}（{kind}）"`，
   保证一致性链路不中断。
4. 生成顺序按 `_KIND_PRIORITY = {character: 0, scene: 1, prop: 2}` 排序，
   受 `max_base_images`（云端默认 6）限制。

### 8.2 第二步：双路注入（Director）

每个镜头的生成请求同时带上两条约束：

**路一 · 视觉参考图**（`agents/director.py`）：

```python
refs = [c.base_image for c in bound_clues if c.base_image]
result = self.rt.tools.call("generate_keyframe", {
    "prompt": shot.image_prompt,
    "out_name": f"{shot.id}.png",
    "ref_images": refs,                       # ← 基准图作为参考图
    "seed": stable_seed(shot.id),
    "shot_id": shot.id,
})
```

**路二 · 外观文本嵌入**（`_build_prompts()`）：

```python
clue_lines = [f"{c.name}：{c.appearance}" for c in bound_clues if c.appearance.strip()]
```

`clue_lines` 会原样进入 `prompts.director_user(...)`，
并要求 LLM 在 `image_prompt` 里**原样保留**这些描述。
LLM 调用失败时有确定性兜底 prompt（`_fallback_prompt`），同样包含 `clue_lines`。

> 也就是说，「一致性」不依赖 LLM 是否听话——兜底路径也会把约束传下去。
> 这正是下一页那两个指标能达到 1.0 的原因。

### 8.3 第三步：度量它

`reelagent/eval/consistency.py` 提供四个指标。关键洞察写在 docstring 里：

> 后两个指标是这套方案的核心：它们度量的不是「生成结果好不好」（那依赖模型），
> 而是「我们的编排逻辑有没有真的把一致性约束传下去」——这是工程可控的部分。

| 指标 | 含义 | 计算 | 无对象时 |
|---|---|---|---|
| `clue_reference_coverage` | **[编排指标]** 绑定线索的镜头中，实际把基准图作为参考注入的比例 | `covered / eligible`，`eligible` = 绑定到有 `base_image` 线索的镜头，`covered` = 其中 `status ∈ {keyframe_done, clip_done, audio_done}` 的 | `None`（附 `{eligible: 0, covered: 0}`） |
| `prompt_clue_fidelity` | **[编排指标]** 镜头 `image_prompt` 中是否原样包含所绑定线索的 `appearance` | 取 `appearance[:12]` 做包含判断（容忍 LLM 轻微改写）；`ok / checked` | `None` |
| `cross_shot_clue_similarity` | 同一**角色**线索在不同镜头画面之间的相似度 | 灰度 32×32 → 减均值除标准差 → 两两 NCC，映射到 0~1 后取均值 | `1.0` |
| `palette_stability` | 相邻关键帧颜色直方图相关性（检测风格漂移） | RGB 三通道各 16 bins 归一化 → Bhattacharyya 系数，相邻帧两两后取均值 | `1.0`（直方图少于 2 张时） |

补充指标 `rhythm_variety`：景别/运镜的多样性、时长 min/max/std、景别直方图。

**综合分** `overall`：只对有可度量对象的维度取均值。

```python
parts = [v for v in (pal, css, cov, fid) if v is not None]
overall = round(float(np.mean(parts)), 4) if parts else 0.0
```

> **「无数据」不等于「满分」**：没有可度量对象时返回 `None` 并显示 `N/A`，
> 而不是伪装成 `1.0`。早期版本这里返回 1.0，导致一致性机制完全失效时门禁照样通过——
> 这是项目中最值得记住的教训，代码注释里明确留痕。

`Evaluator` 还会用 Wilson 置信下界汇报覆盖率（`coverage_wilson_lower`），
避免镜头数少时的高估。

### 8.4 门禁（CI gate）

`Evaluator._gate()` 的阈值与失败原因：

| 条件 | 失败消息 | 阈值 |
|---|---|---|
| `cov is None AND fid is None` | 「一致性策略未生效：没有任何镜头绑定到「有基准图 / 有外观描述」的线索…」 | — |
| `fid < 0.95` | 「一致性文本保真度 … < 0.95：存在镜头未把线索 appearance 注入 prompt」 | 0.95 |
| `cov < 0.90` | 「基准图注入覆盖率 … < 0.90」 | 0.90 |
| `palette_stability < 0.5` | 「色调稳定性 … < 0.50，画面风格漂移」 | 0.50 |
| `judge.overall < 3.0` | 「LLM 评审总分 … < 3.0」 | 3.0 |

「一致性策略未生效」被单独处理为最严重的问题——它意味着机制根本没被用起来，
而不是「效果不够好」。

### 8.5 已知局限

- `cross_shot_clue_similarity` 用灰度归一化互相关，是**代理指标**：
  能捕捉大幅漂移，但不等价于人眼判断的「像同一个人」。
  生产环境应换成人脸 / ReID 特征。
  在 mock 模式下它主要反映背景色调差异，因此约 0.70 是合理值而非「效果不好」。
- 相似度指标只覆盖 `kind == "character"` 的线索，道具与场景不参与该计算
  （它们的稳定性由 `palette_stability` 与人工评审间接反映）。

---

## 9. 可观测性设计

### 9.1 两条独立的可观测链路

| | 引擎侧 | 云层侧 |
|---|---|---|
| 载体 | SQLite `spans` 表 | 结构化日志 + Prometheus 指标 |
| 粒度 | span（LLM / 工具 / 重试 / 阶段 / 镜头） | HTTP 请求 / Job / 供应商调用 / 业务计数 |
| 读取方式 | `python -m reelagent spans <project_id>` | `/metrics`、`/status`、`/api/v1/system/status` |

两者刻意不合并：引擎需要「按项目查看耗时归因」，云层需要「全局速率与告警」。
不同的访问模式用不同的存储更合适。

### 9.2 引擎侧的 Span 模型

`harness/tracing.py` 的两种用法（docstring 里明确说明）：

```python
# 1) 包裹一段执行（推荐）：with 退出时落库
with tracer.span("director.shot", kind="agent", shot_id="s1") as sp:
    ...
    sp.set(prompt_tokens=120)
    sp.fail("...")          # 可选

# 2) 即时记录一个已经发生的事实（不需要 with）
tracer.record("art_director.base_image.failed", status="failed", error=str(exc))
```

> ⚠️ 只有 `with` 退出或显式 `close()` 时才写库。
> 早期版本用 `span(...).set(...)` 的写法不会落库，导致失败被静默吞掉。

`store.span_stats()` 按名称聚合出耗时归因。既有 README 记录的真实输出：

```
名称                                        次数        总耗时        均值    失败
director.shot                             10      17.7s   1770.7ms     0
stage.render                               3      17.2s   5733.4ms     0
director.clip                              9      15.0s   1667.3ms     0
tool.generate_clip                         9      14.9s   1657.1ms     0   ← 瓶颈
tool.generate_keyframe                    14       1.0s     74.0ms     0
director.checkpoint_repaired               1       0.0s      0.0ms     0   ← 回退留痕
```

`director.checkpoint_repaired` 是断点恢复的**可见痕迹**：当磁盘产物与
checkpoint 记录不一致时，Director 会记录这个 span 并说明回退了多少个镜头。

### 9.3 云层的请求上下文

`api/middleware.py::RequestContextMiddleware`：

- 尊重上游传入的 `X-Request-ID`，没有则生成 16 位 hex。
- 通过 `bind(request_id=...)` 把 request_id 绑进日志上下文（`structlog`），
  于是同一请求的所有日志行都能串联，**并且会进入错误响应体**供用户报障。
- 响应头注入 `X-Request-ID` 与 `Server-Timing: app;dur=<ms>`。
- 访问日志对探针与静态资源静默（`QUIET_PATHS` / `QUIET_PREFIXES`），
  避免它们淹没日志。
- 指标使用**路由模板**而非真实路径：

  ```python
  route_obj = request.scope.get("route")
  route = getattr(route_obj, "path", "") or request.url.path
  ```

  这是为了防止每个 ID 变成一条独立时间序列把 Prometheus 打爆
  （`metrics.py` 的 docstring 明确要求「标签基数必须可控」）。

### 9.4 指标清单

`observability/metrics.py` 使用独立 `CollectorRegistry`（避免与进程内其它库
的默认 collector 冲突，也便于测试隔离）：

| 指标名 | 类型 | 标签 |
|---|---|---|
| `reelagent_http_requests_total` | Counter | `method, route, status` |
| `reelagent_http_request_duration_seconds` | Histogram | `method, route` |
| `reelagent_http_requests_in_flight` | Gauge | — |
| `reelagent_jobs_total` | Counter | `kind, status` |
| `reelagent_job_duration_seconds` | Histogram | `kind` |
| `reelagent_job_queue_depth` | Gauge | `status` |
| `reelagent_job_retries_total` | Counter | `kind` |
| `reelagent_job_lease_expired_total` | Counter | — |
| `reelagent_provider_calls_total` | Counter | `provider, capability, status` |
| `reelagent_provider_call_duration_seconds` | Histogram | `provider, capability` |
| `reelagent_provider_cost_usd_total` | Counter | `provider, capability` |
| `reelagent_provider_circuit_open` | Gauge | `provider, capability` |
| `reelagent_credits_consumed_total` | Counter | `org_id` |
| `reelagent_projects_created_total` | Counter | — |
| `reelagent_shots_generated_total` | Counter | `status` |
| `reelagent_worker_active` | Gauge | — |
| `reelagent_worker_jobs_running` | Gauge | `worker_id` |
| `reelagent_auth_failures_total` | Counter | `reason` |
| `reelagent_rate_limit_hits_total` | Counter | `scope` |

> ⚠️ `reelagent_credits_consumed_total` 带 `org_id` 标签。这在租户数量多时
> 会造成基数膨胀，需要在采集侧做聚合或删除该标签（见
> [第 10 节](#10-已知缺口与后续演进)）。

### 9.5 日志脱敏

`observability/logging.py` 在**处理器层面**做正则擦除，而不是指望每个调用点自觉：

- 键名匹配 `api[_-]?key|apikey|secret|password|passwd|token|authorization|bearer|client[_-]?secret|webhook[_-]?secret|session|cookie|pepper|dsn`（忽略大小写）→ 值替换为 `***REDACTED***`
- 值匹配 `sk-xxxx` / `Bearer xxxx` / `whsec_xxxx` / `AKIA…` → 同样擦除
- 递归深度限制为 4，避免大对象上的性能问题

### 9.6 探针语义

`api/routes/system.py` 刻意区分三种健康语义：

| 端点 | 检查 | 用途 |
|---|---|---|
| `/livez` | 不碰任何外部依赖 | 存活探针。**数据库抖动不该导致容器被杀掉重启** |
| `/readyz` | 数据库可连 + 存储可读写 | 就绪探针。不满足则从负载均衡摘除 |
| `/health` | 同上但返回详细诊断（含 `version`、`env`） | 人类可读的汇总 |

`_run_checks()` 的检查项：

| 检查 | critical | 内容 |
|---|---|---|
| `database` | ✅ | `SELECT 1`，返回后端类型 |
| `storage` | ✅ | 写一个 `_healthcheck/probe.txt` 再删掉（真实读写验证） |
| `providers` | ❌ | 各供应商是否配置了 Key（没配也能起服务，只是不能生成） |
| `billing` | ❌ | `billing.enabled` |

非 critical 项不健康时，`/readyz` 仍返回 200——这是刻意的：没配视频 Key
的部署依然是「能接流量的」，只是不能生成成片（而生成请求会被
`resolve_engine_mode()` 明确拒绝）。

---

## 10. 已知缺口与后续演进

以下是**代码里确认存在**的缺口，不做掩饰。

> ℹ️ **本文档记录的是一个仍在演进的代码库。**
> 早期版本的这一节把「容器化文件、Alembic 迁移、控制台界面」
> 列为缺口，它们**已经补齐**（见 10.0）。
> 若你读到某条缺口但仓库里已有对应文件，以仓库为准。

### 10.0 已补齐的基础设施（曾列为缺口）

| 项 | 当前状态 |
|---|---|
| **容器化** | `Dockerfile`、`Dockerfile.dev`、`docker-compose.yml`（含 `migrate` 服务，依赖顺序 `migrate → api → worker`）、`docker-compose.dev.yml`（postgres + redis + minio + minio-init）、`.dockerignore`、`Makefile` |
| **数据库迁移** | `alembic.ini`（`script_location = reelagent_cloud/db/migrations`）+ `reelagent_cloud/db/migrations/`（含 `versions/`）。`make migrate` 会先 `wait_for_db.py` 再 `alembic upgrade head` |
| **控制台界面** | `reelagent_cloud/web/templates/`（`base.html` + `auth/` + `console/` + `pages/` + `partials/`）与 `web/static/`（`css/app.css`、`js/app.js`、`favicon.svg`）。`STATIC_DIR.exists()` 现在为真，`/static` 会被挂载 |
| **环境变量样例** | `.env.example`（含完整 cloud 层变量）与 `.env.production.example` |
| **云层测试** | `tests/test_cloud_{credits,http,jobs,security}.py`（164 个用例）+ `tests/conftest.py`（pytest fixture 驱动真实 FastAPI 应用） |

### 10.1 仍未落地的部分

| 缺口 | 影响 | 现状 |
|---|---|---|
| **`reelagent-admin` 入口悬空** | 见 10.3 | 需自行实现或改用 CLI |
| **产品 GC 任务** | 对象存储只增不减 | 没有任何代码删除对象文件；`Artifact.expires_at` 无人使用 |
| **`reelagent_cloud/db/migrations/versions/`** | — | 目录已建，但需要 `alembic revision --autogenerate` 生成首个版本后才有内容 |

### 10.2 控制台界面的约束（不是缺口，但是必须遵守）

`web/static/` 已提供 `css/app.css` 与 `js/app.js`。
**CSP 的 `script-src 'self'`（无 `unsafe-inline`）意味着
所有前端 JS 必须是外部文件**——新增界面时不要把脚本内联到模板里，
否则会被 CSP 拦掉（而且很容易被误判为「CSP 配错了」而放宽这条约束）。

`web/__init__.py` 里定义的 17 个过滤器和全局变量
（`dt`/`relative`/`bytes`/`duration`/`credits`/`money`/`percent`/`status_label`/
`status_tone`/`stage_label`/`shot_size` 等）已被模板使用。

### 10.3 `reelagent-admin` 入口悬空

`pyproject.toml` 声明了：

```toml
reelagent-admin = "reelagent_cloud.admin:main"
```

但 `reelagent_cloud/admin.py` **不存在**。安装后执行 `reelagent-admin`
会报 `ModuleNotFoundError`。同理，`services/storage.py::purge_org_storage()`
（账号注销时清理整租户对象）目前没有任何调用方。

### 10.4 未实现的 JobKind

`domain/enums.py` 定义了四个 `JobKind`，但 worker 只实现了两个：

| `JobKind` | worker 支持 |
|---|---|
| `generate_project` | ✅ |
| `regenerate_shot` | ✅ |
| `evaluate_project` | ❌ → `NotImplementedError(f"暂不支持的任务类型：{kind.value}")` |
| `render_project` | ❌ 同上 |

同时 `services/email.py` 的 `welcome_email` 只覆盖注册场景，
额度预警、订阅变更通知等邮件事先没有产生方。

### 10.5 其他值得注意的点

- **`/api/v1/system/status` 与 `/api/v1/system/ping` 无鉴权**：
  它们用 `DbSession` + `AppSettings` 但**不挂 `CurrentPrincipal`**，
  因此任何人都能读到队列深度与 `safe_dump()` 的配置快照
  （密钥已打码，但暴露了供应商类型、是否启用 billing 等信息）。
  这不是 bug（`/status` 页面本来就是公开的），但生产环境应评估是否收紧。
- **`GET /api/v1/me` 的 `credits.usd_rate` 字段命名有歧义**：
  它返回的是 `settings.billing.credits_per_usd`（默认 100），
  **不是**「点数 → 美元」的换算率。真实换算率是
  `markup_multiplier × credits_per_usd = 300`。
- **`LedgerKind.EXPIRE` 无产生方**，且 `sign` 语义对负数不友好（见 5.5 节）。
- **`reelagent_credits_consumed_total` 的 `org_id` 标签**在租户多时基数膨胀。
- **单机 SQLite 引擎**：引擎层的 `Store` 是 SQLite，因此引擎本身仍适合
  「单机长任务」而非「高并发服务」。云层通过「一个 job 一个独立 engine
  workspace + 独立 SQLite 文件」绕开了这个限制（`engine_workspace / job_id`），
  但也意味着引擎状态无法跨 worker 共享——**断点续跑只在同一个 job_id 的工作区内有效**。
  跨 worker 的续跑依赖 `Project.engine_project_id` 与工作区仍存在这一前提，
  而 `cleanup_workspace()` 会在任务结束后删除工作区。
  因此**云层当前的「断点恢复」能力实际上主要用于单次执行内的重试与
  单镜重跑，而非跨进程的完整续跑**。这是一个需要在生产使用中验证的点。
- **`live` 路径未经端到端联网验证**（既有 README 的诚实说明沿用）：
  供应商适配器按公开文档实现，接入真实 Key 后可能需要按实际返回调整字段。
  已完整验证的是 mock 路径与全部编排 / 恢复 / 评测逻辑。

### 10.6 演进方向

- 产品 GC 任务：清理过期产物与孤儿对象（**当前对象存储只增不减**）
- 给 worker 加 metrics 端点（worker 指标当前无法被 Prometheus 采集）
- 打通 `JobKind.EVALUATE_PROJECT` / `RENDER_PROJECT`
- 把 `Storage` 的租户校验下沉到方法签名（见 [ADR 0007](adr/0007-tenant-scoped-artifact-storage-keys.md) 方案 E）
- 账号注销流程（`purge_org_storage()` 已有实现，缺入口）
- 剪映草稿导出（含旁白音轨与口播字幕轨）
- 把 `ToolRegistry.specs()` 封装为 MCP Server 对外暴露
- 关键帧局部重生成（迭代编辑体验）
- 用人脸 / ReID 特征替换灰度互相关，提升一致性指标可信度
- 多版本评测对比报告（Prompt / 模型变更的收益与回归判定）
- 生成首个 Alembic 版本（`versions/` 目前为空）

---

## 附录：关键文件索引

| 关注点 | 文件 |
|---|---|
| 应用工厂与生命周期 | `reelagent_cloud/api/app.py` |
| 配置与生产校验 | `reelagent_cloud/config.py` |
| 数据模型（14 张表） | `reelagent_cloud/db/models.py` |
| 任务队列 | `reelagent_cloud/services/jobs.py` |
| 额度账本 | `reelagent_cloud/services/credits.py` |
| 引擎桥接 | `reelagent_cloud/services/pipeline.py` |
| Stripe 与 Webhook | `reelagent_cloud/services/billing.py` |
| 鉴权与授权 | `reelagent_cloud/security/deps.py`、`security/tokens.py` |
| Worker 主循环 | `reelagent_cloud/workers/runner.py` |
| 五阶段状态机 | `reelagent/pipeline/runner.py` |
| 逐镜生成编排 | `reelagent/agents/director.py` |
| 一致性度量 | `reelagent/eval/consistency.py` |
| 确定性种子 | `reelagent/utils.py` |

相关文档：

- [部署指南](DEPLOYMENT.md)
- [运维 Runbook](OPERATIONS.md)
- [REST API 参考](API.md)
- [架构决策记录](adr/)
