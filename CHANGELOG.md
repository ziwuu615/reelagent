# 更新日志

本文件记录 ReelAgent 的所有重要变更。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> **版本历史说明**：`1.0.0` 是本项目的**首个正式版本**。
> 在此之前没有对外发布过版本，因此本文件没有更早的条目——
> 我们不虚构历史版本。

---

## [未发布]

### 计划中

- 修复 `Idempotency-Key` 未生效的问题：`POST /api/v1/projects` 读取并回显该头，
  但 `projects.create_project()` 未接收它，导致同一素材重复提交会重复扣费。
- 实现 `reelagent-admin` 入口（`pyproject.toml` 已声明
  `reelagent_cloud.admin:main`，但该模块不存在）。
- 产品 GC 任务：清理过期产物与孤儿对象
  （当前**没有任何代码**删除对象存储里的文件）。
- 给 worker 加 metrics 端点（worker 指标当前无法被 Prometheus 采集）。
- 补齐 `anonymous` / `user` 两个限流 scope 的调用点。
- 实现 `JobKind.EVALUATE_PROJECT` 与 `JobKind.RENDER_PROJECT`
  （worker 当前只支持 `GENERATE_PROJECT` 与 `REGENERATE_SHOT`）。
- 把 `Storage` 的租户校验下沉到方法签名（当前依赖每个路由自觉调用）。
- 剪映草稿导出（`.zip`，含旁白音轨与口播字幕轨）。
- 把 `ToolRegistry.specs()` 封装为 MCP Server，对外暴露工具。
- 关键帧局部重生成（迭代编辑体验）。
- 用人脸 / ReID 特征替换灰度归一化互相关，提升一致性指标可信度。
- 多版本评测对比报告（Prompt / 模型变更的收益与回归判定）。
- 账号注销流程（`purge_org_storage()` 已实现但无调用方）。

### 已补齐（早期草稿曾列为缺口）

- ✅ 控制台界面：`reelagent_cloud/web/templates/`
  （`base.html` + `auth/` + `console/` + `pages/` + `partials/`）
  与 `web/static/`（`css/app.css`、`js/app.js`、`favicon.svg`）。
- ✅ 容器化与本地编排：`Dockerfile`、`Dockerfile.dev`、
  `docker-compose.yml`（含 `migrate` 服务）、`docker-compose.dev.yml`、
  `.dockerignore`、`Makefile`。
- ✅ 数据库迁移：`alembic.ini` + `reelagent_cloud/db/migrations/`。
- ✅ 云层环境变量样例：`.env.example`（含完整 cloud 层变量）
  与 `.env.production.example`。
- ✅ 云层测试套件：`tests/test_cloud_{credits,http,jobs,security}.py`
  + `tests/conftest.py`（pytest fixture 驱动真实 FastAPI 应用）。

---

## [1.0.0] - 首个正式版本

首个正式版本。以下能力均为**已完成并通过测试**，
但**尚未上线运营**（没有真实用户与营收数据）。

### 新增 —— 引擎（`reelagent`）

#### 多智能体流水线

- 五阶段状态机：`plan → assets → generate → evaluate → render`
  （`reelagent/pipeline/runner.py`）。
- 四个 Agent：
  - **Planner**：种子文本 → 结构化剧本（场景 / 镜头 / 台词 / 旁白 / 线索）。
    prompt 里把创作约束写成硬规则：镜头 2~5 秒、景别交替、
    台词与旁白不同镜、强制登记需重复出现的线索。
  - **ArtDirector**：为每条线索生成**只描述稳定特征**的 `appearance` 文本
    与基准图（角色设定图 / 场景设定图 / 道具设定图）。
  - **Director**：逐镜并发生成（关键帧 → 片段 → 配音）、难度路由、
    参考图注入、重试与降级。
  - **Evaluator**：确定性指标 + LLM-as-Judge 四维 rubric + CI 门禁判定。

#### 断点恢复

- ✅ **阶段级 checkpoint**：五个阶段各自写状态，`resume` 跳过已完成阶段。
- ✅ **镜头级 checkpoint**：每个镜头在 `keyframe_done` / `clip_done` /
  `audio_done` 三个点落盘。
- ✅ **产物校验**：恢复时**不信任数据库里的状态字段**，
  而是逐字段核对产物文件是否真实存在，按实际存在的产物重算状态。
  因此「关键帧还在、片段被删」这类**部分损坏**也能正确处理——
  只重做缺失的部分，不浪费已花的钱。
- ✅ **回退留痕**：回退行为写入 `director.checkpoint_repaired` span。

#### 跨镜头一致性

- ✅ **双路注入**：图像请求带线索基准图作为参考图（`ref_images`），
  prompt 里原样嵌入线索的 `appearance` 文本。
- ✅ **四个度量指标**：
  - `clue_reference_coverage` —— 基准图注入覆盖率
  - `prompt_clue_fidelity` —— prompt 中的线索外观保真度
  - `cross_shot_clue_similarity` —— 同线索跨镜头画面相似度
  - `palette_stability` —— 相邻关键帧色调稳定性
- ✅ **「无数据」不等于「满分」**：没有可度量对象时指标返回 `None`
  并显示 `N/A`，而非伪装成 `1.0`。
- ✅ **Wilson 置信下界**（`coverage_wilson_lower`）用于样本量少时的诚实汇报。

#### 质量评测门禁

- ✅ 确定性指标与 LLM-as-Judge 四维 rubric（叙事连贯性、视觉一致性、
  节奏、prompt 忠实度）双轨合成布尔门禁。
- ✅ 阈值：`prompt_clue_fidelity ≥ 0.95`、`clue_reference_coverage ≥ 0.90`、
  `palette_stability ≥ 0.50`、`judge.overall ≥ 3.0`，
  随判定结果一起返回（`thresholds`），使报告自解释。
- ✅ 「一致性策略未生效」被识别为独立的、最严重的失败类别。
- ✅ Judge 调用失败不阻塞评测（记录 `judge_error`，退化到确定性指标）。

#### 模型路由与成本控制

- ✅ **难度打分**：基础 0.30，逐项加权——
  有人物线索 +0.22、元素 ≥3 条 +0.08、首镜 +0.10、
  特写/近景 +0.10、有台词 +0.08、时长 ≥4s +0.06、已失败过 +0.12。
- ✅ **三档路由**：`draft`（跳过视频模型，零成本）/
  `standard` / `premium`，阈值在难度 0.45 与 0.66。
- ✅ **预算感知**：预算耗尽强制降 `draft`；剩余预算不足以支撑多镜
  `premium` 时降级为 `standard`。
- ✅ **路由报告**：按档位统计、跳过次数、预估节省金额（`routing` 字段）。
- ✅ **成本汇总**：按类别与供应商归集（`Store.cost_summary()`）。

#### 确定性

- ✅ `stable_seed()` 使用 md5 而非内置 `hash()`——
  后者对 `str` 按进程随机化（`PYTHONHASHSEED`），
  会让同一输入两次运行产出不同画面，使评测回归失去意义。
  已用 `PYTHONHASHSEED=7` 与 `=99` 验证结果一致并在测试中锁定。

#### Harness（Agent 运行时底座）

- ✅ **Store**：SQLite checkpoint 持久化，线程安全（并发写同一库）。
- ✅ **Tracer**：span 级全链路建模（`with` 落库 / `record()` 即时记录），
  可按名称聚合出耗时归因。
- ✅ **ToolRegistry**：`specs()` 输出 MCP `tools/list` 结构，
  `call()` 对应 `tools/call`，因此可被 MCP Server 零改动暴露。
- ✅ **ContextWindow**：固定区（系统提示、已确定的线索外观）永不压缩，
  滚动区超出 token 预算时折叠成摘要，`ContextStats` 统计压缩比与节省 token。
- ✅ **run_with_retry**：指数退避 + 抖动，全过程写 span。

#### 产物与合成

- ✅ `ffmpeg` 可用时输出 MP4（含音轨）。
- ✅ 无 `ffmpeg` 时自动降级为 **GIF + WAV + 分镜九宫格 + manifest**，
  仍交付一个可看的成片（而非失败）。
  九宫格每格底部嵌被注入的线索基准图，是一致性机制在画面上的直接证据。

#### 供应商与 CLI

- ✅ 供应商抽象：图像 / 视频 / TTS 统一接口，Agent 不感知具体供应商。
- ✅ 已内置适配器：**火山方舟**（Seedream 图像 / Seedance 视频，
  异步任务 + 轮询）、**OpenAI 兼容**（图像 / TTS）、以及
  `mock`（产出真实 PNG / 多帧 GIF / 时长匹配的 WAV）。
- ✅ **逐项降级**：live 模式下若某个供应商缺少 Key，
  只把那一项降级为 mock 并打印提示，流水线仍然跑完。
- ✅ 五种风格预设：`cinematic` / `anime` / `cyberpunk` / `ink` / `realistic`。
- ✅ CLI 子命令：`demo` / `run` / `resume` / `status` / `spans` /
  `list` / `eval` / `report` / `tools` / `config`。
- ✅ 支持 `.txt` 文件作为素材输入，以及 `--style` / `--aspect` /
  `--budget` / `--concurrency` / `--mode` / `--tier` / `--no-judge` 等参数。

### 新增 —— 云端产品层（`reelagent_cloud`）

#### 多租户与账号

- ✅ 14 张表的领域模型（`db/models.py`），除 `users` 外所有业务表带 `org_id`。
- ✅ 注册在一个事务内完成四件事：建用户、建组织、建 owner 成员关系、
  发放注册赠送额度（500 点）+ 建免费订阅记录。
- ✅ 组织 slug 唯一化（带随机后缀，避免枚举出组织总量）。
- ✅ 成员角色三级：`owner(3)` / `admin(2)` / `member(1)`，
  支持邀请与移除（不能移除 owner）。
- ✅ **软删除与归档**：项目删除是软删（`deleted_at`），
  产物保留以便误删恢复。

#### 额度账本

- ✅ **只追加账本 + 余额快照双写**：`ledger_entries` 是事实，
  `organizations.credits_balance` 是快照，同一事务内更新。
- ✅ 每条记录带 `balance_after`，任意历史时点的余额可单条读出。
- ✅ 7 种账本类型：`signup_grant` / `plan_grant` / `purchase` /
  `consume` / `refund` / `adjustment` / `expire`。
- ✅ **行级排他锁**（`SELECT ... FOR UPDATE`）保证并发下不丢更新。
- ✅ **幂等键**：`uq_ledger_org_idempotency(org_id, idempotency_key)`。
- ✅ **余额守卫**：`new_balance < 0` 时抛 `PaymentRequired`（402）。
- ✅ **数据库级 CHECK 约束** `ck_org_credits_non_negative`。
- ✅ **对账函数** `reconcile()`：校验
  `SUM(ledger_entries.amount) == organizations.credits_balance`，
  返回 `{ledger_sum, balance, consistent, drift}`。
- ✅ **过期作废的类型已定义**（`LedgerKind.EXPIRE`），
  但产生方尚未实现（见「已知问题」）。

#### 计费口径

- ✅ 点数公式：`credits = max(1, ceil(provider_cost_usd ×
  markup_multiplier × credits_per_usd))`，默认 `3.0 × 100 = 300 点/USD`。
- ✅ 预估函数 `quote_project()` 按镜头数预估消耗，
  逐项给出 `image` / `video` / `tts` 分项点数，并乘 1.15 安全系数
  （刻意略微保守，避免实际超支导致用户中途失败）。
- ✅ **预扣 → 结算两段式**：创建项目时预扣，
  任务成功后按实际成本多退少补（`settle()`），
  任务失败/取消时全额退还。

#### 套餐与定价

- ✅ 四个套餐，定价与限制是单一事实来源（`domain/plans.py`）：
  - **体验版** ¥0 —— 500 点/月、6 镜、1 并发、3 个项目、1 成员、带水印、不可商用
  - **创作者版** ¥49/月 —— 3,000 点/月、8 镜、1 并发、20 个项目、2 成员、去水印、可商用
  - **专业版** ¥199/月 —— 15,000 点/月、12 镜、3 并发、项目不限、5 成员、开放 REST API
  - **工作室版** ¥699/月 —— 60,000 点/月、12 镜、8 并发（优先调度）、
    项目不限、20 成员、自定义风格与基准图上传、用量报表导出
- ✅ 套餐限制在创建项目时校验：项目数、并发任务数、
  单条镜头数上限（还受 `REEL_MAX_SHOTS_PER_PROJECT` 约束）、
  是否允许 `premium` 档位。
- ✅ 注册赠送额度 = 体验版月度额度（`SIGNUP_GRANT_CREDITS`）。
- ✅ 失败任务自动全额退还（`FAILURE_REFUND_RATIO = 1.0`）。

#### 任务队列与 Worker

- ✅ **Postgres `FOR UPDATE SKIP LOCKED`** 队列，
  与业务写入同事务，无需外部 broker。
- ✅ **租约 + 心跳**：`lease_seconds` 默认 900s、
  `heartbeat_seconds` 默认 20s；租约过期即可被重新领取。
- ✅ **过期租约回收**：worker 每 120 秒把过期租约的 `RUNNING`
  任务打回 `FAILED`（`error_code='lease_expired'`）。
- ✅ **幂等入队**：`uq_job_org_idempotency(org_id, idempotency_key)`。
- ✅ **优先级调度**：`ORDER BY priority DESC, run_after ASC`，
  套餐优先级 0/1/2/3，单镜重跑为 5。
- ✅ **失败退避**：`min(30 × 2^(attempts-1), 600)` 秒，
  5 个错误码可重试。
- ✅ **两条取消路径**：`PENDING` 立即终结并退款；
  `RUNNING` 通过 `cancel_requested` 标志经心跳传给引擎。
- ✅ **优雅停机**：收到 SIGTERM 后停止领取新任务，
  等在跑的做完，超过 `shutdown_grace_seconds`（默认 60s）才强退。
- ✅ **主循环不因单次异常退出**（异常被捕获、记日志、退避后继续）。
- ✅ CLI：`reelagent-worker --once`（单任务调试）/
  `--concurrency N` / `--worker-id`。

#### 引擎桥接

- ✅ 进出各开一个短事务，长跑阶段完全不持有数据库连接。
- ✅ 引擎在 `asyncio.to_thread()` 中执行，
  通过 `asyncio.run_coroutine_threadsafe` 回调主循环写进度。
- ✅ 产物自动归集到对象存储并按类型登记为 `Artifact`
  （计算 sha256 与 size）。
- ✅ 按实际成本结算预扣额度。
- ✅ **生产环境红线**：没有可用视频供应商 Key 时直接拒绝执行，
  不悄悄退到离线渲染（「让用户花钱买到占位图是最坏的产品事故」）。
- ✅ **BYOK 支持**：组织自带供应商 Key，Fernet 加密后落库，
  任何接口都不明文返回。

#### REST API

- ✅ 统一前缀 `/api/v1`，OpenAPI 文档在 `/docs`，
  另有 `/redoc` 与 `/openapi.json`。
- ✅ 端点覆盖：当前身份与套餐、套餐列表、项目 CRUD、任务查询与取消、
  额度余额与流水与预估、API Key 自助管理、审计日志、
  产物列表与成片入口与下载与分享。
- ✅ **`Idempotency-Key` 请求头**；未提供时服务端用内容指纹
  （`auto-<sha256 前 40 位>`）兜底，避免同一素材被连点两次扣两份钱。
- ✅ **统一错误响应**：`{"error": {"code", "message", "details"}, "request_id"}`，
  每个领域错误有稳定的 `code`（前端据此分支，而不是匹配中文消息）。
- ✅ **未预期异常不外泄细节**：对外只给通用文案 + `request_id`，
  完整堆栈只进日志。
- ✅ 分页参数统一 `limit` / `offset`，`limit` 有上限保护。

#### 控制台页面（路由已实现）

- ✅ 页面路由：仪表盘、项目列表、新建项目、项目详情、
  运行中项目、Studio（分镜编辑）、用量与账单、设置。
- ✅ 表单与 API 路由刻意区分错误处理：表单错误**重新渲染页面并带提示**，
  而不是返回 JSON。
- ✅ Studio 支持改写单镜提示词（`POST /app/projects/{id}/shots/{key}/prompt`）。

> ℹ️ 模板与静态资源已提供：`reelagent_cloud/web/templates/`
> （`base.html` + `auth/` + `console/` + `pages/` + `partials/`）与
> `web/static/`（`css/app.css`、`js/app.js`、`favicon.svg`）。
> CSP 禁止内联脚本，因此前端 JS 全部走外部文件。

#### 鉴权与安全

- ✅ **三种身份**：匿名 / 浏览器会话 / API Key，能力递增且边界清晰。
- ✅ **API Key 不能管理计费与成员**（`require_role` 对非 session 直接 403），
  API Key 的角色被强制降级为 `member`（不继承创建者权限）。
- ✅ **API Key 只存哈希**：`SHA-256(pepper + full_key)`，
  明文仅在创建时返回一次；`prefix` 保留明文便于辨识。
- ✅ **会话代际**（`users.session_version`）：改密或「登出所有设备」时 +1，
  所有已签发 Cookie 立即失效。无需服务端 session 表即可批量吊销。
- ✅ **会话 Cookie 只放 `uid` / `sv` / `iat`**——
  不放角色、额度、套餐（否则权限变更无法即时生效）。
- ✅ **CSRF 双提交 Cookie**（`reelagent_csrf` cookie + `x-csrf-token` 头），
  常数时间比较；API Key 天然免疫因此跳过；控制台表单再叠一层隐藏字段。
- ✅ **口令 Argon2id**（OWASP 推荐档 m=19MiB, t=2, p=1）+ 常见弱口令黑名单 +
  强度校验 + 登录成功时按需 rehash。
- ✅ **登录失败锁定**：8 次失败锁定 15 分钟；
  用户不存在时也做一次哈希校验以抗时序枚举。
- ✅ **多租户隔离三道防线**：查询层强制 `org_id` 过滤、
  `X-Org-Id` 切换需校验成员关系、存储层 key 前缀校验
  （`key_belongs_to_org`，越权尝试记录告警日志）。
- ✅ **BYOK 密钥可逆加密**（Fernet，密钥由 `SECRET_KEY` 派生），
  列表只显示「已配置/未配置」。
- ✅ **日志脱敏**在处理器层面做正则擦除（密钥名与密钥值双重匹配，
  递归深度限制 4）。
- ✅ **安全响应头**：`nosniff`、`X-Frame-Options: DENY`、
  `Referrer-Policy`、`Permissions-Policy`、`Cross-Origin-Opener-Policy`、
  `Cross-Origin-Resource-Policy`（媒体路径放行 cross-origin）。
- ✅ **CSP 不使用 `unsafe-inline` 脚本**（`script-src 'self'`），
  仅 `style-src` 单独放行 `unsafe-inline`；
  启用 billing 时才追加 Stripe 域名。`frame-ancestors 'none'`。
- ✅ **HSTS** 仅在 `https_only=true` 时下发。
- ✅ **TrustedHostMiddleware** 在 `allowed_hosts` 不含 `*` 时启用。
- ✅ **防开放重定向**：登录后的 `next` 参数只允许站内相对路径。
- ✅ **API Key 明文不进 URL**（会进入浏览器历史、Referer 与访问日志）。

#### 限流

- ✅ 两种后端：Redis 固定窗口（多实例共享配额）/ 进程内滑动窗口
  （单实例够用），按是否配置 Redis 自动选择。
- ✅ 七种 scope：`anonymous`（30/min）、`user`（240/min）、
  `api_key`（300/min）、`login`（10 次/300s）、`signup`（5 次/3600s）、
  `project_create`（60/小时）、`upload`（30/小时）。
- ✅ **fail-open**：限流器后端故障时**放行**而非拒绝
  （限流器挂了不应该让整个站点不可用）。
- ✅ **`client_ip()` 防伪造**：只在 X-Forwarded-For 第一跳是内网/回环时采信。

#### 计费集成（Stripe）

- ✅ **Checkout 结账**（订阅模式，支持优惠码）+
  **客户门户**（用户自己在 Stripe 侧改卡/取消，无需我们实现账单 UI）。
- ✅ **Webhook 幂等**：先落 `webhook_events(provider, event_id)`
  （唯一约束）再执行业务。
- ✅ **6 个事件处理器**：`checkout.session.completed`、
  `customer.subscription.created/updated/deleted`、`invoice.paid`、
  `invoice.payment_failed`；未识别的事件标记为 `ignored`。
- ✅ **验签用原始请求体**（路由不挂任何 body 解析依赖）。
- ✅ **处理失败返回 5xx** 让 Stripe 重试，绝不吞异常返回 200。
- ✅ **双重幂等**：`webhook_events` 去重 + 账本 `idempotency_key`
  （`sub-activate:<sub_id>` / `invoice:<invoice_id>`）。
- ✅ **未配置 Stripe 时降级为纯额度模式**：注册赠送额度可正常使用，
  没有充值入口——自托管部署无需依赖 Stripe。

#### 可观测性

- ✅ **结构化日志**（structlog）：生产输出 JSON（可直接被
  Loki / ELK / CloudWatch 采集），本地输出带颜色的 console。
- ✅ **请求上下文**：尊重上游 `X-Request-ID`，否则生成 16 位 hex；
  绑进日志上下文；响应头回写 `X-Request-ID` 与 `Server-Timing`。
- ✅ **19 个 Prometheus 指标**：HTTP（3）、任务（5）、供应商（4）、
  业务（5）、安全（2），使用独立 `CollectorRegistry`。
- ✅ **标签基数可控**：HTTP 指标用路由模板（`/api/projects/{id}`）
  而非真实路径。
- ✅ **探针语义区分**：`/livez`（不碰外部依赖，数据库抖动不该杀掉容器）/
  `/readyz`（数据库 + 存储真实读写验证）/ `/health`（人类可读汇总）。
  非 critical 检查（供应商 Key、billing）不健康时 `/readyz` 仍返回 200。
- ✅ **公开状态页** `/status`（队列深度 + 依赖健康度）。
- ✅ **可选 Sentry**（`reelagent[sentry]`，`send_default_pii=False`）。
- ✅ 审计日志覆盖 16 个动作类型，带 `request_id` 可与日志串联。

#### 配置与运维

- ✅ **启动即校验**：生产环境（`REEL_ENV=prod`）缺关键配置**直接拒绝启动**，
  而不是运行到一半才炸。六项硬性要求：`SECRET_KEY`、非 SQLite、
  `https_only`、显式 `allowed_hosts`、https `base_url`、视频供应商 Key。
- ✅ **密钥集中**：所有 Secret 用 pydantic `SecretStr`，`repr` 自动打码；
  `Settings.safe_dump()` 提供可安全写日志的配置快照。
- ✅ **可降级开关**：`enabled` 让某项能力（Redis、S3、Stripe、SMTP）
  在缺失时自动退化，而不是把整个服务拖死。
- ✅ **schema 检查**：生产环境只检查不修改（缺表直接报错并提示
  `alembic upgrade head`）；非生产环境自动 `create_all()` 降低上手门槛。
- ✅ **连接池参数可调** + `statement_timeout` 兜底（默认 30s）。
- ✅ **邮件发件箱**：业务只写 `email_outbox` 表，worker 负责投递与重试
  （最多 5 次）。未配置 SMTP 时邮件标记为已投递并打日志。

### 新增 —— 文档

- ✅ `README.md`（本文档之外的产品级门面）
- ✅ `docs/ARCHITECTURE.md` —— 分层架构、14 张表、时序、双记账、一致性方案
- ✅ `docs/DEPLOYMENT.md` —— 三种部署路径、环境变量清单、Stripe 配置、
  备份恢复、升级回滚
- ✅ `docs/OPERATIONS.md` —— 面向值班同学的运维 Runbook（按症状组织）
- ✅ `docs/API.md` —— REST API 参考
- ✅ `docs/adr/` —— 7 篇架构决策记录 + 索引
- ✅ `CHANGELOG.md`、`CONTRIBUTING.md`、`SECURITY.md`、
  `CODE_OF_CONDUCT.md`、`LICENSE`

### 测试

- ✅ **212 个自动化测试**，分两部分（框架不同是有意的）：
  - **引擎 48 项**：`tests/test_core.py`（33）+ `tests/test_pipeline.py`（15），
    用标准库 `unittest` 编写，`python -m unittest discover -s tests -t .`
    不需要 pytest 即可运行（与「引擎 clone 即跑」的定位一致）。
  - **云层 164 项**：`tests/test_cloud_credits.py`（27）、
    `tests/test_cloud_http.py`（70）、`tests/test_cloud_jobs.py`（33）、
    `tests/test_cloud_security.py`（34），用 pytest + pytest-asyncio +
    FastAPI `TestClient`，由 `tests/conftest.py` 提供
    `client` / `authed_client` / `db` / `settings` / `csrf` / `org_id` 等 fixture。
- ✅ 覆盖重点：
  - **断点恢复**：产物被删 → checkpoint 失效 → 重新生成
    （且不重做完好的镜头）
  - **部分损坏**：关键帧保留、只重做片段
  - **阶段失败**：落到 `failed`、不吞错误、修复后可从断点续跑并恢复项目状态
  - **确定性**：两次运行指标完全一致；`stable_seed` 不受 hash 随机化影响
  - **回归用例**：`attrs` 含 `kind` 键不再触发参数冲突；基准图必须真的生成
  - **评测语义**：「无可度量对象」返回 `None` 而非 `1.0`
  - **mock 供应商**：产出的是真 PNG / 多帧 GIF / 时长匹配的 WAV
  - **额度账本**：双记账对账、幂等键、余额守卫、人工调整
  - **HTTP 契约**：注册/登录流程、表单校验、CSRF、Cookie 属性、弱口令拒绝
  - **API Key**：认证与 readonly scope 的边界
  - **租户隔离**：跨组织访问被拒绝、自有产物可下载
  - **任务队列**：领取/租约/心跳/回收/退避/结算/取消
- ✅ 冒烟脚本：`scripts/smoke.py`（注册 → 账本 → 任务 → 领取 → 结算 → 对账）
  与 `scripts/smoke_http.py`。

### 实测数据（mock 模式）

> 数据来源：既有项目 README 的记录。复现方式：
> `python -m reelagent demo`。**这不是运营数据**，
> 而是离线 mock 供应商下的端到端实测。

- 默认样例故事、7 个镜头，端到端约 **17 秒**：

  | 指标 | 数值 |
  |---|---|
  | 综合分 | 0.9246 |
  | 色调稳定性 | 1.0000 |
  | 同线索跨镜头相似度 | 0.6986 |
  | 基准图注入覆盖率 | 1.0000 |
  | prompt 线索保真度 | 1.0000 |
  | LLM 评审总分（5 分制） | 4.25 |
  | CI 门禁 | 通过 |
  | 成片时长 | 19.5s（7 镜） |
  | 自动化测试 | 212 项（引擎 48 + 云层 164） |

- **可复现性**：上述数值在不同 `PYTHONHASHSEED` 下完全一致
  （`PYTHONHASHSEED=7` 与 `=99` 结果相同），已在测试中锁定。
- **解读**：一致性与保真度达到 1.0 是**设计使然**——
  这两项度量的是编排层有没有把约束传下去，而注入逻辑是确定性的。
  真正依赖模型能力的是 `cross_shot_clue_similarity`；
  在 mock 下它主要反映背景色调差异，因此 0.6986 是合理值而非「效果不好」。

### 已知问题

以下问题在 1.0.0 时点**确认存在**，不做掩饰。

> ℹ️ 本清单是**演进中的记录**。若干基础设施缺口（控制台模板、
> 容器化文件、Alembic 迁移、云层环境变量样例、云层测试套件）
> 已在后续补齐，见上方「[未发布] → 已补齐」。
> 若你读到某条缺口但仓库里已有对应文件，以仓库为准。

#### 悬空与未实现的功能

- ❌ **`reelagent-admin` 入口悬空**：`pyproject.toml` 声明了
  `reelagent_cloud.admin:main`，但该模块不存在，
  执行后报 `ModuleNotFoundError`。
- ❌ **`JobKind.EVALUATE_PROJECT` 与 `JobKind.RENDER_PROJECT` 未实现**：
  worker 遇到这两种任务会抛
  `NotImplementedError(f"暂不支持的任务类型：{kind.value}")`。
- ❌ **`services/email.py` 只实现了注册欢迎邮件**，
  额度预警、订阅变更通知等邮件事先没有产生方。
- ❌ **账号注销流程未实现**：`purge_org_storage()` 已实现但无调用方。
- ❌ **产品 GC 任务未实现**：**没有任何代码**删除对象存储里的文件；
  `Artifact.expires_at` 字段无人使用，因此磁盘占用只增不减。
- ❌ **`LedgerKind.EXPIRE` 无产生方**，且 `LedgerKind.sign` 把除
  `CONSUME` 之外的所有类型都算作正号，
  因此即便产生 `expire` 条目也会被记成入账。
  若要做额度过期，必须先修正 `sign` 语义或显式传负 `amount`。
- ❌ **`credit_quote` 的幂等键未生效**：
  `POST /api/v1/projects` 读取了 `Idempotency-Key` 头并用内容指纹兜底
  （响应里返回 `idempotency_key`），但 `projects.create_project()` 与
  `start_generation()` 并未收到这个键——任务幂等键仍是
  `gen:<project_id>`。由于项目 ID 本身是新生成的，
  **实际上同一素材重复提交会创建两个项目并各扣一次额度**。
  这个缺口需要修复。

#### 一致性与安全问题

- ❌ **`/api/v1/system/status` 与 `/api/v1/system/ping` 无鉴权**：
  用 `DbSession` + `AppSettings` 但未挂 `CurrentPrincipal`，
  任何人都能读到队列深度与 `safe_dump()` 的配置快照
  （密钥已打码，但暴露了供应商类型与是否启用 billing）。
- ❌ **`GET /api/v1/me` 的 `credits.usd_rate` 字段命名有歧义**：
  返回的是 `settings.billing.credits_per_usd`（默认 100），
  **不是**「点数 → 美元」的换算率（真实换算率是
  `markup_multiplier × credits_per_usd = 300`）。
- ❌ **`reelagent_credits_consumed_total` 带 `org_id` 标签**，
  租户数量多时会造成 Prometheus 基数膨胀。
- ❌ **`Storage` 抽象的租户校验未下沉**：当前依赖
  「每个路由都记得调 `key_belongs_to_org`」，
  这是 code review 层面的约束而非类型层面的保证。
- ❌ **`sanitize_filename` 对 `org_id` 也做清洗**，
  若将来引入非 ASCII 的自定义 org 标识，
  清洗后的值与原始值不同会导致 `key_belongs_to_org` 判定失败。
- ❌ **没有 WAF、IP 黑白名单、2FA**。
- ❌ **`jobs` / `ledger_entries` / `webhook_events` / `usage_records`
  没有自动归档或分区策略**。

#### 影响云层断点恢复能力的限制

- ⚠️ **云层的「断点恢复」主要用于单次执行内的重试与单镜重跑，
  而非跨进程的完整续跑。**
  引擎的 checkpoint 在 `<workspace>/projects/<engine_project_id>/reelagent.db`，
  云层给每个 job 一个独立工作区（`engine_workspace / job_id`），
  且任务结束后 `cleanup_workspace()` 会删除它。
  跨 worker 的续跑依赖工作区仍然存在这一前提。
  这个行为需要在生产使用中验证并按需调整。
- ⚠️ **取消不是立即的**。`should_cancel` 的检查点在阶段边界与镜头循环内，
  因此一个正在进行的供应商 HTTP 请求会跑完才退出，
  从「点取消」到「任务真的停」可能有几十秒延迟。
- ⚠️ **`cross_shot_clue_similarity` 只覆盖 `kind == "character"` 的线索**，
  道具与场景的漂移无法被该指标检测；
  且 Judge 只看文本表格（不含图像），因此**道具变形目前无法被自动发现**。
- ⚠️ **Judge 的输出不完全可复现**，跨阈值边界时门禁可能抖动；
  当前没有重复采样取均值或方差控制。
- ⚠️ **门禁阈值（0.95 / 0.90 / 0.50 / 3.0）是经验值**，
  尚未用真实用户反馈校准。
- ⚠️ **live 路径未经端到端联网验证**（开发时无 API Key）。
  接口按公开文档实现，接入 Key 后可能需要按实际返回调整字段。
  已完整验证的是 mock 路径与全部编排 / 恢复 / 评测逻辑。
- ⚠️ **`rhythm_variety` 给出统计量但不参与门禁判定**，
  因此「十个中景镜头各 3 秒」这类节奏问题不会导致门禁失败。
- ⚠️ **成本单价是估算值**（`providers/base.py`），
  用于预算控制与路由决策，不是账单级精度。

---

[未发布]: https://github.com/reelagent/reel-agent/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/reelagent/reel-agent/releases/tag/v1.0.0
