# 贡献指南（CONTRIBUTING.md）

感谢你有兴趣为 ReelAgent 做贡献。本文说明开发环境搭建、代码规范、
测试要求与 PR 流程。

---

## 目录

- [行为准则](#行为准则)
- [开发环境搭建](#开发环境搭建)
- [本地运行](#本地运行)
- [项目结构与边界约束](#项目结构与边界约束)
- [分支与提交规范](#分支与提交规范)
- [代码规范](#代码规范)
- [测试要求](#测试要求)
- [PR 检查清单](#pr-检查清单)
- [什么时候必须写 ADR](#什么时候必须写-adr)
- [文档同步要求](#文档同步要求)
- [报告问题](#报告问题)

---

## 行为准则

参与本项目即表示你同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
请在所有交流中保持尊重与建设性。

---

## 开发环境搭建

### 要求

| 项 | 要求 | 来源 |
|---|---|---|
| Python | **≥ 3.11** | `pyproject.toml` 的 `requires-python` |
| ffmpeg | 可选 | 缺失时渲染自动降级为 GIF + WAV，功能仍完整 |

Python 3.11 是硬要求，因为代码里用了：

- `enum.StrEnum`（3.11+）与 `X | Y` 的类型语法
- `asyncio.TaskGroup` 风格的结构（虽然本项目用 `asyncio.create_task`）
- `tomllib`

### 安装

**推荐：可编辑安装 + dev 依赖**

```bash
git clone <repo-url> reel-agent
cd reel-agent

python -m venv .venv

# Linux / macOS
. .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1
# Windows cmd
.venv\Scripts\activate.bat

# 可编辑安装 + 开发依赖
pip install -e ".[dev]"
```

**可选依赖组**（`pyproject.toml` 的 `[project.optional-dependencies]`）：

| extra | 内容 | 何时需要 |
|---|---|---|
| `dev` | `pytest` / `pytest-asyncio` / `pytest-cov` / `respx` / `ruff` / `mypy` | 开发必备 |
| `s3` | `aioboto3>=13.0` | 用 S3 / OSS / R2 / MinIO 做对象存储时 |
| `sentry` | `sentry-sdk>=2.0` | 接入 Sentry 时 |

```bash
pip install -e ".[dev,s3]"
```

### 主依赖一览

`pyproject.toml` 的 `dependencies`：

| 分类 | 包 |
|---|---|
| **引擎** | `numpy>=1.24`、`Pillow>=10.0` |
| 配置与校验 | `pydantic>=2.7`、`pydantic-settings>=2.2` |
| Web / API | `fastapi>=0.110`、`uvicorn[standard]>=0.29`、`jinja2>=3.1`、`python-multipart>=0.0.9`、`httpx>=0.27` |
| 持久化 | `SQLAlchemy[asyncio]>=2.0.30`、`alembic>=1.13`、`asyncpg>=0.29`、`aiosqlite>=0.20` |
| 缓存 / 队列 | `redis>=5.0` |
| 计费 | `stripe>=8.0` |
| 安全 | `argon2-cffi>=23.1`、`itsdangerous>=2.1`、`cryptography>=42.0`、`email-validator>=2.1` |
| 可靠性 | `tenacity>=8.2` |
| 可观测 | `structlog>=24.1`、`prometheus-client>=0.20` |
| CLI | `typer>=0.12`、`rich>=13.7` |

> ℹ️ `typer` 与 `rich` 在主依赖里，但**当前 CLI 用的是标准库 `argparse`**
> （`reelagent/cli.py` 与 `reelagent_cloud/server.py` 都是）。
> 它们是早期规划的残留，可以安全移除——但请单独一个 PR 处理，
> 不要和功能改动混在一起。

### 环境变量

**开发环境不需要任何 API Key**。默认 `REEL_MODE=mock`，
内置的离线供应商会产出**真实的** PNG / 多帧 GIF / 时长匹配的 WAV，
因此整条流水线（含合成与评测）都能在无网络下被完整测试。

```bash
cp .env.example .env
# 全部留空即可跑通
```

要接真实模型再填 Key（见 `.env.example` 的注释，以及
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) 第 2 节的完整变量清单）。

> ⚠️ **`.env.example` 只包含引擎层的扁平变量**，不含云层的嵌套变量
> （`REEL_DATABASE__URL` 等）。如果你在开发云层，需要自己补一份。

---

## 本地运行

### 引擎（不需要数据库、不需要网络）

```bash
# 内置样例，一键跑通全流程
python -m reelagent demo

# 用自己的素材
python -m reelagent run "深夜的便利店，她找到一把黄铜钥匙。"
python -m reelagent run story.txt --style cyberpunk --aspect 16:9 --max-shots 10

# 断点续跑（每个阶段与每个镜头都会写 checkpoint）
python -m reelagent resume <项目ID>

# 查看状态与耗时归因
python -m reelagent status <项目ID>
python -m reelagent spans <项目ID>

# 只重跑评测
python -m reelagent eval <项目ID>

# 导出完整报告 JSON
python -m reelagent report <项目ID> [--output path.json]

# 其它
python -m reelagent list
python -m reelagent tools          # 已注册工具（MCP 兼容结构）
python -m reelagent config         # 当前生效配置与风格预设
```

**完整子命令列表**（来自 `reelagent/cli.py::build_parser`）：

| 子命令 | 说明 | 主要参数 |
|---|---|---|
| `run <seed>` | 新建项目并生成 | `--max-shots` `--max-base-images` `--from-stage` + 通用参数 |
| `demo` | 用内置样例跑通全流程 | 同 `run` |
| `resume <project_id>` | 从断点继续 | `--from-stage` + 通用参数 |
| `list` | 项目列表 | `--limit`（默认 20） |
| `status <project_id>` | 阶段 / 镜头 / 成本 | — |
| `spans <project_id>` | span 列表与耗时归因 | `--limit`（默认 60） |
| `eval <project_id>` | 只重跑评测 | `--no-judge` |
| `report <project_id>` | 导出完整报告 JSON | `--output` |
| `tools` | 列出已注册工具 | — |
| `config` | 显示生效配置与风格预设 | — |

**通用参数**（`add_common`，适用于 `run` / `demo` / `resume`）：

| 参数 | 可选值 | 说明 |
|---|---|---|
| `--style` | `cinematic` / `anime` / `cyberpunk` / `ink` / `realistic` | 风格预设 |
| `--aspect` | 例如 `9:16` / `16:9` / `1:1` | 画幅 |
| `--budget` | 浮点 | 单片预算上限（USD），影响模型路由 |
| `--concurrency` | 整数 | 并发镜头数 |
| `--mode` | `mock` / `live` | 运行模式 |
| `--tier` | `draft` / `standard` / `premium` | 强制路由档位 |
| `--no-judge` | — | 跳过 LLM-as-Judge，只算确定性指标 |

**中途 Ctrl-C 是安全的**：每个阶段与每个镜头都会写 checkpoint，
`resume` 会从断点继续。

### API 服务 + worker

云层需要数据库。最简单的方式是用 SQLite（非生产环境会自动建表）：

```bash
# 终端 1：API 服务（--reload 会在代码变更时自动重载，但只支持单进程）
reelagent-server --host 127.0.0.1 --port 8000 --reload

# 终端 2：worker
reelagent-worker --once              # 只跑一个任务后退出（调试用）
reelagent-worker --concurrency 2     # 常驻
reelagent-worker --concurrency 4 --worker-id worker-dev
```

`reelagent-server` 的参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--host` | `0.0.0.0` | |
| `--port` | `8000` | |
| `--reload` | 关 | 代码变更自动重载（会强制 `workers=1`） |
| `--workers` | `1` | 进程数；每个进程有独立的连接池 |

`reelagent-worker` 的参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--once` | 关 | 只领取并执行一个任务后退出，打印 `processed: <job_id>` 或 `no job available` |
| `--concurrency` | 覆盖 `REEL_WORKER__CONCURRENCY`（默认 2） | 单进程同时执行的 Job 数 |
| `--worker-id` | 自动生成 `worker-<8位hex>` | |

打开 <http://127.0.0.1:8000/docs> 看 OpenAPI 文档，
打开 <http://127.0.0.1:8000/> 看落地页、`/app` 看控制台。

> ℹ️ 控制台是服务端渲染的 Jinja2 页面
> （`reelagent_cloud/web/templates/`），静态资源在 `web/static/`。
> **CSP 禁止内联脚本**（`script-src 'self'`），
> 因此新增界面时所有 JS 必须放进 `web/static/js/app.js`（或新增外部文件），
> 不要内联到模板里——否则会被 CSP 拦掉。

### 用 Makefile 提速

```bash
make help          # 列出所有目标
make install       # 可编辑安装 + dev 依赖
make dev           # 起本地依赖（Postgres + Redis + MinIO）
make api           # 本地 API（热重载）
make worker        # 本地 worker
make migrate       # 执行数据库迁移
make test          # 测试 + 覆盖率
make test-fast     # 只跑单元测试（跳过 integration）
make lint fmt typecheck
make docker-build / docker-up / docker-down / docker-logs
make smoke / smoke-docker
make clean / clean-all
```

### 冒烟脚本

```bash
# 服务层冒烟：注册 → 账本 → 任务 → 领取 → 结算 → 对账
python scripts/smoke.py

# HTTP 层冒烟
python scripts/smoke_http.py
```

（两个脚本的具体用法见文件头部的 docstring；
`smoke.py` 会用一个临时 SQLite 文件，不影响你的开发数据。）

---

## 项目结构与边界约束

```
reel-agent/
├── reelagent/                      引擎：纯计算，零 Web 依赖
│   ├── schema.py                   数据模型（dataclass）
│   ├── config.py                   引擎配置与风格预设
│   ├── runtime.py                  依赖装配 + JSON 稳健解析
│   ├── routing.py                  难度打分 / 模型路由 / 成本
│   ├── utils.py                    stable_seed（确定性种子）
│   ├── cli.py                      命令行
│   ├── llm/                        LLM 抽象：base / mock / openai_compat / prompts
│   ├── providers/                  供应商：base / mock / ark
│   ├── harness/                    运行时底座：state / tracing / registry / context / executor
│   ├── agents/                     planner / artdirector / director / evaluator
│   ├── eval/                       一致性度量
│   ├── render/                     ffmpeg / GIF / 分镜九宫格
│   ├── tools/                      媒体工具注册（MCP 兼容）
│   └── pipeline/                   五阶段状态机
│
├── reelagent_cloud/                云端产品层
│   ├── config.py                   产品配置（嵌套环境变量 + 生产校验）
│   ├── server.py                   reelagent-server 入口
│   ├── worker.py                   reelagent-worker 入口
│   ├── api/                        应用工厂、中间件、错误映射、限流、路由
│   ├── security/                   tokens（会话/API Key/CSRF/BYOK）· deps（授权）
│   ├── services/                   用例层：accounts / projects / jobs / credits / billing / pipeline / storage / audit / email
│   ├── domain/                     enums（状态机）· plans（套餐定价）· errors · ids
│   ├── db/                         models（14 张表）· session · base
│   ├── workers/                    Worker 主循环
│   ├── observability/              结构化日志 · Prometheus 指标 · 请求上下文
│   └── web/                        控制台界面：templates/ + static/ + 模板过滤器
│
├── docs/                           架构、部署、运维、API、ADR
├── tests/                          212 个测试（引擎 48 + 云层 164）+ conftest.py
├── scripts/                        冒烟脚本
├── alembic.ini                     Alembic 配置（script_location 指向 db/migrations）
├── Dockerfile / Dockerfile.dev     生产 / 开发镜像
├── docker-compose.yml              生产形态（postgres + redis + migrate + api + worker）
├── docker-compose.dev.yml          本地依赖（postgres + redis + minio）
├── Makefile                        常用开发与部署目标（make help）
├── .env.example / .env.production.example
└── pyproject.toml
```

### 三条不可违反的边界

这三条是架构的核心不变量，code review 时应当作为**硬性检查项**。

#### 1. 引擎层不得引入 Web 依赖

`reelagent/` **只能**依赖标准库 + `numpy` + `Pillow`。
**不许**导入 FastAPI / Starlette / SQLAlchemy / Stripe / Redis /
pydantic / structlog 等任何云层依赖。

**为什么**：这是「核心零依赖、clone 即跑」与
「引擎可单机可服务化」的前提。详见
[ADR 0002](docs/adr/0002-engine-zero-web-dependency.md)。

**检查方式**：

```bash
# 应当没有任何输出
grep -rnE "^\s*(from|import)\s+(fastapi|starlette|sqlalchemy|stripe|redis|pydantic|structlog|prometheus_client)" \
  reelagent/
```

#### 2. 服务层不得直接依赖 FastAPI

`reelagent_cloud/services/` 里的函数**不许**导入或接收
`Request` / `Response` / `HTTPException` 等 Web 类型。

**为什么**：业务逻辑与传输层解耦。这样换传输层
（gRPC / 消息队列 / CLI）时不用改业务代码。
错误通过 `domain/errors.py` 里的语义化异常抛出，
由 `api/errors.py` 统一映射成 HTTP 响应。

**正确做法**：

```python
# ✅ services 层抛领域异常
from ..domain.errors import NotFound, PaymentRequired

async def get_org(session, org_id: str) -> Organization:
    org = await session.get(Organization, org_id)
    if org is None:
        raise NotFound("组织不存在")
    return org
```

**错误做法**：

```python
# ❌ 不要这样
from fastapi import HTTPException

async def get_org(session, org_id: str):
    if org is None:
        raise HTTPException(status_code=404)      # 服务层不该知道 HTTP
```

#### 3. 云层不得把业务逻辑写进 Agent

云层只能通过 `services/pipeline.py` 这一层与引擎交互。
不要为了「省一次调用」而在 `reelagent_cloud` 里重新实现
prompt 拼装、路由决策、一致性注入等引擎逻辑。

**唯一例外**（当前存在的）：`build_engine_settings()` 负责
把云层配置翻译成引擎配置——这属于**配置桥接**，不是业务逻辑。

### 一条产品红线

**生产环境不得用 mock 供应商冒充真实生成。**

```python
def resolve_engine_mode(settings: Settings, org: Organization) -> str:
    """决定这次生成走真实供应商还是离线渲染。

    生产环境若没有可用 Key 则直接拒绝——宁可不接单，也不能交付假成片。
    """
    ...
    if settings.is_production:
        raise UnsupportedOperation("当前部署未配置视频生成供应商，暂时无法生成成片。请联系支持。")
    return "mock"
```

**不要**为了让测试通过或让演示好看而放宽这个限制。
「让用户花钱买到占位图是最坏的产品事故。」

---

## 分支与提交规范

### 分支

- `main` 是受保护分支，**只能通过 PR 合入**。
- 从最新的 `main` 切出特性分支：

```bash
git checkout main
git pull --ff-only
git checkout -b feat/shot-level-retry-policy
```

**分支命名**（`<type>/<简短英文描述>`，用连字符分隔）：

| 前缀 | 用途 |
|---|---|
| `feat/` | 新功能 |
| `fix/` | 修 bug |
| `docs/` | 只改文档 |
| `refactor/` | 重构（不改行为） |
| `test/` | 只改测试 |
| `chore/` | 构建、依赖、工具配置 |
| `perf/` | 性能优化 |
| `adr/` | 只加/改 ADR |

**保持分支小而聚焦**。一个 PR 做一件事。
如果一个改动超过约 500 行 diff，考虑拆成系列 PR
（用 `--stack` 或按依赖顺序合并）。

### 同步上游：用 rebase，不用 merge

```bash
git fetch origin
git rebase origin/main

# 如果已经 push 过，需要强推（仅限你自己的特性分支）
git push --force-with-lease
```

**为什么 rebase**：保持线性历史，让 `git log` 与 `git bisect` 可用。
`--force-with-lease` 比 `--force` 安全（如果远端有别人的新提交会拒绝）。

> ⚠️ **绝不要对 `main` 强推**，也不要对别人的分支强推。

### 提交信息：Conventional Commits

格式：

```
<type>(<scope>)<!>: <简短描述>

<可选正文：为什么这么改，而不是改了什么>

<可选 footer：BREAKING CHANGE: ... / Closes #123 / Refs #456>
```

**Type**（必填）：

| type | 用途 | 出现在 CHANGELOG 的哪一节 |
|---|---|---|
| `feat` | 新功能 | 「新增」 |
| `fix` | 修 bug | 「修复」 |
| `perf` | 性能优化 | 「性能」 |
| `refactor` | 重构（不改行为） | 「变更」 |
| `docs` | 文档 | 「文档」 |
| `test` | 测试 | 通常不出现 |
| `build` | 构建与依赖 | 「构建」 |
| `ci` | CI 配置 | 通常不出现 |
| `chore` | 杂项 | 通常不出现 |
| `revert` | 回滚 | 「回滚」 |

**Scope**（推荐，说明影响范围）：

| scope | 影响范围 |
|---|---|
| `engine` | `reelagent/`（引擎，不指定子模块时用） |
| `pipeline` | `reelagent/pipeline/`（五阶段状态机） |
| `agents` | `reelagent/agents/` |
| `harness` | `reelagent/harness/` |
| `routing` | `reelagent/routing.py` |
| `eval` | `reelagent/eval/`、`reelagent/agents/evaluator.py` |
| `providers` | `reelagent/providers/` |
| `render` | `reelagent/render/` |
| `cloud` | `reelagent_cloud/`（不指定子模块时用） |
| `api` | `reelagent_cloud/api/` |
| `jobs` | `reelagent_cloud/services/jobs.py` |
| `credits` | `reelagent_cloud/services/credits.py` |
| `billing` | `reelagent_cloud/services/billing.py` |
| `security` | `reelagent_cloud/security/` |
| `db` | `reelagent_cloud/db/` |
| `worker` | `reelagent_cloud/workers/` |
| `docs` | 文档 |
| `adr` | ADR |
| `deps` | 依赖变更 |

**示例**：

```
feat(cloud): 支持按组织配置 BYOK 供应商密钥

密钥用 Fernet 加密后存 organizations.byok，任何接口都不明文返回。
解密失败时单条跳过，不影响其它供应商。

Closes #142
```

```
fix(engine): 修正 attrs 含 kind 键时 span 参数冲突

run_with_retry 之前用 **attrs 展开，当 attrs 里有 kind 时会与
span(kind=...) 形参冲突抛 TypeError，并被外层 except 静默吞掉。
改为用 attrs= 关键字传参，并补了回归测试。

Fixes #87
```

```
perf(jobs): 为领取查询补上复合索引

EXPLAIN 显示 (status, priority, run_after) 上是 Seq Scan，
100 万行时领取耗时 800ms。补索引后降到 2ms。
```

**破坏性变更**：在 type 后加 `!`，并在 footer 用 `BREAKING CHANGE:` 说明：

```
feat(api)!: 重命名 /credits/estimate 为 /credits/quote

BREAKING CHANGE: POST /api/v1/credits/estimate 已移除，
改用 POST /api/v1/credits/quote（参数与响应结构相同）。
依赖旧路径的集成需要更新。
```

**提交信息的作用**：
- 自动生成 `CHANGELOG.md`。
- `git log --oneline` 就是一份可读的变更历史。
- 便于定位「哪个提交引入了这个行为」。

**要求**：
- 描述用**中文**（与本仓库的注释、文档语言一致）。
- 描述**不要超过约 72 个字符**。
- 正文写**为什么**，而不是**改了什么**（`git diff` 已经说明了后者）。
- 一个提交只做一件事。提交前不要 `git add .` 一把梭。

---

## 代码规范

### 语言与文档

- **注释与 docstring 用中文**（与现有代码一致）。
- **模块级 docstring 必须说明「为什么这么设计」**，而不只是「这是什么」。
  这是本仓库最鲜明的风格。参考例子：

  ```python
  """任务队列（Postgres `FOR UPDATE SKIP LOCKED`）。

  为什么不用 Celery / RQ / Kafka：
    - **事务一致性**：任务入队与业务写入在同一个事务里。用外部 broker 时，
      「业务提交了但入队失败」或「入队了但业务回滚」都需要额外的 outbox 模式来兜底。
    - **少一个组件**：不需要 Redis/RabbitMQ 就能跑，自托管部署成本更低。
    - **SKIP LOCKED 天然并发**：多个 worker 无锁竞争，谁抢到算谁的。
  代价：吞吐受数据库限制。本项目「一条成片跑几分钟」的量级完全够用；
  若将来要到每秒上千任务，再换成专用 broker 即可（接口已抽象在上述函数里）。
  """
  ```

- **记下踩过的坑**。如果一个实现是为了绕开某个具体的 bug，
  在注释里说明。这个仓库已经有几处这样的注释
  （`run_with_retry` 的 `attrs` 参数、`_apply` 的 `flush()`、
  「无数据 ≠ 满分」），它们对后来者的价值极高。

### 类型注解

- **所有函数都要有类型注解**（含返回值）。
- 每个模块顶部加 `from __future__ import annotations`。
- 优先用现代的联合语法：`str | None` 而不是 `Optional[str]`。
- 容器类型用内置泛型：`list[str]` / `dict[str, int]` 而不是 `List` / `Dict`。

```python
from __future__ import annotations

def usd_to_credits(settings: Settings, usd: float) -> int:
    ...
```

### 格式与静态检查

配置全部在 `pyproject.toml` 里，**不要在编辑器之外另建配置文件**。

#### ruff（lint + format）

**配置**（`[tool.ruff]`）：

| 项 | 值 |
|---|---|
| `line-length` | **110** |
| `target-version` | `py311` |
| `src` | `["reelagent", "reelagent_cloud", "tests"]` |
| isort `known-first-party` | `["reelagent", "reelagent_cloud"]` |

**启用的规则**（`[tool.ruff.lint] select`）：

```
E   pycodestyle 错误
F   pyflakes（未使用导入、未定义名字）
W   pycodestyle 警告
I   isort（导入排序）
N   pep8-naming（命名约定）
UP  pyupgrade（升级到现代语法）
B   flake8-bugbear（常见陷阱）
C4  flake8-comprehensions
SIM flake8-simplify
RUF Ruff 自己的规则
ASYNC flake8-async（异步代码陷阱）
S   flake8-bandit（安全）
```

**忽略的规则**：

| 规则 | 忽略原因（写在配置注释里） |
|---|---|
| `E501` | 行长由 formatter 控制 |
| `S101` | 测试里允许 `assert` |
| `B008` | FastAPI 依赖注入惯用默认参数 |
| `SIM108` | 三元表达式并非总是更清晰 |

**per-file 例外**：

```toml
"tests/*" = ["S105", "S106", "S107"]                    # 测试里允许硬编码「口令」
"reelagent_cloud/db/migrations/*" = ["E", "F", "I"]     # 迁移文件不检查这些
```

**命令**：

```bash
ruff check .            # 只检查
ruff check --fix .      # 自动修可修的
ruff format .           # 格式化（会改写文件）
ruff format --check .   # 只检查格式，不改文件（CI 用）
```

> ℹ️ `S` 系列（flake8-bandit）是**安全 lint**。它抓的典型问题：
> `subprocess` 调用带 `shell=True`、`eval`、硬编码口令、
> `assert` 用于运行时校验、不安全的临时文件。
> 遇到 `S` 告警**不要习惯性 `# noqa`**——先判断是不是真问题。
> 现有代码里的合法例外会带原因，例如：
>
> ```python
> parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 - 容器内需要监听全网卡
> ```

#### mypy

**配置**（`[tool.mypy]`）：

| 项 | 值 |
|---|---|
| `python_version` | `3.11` |
| `warn_unused_ignores` | `true`（多余的 `# type: ignore` 会报错） |
| `warn_redundant_casts` | `true` |
| `no_implicit_optional` | `true` |
| `check_untyped_defs` | `true`（未注解的函数体也检查） |
| `ignore_missing_imports` | `true` |
| `plugins` | `["pydantic.mypy"]` |
| `exclude` | `reelagent_cloud/db/migrations/` |

**overrides**：

```toml
[[tool.mypy.overrides]]
module = ["tests.*"]
disallow_untyped_defs = false        # 测试函数可以省略注解
```

**命令**：

```bash
mypy reelagent reelagent_cloud
```

**原则**：**新代码不应引入新的 mypy 错误。**
如果某个第三方库缺少类型标注，优先在 `ignore_missing_imports`
的覆盖下工作，而不是到处加 `# type: ignore`。
必须加时，请**附上原因**：

```python
first = forwarded.split(",")[0].strip()
if first and _is_private(first):
    ...
return request.client.host if request.client else ""   # 这里不需要 ignore
```

```python
result.rowcount == 0  # type: ignore[attr-defined]  # SQLAlchemy 的 Result.rowcount 未标注
```

### 推荐的编辑器设置

**VS Code**（`.vscode/settings.json`，加入 `.gitignore`）：

```json
{
  "python.defaultInterpreterPath": ".venv/bin/python",
  "editor.formatOnSave": true,
  "editor.rulers": [110],
  "editor.codeActionsOnSave": {
    "source.organizeImports": "explicit"
  },
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff"
  }
}
```

### 可选的 pre-commit

仓库**没有**提供 `.pre-commit-config.yaml`。如果你想要本地钩子，
可以自己建一个（**不要提交到仓库**，除非同时更新本文档）：

```yaml
# .pre-commit-config.yaml（可选，本地使用）
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.5.0            # 请与 pyproject 里的 ruff 版本对齐
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.10.0
    hooks:
      - id: mypy
        args: [reelagent, reelagent_cloud]
        additional_dependencies: [pydantic, types-requests]
```

### 代码风格约定（从现有代码归纳）

| 约定 | 示例 |
|---|---|
| 模块顶部 `from __future__ import annotations` | 每个 `.py` 文件 |
| 常量用 `UPPER_SNAKE`，私有函数用 `_leading_underscore` | `_apply()` / `LOCK_THRESHOLD` |
| 枚举用 `StrEnum` 子类并定义在 `domain/enums.py` | 不要散落字符串 |
| 领域异常定义在 `domain/errors.py`，带稳定的 `code` 与 `http_status` | |
| dataclass 用于纯数据（引擎模型）；pydantic 用于配置与校验 | |
| 分隔线注释用固定宽度 | `# ----...---- #` |
| 参数超长时用 `*` 强制关键字传参 | `get_project(session, *, org_id, project_id)` |

---

## 测试要求

### 现状

测试套件分两部分，**框架不同是有意的**：

| 部分 | 测试文件 | 数量 | 框架 | 依赖 |
|---|---|---|---|---|
| **引擎** | `test_core.py`（33）、`test_pipeline.py`（15） | **48** | 标准库 `unittest` | **无**（clone 即跑） |
| **云层** | `test_cloud_credits.py`（27）、`test_cloud_http.py`（70）、`test_cloud_jobs.py`（33）、`test_cloud_security.py`（34） | **164** | `pytest` + `pytest-asyncio` + FastAPI `TestClient` | `pip install -e ".[dev]"` |
| | | **212** | | |

| 其它 | 说明 |
|---|---|
| 辅助模块 | `tests/support.py`（临时目录管理）、`tests/conftest.py`（pytest fixture） |
| 覆盖率配置 | `[tool.coverage.run]`，`omit = ["*/migrations/*", "*/web/templates/*"]` |

**为什么引擎测试用 `unittest` 而不是 `pytest` 风格**
（`test_core.py` 的 docstring）：

> 用标准库 unittest 编写，因此 `python -m unittest discover tests`
> 可以直接跑，不需要安装 pytest（pytest 也能识别这些用例）。

这符合引擎「零依赖、clone 即跑」的定位。

**云层测试**用 `tests/conftest.py` 提供的 fixture 驱动真实的 FastAPI 应用
与临时数据库：

| fixture | 作用 |
|---|---|
| `tmp_workspace` / `settings` | 临时工作区与配置 |
| `db` | 初始化内存/临时数据库 |
| `app` / `client` | FastAPI 应用与同步 `TestClient` |
| `authed_client` / `second_client` | 已登录的客户端（后者用于跨租户测试） |
| `signup_payload` / `unique_signup_payload()` | 注册表单数据 |
| `csrf` / `extract_csrf_token(html)` | 从 HTML 里取 CSRF 令牌 |
| `org_id` / `sync_db` | 组织 ID 与同步会话 |

⚠️ **云层测试需要一个干净的工作区**。`conftest.py` 里有
`@pytest.fixture(scope="session", autouse=True) def _cleanup_root()`，
会清理测试用的临时目录。**不要在测试运行时手工往 `tests/_tmp` 里放东西。**

### 运行测试

```bash
# 标准库方式（不需要 pytest）
python -m unittest discover -s tests -t . -v

# pytest 方式（配置见 [tool.pytest.ini_options]）
pytest
pytest tests/test_pipeline.py -v
pytest -k "resume or damaged" -v
```

`pyproject.toml` 的 pytest 配置：

```toml
testpaths = ["tests"]
asyncio_mode = "auto"                        # async def 测试无需 @pytest.mark.asyncio
addopts = "-q --strict-markers"
markers = [
    "unit: 纯单元测试，无 IO",
    "integration: 需要数据库/存储等依赖",
    "contract: 校验第三方 API 契约（用 respx 录制回放）",
]
filterwarnings = ["ignore::DeprecationWarning"]
```

**marker 已声明但目前基本未使用**：`unit` / `integration` / `contract`
三个 marker 在 `pyproject.toml` 里声明了（因为 `--strict-markers` 会
拒绝未声明的 marker），但**当前只有 `@pytest.mark.parametrize` 在实际使用**，
没有任何测试打上这三个 marker。

因此：
- `make test-fast`（`pytest -m "not integration"`）当前**等价于跑全部测试**。
- 欢迎给云层测试补上 `@pytest.mark.integration`，
  让「快速单元测试」这个目标真正成立。

**Makefile 的实际命令**（与本文的手写命令略有差异）：

| 目标 | 实际执行 |
|---|---|
| `make test` | `pytest --cov=reelagent --cov=reelagent_cloud --cov-report=term-missing` |
| `make test-fast` | `pytest -m "not integration"` |
| `make lint` | `ruff check .` |
| `make fmt` | `ruff format .` + `ruff check --fix .` |
| `make typecheck` | `mypy .`（整个仓库，配置里的 `exclude` 会跳过迁移目录） |

> ℹ️ `make typecheck` 用 `mypy .`（全仓库），比本文示例里的
> `mypy reelagent reelagent_cloud` 覆盖更广。
> 提交前建议至少跑一次 `make typecheck`，因为它才是 CI 会用的那个命令。

### 覆盖率

配置（`[tool.coverage.run]`）：

```toml
source = ["reelagent", "reelagent_cloud"]
omit = ["*/migrations/*", "*/web/templates/*"]

[tool.coverage.report]
exclude_lines = ["pragma: no cover", "if TYPE_CHECKING:", "raise NotImplementedError"]
```

```bash
pytest --cov=reelagent --cov=reelagent_cloud --cov-report=term-missing
```

> ℹ️ **仓库当前没有设定覆盖率门槛**（没有 `fail_under`）。
> 请关注**关键路径的覆盖**而不是总百分比：
> 断点恢复、额度账本、任务领取/租约/结算、租户隔离。

### 新代码的测试要求

**硬性要求**：

1. **新功能必须带测试。**
2. **修 bug 必须先写一个能复现的回归测试**，然后才动实现代码。
   PR 描述里要说明「这个测试在修复前是会失败的」。
3. **改动影响确定性时必须验证**。任何涉及随机种子、
   图像生成参数、指标计算的改动，都要验证
   **不同 `PYTHONHASHSEED` 下结果一致**：

   ```bash
   PYTHONHASHSEED=7  python -m unittest discover -s tests -t .
   PYTHONHASHSEED=99 python -m unittest discover -s tests -t .
   # 两次的指标数值必须完全相同
   ```

   **为什么**：`reelagent/utils.py::stable_seed()` 用 md5 而非内置 `hash()`
   就是为了这个。一旦有人不小心用了 `hash()`，评测回归就失去意义。

4. **涉及金额的改动必须有对账测试**。
   任何触及 `credits` / `jobs.settle` / `billing` 的改动，
   都要断言「账本求和 == 余额快照」（`reconcile()` 返回 `consistent: True`）。

5. **涉及租户隔离的改动必须有越权测试**。
   即「用 A 组织的凭证访问 B 组织的资源 → 404 或 403」。

### 必须覆盖的关键路径

现有测试已覆盖的重点（不要退化）：

| 路径 | 断言要点 |
|---|---|
| **断点恢复** | 产物被删 → checkpoint 失效 → 重新生成（且**不重做**完好的镜头） |
| **部分损坏** | 关键帧保留、只重做片段 |
| **阶段失败** | 落到 `failed`、不吞错误、修复后可从断点续跑并恢复项目状态 |
| **确定性** | 两次运行指标完全一致；`stable_seed` 不受 hash 随机化影响 |
| **回归** | `attrs` 含 `kind` 键不再触发参数冲突；基准图必须真的生成 |
| **评测语义** | 「无可度量对象」返回 `None` 而非 `1.0` |
| **mock 供应商** | 产出的是真 PNG / 多帧 GIF / 时长匹配的 WAV |
| **成本与路由** | 预算耗尽降 `draft`；已失败镜头升级档位 |

**应当补充的方向**（欢迎贡献）：

- 云层的租户隔离测试（跨组织访问）
- 额度账本的并发安全测试（同一组织并发结算）
- 任务领取的 `SKIP LOCKED` 行为（Postgres 上）
- Webhook 幂等的双保险测试
- API Key 无法访问管理类接口的测试

### 测试的编写约定

**临时目录放仓库内部**（`tests/support.py` 的 docstring）：

> 注意：测试用的临时工作区放在**仓库内部**（`tests/_tmp`），
> 而不是系统临时目录。这样做有两个原因：
> 1. 项目本身的默认 workspace 就在仓库内，测试应当与真实运行路径一致；
> 2. 某些受限环境（容器 / 沙箱 / 只读系统盘）不允许在系统临时目录建
>    sqlite 文件，放在仓库内可以避免「测试全挂但代码没问题」的假故障。

```python
from tests.support import cleanup_tmp, make_tmp

class TestSomething(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tmp("mysuite")

    def tearDown(self):
        cleanup_tmp(self.tmp)
```

**构造引擎 Settings 时显式传 ProviderConfig**：

```python
def make_settings(tmp: Path) -> Settings:
    return Settings(
        mode="mock",
        workspace=tmp,
        style="cinematic",
        aspect_ratio="9:16",
        budget_usd=2.0,
        max_concurrency=2,
        max_attempts=2,
        llm=ProviderConfig("llm", "mock"),
        image=ProviderConfig("mock", "mock"),
        video=ProviderConfig("mock", "mock"),
        tts=ProviderConfig("mock", "mock"),
    )
```

**测试不要联网**。`respx` 已在 dev 依赖里，
用于校验第三方 API 契约（`contract` marker）。

---

## PR 检查清单

提交 PR 前逐项自查。PR 描述里可以直接贴这份清单。

### 功能与正确性

- [ ] 改动**只做一件事**（没有混入无关的重构或格式化）
- [ ] 有测试覆盖（新功能）或回归测试（修 bug）
- [ ] 涉及确定性的改动已验证不同 `PYTHONHASHSEED` 下结果一致
- [ ] 涉及金额的改动已断言账本对账（`SUM(amount) == credits_balance`）
- [ ] 涉及租户隔离的改动有越权测试（跨组织访问被拒绝）
- [ ] 我**手动跑过**相关路径（不只是跑通测试）

### 质量门禁

- [ ] `python -m unittest discover -s tests -t .` 全绿
- [ ] `pytest` 全绿
- [ ] `ruff check .` 无输出
- [ ] `ruff format --check .` 通过
- [ ] `mypy reelagent reelagent_cloud` 没有**新增**错误

### 架构约束

- [ ] `reelagent/` 没有新增 Web / 云层依赖
      （`grep -rnE "^\s*(from|import)\s+(fastapi|sqlalchemy|stripe|pydantic)" reelagent/` 无输出）
- [ ] `reelagent_cloud/services/` 没有导入或接收 FastAPI 类型
- [ ] 没有把业务逻辑写进 Agent
- [ ] 没有在云层放宽生产环境的 mock 限制（`resolve_engine_mode`）

### 数据与安全

- [ ] **没有把密钥写进代码**（包括注释、测试、fixture、示例）
- [ ] **没有把密钥写进日志**（新增的日志调用里没有 secret 字段）
- [ ] 新增的数据库列 / 索引有对应的迁移方案（或明确说明为何不需要）
- [ ] 新增状态变更路径有审计记录（如属于审计范围）
- [ ] 新增的按 key 读取路径调用了 `key_belongs_to_org`
- [ ] 新增的「会话可访问 + 改变状态」端点经过了 CSRF 校验
      （即引用了 `WritablePrincipal` / `SessionPrincipal` / `AdminPrincipal`）
- [ ] 新增的写操作有幂等设计（或明确说明为何不需要）

### 文档

- [ ] `CHANGELOG.md` 的 `[未发布]` 节加了条目
- [ ] 涉及架构决策的改动**写了 ADR**（见下一节）
- [ ] 相关文档已同步更新：
      - 改了环境变量 → [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) 第 2 节
      - 改了 API → [docs/API.md](docs/API.md)
      - 改了数据模型 / 任务生命周期 → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
      - 改了排障方式 → [docs/OPERATIONS.md](docs/OPERATIONS.md)
      - 改了安全边界 → [SECURITY.md](SECURITY.md)
- [ ] 新增的「设计取舍」写在了代码注释里（为什么这么选，否决了什么）

### PR 描述

- [ ] 说明了**为什么**要改（不只是改了什么）
- [ ] 说明了**如何验证**（复现步骤、命令、观察到的结果）
- [ ] 有截图 / 日志 / 指标曲线（如果改动影响可观测行为）
- [ ] 关联了 Issue（`Closes #123`）
- [ ] 标注了需要 reviewer 特别关注的地方

---

## 什么时候必须写 ADR

ADR 放在 [`docs/adr/`](docs/adr/)，命名 `NNNN-kebab-case-title.md`。

### 必须写 ADR 的触发条件

只要改动**满足以下任意一条**，就必须附一篇 ADR：

| # | 触发条件 | 例子 |
|---|---|---|
| 1 | **引入或替换基础设施组件** | 队列、存储后端、缓存、消息系统、搜索引擎 |
| 2 | **改变数据模型或状态机** | 加/删表、改变状态取值集合、改变状态转换规则 |
| 3 | **改变计费或额度语义** | 计价方式、币种、退款策略、套餐定义方式 |
| 4 | **改变鉴权与权限边界** | 新增身份类型、改变角色能力、改变 scope 语义 |
| 5 | **改变引擎与云层的接口契约** | `Pipeline.run()` 的签名、`on_stage` / `should_cancel` 的语义 |
| 6 | **任何很难回滚的决定** | 需要数据迁移、会影响已发出的凭证/链接、会改变外部集成契约 |

### 可以不写 ADR 的情况

- 纯 bug 修复（没改变设计意图）。
- 内部的代码重构（对外行为不变、没有改变组件选择）。
- 新增一个同类的供应商适配器（遵循已有的模式）。
- 性能优化（除非它改变了架构，例如引入缓存层）。
- 文档与测试改动。

**判断标准**：如果半年后有人会问「为什么不用 X 方案」，
就需要写 ADR。

### ADR 格式

```markdown
# ADR NNNN：<决策标题>

- **状态**：提议 | 已接受 | 已废弃 | 被 ADR NNNN 取代
- **日期**：<日期或版本>
- **决策者**：<角色>
- **相关文件**：<代码路径>

## 背景
当时面临什么问题、有哪些约束。要让人理解「为什么必须做选择」。

## 决策
具体决定了什么。要足够精确，让读者能据此判断代码是否符合决策。

## 后果

### 正面

### 负面 / 代价

### 需要长期注意

## 备选方案
每个被否决的方案：优点 → 否决理由。
不用「不好」这种评价，要说清在**当时的约束下**为什么不可行。

## 参考
```

### ADR 的只追加原则

- ADR 一经「已接受」就**不修改正文**。
- 需要改变决策时**写一篇新 ADR**，并把老的那篇状态改为
  `被 ADR NNNN 取代`（同时在新 ADR 里引用旧的）。
- **例外**：修正错别字、补充失效的链接、追加「参考」条目是允许的。
  任何改变语义的编辑都不允许。

**为什么**：保留决策的历史轨迹。「我们曾经这么想，后来因为 X 改了」
本身是有价值的信息，比一份「永远正确」的文档更有用。

---

## 文档同步要求

代码改动应当同步更新对应的文档。这是一份对照表：

| 改了什么 | 更新哪里 |
|---|---|
| 环境变量（新增/改名/改默认值） | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) 第 2 节 + `.env.example`（如果属于引擎层变量） |
| 数据库表 / 列 / 索引 / 约束 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 第 5 节 |
| REST 端点 / 字段 / 错误码 | [docs/API.md](docs/API.md) |
| 任务状态机 / 生命周期 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 第 6 节 + `docs/API.md` 第 3.5 节 |
| 计费 / 额度语义 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 第 7 节 + `docs/adr/0003` |
| 套餐与定价 | 无需改文档（`domain/plans.py` 是单一事实来源，文档引用它） |
| 一致性 / 评测指标 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 第 8 节 + `docs/adr/0005`、`0006` |
| 安全边界 / 鉴权 | [SECURITY.md](SECURITY.md) |
| 排障方式 / 运维命令 | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| Prometheus 指标 | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) 第 9.2 节 |
| 结构化日志的事件名 | [docs/OPERATIONS.md](docs/OPERATIONS.md) 第 8.5 节 |
| 架构决策 | [docs/adr/](docs/adr/) 新增一篇 + 更新 `docs/adr/README.md` 的索引 |
| 用户可见的行为变更 | [CHANGELOG.md](CHANGELOG.md) 的 `[未发布]` 节 |
| 已知缺口 / 限制 | [CHANGELOG.md](CHANGELOG.md) 的「已知问题」 + 相关文档的对应章节 |

### 文档写作约定

- **只写代码里真实存在的**。不确定就先读代码。
- **不编造运营数据**（用户数、营收、QPS、上线时间）。
  这是一个**尚未上线运营**的产品，文档里不要出现这类数字。
- **可以写性能特征**，但必须是从代码或实测里能确认的
  （例如「mock 模式端到端约 17 秒 / 7 个镜头」）。
- **明确标注缺口**。凡是尚未实现的部分，用
  ⚠️ / ❌ / 「当前不支持」明确写出，不要含糊表述。
- **命令与 SQL 必须能直接复制执行**。
- **表格优先**用于环境变量清单、错误码表、字段说明。

---

## 好的第一个贡献

如果你想参与但不知道从哪开始，以下是**价值高、边界清晰**的方向
（都记录在 [CHANGELOG.md](CHANGELOG.md) 的「已知问题」与「计划中」里）：

| 方向 | 难度 | 为什么有价值 |
|---|---|---|
| **修复 `Idempotency-Key` 未生效**（`POST /api/v1/projects`） | 低 | 当前同一素材重复提交会重复扣费（见 CHANGELOG 已知问题） |
| **产品 GC 任务**（清理过期产物与孤儿对象） | 中 | 当前没有任何代码删除对象存储里的文件，磁盘会持续增长 |
| **给 worker 加 metrics 端点** | 中 | worker 的指标（队列深度、租约回收）当前无法被 Prometheus 采集，因为指标注册表是进程内的 |
| **补齐 `anonymous` / `user` 两个限流 scope 的调用点** | 低 | 当前只有三个端点受限流保护 |
| **给云层测试补 `@pytest.mark.integration`** | 低 | 当前 `make test-fast` 等价于跑全部测试，因为没有测试打 marker |
| **把 `Storage` 的租户校验下沉到方法签名** | 中 | 让「忘记校验」在类型层面不可能发生（见 `docs/adr/0007` 的方案 E） |
| **实现 `reelagent-admin`** | 中 | `pyproject.toml` 已声明入口，但模块不存在 |
| **实现 `JobKind.EVALUATE_PROJECT` / `RENDER_PROJECT`** | 中 | 枚举已定义，worker 抛 `NotImplementedError` |
| **账号注销流程** | 中 | `purge_org_storage()` 已实现但无调用方 |
| **补充控制台页面截图到 `docs/screenshots/`** | 低 | 模板已提供，但界面截图尚未归档（见 README 的截图清单） |

**开始之前建议**：先开一个 Issue 说明你的计划，
避免几个人同时做同一件事。

---

## 报告问题

### 普通 Bug / 功能请求

在 Issue 里提供：

1. **复现步骤**（具体命令或 API 调用）。
2. **期望行为**与**实际行为**。
3. **环境**：Python 版本、`REEL_MODE`、是否配置了真实供应商 Key、
   数据库后端（SQLite / Postgres）。
4. **相关日志**（请先**脱敏**：删掉 `sk-` / `whsec_` / Cookie / 真实邮箱）。
5. **`request_id`**（如果是 API 报错——服务端的错误响应里会带它）。
6. 如果是生成结果问题，附上 `python -m reelagent report <project_id>`
   的输出（它含评测指标与 span 统计）。

### 安全漏洞

**不要开公开 Issue。** 请按 [SECURITY.md](SECURITY.md) 的
「如何报告漏洞」一节私下报告。

### 生成质量问题

如果成片效果不好，请区分是**哪一类**问题——这决定了修哪里：

| 症状 | 先看什么 |
|---|---|
| 角色变脸 / 场景漂移 | `.evaluation.metrics` 里的 `clue_reference_coverage` 与 `prompt_clue_fidelity`。如果它们 < 1.0，是**编排层**的 bug（约束没传下去）；如果都是 1.0，是模型能力问题 |
| 评测指标是 `N/A` | 说明「没有可度量对象」——检查 Planner 有没有登记线索、ArtDirector 有没有生成基准图 |
| 色调漂移 | `palette_stability`。低于 0.5 会触发门禁失败 |
| 故事讲不通 | `.evaluation.judge` 的 `top_issues` 与 `suggestions` |
| 成本超预期 | `.cost.by_category` 与 `.routing`（路由决策与跳过次数） |

---

## 相关文档

- [README.md](README.md) —— 产品概览与快速开始
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) —— 架构与数据模型
- [docs/adr/](docs/adr/) —— 架构决策记录
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) —— 部署与环境变量
- [docs/OPERATIONS.md](docs/OPERATIONS.md) —— 运维 Runbook
- [docs/API.md](docs/API.md) —— REST API 参考
- [SECURITY.md](SECURITY.md) —— 安全模型
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) —— 行为准则
- [CHANGELOG.md](CHANGELOG.md) —— 版本历史与已知问题
