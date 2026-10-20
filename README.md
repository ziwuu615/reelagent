# ReelAgent

**多租户 AI 短视频生成 Agent 平台 —— 给一段素材，还你一条成片。**

[![CI](https://img.shields.io/badge/CI-212%20tests%20passing-brightgreen)](#测试)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.0.0-informational)](#版本)

ReelAgent 把「一句话或一篇短小说」变成一条可交付的短视频：
多智能体流水线自主完成 **剧本拆解 → 角色与场景设定 → 逐镜生成 → 配音 →
质量评测 → 合成成片**，并在机制上保证**跨镜头角色与场景一致**。

它是一个**已完成、可一键部署的生产级系统**，包含引擎与云端产品层两个包：

| 包 | 定位 | 依赖 |
|---|---|---|
| **`reelagent/`** | **引擎**：多智能体流水线、断点恢复、跨镜头一致性、质量评测、模型路由 | 仅 `numpy` + `Pillow`（零 Web 依赖） |
| **`reelagent_cloud/`** | **云端产品层**：多租户、账号、额度账本、Stripe 计费、任务队列、worker、REST API | FastAPI / SQLAlchemy / Stripe / Redis / Argon2 |

> **当前状态**：代码已完成并有 **212 项自动化测试**覆盖
> （引擎 48 项 + 云层 164 项），**尚未上线运营**
> ——本文不含任何真实用户数、营收或客户案例。
> 引擎路径已完整验证；live 供应商路径未经端到端联网验证（见
> [已知限制](#已知限制与缺口)）。

---

## 目录

- [产品截图](#产品截图)
- [这是什么 / 解决什么问题](#这是什么--解决什么问题)
- [核心能力](#核心能力)
- [架构](#架构)
- [快速开始](#快速开始)
- [REST API 示例](#rest-api-示例)
- [配置说明](#配置说明)
- [项目结构](#项目结构)
- [技术选型与权衡](#技术选型与权衡)
- [测试](#测试)
- [部署](#部署)
- [已知限制与缺口](#已知限制与缺口)
- [路线图](#路线图)
- [贡献](#贡献)
- [License](#license)

---

## 产品截图

> 📸 **截图状态**：仓库提供的演示素材在 [`docs/`](docs/) 下
> （`demo_storyboard.png`、`demo.gif`）。
> 控制台界面已实现（`reelagent_cloud/web/templates/` + `static/`），
> 但**产品界面截图尚未归档到 `docs/screenshots/`**。
> 部署后请按下方清单补充截图。

### 分镜九宫格（mock 模式实际产出）

![分镜九宫格](docs/demo_storyboard.png)

> 上图为 mock 模式产出的分镜九宫格。每格底部两张小图是**被注入的线索基准图**
> ——这就是跨镜头一致性机制在画面上的直接证据。

### 引擎 CLI 输出

![演示动图](docs/demo.gif)

### 控制台界面

控制台已实现为服务端渲染的 Jinja2 页面（`reelagent_cloud/web/`），
**未归档截图**。以下路径对应真实路由，部署后可直接截图：

| 页面 | 占位路径 | 对应路由 |
|---|---|---|
| 落地页 | `docs/screenshots/landing.png` | `GET /` |
| 定价页 | `docs/screenshots/pricing.png` | `GET /pricing` |
| 仪表盘 | `docs/screenshots/dashboard.png` | `GET /app` |
| 新建项目 | `docs/screenshots/project-new.png` | `GET /app/projects/new` |
| 生成中 | `docs/screenshots/project-running.png` | `GET /app/projects/{id}` |
| Studio | `docs/screenshots/studio.png` | `GET /app/projects/{id}/studio` |
| 用量与账单 | `docs/screenshots/usage.png` | `GET /app/usage` |

见 [`docs/screenshots/`](docs/screenshots/)（目录待创建）。

> ℹ️ CSP 禁止内联脚本（`script-src 'self'`），因此所有前端 JS 都在
> `web/static/js/app.js` 里。新增界面时不要把脚本内联到模板。

---

## 这是什么 / 解决什么问题

### 场景一：做号的人，一天要出三条片

小林在做一个悬疑短故事账号，日更。他的工作流曾经是：

1. 写分镜脚本（1 小时）
2. 逐个镜头写 prompt、调模型、挑图、重生成（3～4 小时）
3. 找配音、对时长（40 分钟）
4. 用剪辑软件拼接、加字幕（1 小时）

**最痛苦的不是任何单一步骤，而是第 2 步里反复出现的同一件事**：
他要在每个镜头的 prompt 里把主角的外貌重写一遍，而且
**模型每次给的脸都不一样**。第 3 个镜头的主角和第 7 个镜头的主角
看起来像两个人。改到第 12 次的时候，他放弃了那条片子。

**ReelAgent 做的事**：他把故事文本贴进去，点生成。
流水线会先为「主角」这个线索生成一张**基准设定图**和一段
**只描述稳定特征的外观文本**（发型、五官、服装材质，不含动作和光线），
然后每个镜头生成时**同时**把基准图作为参考图、
把外观文本原样嵌进 prompt。

结果是同一个人在整条片子里还是同一个人。
更重要的是——**这件事是被度量的**：
`clue_reference_coverage` 与 `prompt_clue_fidelity` 两个指标
会告诉他「一致性约束有没有真的被传下去」，
`cross_shot_clue_similarity` 会告诉他「画面之间的相似度是多少」。
他不用靠肉眼判断，也不用在改了 prompt 之后担心是不是引入了回归。

### 场景二：一个 5 人小团队，要批量交付

一个做企业宣传片的小工作室接了 20 条片子的活。他们的痛点是：

- **成本不可控**。视频生成是最贵的环节，一条 12 镜的片子如果全用最高档位，
  成本会失控；但如果全用低档位，关键镜头质量不够。
- **任务会挂**。一条片子跑 8 分钟，中途供应商超时、进程被重启、
  机器 OOM——任何一次中断都要从头再来，重复付费。
- **钱要对得上**。客户按条付费，他们必须知道每条片子实际花了多少，
  不能只给一个总数。

**ReelAgent 做的事**：

- **模型路由**：按难度打分（有人物线索 +0.22、首镜 +0.10、
  特写 +0.10、已失败过 +0.12…）动态选档位。
  难度低的镜头走 `draft` 档——**跳过视频模型，零成本**。
  预算耗尽时强制降档。所有决策与「跳过几次、省了多少钱」都记在
  路由报告里。
- **断点恢复**：每个阶段与每个镜头都写 checkpoint。
  恢复时**不信任数据库里的状态字段**，而是逐个核对产物文件是否真实存在，
  因此「关键帧还在、片段被删」这类部分损坏也能正确处理——
  只重做缺失的部分。**Ctrl-C 是安全的。**
- **额度账本**：组织余额是一个快照，`ledger_entries` 是只追加的事实。
  两者同一事务更新。任何时刻跑一次对账，都必须满足
  `SUM(账本) == 余额`——不等就是有 bug。
  每一笔消耗都带 `balance_after`，所以「第 3 笔扣了 420 点，
  扣完剩 1580 点」这种话是可以逐条对出来的。

### 场景三：要把它接进自己的系统

一个客户的内部工具需要「输入文案 → 拿到视频链接」的能力。
他们不想用网页，要 API。

**ReelAgent 做的事**：开放 `/api/v1/*`，支持 `Idempotency-Key`
（客户端重试不会重复扣费）、统一的错误码（按 `code` 分支而不是匹配文案）、
按 API Key 独立限流。**并且 API Key 被刻意限制在「读写项目」范围内**
——即使它泄漏在 CI 日志里，攻击者也无法改套餐或踢人。

---

## 核心能力

### 1. 跨镜头一致性（双路注入 + 可度量）

| 机制 | 实现 |
|---|---|
| **视觉锚点** | 为每条线索（角色 / 场景 / 道具）生成一张基准图 |
| **稳定外观文本** | LLM 生成 `appearance`，**只描述不随镜头变化的特征**（禁止写动作与光线） |
| **双路注入** | 图像请求带基准图作为参考图（`ref_images`）+ prompt 里原样嵌入 `appearance` |
| **确定性兜底** | LLM 调用失败时走本地兜底 prompt，**约束仍然被传下去** |

**四个度量指标**（全部可自动计算、可回归、零成本）：

| 指标 | 度量对象 |
|---|---|
| `clue_reference_coverage` | 基准图注入覆盖率（**编排逻辑**） |
| `prompt_clue_fidelity` | prompt 中线索外观的保真度（**编排逻辑**） |
| `cross_shot_clue_similarity` | 同线索跨镜头画面相似度（生成结果） |
| `palette_stability` | 相邻关键帧色调稳定性（生成结果） |

> 「**无数据」不等于「满分」**：没有可度量对象时指标返回 `None` 并显示 `N/A`，
> 而不是伪装成 `1.0`。早期版本这里返回 1.0，导致一致性机制完全失效时
> 门禁照样通过——这是项目中最值得记住的教训。

### 2. 断点恢复（三层保障）

| 层级 | 机制 |
|---|---|
| **阶段级** | 五个阶段各自写 checkpoint，`resume` 跳过已完成阶段 |
| **镜头级** | 每个镜头在 `keyframe_done` / `clip_done` / `audio_done` 三个点落盘 |
| **产物校验** | 恢复时逐字段核对产物文件是否真实存在，**按实际存在的产物重算状态** |

```python
kf    = _keep(prev.keyframe_path)     # 文件不在 -> 视为没有，返回 None
clip  = _keep(prev.clip_path)
audio = _keep(prev.audio_path)
# 状态按"实际存在的产物"重算，而不是沿用 DB 里的乐观状态
```

这样能正确处理**部分损坏**：关键帧还在、片段被删 → 只重做片段，
不浪费已花的钱。回退行为写入 `director.checkpoint_repaired` span，留下可观测痕迹。

### 3. 质量评测门禁（双轨）

| 轨道 | 内容 | 特性 |
|---|---|---|
| **确定性指标** | 一致性 4 项 + 节奏统计 + Wilson 置信下界 | 零成本、可复现、进 CI |
| **LLM-as-Judge** | 叙事连贯性 / 视觉一致性 / 节奏 / prompt 忠实度四维 rubric | 懂语义，会漂移 |

两者合成布尔门禁，阈值随结果一起返回（自解释）：

| 条件 | 阈值 |
|---|---|
| `prompt_clue_fidelity` | ≥ 0.95 |
| `clue_reference_coverage` | ≥ 0.90 |
| `palette_stability` | ≥ 0.50 |
| `judge.overall` | ≥ 3.0 |

「**一致性策略未生效**」（没有任何镜头绑定到有基准图/有外观描述的线索）
被单独识别为最严重的失败类别——它意味着机制根本没被用起来，
而不是「效果不够好」。

### 4. 模型路由与成本

```
难度分 = 0.30 基础分
       + 有人物线索（一致性风险高）      +0.22
       + 元素多（≥3 条线索）             +0.08
       + 首镜（要立住世界观）            +0.10
       + 特写/近景（对细节敏感）          +0.10
       + 有台词（对口型要求）            +0.08
       + 长镜头（≥4s，更易崩）           +0.06
       + 已经失败过（该升级了）          +0.12
```

| 档位 | 行为 |
|---|---|
| `draft`（难度 < 0.45） | **跳过视频模型**，用本地运动合成 → **零成本** |
| `standard`（0.45 ~ 0.66） | 调用标准视频模型 |
| `premium`（≥ 0.66） | 调用高保真视频模型 |

预算耗尽时强制降到 `draft`；剩余预算不足以支撑多镜 `premium` 时降级为 `standard`。
「跳过次数」与「预估节省金额」都记录在 `routing` 报告里。

### 5. 多租户与额度账本

| 能力 | 实现 |
|---|---|
| **租户隔离** | 除 `users` 外所有表带 `org_id`；查询层强制过滤；存储 key 前缀校验 |
| **额度记账** | 只追加账本 + 余额快照双写，同事务更新，带 `balance_after` |
| **并发安全** | `SELECT ... FOR UPDATE` 行级锁 |
| **幂等** | 账本 / 任务 / Webhook 三处唯一约束 |
| **对账** | `reconcile()`：`SUM(账本) == 余额`，返回 `drift` |
| **余额守卫** | 显式检查 + 数据库 CHECK 约束 `credits_balance >= 0` |
| **两段式扣费** | 预扣 → 按实际成本多退少补；失败/取消全额退还 |

### 6. 生产级任务队列

| 能力 | 实现 |
|---|---|
| **队列** | Postgres `FOR UPDATE SKIP LOCKED`，与业务写入同事务，**无需外部 broker** |
| **租约** | `lease_expires_at` + 心跳续约；worker 崩溃后租约过期即可被重新领取，无需故障检测服务 |
| **回收** | worker 每 120 秒把过期租约的 `running` 任务打回 `failed` |
| **幂等** | `uq_job_org_idempotency(org_id, idempotency_key)` |
| **优先级** | `ORDER BY priority DESC, run_after ASC`，套餐 0/1/2/3，单镜重跑 5 |
| **退避** | 失败重试 `min(30 × 2^(attempts-1), 600)` 秒 |
| **取消** | `pending` 立即终结并退款；`running` 通过心跳把取消信号传给引擎 |
| **优雅停机** | SIGTERM 后停止领新任务 → 等在跑的做完 → 超时才强退 |

### 7. 可观测

- **引擎侧**：SQLite span 表，按名称聚合出耗时归因
  （`python -m reelagent spans <project_id>`）。
- **云层侧**：结构化日志（JSON，**处理器层面自动脱敏**）
  + 19 个 Prometheus 指标 + 请求上下文（`request_id` 贯穿日志与错误响应）。
- **探针语义区分**：`/livez`（不碰外部依赖）/ `/readyz`（数据库 + 存储真实读写）/
  `/health`（人类可读汇总）。
- **标签基数可控**：HTTP 指标用路由模板而非真实路径。

### 8. 安全

| 能力 | 实现 |
|---|---|
| **三种身份** | 匿名 / 浏览器会话 / API Key，能力递增且边界清晰 |
| **API Key 降权** | **不能**管计费与成员，角色强制降级为 `member`，不继承创建者权限 |
| **会话代际** | 改密或登出所有设备时 +1，所有已签发 Cookie 立即失效 |
| **CSRF** | 双提交 Cookie + 常数时间比较；表单再叠一层隐藏字段 |
| **口令** | Argon2id（OWASP 推荐档）+ 强度策略 + 登录失败锁定 |
| **API Key** | 256 位随机串，只存 `SHA-256(pepper + key)`，明文仅创建时返回一次 |
| **BYOK 密钥** | Fernet 可逆加密，任何接口都不明文返回 |
| **越权检测** | 跨租户文件访问写 `cross_tenant_file_access_blocked` 告警日志 |
| **安全头** | CSP 不使用 `unsafe-inline` 脚本、`frame-ancestors 'none'`、HSTS、`nosniff` 等 |

---

## 架构

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

**依赖方向严格单向**：`api/` → `services/` → 引擎。
引擎从不反向依赖云层。唯一的跨界契约是
`services/pipeline.py`（云层 → 引擎）与 `Pipeline.run()` 的两个宿主回调
（`on_stage` 上报进度、`should_cancel` 响应取消）。

详细的架构说明（14 张表的职责与索引、任务生命周期时序、双记账设计、
一致性方案）见 **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**。
决策理由见 **[docs/adr/](docs/adr/)**。

---

## 快速开始

### 前置要求

| 项 | 要求 |
|---|---|
| Python | **≥ 3.11**（`pyproject.toml` 的 `requires-python`） |
| ffmpeg | **可选**。缺失时自动降级为 GIF + WAV + 分镜九宫格，功能仍完整 |
| API Key | **不需要**。默认 mock 模式会产出真实的 PNG / 多帧 GIF / WAV |

### 路径一：引擎（零配置，clone 即跑）

```bash
git clone <repo-url> reel-agent
cd reel-agent

python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e .              # 引擎只需要 numpy + Pillow

# 用内置样例跑通全流程（7 个镜头，端到端约 17 秒）
python -m reelagent demo
```

产物落在 `workspace/projects/<项目ID>/final/`：
成片（`final.mp4`，无 ffmpeg 时是 `final.gif`）、音轨、分镜九宫格、
`manifest.json`。

**常用命令**：

```bash
python -m reelagent demo                       # 内置样例，一键跑通
python -m reelagent run <文本|.txt>            # 新建项目并生成
python -m reelagent resume <项目ID>            # 从断点继续（不会重复生成）
python -m reelagent status <项目ID>            # 阶段 / 镜头 / 成本
python -m reelagent spans <项目ID>             # 链路追踪与耗时归因
python -m reelagent eval <项目ID>              # 只重跑评测
python -m reelagent report <项目ID>            # 导出完整报告 JSON
python -m reelagent list                       # 项目列表
python -m reelagent tools                      # 已注册工具（MCP 兼容结构）
python -m reelagent config                     # 当前生效配置
```

**中途 Ctrl-C 是安全的**：每个阶段与每个镜头都会写 checkpoint，
`resume` 会从断点继续。

**风格预设**：`cinematic`（电影感，默认）· `anime`（日式动画）·
`cyberpunk`（赛博朋克）· `ink`（水墨）· `realistic`（写实记录）

### 路径二：本地开发（API + worker + SQLite）

```bash
pip install -e ".[dev]"

# 终端 1：API 服务（非生产环境会自动建表）
reelagent-server --host 127.0.0.1 --port 8000 --reload

# 终端 2：worker
reelagent-worker --once              # 只跑一个任务（调试）
reelagent-worker --concurrency 2     # 常驻
```

打开 <http://127.0.0.1:8000/docs> 看 OpenAPI 文档。

> ℹ️ 控制台页面在 `reelagent_cloud/web/templates/` 与 `web/static/`
> 齐全的前提下可用。本机 `--reload` 起服务即可访问
> `http://127.0.0.1:8000/`（落地页）与 `/app`（控制台）。
> JSON API 与 `/docs` 同样可用。

### 路径三：Docker Compose（一条命令起全套）

仓库已提供 `Dockerfile`、`docker-compose.yml`（含 `migrate` 服务）、
`docker-compose.dev.yml`（本地依赖：Postgres + Redis + MinIO）与 `Makefile`。

```bash
# 生产形态：Postgres + Redis + migrate → api → worker
export REEL_SECURITY__SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
export ARK_API_KEY=...  DEEPSEEK_API_KEY=...  DASHSCOPE_API_KEY=...

make docker-up            # 或 docker compose up -d
make docker-logs          # 跟踪所有服务日志

# 验证
curl -fsS http://127.0.0.1:8000/readyz | jq
```

```bash
# 只起本地依赖，宿主上裸跑 Python（开发用）
make dev                  # docker compose -f docker-compose.dev.yml up -d
make api                  # 本地 API（热重载）
make worker               # 本地 worker
```

完整的变量说明、生产检查清单与运维步骤见
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

---

## REST API 示例

完整参考见 **[docs/API.md](docs/API.md)**（含全部端点、字段、错误码与限流规则）。

### 流程概览

```bash
export BASE="https://reelagent.example.com"
export KEY="reel_a1b2c3d4_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
```

#### ① 预估成本（可选）

```bash
curl -s -X POST "$BASE/api/v1/credits/quote?shots=6" \
  -H "Authorization: Bearer $KEY" | jq
```

```json
{
  "estimated_credits": 780,
  "breakdown": {"image": 150, "video": 585, "tts": 45},
  "balance": 14820,
  "affordable": true,
  "max_shots": 12
}
```

#### ② 创建项目并开始生成

```bash
curl -s -X POST "$BASE/api/v1/projects" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-1732060800" \
  -d '{
    "seed_text": "深夜的便利店只剩下最后一盏灯。林晚把湿透的伞收在门口，走向货架。她在最底层找到一盒过期牛奶，生产日期是三年前的今天。收银台后的老人抬起头，说这盒牛奶他等了很久。",
    "title": "深夜便利店",
    "style": "cinematic",
    "aspect_ratio": "9:16",
    "shots": 6
  }' | jq
```

```json
{
  "project": {
    "id": "proj_01M4Z76RZGNWY5TB0NEE2MTZYV",
    "title": "深夜便利店",
    "status": "queued",
    "style": "cinematic",
    "aspect_ratio": "9:16",
    "total_shots": 6,
    "finished_shots": 0,
    "duration_seconds": 0.0,
    "credits_spent": 780,
    "created_at": "2025-11-20T03:14:07.123456+00:00",
    "updated_at": "2025-11-20T03:14:07.123456+00:00"
  },
  "job": {
    "id": "job_01M4Z76S1P2Q3R4S5T6V7W8X9Y",
    "kind": "generate_project",
    "status": "pending",
    "stage": "queued",
    "progress": 0.0,
    "attempts": 0,
    "max_attempts": 3,
    "error": null,
    "error_code": null,
    "result": {},
    "created_at": "2025-11-20T03:14:07.123456+00:00",
    "started_at": null,
    "finished_at": null
  },
  "quote": {
    "estimated_credits": 780,
    "breakdown": {"image": 150, "video": 585, "tts": 45}
  },
  "idempotency_key": "demo-1732060800"
}
```

#### ③ 轮询任务状态

```bash
curl -s "$BASE/api/v1/jobs/job_01M4Z76S1P2Q3R4S5T6V7W8X9Y" \
  -H "Authorization: Bearer $KEY" | jq '{status, stage, progress}'
```

```json
{"status": "running", "stage": "generating", "progress": 0.55}
```

`stage` 的取值与进度：

| `stage` | 中文 | `progress` |
|---|---|---|
| `queued` | 排队中 | 0.0 |
| `planning` | 拆解剧本 | 0.08 |
| `assets` | 生成角色与场景 | 0.18 |
| `generating` | 逐镜生成 | 0.55 |
| `evaluating` | 质量评测 | 0.82 |
| `rendering` | 合成成片 | 0.93 |
| `done` | 完成 | 1.0 |

任务终态：`succeeded` / `failed`（可重试）/ `dead`（需人工介入）/
`cancelled`。

**成功后的 `result`（含结算明细）**：

```json
{
  "artifacts": ["org/org_.../projects/proj_.../final/final.gif"],
  "shots_total": 6,
  "shots_finished": 6,
  "evaluation_gate": true,
  "settlement": {
    "reserved": 780,
    "actual": 731,
    "delta": -49,
    "refunded": 49
  }
}
```

#### ④ 取成片

```bash
# 拿到成片入口（优先 MP4，其次 GIF）
curl -s "$BASE/api/v1/projects/proj_01M4Z76RZGNWY5TB0NEE2MTZYV/final" \
  -H "Authorization: Bearer $KEY" | jq
```

```json
{
  "available": true,
  "id": "art_01M4Z77FINAL0000000000001",
  "kind": "gif",
  "content_type": "image/gif",
  "size_bytes": 4823040,
  "url": "/api/v1/artifacts/art_01M4Z77FINAL0000000000001/download?disposition=inline"
}
```

```bash
# 下载
curl -sOJ "$BASE/api/v1/artifacts/art_01M4Z77FINAL0000000000001/download?disposition=attachment" \
  -H "Authorization: Bearer $KEY"
```

#### ⑤ 看评测结果与成本

```bash
curl -s "$BASE/api/v1/projects/proj_01M4Z76RZGNWY5TB0NEE2MTZYV" \
  -H "Authorization: Bearer $KEY" \
  | jq '{gate: .evaluation.gate, metrics: .evaluation.metrics, cost: .cost.total_usd}'
```

```json
{
  "gate": {
    "passed": true,
    "failures": [],
    "thresholds": {
      "prompt_clue_fidelity": 0.95,
      "clue_reference_coverage": 0.9,
      "palette_stability": 0.5,
      "judge_overall": 3.0
    }
  },
  "metrics": {
    "overall": 0.9246,
    "palette_stability": 1.0,
    "cross_shot_clue_similarity": 0.6986,
    "clue_reference_coverage": 1.0,
    "prompt_clue_fidelity": 1.0,
    "consistency_strategy_active": true
  },
  "cost": 0.8123
}
```

#### ⑥ 确认扣费（用户可自证）

```bash
curl -s "$BASE/api/v1/credits?limit=5" -H "Authorization: Bearer $KEY" | jq
```

```json
{
  "balance": 14089,
  "items": [
    {
      "id": "led_...",
      "kind": "refund",
      "amount": 49,
      "balance_after": 14089,
      "description": "按实际用量退还 49 点（job_...）",
      "project_id": "proj_01M4Z76RZGNWY5TB0NEE2MTZYV",
      "job_id": "job_01M4Z76S1P2Q3R4S5T6V7W8X9Y",
      "created_at": "2025-11-20T03:22:41.654321+00:00"
    },
    {
      "id": "led_...",
      "kind": "consume",
      "amount": -780,
      "balance_after": 14040,
      "description": "预扣：生成《深夜便利店》",
      "project_id": "proj_01M4Z76RZGNWY5TB0NEE2MTZYV",
      "job_id": "job_01M4Z76S1P2Q3R4S5T6V7W8X9Y",
      "created_at": "2025-11-20T03:14:07.123456+00:00"
    }
  ]
}
```

**因为每条都带 `balance_after`，用户可以自己验证
「上一条余额 + 本条金额 = 本条余额」。**

### 鉴权

```http
Authorization: Bearer reel_a1b2c3d4_XXXXXXXXXXXXXXXXXXXXXXXX
```

或：

```http
X-API-Key: reel_a1b2c3d4_XXXXXXXXXXXXXXXXXXXXXXXX
```

**API Key 的能力边界**：可以读写项目，**不能**管理计费与成员
（角色被强制降级为 `member`，不继承创建者的权限）。
理由：API Key 会出现在 CI、脚本、第三方集成里，泄漏概率远高于浏览器会话。

### 幂等

创建项目的接口支持 `Idempotency-Key` 请求头。
未提供时服务端用内容指纹（`auto-<sha256 前 40 位>`）兜底并在响应里回显。

> ⚠️ **已知缺口**：`POST /api/v1/projects` 读取并回显这个键，
> 但 `projects.create_project()` 并未接收它，因此
> **同一素材重复提交会创建两个项目、各扣一次预扣额度**。
> 客户端请自行用 `Idempotency-Key` 去重。
> 详见 [CHANGELOG 已知问题](CHANGELOG.md#一致性与安全问题)。

### 错误响应

**所有**错误（含 422 与 500）都是同一个结构：

```json
{
  "error": {
    "code": "insufficient_credits",
    "message": "额度不足：本次需要 420 点，当前可用 180 点",
    "details": {"required": 420, "available": 180}
  },
  "request_id": "a1b2c3d4e5f60718"
}
```

**请按 `error.code` 分支，不要匹配 `message`。** 报障时请提供 `request_id`。

主要错误码：

| `code` | HTTP | 何时出现 |
|---|---|---|
| `validation_failed` | 422 | 参数不合法（`details.fields` 列出字段错误） |
| `unauthorized` | 401 | 未登录或凭证无效 |
| `forbidden` | 403 | 权限不足 / 越权 / CSRF 失败 |
| `insufficient_credits` | 402 | 额度不足（`details.required` / `available`） |
| `rate_limited` | 429 | 触发限流（带 `Retry-After`） |
| `not_found` | 404 | 资源不存在**或不属于当前租户** |
| `invalid_state` | 409 | 当前状态不允许该操作 |
| `provider_failure` | 502 | 上游供应商调用失败 |
| `provider_circuit_open` | 503 | 供应商熔断中（`details.retry_after_seconds`） |
| `unsupported_operation` | 400 | 该功能在当前部署下不可用 |
| `internal_error` | 500 | 未预期异常（只给通用文案 + `request_id`） |

完整错误码表（含 `details` 结构）见
[docs/API.md 第 4 节](docs/API.md#4-错误码表)。

### 限流

| scope | 依据 | 限额 | 窗口 |
|---|---|---|---|
| `login` | IP | 10 | 300s |
| `signup` | IP | 5 | 3600s |
| `project_create` | 组织 | 60 | 3600s |
| `api_key` | API Key | 300 | 60s |

限流失败是 **fail-open**（限流器故障时放行而非拒绝）。

> ℹ️ `anonymous` 与 `user` 两个 scope 在配置里定义了但**当前没有调用点**，
> 因此只有登录、注册、创建项目三个端点受限流保护。

### Webhook

Stripe 回调端点是 `POST /webhooks/stripe`（不在 OpenAPI 文档里，
靠 `Stripe-Signature` 验签，**必须用原始请求体**）。

订阅这 6 个事件：`checkout.session.completed`、
`customer.subscription.created` / `.updated` / `.deleted`、
`invoice.paid`、`invoice.payment_failed`。

详见 [docs/API.md 第 15 节](docs/API.md#15-webhook)。

---

## 配置说明

### 命名规则

`Settings` 的 `env_prefix="REEL_"`、`env_nested_delimiter="__"`，因此：

| 形式 | 示例 |
|---|---|
| 嵌套配置 | `REEL_<SECTION>__<FIELD>` → `REEL_DATABASE__URL` |
| 更深嵌套 | `REEL_PROVIDERS__VIDEO__API_KEY` |
| 顶层配置 | `REEL_<FIELD>` → `REEL_ENV`、`REEL_BASE_URL` |

会依次读 `.env` 与 `.env.local`（后者覆盖前者），
**已存在的真实环境变量不会被 `.env` 覆盖**。

### 最小可用配置（本地）

```bash
REEL_ENV=dev
REEL_DATABASE__URL=sqlite+aiosqlite:///./workspace/reelagent.db
```

其余全部有默认值。**不需要任何 API Key**（默认走 mock 供应商）。

### 生产必需配置

```bash
REEL_ENV=prod
REEL_BASE_URL=https://reelagent.example.com
REEL_SECURITY__SECRET_KEY=<48 字节随机串>
REEL_SECURITY__HTTPS_ONLY=true
REEL_SECURITY__ALLOWED_HOSTS='["reelagent.example.com"]'
REEL_DATABASE__URL=postgresql+asyncpg://user:pw@host:5432/reelagent
REEL_PROVIDERS__VIDEO__API_KEY=<视频供应商 Key>
```

**六项启动校验任一不满足，进程直接拒绝启动**（「宁可起不来，
也不要带病上线」）：

| 校验 | 失败消息 |
|---|---|
| `SECRET_KEY` 非空 | `REEL_SECURITY__SECRET_KEY 未配置` |
| 非 SQLite | `生产环境不应使用 SQLite，请配置 REEL_DATABASE__URL` |
| `HTTPS_ONLY=true` | `生产环境必须开启 REEL_SECURITY__HTTPS_ONLY=true` |
| `ALLOWED_HOSTS` 不含 `*` | `生产环境必须显式配置 REEL_SECURITY__ALLOWED_HOSTS` |
| `BASE_URL` 是 https | `生产环境 REEL_BASE_URL 必须是 https` |
| 有视频供应商 Key | `未配置任何视频供应商 Key（REEL_PROVIDERS__VIDEO__API_KEY），产品将无法产出成片` |

### 接入真实模型

```bash
cp .env.example .env
```

```ini
REEL_MODE=live
REEL_LLM_BASE_URL=https://api.deepseek.com/v1
REEL_LLM_API_KEY=sk-xxx
REEL_LLM_MODEL=deepseek-chat

REEL_IMAGE_PROVIDER=ark
REEL_IMAGE_API_KEY=xxx          # 火山方舟，Seedream
REEL_VIDEO_PROVIDER=ark
REEL_VIDEO_API_KEY=xxx          # 火山方舟，Seedance
REEL_TTS_API_KEY=xxx            # 阿里百炼 Qwen3-TTS
```

已内置适配器：**火山方舟**（Seedream 图像 / Seedance 视频，
异步任务 + 轮询）、**OpenAI 兼容**（图像 / TTS）。
其它供应商参照 `reelagent/providers/ark.py` 实现即可，
**新增一个 provider 不影响任何 Agent 代码**。

**逐项降级**：live 模式下若某个供应商缺少 Key，
只会把那一项降级为 mock 并打印提示，流水线仍然跑完，不会中途炸掉。

> ℹ️ 仓库提供两份环境变量样例：
> `.env.example`（开发：引擎变量 + 完整 cloud 层变量）与
> `.env.production.example`（生产形态）。
> 逐项说明见
> **[docs/DEPLOYMENT.md 第 2 节](docs/DEPLOYMENT.md#2-环境变量完整清单)**。

### 环境变量速查

| 我想调整… | 改这个 |
|---|---|
| 运行模式（mock / live） | `REEL_MODE` |
| API 进程并发能力 | `REEL_DATABASE__POOL_SIZE` + `--workers` |
| 生成吞吐 | `REEL_WORKER__CONCURRENCY` + worker 副本数 |
| 单任务内并行度 | `REEL_WORKER__SHOT_CONCURRENCY` |
| 任务多久被判为「worker 崩了」 | `REEL_WORKER__LEASE_SECONDS`（默认 900s） |
| 优雅停机等的时长 | `REEL_WORKER__SHUTDOWN_GRACE_SECONDS`（默认 60s） |
| 单片预算 | `REEL_JOB_BUDGET_USD`（默认 2.0） |
| 单项目镜头上限 | `REEL_MAX_SHOTS_PER_PROJECT`（默认 12） |
| 单项目基准图上限 | `REEL_MAX_BASE_IMAGES`（默认 6） |
| 用户被扣多少点 | `REEL_BILLING__MARKUP_MULTIPLIER`（3.0）× `REEL_BILLING__CREDITS_PER_USD`（100） |
| 限流松紧 | `REEL_RATE_LIMIT__*` |
| 日志格式 | `REEL_OBSERVABILITY__LOG_FORMAT`（`json` / `console`） |
| 对象存储 | `REEL_STORAGE__BACKEND`（`local` / `s3`） |

完整清单（含默认值与说明）见
**[docs/DEPLOYMENT.md 第 2 节](docs/DEPLOYMENT.md#2-环境变量完整清单)**。

### 计费口径

```
点数 = max(1, ceil(供应商成本USD × markup_multiplier × credits_per_usd))
默认：1 USD 供应商成本 ≈ 300 点
```

**对外只展示「点数」，不暴露供应商真实成本结构。**

| 套餐 | 价格 | 月度点数 | 镜头上限 | 并发 |
|---|---|---|---|---|
| 体验版 | ¥0 | 500 | 6 | 1 |
| 创作者版 | ¥49/月 | 3,000 | 8 | 1 |
| 专业版 | ¥199/月 | 15,000 | 12 | 3 |
| 工作室版 | ¥699/月 | 60,000 | 12 | 8 |

定价与限制的**单一事实来源**是 `reelagent_cloud/domain/plans.py`
（避免「官网写一个价、后端校验另一个价」）。
注册赠送 500 点。失败任务**全额退还**。

### Stripe（可选）

未配置时产品降级为**纯额度模式**：注册赠送额度可正常使用，
没有充值入口，因此自托管部署无需依赖 Stripe。

启用时：

```bash
REEL_BILLING__ENABLED=true
REEL_BILLING__SECRET_KEY=sk_...
REEL_BILLING__WEBHOOK_SECRET=whsec_...
REEL_BILLING__PRICE_STARTER=price_...
REEL_BILLING__PRICE_PRO=price_...
REEL_BILLING__PRICE_STUDIO=price_...
```

Webhook 端点：`https://<你的域名>/webhooks/stripe`。
详见 [docs/DEPLOYMENT.md 第 6 节](docs/DEPLOYMENT.md#6-stripe-配置)。

---

## 项目结构

```
reel-agent/
├── README.md                       本文件（产品级门面）
├── CHANGELOG.md                    版本历史与已知问题
├── CONTRIBUTING.md                 开发环境、规范、PR 清单
├── SECURITY.md                     安全模型与漏洞报告
├── CODE_OF_CONDUCT.md              行为准则
├── LICENSE                         Apache-2.0
├── pyproject.toml                  依赖、ruff / mypy / pytest / coverage 配置
├── .env.example                    引擎层环境变量示例
│
├── reelagent/                      ★ 引擎：纯计算，零 Web 依赖
│   ├── schema.py                   数据模型（Project / Script / Scene / Shot / Clue）
│   ├── config.py                   引擎配置与五种风格预设
│   ├── runtime.py                  依赖装配 + JSON 稳健解析
│   ├── routing.py                  难度打分 / 模型路由 / 成本估算
│   ├── utils.py                    stable_seed（跨进程确定的随机种子）
│   ├── cli.py                      命令行入口
│   ├── llm/                        LLM 抽象：base / mock / openai_compat / prompts
│   ├── providers/                  图像·视频·TTS 供应商：base / mock / ark
│   ├── harness/                    运行时底座：state（checkpoint）· tracing（span）
│   │                               registry（工具）· context（token 预算）· executor（重试）
│   ├── agents/                     planner · artdirector · director · evaluator
│   ├── eval/                       跨镜头一致性度量
│   ├── render/                     ffmpeg / GIF 合成 · 分镜九宫格
│   ├── tools/                      媒体工具注册（MCP 兼容 JSON Schema）
│   └── pipeline/                   五阶段状态机与断点恢复
│
├── reelagent_cloud/                ★ 云端产品层
│   ├── config.py                   产品配置（嵌套环境变量 + 生产启动校验）
│   ├── server.py                   reelagent-server 入口
│   ├── worker.py                   reelagent-worker 入口
│   ├── deps.py                     共享基础依赖（数据库会话、客户端 IP）
│   ├── api/                        应用工厂 · 中间件 · 错误映射 · 限流 · 路由
│   │   ├── app.py                  lifespan（配置 → 引擎 → 迁移检查 → 中间件）
│   │   ├── middleware.py           请求上下文 + 安全响应头
│   │   ├── errors.py               领域异常 → 统一 JSON 错误体
│   │   ├── ratelimit.py            Redis / 进程内限流（fail-open）
│   │   └── routes/                 pages · auth · console · api · files · billing · system
│   ├── security/                   tokens（会话/API Key/CSRF/BYOK）· deps（授权）
│   │                               passwords（Argon2id）
│   ├── services/                   用例层
│   │   ├── accounts.py             注册 / 登录 / 成员 / API Key
│   │   ├── projects.py             项目用例 + 引擎产出同步
│   │   ├── jobs.py                 任务队列（SKIP LOCKED / 租约 / 结算）
│   │   ├── credits.py              额度账本（双记账 / 对账）
│   │   ├── billing.py              Stripe 订阅与 Webhook
│   │   ├── pipeline.py             引擎桥接（短事务 / 线程池 / 产物归集）
│   │   ├── storage.py              对象存储抽象（local / S3 兼容）
│   │   ├── audit.py                审计日志
│   │   └── email.py                邮件发件箱
│   ├── domain/                     enums（状态机）· plans（套餐定价）· errors · ids（ULID）
│   ├── db/                         models（14 张表）· session · base
│   ├── workers/                    Worker 主循环（领取/心跳/回收/优雅停机）
│   ├── observability/              结构化日志 · Prometheus 指标 · 请求上下文
│   └── web/                        控制台界面：templates/（Jinja2）+
│                                   static/（css/js）+ 17 个模板过滤器
│
├── docs/
│   ├── ARCHITECTURE.md             分层架构 · 14 张表 · 时序 · 双记账 · 一致性方案
│   ├── DEPLOYMENT.md               三种部署路径 · 环境变量 · Stripe · 备份 · 升级回滚
│   ├── OPERATIONS.md               运维 Runbook（按症状组织，含可执行 SQL）
│   ├── API.md                      REST API 参考
│   ├── adr/                        架构决策记录（7 篇 + 索引）
│   ├── demo.gif                    引擎 CLI 演示动图
│   └── demo_storyboard.png         分镜九宫格
│
├── tests/
│   ├── test_core.py                33 个单元测试
│   ├── test_pipeline.py            15 个端到端测试
│   └── support.py                  临时目录管理
│
└── scripts/
    ├── smoke.py                    服务层冒烟（注册→账本→任务→领取→结算→对账）
    └── smoke_http.py               HTTP 层冒烟
```

---

## 技术选型与权衡

### 为什么任务队列用 Postgres `FOR UPDATE SKIP LOCKED` 而非 Celery

**选它的理由**：

1. **事务一致性天然成立。** 用户点「开始生成」时，同一个事务里要完成：
   校验套餐限制 → 预扣额度 → 建项目 → 入队任务。
   用外部 broker 时，「业务提交了但入队失败」或「入队了但业务回滚」
   都需要额外的 transactional outbox 模式来兜底——那是额外的一整套机制。
2. **少一个组件。** 自托管部署只需要 Postgres（+ 可选 Redis 用于限流），
   不需要 Redis/RabbitMQ 常驻做 broker。
3. **`SKIP LOCKED` 天然并发。** 多 worker 无锁竞争，拿不到锁的直接跳过这条
   去抢下一条。加 worker 就是加副本，不需要重新分片。
4. **可靠性三件套自己实现得更贴合**：
   - **租约**：worker 崩溃后租约过期即可被重新领取，**不需要故障检测服务**。
   - **心跳**：长任务周期性续约，同时把「用户取消」翻译成引擎可见的信号。
   - **幂等键**：唯一约束保证同一请求只创建一个任务。

**代价（明确写在代码注释里）**：

> 吞吐受数据库限制。本项目「一条成片跑几分钟」的量级完全够用；
> 若将来要到每秒上千任务，再换成专用 broker 即可（接口已抽象在上述函数里）。

另外：SQLite 方言会忽略 `FOR UPDATE`，本地单 worker 场景仍然正确，
但多 worker + SQLite 不是受支持的组合。

完整讨论见 [ADR 0001](docs/adr/0001-postgres-skip-locked-queue.md)。

### 为什么用 dataclass 而不是 pydantic 做引擎模型

`reelagent/schema.py` 的 docstring：

> 刻意采用 dataclass 而非 Pydantic：核心零第三方依赖，任何机器 clone 即跑，
> 同时 `to_dict / from_dict` 保证了完整的 JSON 往返能力——这是断点恢复的基础。

| | dataclass（当前选择） | pydantic |
|---|---|---|
| 依赖 | 零（标准库） | 第三方 |
| clone 即跑 | ✅ | 需要先装依赖 |
| 自动校验 | ❌ | ✅ |
| 序列化性能 | 手写 `to_dict` | 优化过的 |
| 与云层模型统一 | ❌（两套结构） | ✅ |

**在数据模型简单时这笔交易划算**。`Serializable.from_dict` 还刻意做了
「只接受已知字段」的容错，因此旧版本 JSON 在新版本代码里仍能加载：

```python
@classmethod
def from_dict(cls, data: dict[str, Any]):
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})
```

**注意**：云层用 pydantic（配置与校验），引擎用 dataclass——
这是**有意的分裂**，不是不一致。

### 为什么引擎与云层严格分离

`reelagent/` **只能**依赖标准库 + `numpy` + `Pillow`，
不导入 FastAPI / SQLAlchemy / Stripe / Redis / pydantic / structlog。

**收益**：

1. **引擎可以被单机使用，也可以被服务化**。切换宿主不需要改任何 Agent 代码。
2. **引擎测试不需要数据库、不需要网络、不需要 API Key**。48 个引擎用例 clone 即跑
   （云层另有 164 个 pytest 用例，需要 `[dev]` 依赖）。
3. **失败域隔离**。计费/鉴权/限流的 bug 不会污染生成逻辑。

**代价**：需要一层桥接代码（`services/pipeline.py`），
而它要解决四个非平凡的问题：

| 问题 | 对策 |
|---|---|
| 一条成片跑几分钟，不能长期占用数据库连接 | **进出各开一个短事务**，中间长跑阶段完全不持有连接 |
| 引擎内部同步阻塞（PIL 渲染、urllib 调供应商） | `asyncio.to_thread()` 执行；工作线程通过 `run_coroutine_threadsafe` 回调主循环写进度 |
| 引擎在本地临时目录工作 | 跑完搬进对象存储并在 `artifacts` 表登记（含 sha256 与 size） |
| 预估额度与实际成本有偏差 | 用引擎汇总的真实成本 `settle()` 多退少补 |

完整讨论见 [ADR 0002](docs/adr/0002-engine-zero-web-dependency.md)。

### 为什么额度用「只追加账本 + 余额快照」双写

| 方案 | 可审计 | 读取速度 | `>= 0` 约束 |
|---|---|---|---|
| 只存余额 | ❌ 无从追查 | ✅ O(1) | ✅ |
| 只存账本 | ✅ | ❌ 每次聚合 | ❌（CHECK 不能跨表） |
| **双写（当前）** | ✅ | ✅ O(1) | ✅ |

**核心不变量**：

```
organizations.credits_balance == SUM(ledger_entries.amount) WHERE org_id = ?
```

两者在**同一事务内**更新，每次都留一条带 `balance_after` 的记录。
`reconcile()` 就是这个等式的实现，`drift != 0` 即存在 bug。

**唯一的写入路径** `_apply()`：

```python
# 1. 幂等短路
# 2. 行级排他锁（SELECT ... FOR UPDATE）
# 3. 余额守卫（new_balance < 0 时抛 PaymentRequired）
# 4. 写账本 + 更新快照（同一事务）
# 5. 显式 flush（会话是 autoflush=False）
```

**关于第 5 步**：会话配置为 `autoflush=False`，
因此不显式 `flush()` 会让同一会话内后续的 `get_balance()`
读到数据库旧值——账本已是新值、余额还是旧值，等式当场破裂。
代码里为此写了明确注释。

**代价**：两处必须同步更新，存在不一致的可能。
缓解手段是「写入路径唯一 + 对账兜底」，
但**任何一次绕过 `_apply()` 的直接赋值都会破坏等式**。

完整讨论见 [ADR 0003](docs/adr/0003-append-only-credit-ledger.md)。

### 为什么 API Key 不能管理计费与成员

| | 浏览器会话 | API Key |
|---|---|---|
| 存放位置 | HttpOnly Cookie | CI 变量、`.env`、脚本、第三方连接器 |
| 泄漏概率 | 中 | **高** |
| 泄漏后检测 | 用户会立刻发现被登出 | 通常**毫无感知** |
| 使用场景 | 交互式，有人判断 | 自动化，无人在环 |

如果 API Key 有会话的权限，一次泄漏的后果是：**改订阅套餐、取消订阅、
把 owner 踢出组织、创建新的 API Key（自持久化）**。
这是财务与账号控制权的损失，不可逆且难以察觉。

因此 `require_role()` 的第一行就是硬拦截：

```python
if self.kind != "session":
    raise Forbidden("该操作需要使用浏览器登录（API Key 不具备管理权限）")
```

**并且 API Key 的角色被强制降级为 `member`**——
不继承创建者的角色，否则管理员离职前创建的 Key 会永久保留管理员能力。

完整讨论见 [ADR 0004](docs/adr/0004-api-key-cannot-manage-billing.md)。

### 为什么用 ULID 而不是 UUID4 做主键

`domain/ids.py` 的 docstring：

> 用 ULID（Crockford Base32，26 字符）而非 UUID4：
> - **单调递增**：按时间有序，做主键时 B-tree 插入局部性好，避免 UUID 的随机写放大
> - **可读**：带业务前缀（`proj_01H...`），日志中 URL 里一眼能看出类型
> - **无中心依赖**：不需要数据库自增或 Snowflake 服务

并且 `parse_ulid_timestamp()` 能从 ID 直接反解创建时间
（产物列表接口的 `created_hint` 字段就是这个值）。

### 其它取舍

| 取舍 | 选择 | 理由 |
|---|---|---|
| **mock 供应商产出真实媒体** | 产出真 PNG / 多帧 GIF / 时长匹配的 WAV，而不是假路径 | 否则 mock 模式会掩盖大量集成问题 |
| **确定性优先于多样性** | mock 的 LLM 是启发式而非随机 | 换来可复现与可写断言 |
| **不用内置 `hash()`** | `stable_seed()` 用 md5 | `hash()` 对 `str` 按进程随机化（`PYTHONHASHSEED`），会让同一输入两次运行画面不同，评测回归失去意义 |
| **枚举用 `VARCHAR + CHECK`** | `native_enum=False` | 加值不需要 `ALTER TYPE`（后者在 PG 里不能在事务块内执行） |
| **JSON 在 PG 上用 JSONB** | `JSON().with_variant(JSONB(), "postgresql")` | 可索引，同时保持 SQLite 兼容 |
| **日志脱敏在处理器层面做** | 正则擦除 + 递归深度限制 | 不依赖每个调用点自觉 |
| **限流 fail-open** | 后端故障时放行 | 「限流器本身挂了不应该让整个站点不可用」（有争议，理由写在注释里） |
| **审计写入不参与业务事务** | 单独提交并吞掉异常 | 业务成功但审计失败时，宁可丢一条审计也不回滚用户操作 |
| **HTTP 指标用路由模板** | `/api/projects/{id}` 而非真实路径 | 否则每个 ID 都会变成一条时间序列，把 Prometheus 打爆 |

---

## 测试

```bash
# 全部测试（引擎 + 云层）
pytest
pytest tests/test_pipeline.py -v
pytest --cov=reelagent --cov=reelagent_cloud --cov-report=term-missing

# 只跑引擎测试（标准库方式，不需要 pytest）
python -m unittest discover -s tests -t . -v

# 只跑单元测试（跳过 integration 标记）
pytest -m "not integration"
```

也可以用 Makefile：

```bash
make test        # 跑测试并输出覆盖率
make test-fast   # 只跑单元测试（跳过 integration）
make lint fmt typecheck
```

测试套件分两部分，框架不同（这是有意的）：

| 部分 | 文件 | 数量 | 框架 | 依赖 |
|---|---|---|---|---|
| **引擎测试** | `test_core.py`（33）、`test_pipeline.py`（15） | **48** | 标准库 `unittest` | **无**（clone 即跑，不需要 pytest） |
| **云层测试** | `test_cloud_credits.py`（27）、`test_cloud_http.py`（70）、`test_cloud_jobs.py`（33）、`test_cloud_security.py`（34） | **164** | `pytest` + `pytest-asyncio` + FastAPI `TestClient` | 需要 `pip install -e ".[dev]"` |
| | | **212** | | |

**引擎测试刻意不依赖 pytest**（`test_core.py` 的 docstring 说明了原因）：
这样「引擎可 clone 即跑」的承诺在测试层面也成立。
**云层测试**覆盖额度账本、HTTP 契约、任务队列与安全边界，
用 `tests/conftest.py` 提供的 fixture
（`client` / `authed_client` / `db` / `settings` / `csrf` / `org_id` 等）
驱动真实的 FastAPI 应用与临时数据库。

### 覆盖重点

**引擎侧**：

| 主题 | 断言内容 |
|---|---|
| **断点恢复** | 产物被删 → checkpoint 失效 → 重新生成（且**不重做完好**的镜头） |
| **部分损坏** | 关键帧保留、只重做片段 |
| **阶段失败** | 落到 `failed`、不吞错误、修复后可从断点续跑并恢复项目状态 |
| **确定性** | 两次运行指标完全一致；`stable_seed` 不受 hash 随机化影响 |
| **回归** | `attrs` 含 `kind` 键不再触发参数冲突；基准图必须真的生成 |
| **评测语义** | 「无可度量对象」返回 `None` 而非 `1.0` |
| **mock 供应商** | 产出的是真 PNG / 多帧 GIF / 时长匹配的 WAV |

**云层侧**：

| 主题 | 断言内容 |
|---|---|
| **额度账本** | 双记账对账（`SUM(账本) == 余额`）、幂等键、余额守卫、人工调整 |
| **HTTP 契约** | 注册/登录流程、表单校验、CSRF、会话 Cookie 属性、弱口令拒绝 |
| **API Key** | 认证可用、`X-API-Key` 头等价、readonly scope 无法建项目 |
| **租户隔离** | 跨组织访问被拒绝、自有产物可下载、越权尝试被记录 |
| **任务队列** | 领取/租约/心跳/回收/退避/结算/取消的状态机与幂等 |
| **安全** | 凭证明文不落库、日志脱敏、权限边界 |

### 确定性验证

```bash
PYTHONHASHSEED=7  python -m unittest discover -s tests -t .
PYTHONHASHSEED=99 python -m unittest discover -s tests -t .
# 两次的指标数值必须完全相同
```

### 代码规范

```bash
ruff check .            # lint（含安全规则 S）
ruff format --check .   # 格式
mypy reelagent reelagent_cloud
```

配置全部在 `pyproject.toml`：

| 工具 | 关键配置 |
|---|---|
| ruff | `line-length = 110`、`target-version = "py311"`、`select = [E,F,W,I,N,UP,B,C4,SIM,RUF,ASYNC,S]` |
| mypy | `python_version = "3.11"`、`check_untyped_defs`、`plugins = ["pydantic.mypy"]` |
| pytest | `testpaths = ["tests"]`、`asyncio_mode = "auto"`、markers `unit`/`integration`/`contract` |

### 冒烟脚本

```bash
python scripts/smoke.py          # 注册 → 账本 → 任务 → 领取 → 结算 → 对账
python scripts/smoke_http.py     # HTTP 层
```

### 实测数据（mock 模式）

> 数据来源：项目记录。复现方式：`python -m reelagent demo`。
> **这不是运营数据**，而是离线 mock 供应商下的端到端实测。

默认样例故事、7 个镜头，端到端约 **17 秒**：

| 指标 | 数值 |
|---|---|
| 综合分 | **0.9246** |
| 色调稳定性 | 1.0000 |
| 同线索跨镜头相似度 | 0.6986 |
| 基准图注入覆盖率 | **1.0000** |
| prompt 线索保真度 | **1.0000** |
| LLM 评审总分（5 分制） | 4.25 |
| CI 门禁 | 通过 |
| 成片时长 | 19.5s（7 镜） |
| 自动化测试 | **212 项**（引擎 48 + 云层 164） |

**可复现性**：上述数值在不同 `PYTHONHASHSEED` 下完全一致
（`PYTHONHASHSEED=7` 与 `=99` 结果相同），已在测试中锁定。

**怎么读这些数字**：

- 一致性与保真度达到 1.0 是**设计使然**——这两项度量的是编排层
  有没有把约束传下去，而注入逻辑是确定性的。它们**不是质量信号**。
- 真正依赖模型能力的是 `cross_shot_clue_similarity`。
  在 mock 下它主要反映背景色调差异，因此 0.6986 是合理值而非「效果不好」；
  换成真实图像模型后这个指标才有比较意义。

### 耗时归因示例

```bash
python -m reelagent spans proj_xxxxxxxxxx
```

```
名称                                        次数        总耗时        均值    失败
director.shot                             10      17.7s   1770.7ms     0
stage.render                               3      17.2s   5733.4ms     0
director.clip                              9      15.0s   1667.3ms     0
tool.generate_clip                         9      14.9s   1657.1ms     0   ← 瓶颈
tool.generate_keyframe                    14       1.0s     74.0ms     0
director.checkpoint_repaired               1       0.0s      0.0ms     0   ← 回退留痕
```

---

## 部署

**完整部署指南见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。**
**面向值班同学的排障手册见 [docs/OPERATIONS.md](docs/OPERATIONS.md)。**

### 三种部署路径

| 路径 | 适用 | 数据库 | 存储 | Redis |
|---|---|---|---|---|
| **A. 单机本地** | 开发、演示、单机创作 | SQLite | local | 不用 |
| **B. 云托管容器** | 小规模生产（推荐起点） | Postgres | S3 兼容 | 建议 |
| **C. Kubernetes** | 滚动更新、多副本、HPA | Postgres | S3 兼容 | 需要 |

### 生产环境要点

| 项 | 说明 |
|---|---|
| **进程** | API（`reelagent-server`）与 worker（`reelagent-worker`）用**同一个镜像**，只是启动命令不同 |
| **配置一致** | API 与 worker **必须**用完全相同的 `SECRET_KEY` 与存储配置 |
| **连接池** | `(POOL_SIZE + MAX_OVERFLOW) × 进程数 < PG max_connections`——**扩容前必算** |
| **优雅停机** | 容器 `terminationGracePeriodSeconds` **必须大于** `REEL_WORKER__SHUTDOWN_GRACE_SECONDS` |
| **schema** | 生产环境**只检查不修改**，缺表直接拒绝启动；结构变更必须走 Alembic |
| **反向代理** | 不改写请求体（Stripe 验签）、不把 5xx 重写成 200（Stripe 重试）、应用端口不对公网开放（防 XFF 伪造绕过 IP 限流） |
| **探针** | `/livez`（不查数据库）用于 liveness，`/readyz`（查数据库+存储）用于 readiness |

### 备份

```bash
# 全库
pg_dump -Fc -Z 6 -h "$PGHOST" -U reelagent -d reelagent -f "reelagent-$(date +%F).dump"

# 关键三表（丢了无法重建）
pg_dump -Fc -t ledger_entries -t webhook_events -t jobs \
  -h "$PGHOST" -U reelagent -d reelagent -f "reelagent-critical-$(date +%F).dump"
```

**必须一起备份的还有 `REEL_SECURITY__SECRET_KEY`**：
丢了它所有 BYOK 密钥**无法解密**（用户必须重新填写）、
所有会话与分享链接失效。

备份策略、恢复演练步骤、升级与回滚流程见
[docs/DEPLOYMENT.md 第 7、8 节](docs/DEPLOYMENT.md#7-备份与恢复)。

---

## 已知限制与缺口

> 以下问题在 1.0.0 时点**确认存在**，如实列出。
> 完整清单（含每一条的具体位置）见 [CHANGELOG 已知问题](CHANGELOG.md#已知问题)。
>
> ℹ️ **本文档记录的是一个仍在演进的代码库**：容器化、Alembic 迁移与
> 控制台界面已经补齐（见下方「已补齐」）。若你读到某条「缺口」但
> 仓库里已有对应文件，以仓库为准。

### ✅ 已补齐（本清单早期版本列为缺口）

| 项 | 当前状态 |
|---|---|
| **控制台界面** | `reelagent_cloud/web/templates/`（base + auth/console/pages/partials）与 `web/static/`（`css/app.css`、`js/app.js`、`favicon.svg`）**已提供** |
| **容器化** | `Dockerfile`、`Dockerfile.dev`、`docker-compose.yml`（含 `migrate` 服务）、`docker-compose.dev.yml`、`.dockerignore`、`Makefile` **已提供** |
| **数据库迁移** | `alembic.ini` + `reelagent_cloud/db/migrations/` **已提供**，`make migrate` 可用 |
| **云层环境变量样例** | `.env.example` 已含完整 cloud 层变量；另有 `.env.production.example` |

### 🔴 仍需注意

| 缺口 | 影响 | 现状 |
|---|---|---|
| **`reelagent-admin` 入口悬空** | `pyproject.toml` 声明了 `reelagent_cloud.admin:main`，但该模块不存在 | 执行会报 `ModuleNotFoundError`；用 CLI 或脚本代替 |
| **live 路径未端到端联网验证** | 供应商适配器按公开文档实现，接入真实 Key 后可能需要按实际返回调整字段 | 已完整验证的是 mock 路径与全部编排/恢复/评测逻辑 |

### 🟠 功能未实现

| 缺口 | 说明 |
|---|---|
| `JobKind.EVALUATE_PROJECT` / `RENDER_PROJECT` | 枚举已定义，worker 抛 `NotImplementedError` |
| `LedgerKind.EXPIRE` | 无产生方，且 `sign` 语义对负数不友好 |
| 账号注销流程 | `purge_org_storage()` 已实现但**无调用方** |
| 产品 GC 任务 | **没有任何代码删除对象存储里的文件**；`Artifact.expires_at` 无人使用 |
| 邮件通知 | 只实现了注册欢迎邮件 |
| `Idempotency-Key` 未真正生效 | `POST /api/v1/projects` 回显该键但未传给 `create_project()`，**同一素材重复提交会重复扣费** |
| 限流覆盖不全 | `anonymous` / `user` 两个 scope 定义了但**无调用点** |

### 🟡 安全与运维待改进

| 缺口 | 说明 |
|---|---|
| `/api/v1/system/status` 无鉴权 | 暴露版本、环境、全库队列深度与配置快照（密钥已打码）。建议代理层限制来源 |
| 存储租户校验未下沉 | 依赖「每个路由记得调 `key_belongs_to_org`」，是 code review 约束而非类型保证 |
| `X-Org-Id` 分支不检查 `is_suspended` | 停用组织时需同时吊销其 API Key |
| `reelagent_credits_consumed_total` 带 `org_id` 标签 | 租户多时会导致 Prometheus 基数膨胀 |
| worker 指标无法被 Prometheus 采集 | 指标注册表是**进程内**的，而 `/metrics` 只在 API 进程上。需用数据库导出器或给 worker 加端点 |
| 无 2FA / OAuth / SSO / WAF / IP 白名单 | 明确的缺口 |
| 审计日志可缺失且可修改 | 写入不参与业务事务；无防篡改机制，不适合作为严格合规证据链 |
| `GET /api/v1/me` 的 `credits.usd_rate` 命名有歧义 | 它返回的是 `credits_per_usd`（100），**不是**换算率（真实是 300） |

### ⚪ 产品/能力限制

| 限制 | 说明 |
|---|---|
| **一致性指标是代理指标** | `cross_shot_clue_similarity` 用灰度归一化互相关，能捕捉大幅漂移但不等价于人眼判断。生产环境应换成人脸/ReID 特征 |
| **道具变形无法被自动检测** | 相似度指标只覆盖 `kind == "character"` 的线索；Judge 只看文本表格（不含图像） |
| **Judge 输出不完全可复现** | 跨阈值边界时门禁可能抖动；无重复采样取均值或方差控制 |
| **门禁阈值是经验值** | 0.95 / 0.90 / 0.50 / 3.0 尚未用真实用户反馈校准 |
| **`rhythm_variety` 不参与门禁** | 「十个中景镜头各 3 秒」这类节奏问题不会导致门禁失败 |
| **取消不是立即的** | 检查点在阶段与镜头边界，正在进行的供应商请求会跑完才退出 |
| **渲染是简单拼接** | 没有转场、字幕烧录、BGM 混音与剪辑节奏对齐 |
| **无 ffmpeg 时输出 GIF** | 时长与帧率精度不如 MP4 |
| **成本单价是估算值** | 用于预算控制与路由决策，不是账单级精度 |
| **云层断点恢复能力有限** | 引擎工作区每个 job 独立且任务结束后会被删除，因此跨进程完整续跑依赖工作区仍存在；实际主要用于单次执行内的重试与单镜重跑 |

---

## 路线图

计划中的工作（欢迎贡献，见 [CONTRIBUTING.md](CONTRIBUTING.md#好的第一个贡献)）：

- [ ] 修复 `Idempotency-Key` 未生效的问题（`POST /api/v1/projects` 会重复扣费）
- [ ] 实现 `reelagent-admin` 管理入口（入口已声明但模块不存在）
- [ ] 产品 GC 任务（清理过期产物与孤儿对象——**当前没有任何代码删除对象文件**）
- [ ] 给 worker 加 metrics 端点（当前 worker 指标无法被 Prometheus 采集）
- [ ] 补齐 `anonymous` / `user` 两个限流 scope 的调用点
- [ ] 实现 `JobKind.EVALUATE_PROJECT` / `RENDER_PROJECT`
- [ ] 把 `Storage` 的租户校验下沉到方法签名
- [ ] 账号注销流程（`purge_org_storage()` 已有实现，缺入口）
- [ ] 剪映草稿导出（`.zip`，含旁白音轨与口播字幕轨）
- [ ] 把 `ToolRegistry.specs()` 封装为 MCP Server 对外暴露
- [ ] 关键帧局部重生成（迭代编辑体验）
- [ ] 用人脸 / ReID 特征替换灰度互相关，提升一致性指标可信度
- [ ] 多版本评测对比报告（Prompt / 模型变更的收益与回归判定）

---

## 贡献

请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)（开发环境、分支与提交规范、
代码规范、测试要求、PR 检查清单、什么时候必须写 ADR）。

**快速开始**：

```bash
git clone <repo-url> reel-agent && cd reel-agent
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

python -m reelagent demo                       # 零配置跑通引擎
python -m unittest discover -s tests -t .      # 48 项引擎测试（不需要 pytest）
pytest                                         # 212 项（引擎 + 云层）
ruff check . && ruff format --check . && mypy reelagent reelagent_cloud
```

也可以用 `make install && make test && make lint typecheck`。

**提交规范**：Conventional Commits（`feat(cloud):` / `fix(engine):` / `docs(adr):` …），
描述用中文，正文写**为什么**。
**三条不可违反的边界**（详见 CONTRIBUTING）：

1. `reelagent/` **不得**引入 Web 依赖。
2. `reelagent_cloud/services/` **不得**直接依赖 FastAPI。
3. 云层**不得**把业务逻辑写进 Agent。

**架构决策**记录在 [docs/adr/](docs/adr/)。引入基础设施组件、
改变数据模型/状态机/计费语义/鉴权边界、或任何难以回滚的决定，
都需要写一篇 ADR。

参与本项目即表示你同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

**安全问题**请**不要**开公开 Issue，按 [SECURITY.md](SECURITY.md) 私下报告。

---

## License

[Apache License 2.0](LICENSE)

```
Copyright 2025 ReelAgent Team

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

---

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 分层架构 · 14 张表的职责与索引 · 任务生命周期时序 · 额度双记账 · 跨镜头一致性方案 · 已知缺口 |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | 三种部署路径（含 Dockerfile / Compose / K8s 示例）· 环境变量完整清单 · 生产启动校验 · 域名与 HTTPS · Stripe · 备份恢复 · 升级回滚 · 部署检查清单 |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | 运维 Runbook：7 个症状场景，每个都有可执行的 SQL 与命令 |
| [docs/API.md](docs/API.md) | REST API 参考：逐端点的请求/响应示例 · 错误码表 · 限流规则 · Webhook 事件 |
| [docs/adr/](docs/adr/) | 7 篇架构决策记录 + 索引与格式约定 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发环境 · 分支与提交规范 · 代码规范 · 测试要求 · PR 检查清单 |
| [SECURITY.md](SECURITY.md) | 安全模型：身份、会话、CSRF、租户隔离、密钥、限流、Webhook、已知缺口 |
| [CHANGELOG.md](CHANGELOG.md) | 1.0.0 的能力清单与已知问题 |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | 贡献者公约（中文版） |
