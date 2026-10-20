# 部署指南（DEPLOYMENT.md）

> 适用版本：**1.0.0**
>
> 本文描述把一个已完成、可部署的 ReelAgent 实例跑起来所需的全部步骤。
> 产品**尚未上线运营**，因此文中不含任何真实客户、域名或容量数据；
> 所有示例值都是占位符。
>
> ℹ️ **先读这一节**：仓库**已提供**容器化文件（`Dockerfile`、
> `docker-compose.yml`、`docker-compose.dev.yml`、`Makefile`）、
> Alembic 配置（`alembic.ini` + `reelagent_cloud/db/migrations/`）
> 与控制台界面（`web/templates/` + `web/static/`）。
> 部署文件清单见[第 1.4 节](#14-部署文件清单仓库已提供)。

---

## 目录

- [1. 部署路径总览](#1-部署路径总览)
- [2. 环境变量完整清单](#2-环境变量完整清单)
- [3. 生产环境启动校验](#3-生产环境启动校验)
- [4. 域名与 HTTPS](#4-域名与-https)
- [5. 数据库与 schema 初始化](#5-数据库与-schema-初始化)
- [6. Stripe 配置](#6-stripe-配置)
- [7. 备份与恢复](#7-备份与恢复)
- [8. 升级与回滚](#8-升级与回滚)
- [9. 可观测性接入](#9-可观测性接入)
- [10. 部署检查清单](#10-部署检查清单)

---

## 1. 部署路径总览

三条路径，按规模与需求选择：

| 路径 | 适用场景 | 数据库 | 存储 | Redis | Stripe |
|---|---|---|---|---|---|
| **A. 单机本地** | 开发、演示、单机创作 | SQLite | local | 不用 | 不用 |
| **B. 云托管容器** | 小规模生产（推荐起点） | Postgres | S3 兼容 | 建议 | 按需 |
| **C. Kubernetes** | 需要滚动更新、多副本、HPA | Postgres | S3 兼容 | 需要 | 按需 |

---

### 1.1 路径 A：单机本地（SQLite + local 存储）

**用途**：验证功能、跑通流程、单机创作。**不建议用于多用户生产环境**
（生产启动校验会直接拒绝 SQLite，见第 3 节）。

```bash
# 1) 准备 Python 环境（要求 >= 3.11）
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2) 安装
pip install -e ".[dev]"       # 开发；生产去掉 [dev]

# 3) 复制配置（注意：见 1.4 节的缺口说明）
cp .env.example .env

# 4) 启动 API（非生产环境会自动建表）
reelagent-server --host 127.0.0.1 --port 8000 --reload

# 5) 另开一个终端启动 worker
reelagent-worker --once           # 只跑一个任务（调试）
reelagent-worker --concurrency 2  # 常驻
```

各入口的含义（来自 `pyproject.toml` 的 `[project.scripts]`）：

| 命令 | 实现 | 用途 |
|---|---|---|
| `reelagent` | `reelagent.cli:main` | **引擎** CLI（`demo` / `run` / `resume` / `status` / `spans` / …） |
| `reelagent-server` | `reelagent_cloud.server:main` | HTTP 服务 |
| `reelagent-worker` | `reelagent_cloud.worker:main` | 任务执行进程 |
| `reelagent-admin` | `reelagent_cloud.admin:main` | ⚠️ **该模块不存在**，执行会报 `ModuleNotFoundError`（见 1.4） |

`reelagent-server` 的可用参数：

```bash
reelagent-server --host 0.0.0.0 --port 8000 [--reload] [--workers N]
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--host` | `0.0.0.0` | 容器内需要监听全网卡 |
| `--port` | `8000` | |
| `--reload` | 关 | 开发模式，代码变更自动重载（**会强制 workers=1**） |
| `--workers` | `1` | 进程数，生产建议 CPU 核数 |

⚠️ `--workers N` 表示有 N 个进程，**每个进程都会建自己的连接池**
（见第 2 节的容量公式）。

`reelagent-worker` 的可用参数：

```bash
reelagent-worker [--once] [--concurrency N] [--worker-id ID]
```

| 参数 | 说明 |
|---|---|
| `--once` | 只领取并执行一个任务后退出（调试用，会打印 `processed: <job_id>`） |
| `--concurrency` | 覆盖 `REEL_WORKER__CONCURRENCY` |
| `--worker-id` | 覆盖自动生成的 worker ID（默认 `worker-<8位hex>`） |

**引擎单独使用**（完全不需要云层）：

```bash
python -m reelagent demo                              # 内置样例，零配置跑通
python -m reelagent run "深夜的便利店，她找到一把黄铜钥匙。"
python -m reelagent run story.txt --style cyberpunk --aspect 16:9 --max-shots 10
python -m reelagent resume <项目ID>
python -m reelagent status <项目ID>
python -m reelagent spans <项目ID>
python -m reelagent report <项目ID>
python -m reelagent config
```

产物落在 `workspace/projects/<项目ID>/final/`。

---

### 1.2 路径 B：云托管容器（推荐起点）

#### 组件清单

| 组件 | 必需 | 说明 |
|---|---|---|
| PostgreSQL | ✅ | ≥ 13。生产环境**强制**（SQLite 会被启动校验拒绝） |
| 应用镜像 | ✅ | 同时用作 API 与 worker（同一镜像，不同启动命令） |
| 对象存储 | 建议 | 本地盘可用但扩容与多副本会出问题 |
| Redis | 建议 | 限流共享配额；**不启用时限流退化为进程内实现**（单实例正确，多副本会各自计数） |
| 反向代理 | ✅ | TLS 终止、`X-Forwarded-For` |
| Stripe | 按需 | 不配置则降级为「纯额度模式」 |

#### 1.2.1 容器镜像（仓库已提供）

仓库根目录已提供：

| 文件 | 用途 |
|---|---|
| `Dockerfile` | 生产镜像 |
| `Dockerfile.dev` | 开发镜像 |
| `.dockerignore` | 构建上下文裁剪 |
| `docker-compose.yml` | 生产形态：`postgres` + `redis` + `migrate` + `api` + `worker` |
| `docker-compose.dev.yml` | 本地依赖：`postgres` + `redis` + `minio` + `minio-init` |
| `Makefile` | `make docker-build` / `docker-up` / `docker-down` / `docker-logs` 等 |

```bash
make help          # 列出全部目标
make docker-build  # 构建生产镜像
make docker-up     # 起完整生产形态（migrate → api → worker）
make docker-logs
make docker-down   # 停止并移除容器（**保留**数据卷）
```

> ℹ️ `docker-compose.yml` 里 `migrate` 是一个一次性服务，
> `api` 与 `worker` 通过依赖关系等它成功后才启动——
> 这样避免了多副本并发跑迁移。
>
> `docker-compose.dev.yml` 只起依赖（含 MinIO 做 S3 兼容对象存储），
> 配合 `make dev && make api && make worker` 在宿主上裸跑 Python，
> 调试体验更好。

**下面保留一份最小 Dockerfile 作为参考**
（真实文件以仓库根目录的 `Dockerfile` 为准）：

```dockerfile
# ---------- 构建阶段 ----------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY pyproject.toml README.md ./
COPY reelagent ./reelagent
COPY reelagent_cloud ./reelagent_cloud

# 只装运行依赖（含 s3 可选依赖；如需 Sentry 再加 sentry）
RUN pip install --prefix=/install ".[s3]"

# ---------- 运行阶段 ----------
FROM python:3.12-slim

# ffmpeg 用于合成 MP4；不装也能跑（自动降级为 GIF + WAV）
# 注意：如果没有 ffmpeg，引擎会用 PIL 生成真实的多帧 GIF，仍然交付成片
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl \
 && rm -rf /var/lib/apt/lists/*

# 非 root 运行
RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=builder /install /usr/local
COPY --chown=app:app reelagent ./reelagent
COPY --chown=app:app reelagent_cloud ./reelagent_cloud
COPY --chown=app:app pyproject.toml README.md ./

# 产物与临时工作区（生产建议改挂外部卷或直接用 S3）
RUN mkdir -p /app/workspace && chown -R app:app /app/workspace
VOLUME ["/app/workspace"]

USER app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# 存活探针不碰数据库，适合在这里做 healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/livez || exit 1

# 默认启动 API；worker 用 command 覆盖
CMD ["reelagent-server", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
```

**worker 的启动命令**（在 compose 或 K8s 里覆盖 `command`）：

```bash
reelagent-worker --concurrency 2
```

#### 1.2.2 docker-compose（仓库已提供）

仓库根目录的 `docker-compose.yml` 是生产形态，
服务为 `postgres` + `redis` + `migrate` + `api` + `worker`，
命名卷为 `pgdata` / `redisdata` / `artifacts`；
`docker-compose.dev.yml` 是本地依赖形态，
服务为 `postgres` + `redis` + `minio` + `minio-init`。

```bash
docker compose up -d          # 或 make docker-up
docker compose ps
docker compose logs -f api worker
docker compose down           # 保留数据卷
docker compose down -v        # ⚠️ 连数据卷一起删
```

**下面保留一份参考配置**（含 MinIO 做 S3 兼容对象存储的写法，
适合在生产形态之外单独验证对象存储）。真实文件以仓库为准：

```yaml
# 参考配置 —— 含 MinIO，用于验证 S3 兼容对象存储
name: reelagent

services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: reelagent
      POSTGRES_PASSWORD: change-me-in-production
      POSTGRES_DB: reelagent
      # 连接数上限：留出余量给运维与迁移
      POSTGRES_INITDB_ARGS: "--data-checksums"
    command:
      - postgres
      - -c
      - max_connections=200
      - -c
      - shared_buffers=512MB
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U reelagent -d reelagent"]
      interval: 10s
      timeout: 5s
      retries: 5
    ports:
      - "5432:5432"          # 生产不要暴露到公网

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes", "--maxmemory-policy", "noeviction"]
    volumes:
      - redisdata:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5

  # S3 兼容对象存储（本地验证用；生产请用云对象存储）
  minio:
    image: minio/minio:latest
    restart: unless-stopped
    command: ["server", "/data", "--console-address", ":9001"]
    environment:
      MINIO_ROOT_USER: reelagent
      MINIO_ROOT_PASSWORD: change-me-in-production
    volumes:
      - miniodata:/data
    ports:
      - "9000:9000"
      - "9001:9001"
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 10s
      timeout: 5s
      retries: 5

  api:
    build: .
    restart: unless-stopped
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_healthy}
      minio: {condition: service_healthy}
    command: ["reelagent-server", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
    environment:
      REEL_ENV: prod
      REEL_BASE_URL: https://reelagent.example.com
      REEL_SECURITY__SECRET_KEY: ${REEL_SECURITY__SECRET_KEY:?必须设置}
      REEL_SECURITY__HTTPS_ONLY: "true"
      REEL_SECURITY__ALLOWED_HOSTS: '["reelagent.example.com"]'
      REEL_DATABASE__URL: postgresql+asyncpg://reelagent:change-me-in-production@postgres:5432/reelagent
      REEL_REDIS__ENABLED: "true"
      REEL_REDIS__URL: redis://redis:6379/0
      REEL_STORAGE__BACKEND: s3
      REEL_STORAGE__S3_BUCKET: reelagent-artifacts
      REEL_STORAGE__S3_ENDPOINT_URL: http://minio:9000
      REEL_STORAGE__S3_ACCESS_KEY_ID: reelagent
      REEL_STORAGE__S3_SECRET_ACCESS_KEY: change-me-in-production
      REEL_STORAGE__S3_REGION: us-east-1
      # 视频供应商（生产必需，否则生成请求会被拒绝）
      REEL_PROVIDERS__VIDEO__PROVIDER: ark
      REEL_PROVIDERS__VIDEO__MODEL: doubao-seedance-1-0-pro-250528
      REEL_PROVIDERS__VIDEO__BASE_URL: https://ark.cn-beijing.volces.com/api/v3
      REEL_PROVIDERS__VIDEO__API_KEY: ${ARK_API_KEY:?必须设置}
      REEL_PROVIDERS__IMAGE__API_KEY: ${ARK_API_KEY:?必须设置}
      REEL_PROVIDERS__LLM__API_KEY: ${DEEPSEEK_API_KEY:?必须设置}
      REEL_PROVIDERS__TTS__API_KEY: ${DASHSCOPE_API_KEY:?必须设置}
      REEL_OBSERVABILITY__LOG_FORMAT: json
    ports:
      - "8000:8000"
    volumes:
      - workspace:/app/workspace

  worker:
    build: .
    restart: unless-stopped
    depends_on:
      postgres: {condition: service_healthy}
      minio: {condition: service_healthy}
    command: ["reelagent-worker", "--concurrency", "2"]
    environment:
      # 与 api 完全相同的配置（见下文的「配置共享」提示）
      REEL_ENV: prod
      REEL_BASE_URL: https://reelagent.example.com
      REEL_SECURITY__SECRET_KEY: ${REEL_SECURITY__SECRET_KEY:?必须设置}
      REEL_SECURITY__HTTPS_ONLY: "true"
      REEL_SECURITY__ALLOWED_HOSTS: '["reelagent.example.com"]'
      REEL_DATABASE__URL: postgresql+asyncpg://reelagent:change-me-in-production@postgres:5432/reelagent
      REEL_STORAGE__BACKEND: s3
      REEL_STORAGE__S3_BUCKET: reelagent-artifacts
      REEL_STORAGE__S3_ENDPOINT_URL: http://minio:9000
      REEL_STORAGE__S3_ACCESS_KEY_ID: reelagent
      REEL_STORAGE__S3_SECRET_ACCESS_KEY: change-me-in-production
      REEL_STORAGE__S3_REGION: us-east-1
      REEL_PROVIDERS__VIDEO__API_KEY: ${ARK_API_KEY:?必须设置}
      REEL_PROVIDERS__IMAGE__API_KEY: ${ARK_API_KEY:?必须设置}
      REEL_PROVIDERS__LLM__API_KEY: ${DEEPSEEK_API_KEY:?必须设置}
      REEL_PROVIDERS__TTS__API_KEY: ${DASHSCOPE_API_KEY:?必须设置}
      # 优雅停机宽限期（应小于容器编排的 terminationGracePeriod）
      REEL_WORKER__SHUTDOWN_GRACE_SECONDS: "60"
    volumes:
      - workspace:/app/workspace

volumes:
  pgdata:
  redisdata:
  miniodata:
  workspace:
```

> ⚠️ **配置共享的关键点**：**API 进程与 worker 进程必须使用完全相同的
> 供应商 Key、`SECRET_KEY`、数据库与存储配置。**
> 原因：
> - `SECRET_KEY` 不一致 → BYOK 密钥在 worker 侧解不开
>   （BYOK 用 Fernet 加密，密钥由 `SECRET_KEY` 派生），
>   worker 会静默跳过解不开的供应商（`decrypt_byok_map` 的
>   `except TokenError: continue`），导致用错 Key 去调供应商。
> - 存储配置不一致 → worker 上传到 A bucket，API 从 B bucket 读取。
> - 供应商 Key 不一致 → `resolve_engine_mode()` 在生产环境的判定结果不同，
>   可能出现「API 允许创建任务但 worker 拒绝执行」。
>
> **建议用一个 `.env` 文件让两个服务共享**，而不是在 compose 里各写一份
> （上面的示例为了自包含才重复了一遍）。

#### 1.2.3 用 `.env` 管理配置

`Settings` 的 `SettingsConfigDict` 配置了：

```python
env_prefix="REEL_",
env_nested_delimiter="__",
env_file=(".env", ".env.local"),
env_file_encoding="utf-8",
extra="ignore",
case_sensitive=False,
```

因此：

- 嵌套项写作 `REEL_<SECTION>__<FIELD>`（如 `REEL_DATABASE__URL`）。
- 更深的嵌套写作 `REEL_PROVIDERS__VIDEO__API_KEY`。
- 顶层项写作 `REEL_<FIELD>`（如 `REEL_ENV`）。
- 会依次读 `.env` 与 `.env.local`，后者覆盖前者。
  **`.env.local` 适合放本机私密配置，应当加入 `.gitignore`。**

⚠️ **`.env.example` 当前只包含引擎层变量**
（`REEL_MODE` / `REEL_WORKSPACE` / `REEL_LLM_*` / `REEL_IMAGE_*` …），
**不含任何云层变量**。第 2 节的清单是云层变量的完整来源，
建议你据此扩充自己的 `.env`。

---

### 1.3 路径 C：Kubernetes

#### 1.3.1 配置（Secret + ConfigMap）

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: reelagent-secrets
  namespace: reelagent
type: Opaque
stringData:
  REEL_SECURITY__SECRET_KEY: "<用 openssl rand -base64 48 生成>"
  REEL_DATABASE__URL: "postgresql+asyncpg://reelagent:<pw>@pg.internal:5432/reelagent"
  REEL_PROVIDERS__LLM__API_KEY: "<deepseek key>"
  REEL_PROVIDERS__IMAGE__API_KEY: "<ark key>"
  REEL_PROVIDERS__VIDEO__API_KEY: "<ark key>"
  REEL_PROVIDERS__TTS__API_KEY: "<dashscope key>"
  REEL_STORAGE__S3_ACCESS_KEY_ID: "<s3 ak>"
  REEL_STORAGE__S3_SECRET_ACCESS_KEY: "<s3 sk>"
  REEL_BILLING__SECRET_KEY: "<stripe sk>"
  REEL_BILLING__WEBHOOK_SECRET: "<stripe whsec>"
  REEL_OBSERVABILITY__SENTRY_DSN: ""      # 可选
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: reelagent-config
  namespace: reelagent
data:
  REEL_ENV: "prod"
  REEL_BASE_URL: "https://reelagent.example.com"
  REEL_SUPPORT_EMAIL: "support@example.com"
  REEL_PRODUCT_NAME: "ReelAgent"
  REEL_SECURITY__HTTPS_ONLY: "true"
  REEL_SECURITY__ALLOWED_HOSTS: '["reelagent.example.com"]'
  REEL_SECURITY__SESSION_MAX_AGE_SECONDS: "1209600"
  REEL_SECURITY__ALLOW_SIGNUP: "true"
  REEL_REDIS__ENABLED: "true"
  REEL_REDIS__URL: "redis://redis.reelagent.svc.cluster.local:6379/0"
  REEL_STORAGE__BACKEND: "s3"
  REEL_STORAGE__S3_BUCKET: "reelagent-artifacts"
  REEL_STORAGE__S3_REGION: "cn-beijing"
  REEL_STORAGE__S3_ENDPOINT_URL: ""
  REEL_DATABASE__POOL_SIZE: "10"
  REEL_DATABASE__MAX_OVERFLOW: "20"
  REEL_DATABASE__STATEMENT_TIMEOUT_MS: "30000"
  REEL_WORKER__CONCURRENCY: "2"
  REEL_WORKER__SHOT_CONCURRENCY: "3"
  REEL_WORKER__LEASE_SECONDS: "900"
  REEL_WORKER__HEARTBEAT_SECONDS: "20"
  REEL_WORKER__SHUTDOWN_GRACE_SECONDS: "60"
  REEL_OBSERVABILITY__LOG_FORMAT: "json"
  REEL_OBSERVABILITY__LOG_LEVEL: "INFO"
  REEL_OBSERVABILITY__METRICS_ENABLED: "true"
  REEL_BILLING__ENABLED: "true"
  REEL_BILLING__CURRENCY: "cny"
  REEL_BILLING__MARKUP_MULTIPLIER: "3.0"
  REEL_BILLING__CREDITS_PER_USD: "100"
  REEL_EMAIL__ENABLED: "false"
```

#### 1.3.2 API Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: reelagent-server
  namespace: reelagent
spec:
  replicas: 2
  selector:
    matchLabels: {app: reelagent-server}
  strategy:
    type: RollingUpdate
    rollingUpdate: {maxSurge: 1, maxUnavailable: 0}
  template:
    metadata:
      labels: {app: reelagent-server}
    spec:
      # 连接池容量 = POOL_SIZE + MAX_OVERFLOW = 30，× 副本数
      # 必须小于 Postgres max_connections 减去其它消费方
      containers:
        - name: server
          image: registry.example.com/reelagent:1.0.0
          command: ["reelagent-server", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
          ports:
            - {name: http, containerPort: 8000}
          envFrom:
            - configMapRef: {name: reelagent-config}
            - secretRef: {name: reelagent-secrets}
          resources:
            requests: {cpu: "250m", memory: "512Mi"}
            limits: {cpu: "2", memory: "2Gi"}
          # 存活探针：不查数据库。数据库抖动不该导致容器被杀掉重启
          livenessProbe:
            httpGet: {path: /livez, port: http}
            initialDelaySeconds: 15
            periodSeconds: 20
            timeoutSeconds: 5
            failureThreshold: 3
          # 就绪探针：查数据库 + 存储真实读写
          readinessProbe:
            httpGet: {path: /readyz, port: http}
            initialDelaySeconds: 10
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3
          # 启动探针：给迁移检查留时间
          startupProbe:
            httpGet: {path: /livez, port: http}
            periodSeconds: 5
            failureThreshold: 24
          volumeMounts:
            - {name: workspace, mountPath: /app/workspace}
      volumes:
        - name: workspace
          emptyDir: {sizeLimit: 20Gi}
---
apiVersion: v1
kind: Service
metadata:
  name: reelagent-server
  namespace: reelagent
spec:
  selector: {app: reelagent-server}
  ports:
    - {name: http, port: 80, targetPort: http}
```

#### 1.3.3 Worker Deployment（关键：优雅停机）

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: reelagent-worker
  namespace: reelagent
spec:
  replicas: 2
  selector:
    matchLabels: {app: reelagent-worker}
  strategy:
    type: RollingUpdate
    rollingUpdate: {maxSurge: 1, maxUnavailable: 0}
  template:
    metadata:
      labels: {app: reelagent-worker}
    spec:
      # ⚠️ 必须 > REEL_WORKER__SHUTDOWN_GRACE_SECONDS(60)
      # 否则 SIGTERM 后任务会在收尾前被 SIGKILL，留下僵尸任务
      # 又因为宽限期 60s，这里给 120s 留出余量
      terminationGracePeriodSeconds: 120
      containers:
        - name: worker
          image: registry.example.com/reelagent:1.0.0
          command: ["reelagent-worker", "--concurrency", "2"]
          envFrom:
            - configMapRef: {name: reelagent-config}
            - secretRef: {name: reelagent-secrets}
          resources:
            # PIL 渲染多帧 GIF 有内存峰值，按 CONCURRENCY 线性估算并留 50% 余量
            requests: {cpu: "500m", memory: "1Gi"}
            limits: {cpu: "4", memory: "4Gi"}
          volumeMounts:
            # ⚠️ 强烈建议把临时工作区放在独立的、可随时清空的卷上，
            #    不要与对象存储或 PG 数据目录共用一块盘（见 OPERATIONS 第 7 节）
            - {name: engine-workspace, mountPath: /app/workspace/engine}
      volumes:
        - name: engine-workspace
          emptyDir: {sizeLimit: 50Gi}
```

> ⚠️ **不要给 worker 配 `livenessProbe` 指向 `/livez`**：
> worker 不提供 HTTP 服务。要判断 worker 是否活着，
> 用指标 `reelagent_worker_active`，或让 worker 暴露一个健康端口（当前没有）。

#### 1.3.4 Ingress

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: reelagent
  namespace: reelagent
  annotations:
    # 上传产物可能较大（REEL_STORAGE__MAX_UPLOAD_MB 默认 32）
    nginx.ingress.kubernetes.io/proxy-body-size: "64m"
    # 生成任务的提交与轮询都是短请求，但 /readyz 等不应被缓存
    nginx.ingress.kubernetes.io/proxy-read-timeout: "120"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "120"
    # ⚠️ 不要启用任何会改写请求体的插件/中间件：
    #    Strip Webhook 的验签依赖原始请求体
    cert-manager.io/cluster-issuer: letsencrypt-prod
spec:
  ingressClassName: nginx
  tls:
    - hosts: [reelagent.example.com]
      secretName: reelagent-tls
  rules:
    - host: reelagent.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: reelagent-server
                port: {number: 80}
```

`uvicorn` 启动时已设置 `proxy_headers=True` 与
`forwarded_allow_ips="127.0.0.1"`。因此你的 Ingress / 代理
**必须**从集群内地址转发（这样 `client_ip()` 才会采信
`X-Forwarded-For`，见第 4.4 节）。

#### 1.3.5 HPA（可选）

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: reelagent-worker
  namespace: reelagent
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: reelagent-worker
  minReplicas: 2
  maxReplicas: 10
  metrics:
    # 用队列深度做 HPA 需要自定义指标适配器把 Prometheus 指标暴露给 HPA。
    # 备选：用 CPU 做近似（生成是 CPU 密集型的，相关性尚可）。
    - type: Resource
      resource:
        name: cpu
        target: {type: Utilization, averageUtilization: 70}
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
    scaleDown:
      # 缩容要慢：正在跑的任务不应该被打断
      stabilizationWindowSeconds: 600
```

> ⚠️ **扩 worker 前必须算连接池账**：
> `(POOL_SIZE + MAX_OVERFLOW) × worker 进程数` 会挤占 Postgres 连接。
> 10 个副本 × 2 workers × 30 = 600 连接，远超默认 `max_connections=100`。
> **这是扩容最常见的踩坑点**（详见 [OPERATIONS.md](OPERATIONS.md) 第 6 节）。

---

### 1.4 部署文件清单（仓库已提供）

仓库根目录已包含以下文件，**开箱可用**：

| 文件 | 用途 |
|---|---|
| `Dockerfile` / `Dockerfile.dev` / `.dockerignore` | 生产 / 开发镜像与构建上下文裁剪 |
| `docker-compose.yml` | 生产形态（`postgres` + `redis` + `migrate` + `api` + `worker`） |
| `docker-compose.dev.yml` | 本地依赖（`postgres` + `redis` + `minio` + `minio-init`） |
| `alembic.ini` + `reelagent_cloud/db/migrations/` | 数据库迁移（`script_location = reelagent_cloud/db/migrations`） |
| `Makefile` | `make dev / api / worker / migrate / test / lint / typecheck / docker-*` |
| `.env.example` / `.env.production.example` | 环境变量样例（后者是生产形态） |
| `reelagent_cloud/web/templates/` + `web/static/` | 控制台界面与静态资源（`css/app.css`、`js/app.js`、`favicon.svg`） |
| `tests/conftest.py` + `tests/test_cloud_*.py` | 云层测试套件（pytest fixture 驱动真实 FastAPI 应用） |

**仍需注意的一处缺口**：

| 缺口 | 后果 | 应对 |
|---|---|---|
| `reelagent_cloud/admin.py` | `reelagent-admin` 命令报 `ModuleNotFoundError`（`pyproject.toml` 声明了该入口） | 不要使用该命令；用 CLI / 运维脚本代替 |

另外，`reelagent_cloud/db/migrations/versions/` 目录已建但**内容为空**——
需要生成首个版本：

```bash
REEL_ENV=staging alembic revision --autogenerate -m "initial schema"
# ⚠️ 生成后必须人工审查（枚举是 VARCHAR+CHECK、JSON 用 JSONB、
#    复合唯一约束与 CHECK 约束都要出现）。详见第 5.3 节。
```

---

## 2. 环境变量完整清单

命名规则：`env_prefix="REEL_"`、`env_nested_delimiter="__"`。
因此 `REEL_<SECTION>__<FIELD>`（嵌套）与 `REEL_<FIELD>`（顶层）。

### 2.1 顶层（产品与引擎参数）

| 变量 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `REEL_ENV` | `dev` \| `staging` \| `prod` | `dev` | **`prod` 会启用严格启动校验**（见第 3 节） |
| `REEL_PRODUCT_NAME` | str | `ReelAgent` | 显示名称 |
| `REEL_BASE_URL` | str | `http://localhost:8000` | 对外访问地址。**生产必须 https** |
| `REEL_SUPPORT_EMAIL` | str | `support@reelagent.local` | 支持邮箱（出现在页面与文档里） |
| `REEL_DOCS_URL` | str | `/docs` | 用于展示的文档链接 |
| `REEL_STYLE` | str | `cinematic` | 默认风格（`cinematic`/`anime`/`cyberpunk`/`ink`/`realistic`） |
| `REEL_ASPECT_RATIO` | str | `9:16` | 默认画幅（`9:16`/`16:9`/`1:1`/`4:3`/`3:4`） |
| `REEL_MAX_SHOTS_PER_PROJECT` | int | `12` | 单项目镜头数硬上限（与套餐限制取较小值） |
| `REEL_MAX_BASE_IMAGES` | int | `6` | 单项目基准图上限 |
| `REEL_JOB_BUDGET_USD` | float | `2.0` | 单任务预算上限，超预算时路由强制降 `draft` 档 |
| `REEL_WORKSPACE_DIR` | Path | `<项目根>/workspace` | 云层 workspace 根；`engine_workspace` = 此值 + `/engine` |

> ⚠️ 注意 `REEL_WORKSPACE_DIR`（云层，顶层字段 `workspace_dir`）与
> `REEL_WORKSPACE`（**引擎**自己读的扁平变量，见 2.13 节）是**两个不同的设置**。

### 2.2 `database`

| 变量 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `REEL_DATABASE__URL` | str | `sqlite+aiosqlite:///./workspace/reelagent.db` | **生产必须配 Postgres**（`postgresql+asyncpg://...`） |
| `REEL_DATABASE__ECHO` | bool | `false` | 打印 SQL（调试用） |
| `REEL_DATABASE__POOL_SIZE` | int | `10` | 池常驻连接数 |
| `REEL_DATABASE__MAX_OVERFLOW` | int | `20` | 峰值溢出连接数 |
| `REEL_DATABASE__POOL_TIMEOUT` | int | `30` | 等待可用连接的超时秒数 |
| `REEL_DATABASE__POOL_RECYCLE` | int | `1800` | 连接最大存活秒数 |
| `REEL_DATABASE__POOL_PRE_PING` | bool | `true` | 取连接前先 ping |
| `REEL_DATABASE__STATEMENT_TIMEOUT_MS` | int | `30000` | 单语句超时（PG `statement_timeout`） |

**容量公式**（务必算）：

```
(POOL_SIZE + MAX_OVERFLOW) × (API 进程数 + worker 进程数)
    < postgres max_connections − 运维保留
```

### 2.3 `redis`（可选）

| 变量 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `REEL_REDIS__ENABLED` | bool | `false` | **不启用时限流退化为进程内实现** |
| `REEL_REDIS__URL` | str | `redis://localhost:6379/0` | |
| `REEL_REDIS__SOCKET_TIMEOUT` | float | `2.0` | |
| `REEL_REDIS__MAX_CONNECTIONS` | int | `32` | |

> **多副本部署必须启用 Redis**：进程内限流的配额是每进程各自计数的，
> 2 个副本意味着实际限额翻倍。

### 2.4 `storage`

| 变量 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `REEL_STORAGE__BACKEND` | `local` \| `s3` | `local` | |
| `REEL_STORAGE__LOCAL_DIR` | Path | `<项目根>/workspace/storage` | local 后端的对象根目录 |
| `REEL_STORAGE__S3_BUCKET` | str | `""` | **`backend=s3` 时必填**，否则启动时报 `ValueError` |
| `REEL_STORAGE__S3_REGION` | str | `""` | |
| `REEL_STORAGE__S3_ENDPOINT_URL` | str | `""` | 留空用 AWS 默认；MinIO/R2 需填 |
| `REEL_STORAGE__S3_ACCESS_KEY_ID` | Secret | `""` | |
| `REEL_STORAGE__S3_SECRET_ACCESS_KEY` | Secret | `""` | |
| `REEL_STORAGE__S3_PUBLIC_BASE_URL` | str | `""` | CDN 前缀；填了则 `presigned_url()` 直接返回它 |
| `REEL_STORAGE__PRESIGN_TTL_SECONDS` | int | `3600` | |
| `REEL_STORAGE__MAX_UPLOAD_MB` | int | `32` | |

> ⚠️ **S3 后端需要安装可选依赖**：
> `pip install "reelagent[s3]"`（即 `aioboto3>=13.0`）。
> 未安装时 `S3Storage._session()` 会抛
> `UnsupportedOperation("使用 S3 存储需要安装可选依赖：pip install 'reelagent[s3]'")`。

### 2.5 `security`

| 变量 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `REEL_SECURITY__SECRET_KEY` | Secret | `""` | **生产必填**。会话签名、CSRF、BYOK 加密、分享 token 都由它派生 |
| `REEL_SECURITY__API_KEY_PEPPER` | Secret | `""` | API Key 哈希的额外 pepper。非生产环境留空时自动派生为 `<secret_key>:apikey` |
| `REEL_SECURITY__SESSION_COOKIE` | str | `reelagent_session` | |
| `REEL_SECURITY__SESSION_MAX_AGE_SECONDS` | int | `1209600`（14 天） | |
| `REEL_SECURITY__CSRF_COOKIE` | str | `reelagent_csrf` | 非 HttpOnly（前端要读出来放请求头） |
| `REEL_SECURITY__CSRF_HEADER` | str | `x-csrf-token` | |
| `REEL_SECURITY__HTTPS_ONLY` | bool | `false` | **生产必须 `true`**。控制 Cookie 的 `secure` 与 HSTS |
| `REEL_SECURITY__ALLOWED_HOSTS` | list[str] | `["*"]` | **生产不能含 `*`**。不含 `*` 时会挂 `TrustedHostMiddleware` |
| `REEL_SECURITY__LOGIN_MAX_ATTEMPTS` | int | `10` | 登录限流窗口内允许的次数 |
| `REEL_SECURITY__LOGIN_WINDOW_SECONDS` | int | `300` | |
| `REEL_SECURITY__MIN_PASSWORD_LENGTH` | int | `10` | |
| `REEL_SECURITY__ALLOW_SIGNUP` | bool | `true` | 私有部署建议关掉 |
| `REEL_SECURITY__SIGNUP_INVITE_CODE` | Secret | `""` | 非空则注册时必须提供邀请码 |

`ALLOWED_HOSTS` 的写法（pydantic-settings 解析 JSON 数组）：

```bash
REEL_SECURITY__ALLOWED_HOSTS='["reelagent.example.com","www.reelagent.example.com"]'
```

> ⚠️ **`SECRET_KEY` 轮换的后果**（务必知悉）：
> - 所有已签发的会话 Cookie 立即失效（用户需重新登录）。
> - **所有组织的 BYOK 密钥解不开**，用户需要重新填写。
>   代码里对此有明确提示：
>   `TokenError("密钥解密失败；SECRET_KEY 可能已轮换，请重新填写 BYOK 密钥")`。
> - 已发出的分享链接失效。
>
> 因此 `SECRET_KEY` 应当作为**长期稳定**的密钥管理，而不是定期轮换的秘密。
> 如果确实需要轮换，需要一套双密钥过渡方案（当前**未实现**）。

生成一个合适的值：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 2.6 `rate_limit`

| 变量 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `REEL_RATE_LIMIT__ENABLED` | bool | `true` | 关闭后所有限流失效 |
| `REEL_RATE_LIMIT__ANONYMOUS_PER_MINUTE` | int | `30` | 按 IP |
| `REEL_RATE_LIMIT__USER_PER_MINUTE` | int | `240` | 按用户 |
| `REEL_RATE_LIMIT__PROJECT_CREATES_PER_HOUR` | int | `60` | 按组织，防刷额度 |
| `REEL_RATE_LIMIT__API_KEY_PER_MINUTE` | int | `300` | 按单个 API Key |

固定值（不在配置里，写死在 `ratelimit.py::limits_from`）：

| scope | 限额 | 窗口 |
|---|---|---|
| `login` | `LOGIN_MAX_ATTEMPTS`（10） | `LOGIN_WINDOW_SECONDS`（300s） |
| `signup` | 5 | 3600s |
| `upload` | 30 | 3600s |

### 2.7 `worker`

| 变量 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `REEL_WORKER__CONCURRENCY` | int | `2` | 单进程同时执行的 **Job** 数 |
| `REEL_WORKER__SHOT_CONCURRENCY` | int | `3` | 单 Job 内并发**镜头**数 |
| `REEL_WORKER__POLL_INTERVAL_SECONDS` | float | `2.0` | 无任务时的轮询间隔 |
| `REEL_WORKER__LEASE_SECONDS` | int | `900` | **租约时长**。超时未续约视为崩溃，任务可被重新领取 |
| `REEL_WORKER__HEARTBEAT_SECONDS` | int | `20` | 心跳/续约间隔 |
| `REEL_WORKER__JOB_TIMEOUT_SECONDS` | int | `3600` | 任务最长执行时间 |
| `REEL_WORKER__MAX_ATTEMPTS` | int | `3` | 任务最大重试次数 |
| `REEL_WORKER__SHUTDOWN_GRACE_SECONDS` | int | `60` | 优雅停机宽限期 |

> ⚠️ 容器编排的 `terminationGracePeriodSeconds` **必须大于**
> `SHUTDOWN_GRACE_SECONDS`，否则任务会在收尾前被 SIGKILL。

> ℹ️ 生命周期注释里把租约称为「15 分钟」，与默认值 900s 一致。

### 2.8 `email`（可选）

| 变量 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `REEL_EMAIL__ENABLED` | bool | `false` | 关闭时邮件标记为已投递并打日志（开发友好） |
| `REEL_EMAIL__HOST` | str | `""` | |
| `REEL_EMAIL__PORT` | int | `587` | |
| `REEL_EMAIL__USERNAME` | str | `""` | |
| `REEL_EMAIL__PASSWORD` | Secret | `""` | |
| `REEL_EMAIL__USE_TLS` | bool | `true` | |
| `REEL_EMAIL__FROM_ADDRESS` | str | `ReelAgent <no-reply@reelagent.local>` | |

邮件通过 `email_outbox` 表异步投递（最多重试 5 次），
投递由 worker 的 housekeeping 循环负责（每 30 秒一次）。
**因此没有 worker 就没有邮件投递。**

### 2.9 `observability`

| 变量 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `REEL_OBSERVABILITY__LOG_LEVEL` | str | `INFO` | |
| `REEL_OBSERVABILITY__LOG_FORMAT` | `json` \| `console` | `json` | 生产用 json |
| `REEL_OBSERVABILITY__METRICS_ENABLED` | bool | `true` | 关闭后 `/metrics` 返回 404 |
| `REEL_OBSERVABILITY__METRICS_PATH` | str | `/metrics` | 注意：实际路由硬编码为 `/metrics` |
| `REEL_OBSERVABILITY__SLOW_REQUEST_MS` | int | `3000` | 慢请求标记阈值（日志字段 `slow`） |
| `REEL_OBSERVABILITY__SENTRY_DSN` | Secret | `""` | 非空则初始化 Sentry（需装 `reelagent[sentry]`） |

> ℹ️ `METRICS_PATH` 在配置里有，但 `api/routes/system.py` 里的路由装饰器
> 写死为 `@router.get("/metrics")`。**改这个配置不会改变实际路径。**

### 2.10 `billing`（Stripe）

| 变量 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `REEL_BILLING__ENABLED` | bool | `false` | **`true` 时必须配 `SECRET_KEY`**，否则启动报 `ValueError` |
| `REEL_BILLING__SECRET_KEY` | Secret | `""` | Stripe secret key（`sk_...`） |
| `REEL_BILLING__WEBHOOK_SECRET` | Secret | `""` | Stripe webhook signing secret（`whsec_...`） |
| `REEL_BILLING__PUBLISHABLE_KEY` | str | `""` | 前端用（`pk_...`） |
| `REEL_BILLING__CURRENCY` | str | `cny` | |
| `REEL_BILLING__PRICE_STARTER` | str | `""` | 创作者版（¥49/月）的 Stripe Price ID |
| `REEL_BILLING__PRICE_PRO` | str | `""` | 专业版（¥199/月） |
| `REEL_BILLING__PRICE_STUDIO` | str | `""` | 工作室版（¥699/月） |
| `REEL_BILLING__MARKUP_MULTIPLIER` | float | `3.0` | 供应商成本 → 用户扣费的加价倍率 |
| `REEL_BILLING__CREDITS_PER_USD` | int | `100` | 100 额度 = 1 美元（供应商成本口径，未含加价） |

**点数换算**：

```
credits = max(1, ceil(provider_cost_usd × markup_multiplier × credits_per_usd))
默认：1 USD 供应商成本 → 3.0 × 100 = 300 点
```

### 2.11 `providers`

对四个能力分别配置。`<KIND>` ∈ `llm` / `image` / `video` / `tts`。

| 变量模板 | 类型 | 说明 |
|---|---|---|
| `REEL_PROVIDERS__<KIND>__ENABLED` | bool | 默认 `false`（实际是否可用看 `api_key` 是否非空） |
| `REEL_PROVIDERS__<KIND>__PROVIDER` | str | 供应商标识 |
| `REEL_PROVIDERS__<KIND>__MODEL` | str | 模型 ID |
| `REEL_PROVIDERS__<KIND>__API_KEY` | Secret | **是否配置由它决定**（`ProviderSettings.configured`） |
| `REEL_PROVIDERS__<KIND>__BASE_URL` | str | API base |
| `REEL_PROVIDERS__<KIND>__TIMEOUT_SECONDS` | float | 默认 `180.0` |
| `REEL_PROVIDERS__<KIND>__MAX_CONCURRENCY` | int | 默认 `4`，避免触发厂商限流 |
| `REEL_PROVIDERS__<KIND>__MAX_RETRIES` | int | 默认 `3` |
| `REEL_PROVIDERS__<KIND>__CIRCUIT_BREAKER_THRESHOLD` | int | 默认 `5` |
| `REEL_PROVIDERS__<KIND>__CIRCUIT_BREAKER_RESET_SECONDS` | int | 默认 `60` |

**各能力的默认值**：

| 能力 | `PROVIDER` | `MODEL` | `BASE_URL` |
|---|---|---|---|
| `llm` | `deepseek` | `deepseek-chat` | `https://api.deepseek.com/v1` |
| `image` | `ark` | `doubao-seedream-4-0-250828` | `https://ark.cn-beijing.volces.com/api/v3` |
| `video` | `ark` | `doubao-seedance-1-0-pro-250528` | `https://ark.cn-beijing.volces.com/api/v3` |
| `tts` | `dashscope` | `qwen3-tts-flash` | `https://dashscope.aliyuncs.com/api/v1` |

还有一个平级字段：

| 变量 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `REEL_PROVIDERS__ALLOW_BYOK` | bool | `true` | 是否允许用户在控制台填自己的 Key（平台 Key 作兜底） |

> ⚠️ **生产环境必须至少配置视频供应商**，否则
> `Settings._validate_environment()` 会拒绝启动。
> 更严格的限制在 `resolve_engine_mode()`：即使启动通过，
> 运行时若发现该组织既无 BYOK 视频 Key 也无平台视频 Key，
> 会直接抛 `UnsupportedOperation` **拒绝执行**（HTTP 400），
> 而**不会**退到 mock 离线渲染。

> ℹ️ `ProvidersSettings.any_video_configured()` 与
> `self.providers.video.configured` 在启动校验里是等价的
> （前者就是后者的包装），因此这一项实际只检查 `video` 的 Key。

### 2.12 引擎层读取的扁平变量

引擎（`reelagent/config.py`）**自己解析 `.env`**（不依赖 python-dotenv），
读取的是**另一套扁平命名**。这些变量主要影响：

- 用 `python -m reelagent` 独立跑引擎时。
- 云层未显式覆盖时的兜底行为。

| 变量 | 默认 | 说明 |
|---|---|---|
| `REEL_MODE` | `mock` | `mock`（离线，产出真实媒体）或 `live` |
| `REEL_WORKSPACE` | `workspace` | 引擎 workspace 根（相对项目根或绝对路径） |
| `REEL_STYLE` | `cinematic` | 与云层同名变量共享 |
| `REEL_ASPECT_RATIO` | `9:16` | **注意**：`.env.example` 里没有这一项 |
| `REEL_BUDGET_USD` | `2.0` | 单片预算上限（美元） |
| `REEL_CONTEXT_BUDGET` | `24000` | 上下文窗口 token 预算 |
| `REEL_MAX_CONCURRENCY` | `3` | 并发镜头数 |
| `REEL_MAX_ATTEMPTS` | `3` | 单镜失败重试次数 |
| `REEL_FFMPEG` | `""` | ffmpeg 路径；留空则从 PATH 探测，探测不到降级 GIF |
| `REEL_LLM_BASE_URL` | `https://api.deepseek.com/v1` | |
| `REEL_LLM_API_KEY` | `""` | |
| `REEL_LLM_MODEL` | `deepseek-chat` | |
| `REEL_LLM_JUDGE_MODEL` | `""` | 用于评估的更强模型；留空复用上面的 |
| `REEL_IMAGE_PROVIDER` | `ark` | |
| `REEL_IMAGE_MODEL` | `doubao-seedream-4-0-250828` | |
| `REEL_IMAGE_API_KEY` | `""` | |
| `REEL_IMAGE_BASE_URL` | `https://ark.cn-beijing.volces.com/api/v3` | |
| `REEL_VIDEO_PROVIDER` | `ark` | |
| `REEL_VIDEO_MODEL` | `doubao-seedance-1-0-pro-250528` | |
| `REEL_VIDEO_API_KEY` | `""` | |
| `REEL_VIDEO_BASE_URL` | `https://ark.cn-beijing.volces.com/api/v3` | |
| `REEL_TTS_PROVIDER` | `dashscope` | |
| `REEL_TTS_MODEL` | `qwen3-tts-flash` | |
| `REEL_TTS_API_KEY` | `""` | |
| `REEL_TTS_BASE_URL` | `https://dashscope.aliyuncs.com/api/v1` | |

`.env.example` 覆盖的就是这一组（**缺少 `REEL_ASPECT_RATIO`**）。

**云层如何组装引擎配置**（`services/pipeline.py::build_engine_settings`）：

| 引擎字段 | 来源 |
|---|---|
| `mode` | `resolve_engine_mode()` 决定（`live` / `mock` / 生产环境抛异常） |
| `workspace` | `settings.engine_workspace / job_id`（每个任务独立目录） |
| `style` | `org.default_style or settings.style` |
| `aspect_ratio` | `settings.aspect_ratio` |
| `budget_usd` | `job.payload["budget_usd"]` 或 `settings.job_budget_usd` |
| `context_budget` | **硬编码 `24_000`** |
| `max_concurrency` | `settings.worker.shot_concurrency` |
| `max_attempts` | **硬编码 `2`** |
| `ffmpeg` | `shutil.which("ffmpeg")` |
| 各供应商 `api_key` | 组织 BYOK（解密后）**优先**，否则平台配置 |

> ℹ️ 注意 `context_budget` 与 `max_attempts` 在桥接层是**硬编码**的，
> 因此 `REEL_CONTEXT_BUDGET` / `REEL_MAX_ATTEMPTS` 对云层路径**无效**
> （只对 `python -m reelagent` 直接跑引擎有效）。

### 2.13 变量速记表

| 我想调整… | 改这个 |
|---|---|
| 起服务的并发处理能力 | `REEL_DATABASE__POOL_SIZE` / `--workers` |
| 生成吞吐 | `REEL_WORKER__CONCURRENCY` + worker 副本数 |
| 单任务内并行度 | `REEL_WORKER__SHOT_CONCURRENCY` |
| 任务多久被判为「worker 崩了」 | `REEL_WORKER__LEASE_SECONDS` |
| 优雅停机等的时长 | `REEL_WORKER__SHUTDOWN_GRACE_SECONDS` + K8s `terminationGracePeriodSeconds` |
| 单片预算 | `REEL_JOB_BUDGET_USD` |
| 单项目镜头上限 | `REEL_MAX_SHOTS_PER_PROJECT`（还受套餐限制） |
| 单项目基准图上限 | `REEL_MAX_BASE_IMAGES` |
| 用户被扣多少点 | `REEL_BILLING__MARKUP_MULTIPLIER` + `REEL_BILLING__CREDITS_PER_USD` |
| 限流松紧 | `REEL_RATE_LIMIT__*` |
| 日志格式 | `REEL_OBSERVABILITY__LOG_FORMAT` |

---

## 3. 生产环境启动校验

`REEL_ENV=prod` 时，`Settings._validate_environment()` 会检查六项。
**任一项不满足，进程直接拒绝启动**（抛 `ValueError`，消息里列出所有问题）。

设计意图（代码注释原话）：
「生产环境必须在启动时就满足硬性要求——宁可起不来，也不要带病上线。」

| # | 校验项 | 失败消息 | 如何满足 |
|---|---|---|---|
| 1 | `security.secret_key` 非空 | `REEL_SECURITY__SECRET_KEY 未配置` | 生成一个 48 字节随机串 |
| 2 | 数据库不是 SQLite | `生产环境不应使用 SQLite，请配置 REEL_DATABASE__URL` | 配 `postgresql+asyncpg://...` |
| 3 | `security.https_only = true` | `生产环境必须开启 REEL_SECURITY__HTTPS_ONLY=true` | 设为 `true` |
| 4 | `allowed_hosts` 不含 `*` | `生产环境必须显式配置 REEL_SECURITY__ALLOWED_HOSTS` | 写明域名列表 |
| 5 | `base_url` 不以 `http://` 开头 | `生产环境 REEL_BASE_URL 必须是 https` | 改为 `https://...` |
| 6 | 至少配置了视频供应商 Key | `未配置任何视频供应商 Key（REEL_PROVIDERS__VIDEO__API_KEY），产品将无法产出成片` | 配平台 Key 或依赖 BYOK |

**失败时的输出形态**：

```
配置校验未通过（环境=prod）：
  - REEL_SECURITY__SECRET_KEY 未配置
  - 生产环境必须开启 REEL_SECURITY__HTTPS_ONLY=true
  - ...
```

**非生产环境**的行为差异：如果 `api_key_pepper` 为空，
会自动派生为 `<secret_key>:apikey`（保证 clone 即跑）。

### 3.1 其他层级的启动校验

除上述六项，还有两层会在启动时或首次使用时失败：

**① `StorageSettings._check_s3`**（pydantic 模型校验，启动即失败）：

```
storage.backend=s3 时必须配置 s3_bucket
```

**② `BillingSettings._check`**（启动即失败）：

```
billing.enabled=true 时必须配置 stripe secret_key
```

**③ `_ensure_schema()`**（lifespan 里执行，生产环境缺表即失败）：

```python
required = {"users", "organizations", "projects", "jobs", "ledger_entries"}
if missing and settings.is_production:
    raise RuntimeError(f"数据库缺少必要表：{sorted(missing)}。请先执行 `alembic upgrade head`。")
```

⚠️ 这个错误消息要求的 `alembic upgrade head` 已经可用
（`alembic.ini` 与 `reelagent_cloud/db/migrations/` 都在），
但 `versions/` 目录**需要先生成首个版本**。见第 5 节。

**④ `S3Storage._session()`**（首次使用时失败，不是启动时）：

```
使用 S3 存储需要安装可选依赖：pip install 'reelagent[s3]'
```

### 3.2 启动自检流程

```bash
# 1) 校验配置能否加载（会在任何网络/数据库操作之前失败）
python -c "from reelagent_cloud.config import get_settings; s=get_settings(); print(s.safe_dump())"
```

成功的输出是一份**密钥已打码**的配置快照：

```python
{
  "env": "prod",
  "base_url": "https://reelagent.example.com",
  "database": "postgresql",
  "redis": True,
  "storage": "s3",
  "billing": True,
  "rate_limit": True,
  "providers": {
    "llm":    {"provider": "deepseek",  "configured": True},
    "image":  {"provider": "ark",       "configured": True},
    "video":  {"provider": "ark",       "configured": True},
    "tts":    {"provider": "dashscope", "configured": True}
  }
}
```

```bash
# 2) 起服务，看结构化日志里的启动序列
reelagent-server --host 0.0.0.0 --port 8000 --workers 2 2>&1 | head -40
```

期望看到的日志序列（顺序是刻意的）：

```
app_starting   env=prod base_url=https://... config={...}
rate_limiter_ready  backend=redis
app_ready      boot_ms=<n>
```

如果 `backend=memory` 而你配置了 Redis，说明 Redis 连接失败——
`RateLimiter.__init__` 里的 `except` 会打
`redis_unavailable_falling_back` 警告并退到进程内实现。

```bash
# 3) 探针
curl -fsS http://127.0.0.1:8000/livez
curl -fsS http://127.0.0.1:8000/readyz | jq
```

`/readyz` 的期望结果：

```json
{
  "status": "ready",
  "checks": {
    "database":  {"ok": true, "critical": true, "backend": "postgresql"},
    "storage":   {"ok": true, "critical": true, "backend": "s3"},
    "providers": {"ok": true, "critical": false, "video": "ark", "image": "ark", "llm": "deepseek"},
    "billing":   {"ok": true, "critical": false, "enabled": true}
  }
}
```

> `storage` 检查会**真实写一次** `_healthcheck/probe.txt` 再删除。
> 因此看到 `ok: true` 就说明对象存储的写权限是通的。

```bash
# 4) worker 起来后确认它领到了任务循环
reelagent-worker --once
# 输出：no job available  或  processed: <job_id>
```

```bash
# 5) 端到端冒烟（注册 → 账本 → 任务 → 领取 → 结算 → 对账）
python scripts/smoke.py
```

---

## 4. 域名与 HTTPS

### 4.1 必改的三处配置

| 变量 | 示例 | 作用 |
|---|---|---|
| `REEL_BASE_URL` | `https://reelagent.example.com` | 生成分享链接、Stripe 回调地址的基础 |
| `REEL_SECURITY__ALLOWED_HOSTS` | `["reelagent.example.com"]` | 非 `*` 时挂 `TrustedHostMiddleware`，拒绝 Host 头不匹配的请求 |
| `REEL_SECURITY__HTTPS_ONLY` | `true` | 控制 Cookie 的 `secure` 属性 + 下发 HSTS |

### 4.2 TLS 终止位置

两个选择：

**A. 在反向代理 / Ingress 终止（推荐）**

```nginx
server {
    listen 443 ssl http2;
    server_name reelagent.example.com;

    ssl_certificate     /etc/letsencrypt/live/reelagent.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/reelagent.example.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # 让应用知道原始协议与客户端 IP
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # ⚠️ 不要对 /webhooks/ 做任何请求体改写
    client_max_body_size 64m;      # 匹配 REEL_STORAGE__MAX_UPLOAD_MB

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }
}

server {
    listen 80;
    server_name reelagent.example.com;
    return 301 https://$host$request_uri;
}
```

**B. 应用自己处理 TLS**

`reelagent-server` 用的是 `uvicorn.run(...)`，**没有暴露 `ssl_certfile` 参数**
（`server.py` 里只传了 host/port/reload/workers/proxy_headers）。
因此要由应用直接处理 TLS，需要用 `uvicorn` 直接启动：

```bash
uvicorn reelagent_cloud.api.app:app_factory --factory \
  --host 0.0.0.0 --port 8443 \
  --ssl-keyfile /etc/ssl/private/reelagent.key \
  --ssl-certfile /etc/ssl/certs/reelagent.crt \
  --proxy-headers --forwarded-allow-ips 127.0.0.1
```

（`app_factory` 是无参工厂，专门为 `uvicorn --factory` 准备的。）

### 4.3 代理头与安全头

应用侧已经做的（`api/middleware.py::SecurityHeadersMiddleware`）：

| 响应头 | 值 | 条件 |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | 总是 |
| `X-Frame-Options` | `DENY` | 总是 |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | 总是 |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=(), interest-cohort=()` | 总是 |
| `Cross-Origin-Opener-Policy` | `same-origin` | 总是 |
| `Cross-Origin-Resource-Policy` | `cross-origin` | 仅 `/files/` 与 `/s/`（媒体需被页面嵌入） |
| `Cross-Origin-Resource-Policy` | `same-origin` | 其它路径 |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | **仅 `https_only=true`** |
| `Content-Security-Policy` | 见下 | 总是（除非上游已设置） |
| `X-Request-ID` | 见下 | 总是 |
| `Server-Timing` | `app;dur=<ms>` | 总是 |

**CSP 的构成**（`_csp()`）：

```
default-src 'self'
script-src 'self'                          ← 不含 'unsafe-inline'（刻意）
style-src 'self' 'unsafe-inline'           ← 仅样式放行内联
img-src 'self' data: blob:
media-src 'self' blob:
font-src 'self' data:
connect-src 'self' [ws: wss: 仅非生产]
frame-ancestors 'none'
base-uri 'self'
form-action 'self'
object-src 'none'
```

启用 `billing.enabled` 时额外追加：

```
connect-src 'self' https://api.stripe.com
frame-src https://js.stripe.com https://checkout.stripe.com
script-src 'self' https://js.stripe.com
```

> ⚠️ **不要给 CSP 加 `'unsafe-inline'` 到 `script-src`**。
> 代码注释明确写了这是「一开始就定死」的约束，
> 目的是避免「为了先跑起来放宽 CSP 再也收不回来」。
>
> **重要含义**：所有前端 JS 必须是**外部文件**。
> 仓库里的 `reelagent_cloud/web/static/js/app.js` 就是唯一的脚本入口
> （另有 `css/app.css` 与 `favicon.svg`）。
> 新增界面时请把脚本放进这个文件或新增外部文件，
> **不要内联到模板**——否则会被拦掉，而且很容易被误判为
> 「CSP 配错了」而放宽这条约束（那正是代码注释警告的情形）。

**反向代理侧应当补充的头**（应用没做的）：

```nginx
add_header X-XSS-Protection "0" always;   # 现代浏览器已弃用该头，显式关闭避免误配
# 如果应用未开 https_only，在代理层补 HSTS：
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
```

**上游已设置 CSP 时应用不会覆盖**：

```python
if not response.headers.get("Content-Security-Policy"):
    response.headers["Content-Security-Policy"] = _csp(settings)
```

因此你可以在代理层收紧 CSP。

### 4.4 客户端 IP 的正确传递

`deps.py::client_ip()`：

```python
forwarded = request.headers.get("x-forwarded-for", "")
if forwarded:
    first = forwarded.split(",")[0].strip()
    if first and _is_private(first):
        # 第一跳是内网/回环 → 认为是可信代理链，取最后一跳
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        return parts[-1] if parts else first
    return first
real_ip = request.headers.get("x-real-ip", "").strip()
if real_ip:
    return real_ip
return request.client.host if request.client else ""
```

**行为解读**：

| 场景 | 取到的 IP |
|---|---|
| 无代理，直连 | `request.client.host`（真实客户端） |
| 单层可信代理，`XFF: <client>` | `<client>`（因为 `first` 是公网 IP，直接返回它） |
| 多层代理，`XFF: <内网IP>, <client>` | `<client>`（取最后一跳） |
| 攻击者直接伪造 `XFF: 1.2.3.4` | **`1.2.3.4`**（被采信！） |

⚠️ **最后一行是必须靠部署侧防护的点**：
应用**无法区分**「可信代理加的 XFF」与「攻击者伪造的 XFF」。
因此：

- **必须确保应用端口不对公网开放**（只让反向代理能访问）。
  在 K8s 里用 `NetworkPolicy`；在单机上让 uvicorn 只监听 `127.0.0.1`。
- 代理**必须**用 `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`
  （追加而不是覆盖），这样第一跳会是代理自己看到的上游地址。
- `uvicorn` 侧的 `forwarded_allow_ips="127.0.0.1"` 只影响
  uvicorn 自己的 `request.client` 计算，不影响上面这段逻辑。

> 如果应用端口暴露到公网，攻击者可以伪造 XFF 绕过**所有基于 IP 的限流**
> （`anonymous`、`login`、`signup` 三个 scope）。

### 4.5 HTTPS 上线检查

```bash
# ① 证书链
openssl s_client -connect reelagent.example.com:443 -servername reelagent.example.com </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates

# ② HTTP 是否重定向到 HTTPS
curl -sI http://reelagent.example.com | head -3
# 期望：301 或 308，Location: https://...

# ③ 安全头是否齐全
curl -sI https://reelagent.example.com | grep -iE \
  'strict-transport|x-frame|x-content-type|content-security|referrer|permissions-policy|cross-origin'

# ④ Cookie 属性（需要一个已登录会话）
curl -sI -X POST https://reelagent.example.com/login \
  -d 'email=a@b.com&password=x' | grep -i set-cookie
# 期望：reelagent_session=...; HttpOnly; Secure; SameSite=Lax
#       reelagent_csrf=...; Secure; SameSite=Lax（无 HttpOnly，前端要读）
```

---

## 5. 数据库与 schema 初始化

### 5.1 应用启动时的 schema 行为

`api/app.py::_ensure_schema()`：

| 环境 | 行为 |
|---|---|
| 缺表 + `REEL_ENV=prod` | **抛 `RuntimeError`**，拒绝启动，提示执行 `alembic upgrade head` |
| 缺表 + 非生产 | 打 `auto_creating_schema` 警告，执行 `Base.metadata.create_all()` |
| 表齐全 | 什么都不做（**只检查 `{users, organizations, projects, jobs, ledger_entries}` 五张表是否存在，不校验列与索引**） |

⚠️ **注意检查的局限性**：它只检查 5 张表的存在性。
如果 `artifacts` / `shots` / `ledger_entries` 之外的表缺失，
或者表的**列**与模型不一致，启动不会报错，直到运行时才炸。

### 5.2 生产初始化

**推荐方式：生成首个 Alembic 版本并应用。**

```bash
# 1) 从模型生成首个迁移（用 staging 绕过 prod 的严格启动校验）
REEL_ENV=staging alembic revision --autogenerate -m "initial schema"

# 2) 人工审查生成的脚本（见第 5.3 节的关注点清单）

# 3) 应用到数据库
REEL_ENV=staging alembic upgrade head

# 4) 验证（跑下面的表/索引/约束检查 SQL）
```

容器形态下这一步由 `docker-compose.yml` 里的 `migrate` 服务自动完成：

```bash
make docker-up     # migrate → api → worker
```

**兜底方式：用模型定义直接建表**（仅当迁移不可用时，例如首次接入）。

> ⚠️ 这不是迁移方案的替代品，只负责「从零建表」。
> 之后的任何结构变更**必须**走 Alembic。

```python
# scripts/init_schema.py  ——  一次性初始化（仅用于首次上线）
import asyncio

from reelagent_cloud.config import get_settings
from reelagent_cloud.db.session import create_all, dispose_engine, init_engine


async def main() -> None:
    settings = get_settings()
    init_engine(settings.database)
    await create_all()
    await dispose_engine()
    print("schema created")


asyncio.run(main())
```

```bash
REEL_ENV=staging python scripts/init_schema.py   # 用 staging 绕过 prod 的严格校验
```

> ⚠️ 上面刻意用 `REEL_ENV=staging`：`REEL_ENV=prod` 时
> `_ensure_schema()` 会在缺表时直接拒绝启动，
> 而 `create_all()` 可以通过这个脚本直接调用（不经过 lifespan）。
>
> 两条路径**不要混用**：先用 `create_all()` 建了表，
> 再跑 `alembic upgrade head` 会因为「表已存在」而失败
> （此时需要 `alembic stamp head` 把版本标记为已应用）。

**初始化后必须验证索引与约束**：

```sql
-- 表清单（期望 14 张）
SELECT tablename FROM pg_tables
 WHERE schemaname = 'public'
 ORDER BY tablename;
```

期望的 14 张表：

```
api_keys        artifacts       audit_logs      email_outbox
jobs            ledger_entries  memberships     organizations
projects        shots           subscriptions   usage_records
users           webhook_events
```

```sql
-- 关键索引（jobs 表的领取性能依赖它们）
SELECT indexname, indexdef FROM pg_indexes
 WHERE tablename = 'jobs' ORDER BY indexname;
```

期望包含：

| 索引 | 定义 |
|---|---|
| `ix_jobs_claim` | `(status, priority, run_after)` |
| `ix_jobs_lease` | `(status, lease_expires_at)` |
| `ix_jobs_org_created` | `(org_id, created_at)` |
| `uq_job_org_idempotency` | UNIQUE `(org_id, idempotency_key)` |

```sql
-- 关键唯一约束（幂等的基础）
SELECT conname, conrelid::regclass AS table_name, pg_get_constraintdef(oid)
  FROM pg_constraint
 WHERE conname IN (
   'uq_job_org_idempotency', 'uq_ledger_org_idempotency',
   'uq_webhook_provider_event', 'uq_shot_project_key',
   'uq_membership_user_org', 'ck_org_credits_non_negative')
 ORDER BY conname;
```

**六个约束都必须存在**：

| 约束 | 表 | 缺失后果 |
|---|---|---|
| `uq_job_org_idempotency` | `jobs` | 重复提交会创建重复任务、重复扣费 |
| `uq_ledger_org_idempotency` | `ledger_entries` | **Stripe 重复投递会重复发额度** |
| `uq_webhook_provider_event` | `webhook_events` | Webhook 重复处理 |
| `uq_shot_project_key` | `shots` | 同一项目出现重复镜头 |
| `uq_membership_user_org` | `memberships` | 同一用户在同一组织有多条成员关系 |
| `ck_org_credits_non_negative` | `organizations` | **余额可以被扣成负数** |

### 5.3 Alembic 迁移

> ✅ **仓库已提供 `alembic.ini` 与 `reelagent_cloud/db/migrations/`**
> （`script_location = reelagent_cloud/db/migrations`）。
> **但 `versions/` 目录内容为空**，因此需要先生成首个版本。
> 若你的 Fork 还没有这两个文件，下面的「从零接入」步骤同样适用。

`alembic` 已经在主依赖里（`pyproject.toml` 的 `dependencies`），
因此**不需要额外安装**。配置里也已经为迁移目录预留了例外：

```toml
[tool.ruff.lint.per-file-ignores]
"reelagent_cloud/db/migrations/*" = ["E", "F", "I"]

[tool.mypy]
exclude = ["reelagent_cloud/db/migrations/"]
```

```toml
[tool.coverage.run]
omit = ["*/migrations/*", "*/web/templates/*"]
```

**从零接入（仅在缺文件时）**

**步骤 1：初始化**

```bash
alembic init -t async reelagent_cloud/db/migrations
```

（`-t async` 生成异步模板，匹配 asyncpg）

**步骤 2：改 `alembic.ini` 的 `script_location`**

```ini
script_location = reelagent_cloud/db/migrations
# 不要在这里写死 URL，交给 env.py 从应用配置读
sqlalchemy.url =
```

**步骤 3：改 `reelagent_cloud/db/migrations/env.py`**

```python
"""Alembic 环境。

关键点：
  1. URL 从应用配置读（单一事实来源），不写在 alembic.ini 里
  2. target_metadata 指向 models 的 Base，以便 autogenerate 能比对
  3. 必须 import models 模块，否则 Base.metadata 里没有表
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from reelagent_cloud.config import get_settings
from reelagent_cloud.db.base import Base
from reelagent_cloud.db import models  # noqa: F401  —— 必须导入以注册映射

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# URL 从应用配置读（会触发生产环境校验）
config.set_main_option("sqlalchemy.url", get_settings().database.url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # 枚举在模型里是 native_enum=False（VARCHAR + CHECK），
        # 因此不需要 compare_type 的特殊处理
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        # JSONB 变体在不同方言下的渲染差异，交给 SQLAlchemy 处理
        render_as_batch=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    import asyncio

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

**步骤 4：生成首个迁移**

```bash
# 用 staging 绕过 prod 校验（迁移本身不需要生产级配置）
REEL_ENV=staging alembic revision --autogenerate -m "initial schema"
```

**步骤 5：审查生成的迁移脚本**

`--autogenerate` 的输出**必须人工审查**。本项目需要特别注意：

| 关注点 | 说明 |
|---|---|
| **枚举** | 模型用 `make_enum(..., native_enum=False)`，生成的是 `VARCHAR(32)` + CHECK 约束。**不要**让 alembic 生成 PG 原生 `ENUM` 类型（那会导致加值需要 `ALTER TYPE`） |
| **JSON** | `JSONType = JSON().with_variant(JSONB(), "postgresql")`——PG 上应是 `JSONB` |
| **时区** | 所有 `DateTime(timezone=True)`，确认生成的 DDL 是 `TIMESTAMP WITH TIME ZONE` |
| **索引名** | 确认 `ix_jobs_claim` / `ix_jobs_lease` 等显式命名的索引被正确创建 |
| **CHECK 约束** | `ck_org_credits_non_negative` 必须出现在迁移里 |
| **复合唯一约束** | `uq_job_org_idempotency` / `uq_ledger_org_idempotency` / `uq_webhook_provider_event` 必须出现 |

**步骤 6：应用到数据库**

```bash
REEL_ENV=staging alembic upgrade head
```

**步骤 7：验证**（跑 5.2 节的三个 SQL 验证查询）

**步骤 8：把迁移纳入部署流程**

```bash
# 部署流程里，在启动 API 之前执行
alembic upgrade head
```

⚠️ **建议用独立的迁移 Job，而不是在应用启动时自动迁移**：
- 多副本同时启动时，并发跑迁移会互相冲突。
- 迁移失败应该阻止部署继续，而不是让应用带着半迁移的库运行。
- 应用启动时的 `_ensure_schema()` **只检查不修改**，这是刻意的
  （注释：「结构变更必须走 Alembic，否则一次误部署就可能悄悄改掉线上表结构」）。

### 5.4 从 SQLite 迁移到 Postgres

非生产环境用 SQLite 起步后想切到 Postgres 的话：

**没有提供数据迁移工具。** 因为非生产环境的数据通常是可丢弃的
（演示项目），最简单的做法是：

```bash
# 1) 在 Postgres 上初始化 schema
REEL_ENV=staging python scripts/init_schema.py

# 2) 切换配置
REEL_DATABASE__URL=postgresql+asyncpg://user:pw@host:5432/reelagent

# 3) 重新注册账号（SQLite 里的账号不会自动迁移）
```

如果确实需要迁移数据，可以用 `pgloader`：

```bash
pgloader sqlite:///workspace/reelagent.db \
         postgresql://user:pw@host:5432/reelagent
```

⚠️ 但要注意类型差异（枚举的 VARCHAR、JSON 的 JSONB、
时间的时区），迁移后必须逐表核对行数与关键字段。

---

## 6. Stripe 配置

### 6.1 为什么用 Stripe

产品层面的理由（`services/billing.py` 的 docstring）：

- **不需要自己实现账单 UI**：客户门户让用户自己在 Stripe 侧改卡/取消。
- **未配置时优雅降级**：`billing.enabled=false` 时产品变成「纯额度模式」——
  注册赠送额度可正常使用，但没有充值入口。
  这样自托管部署无需依赖 Stripe。

### 6.2 配置步骤

**步骤 1：创建 Stripe 账户与商品**

在 Stripe Dashboard 创建三个**订阅型**商品（免费版不需要）：

| 套餐 | 名称建议 | 价格 | 计费周期 |
|---|---|---|---|
| `starter` | 创作者版 | ¥49 | 每月 |
| `pro` | 专业版 | ¥199 | 每月 |
| `studio` | 工作室版 | ¥699 | 每月 |

记下每个价格的 **Price ID**（形如 `price_1ABC...`）。

**步骤 2：配置应用**

```bash
REEL_BILLING__ENABLED=true
REEL_BILLING__SECRET_KEY=sk_live_...              # 保密
REEL_BILLING__WEBHOOK_SECRET=whsec_...            # 保密，下一步获取
REEL_BILLING__PUBLISHABLE_KEY=pk_live_...         # 可公开
REEL_BILLING__CURRENCY=cny
REEL_BILLING__PRICE_STARTER=price_1ABC...         # 创作者版
REEL_BILLING__PRICE_PRO=price_1DEF...             # 专业版
REEL_BILLING__PRICE_STUDIO=price_1GHI...          # 工作室版
REEL_BILLING__MARKUP_MULTIPLIER=3.0
REEL_BILLING__CREDITS_PER_USD=100
```

⚠️ **`billing.enabled=true` 但没有 `secret_key` 会启动失败**：

```
ValueError: billing.enabled=true 时必须配置 stripe secret_key
```

**步骤 3：配置 Webhook 端点**

在 Stripe Dashboard → Developers → Webhooks → Add endpoint：

| 项 | 值 |
|---|---|
| Endpoint URL | `https://reelagent.example.com/webhooks/stripe` |
| 事件 | 见下表 6 个 |

**必须订阅的 6 个事件**（来自 `_HANDLERS`）：

| 事件 | 处理内容 |
|---|---|
| `checkout.session.completed` | 结账完成 → 激活订阅、发放首期额度、写审计 |
| `customer.subscription.created` | 订阅创建 → 同步套餐/状态/周期 |
| `customer.subscription.updated` | 订阅变更 → 同步（升级/降级/改卡/取消预约） |
| `customer.subscription.deleted` | 订阅删除 → 组织降级为 `free`，恢复水印 |
| `invoice.paid` | 发票支付 → 续费时发放周期额度 |
| `invoice.payment_failed` | 支付失败 → 订阅置 `past_due`（**不动额度**，给宽限期） |

**未订阅的其它事件不会被处理**（如果投递过来会被标记为 `ignored` 并返回 200）。
建议只订阅这 6 个以减少噪音。

**步骤 4：拿到签名密钥**

创建端点后，在端点详情页点 **Reveal** 拿到 `whsec_...`，
填入 `REEL_BILLING__WEBHOOK_SECRET`。

**本地调试用 Stripe CLI**：

```bash
stripe login
stripe listen --forward-to http://127.0.0.1:8000/webhooks/stripe
# CLI 会打印一个临时 whsec_...，用它作为本地的 WEBHOOK_SECRET
stripe trigger checkout.session.completed
```

### 6.3 Webhook 的三条硬性要求

这三条都有对应的代码注释，是出账务事故的高发点：

**① 必须用原始请求体验签**

> 「任何中间件若提前消费/改写 body（例如把 JSON 解析一遍），签名就会对不上。」

因此路由是这样写的：

```python
router = APIRouter(prefix="/webhooks", tags=["webhooks"], include_in_schema=False)

@router.post("/stripe")
async def stripe_webhook(request: Request, settings: AppSettings,
                         stripe_signature: str = Header("", alias="Stripe-Signature")) -> Response:
    payload = await request.body()      # ← 原始字节，不经过任何解析
```

**部署要求**：
- 反向代理**不能**改写请求体（不要开压缩重写、不要开 WAF 的 body 规范化）。
- 不要给这个路由加任何 `Depends` 做 body 解析。
- 如果代理层有 body 大小限制，要保证 Stripe 的事件体放得下。

`/webhooks/*` **不在 OpenAPI 文档里**（`include_in_schema=False`），
因此不要用 `/docs` 来确认它存在。

**② 必须先落库再处理**

```python
row, first = await record_event(session, provider="stripe", event=event)
if not first:
    return "duplicate"
```

`webhook_events(provider, event_id)` 的唯一约束保证只处理一次。
`event_id` 缺失会抛 `ValidationFailed("Webhook 缺少事件 ID")`。

**③ 处理失败必须返回 5xx**

```python
except Exception as exc:
    log.exception("stripe_webhook_processing_failed", event_id=event_id, error=str(exc))
    return JSONResponse(
        {"error": "processing_failed", "event_id": event_id},
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
```

> 「吞掉异常返回 200 会导致用户付了钱没到账。」

**因此：不要在代理层把 5xx 重写成 200。**
Stripe 只对非 2xx 的响应进行重试。

### 6.4 额度发放的幂等链

双重保险，两层都不可省：

| 层 | 机制 | 幂等键 |
|---|---|---|
| 1 | `webhook_events` 唯一约束 | `(provider='stripe', event_id)` |
| 2 | 账本幂等键 | `sub-activate:<subscription_id>`（激活/更新）<br>`invoice:<invoice_id>`（周期续费） |

**价值**：即使第 1 层被绕过（例如运维为了重放而删了
`webhook_events` 记录，见 [OPERATIONS.md](OPERATIONS.md) 第 5.8 节），
第 2 层仍保证额度不会重复发放。

### 6.5 计费流程时序

```
用户                     应用                      Stripe
 │                        │                          │
 │─ 点「升级到专业版」 ──▶│                          │
 │                        │ create_checkout_session()│
 │                        │─────────────────────────▶│
 │                        │   mode=subscription      │
 │                        │   client_reference_id=org│
 │                        │   metadata={org_id,tier} │
 │◀── 303 重定向到 Stripe ─│                          │
 │                                                   │
 │─ 在 Stripe 完成支付 ─────────────────────────────▶│
 │                                                   │
 │                        │◀── webhook: checkout.session.completed ──│
 │                        │  ① 验签（原始 body）      │
 │                        │  ② 写 webhook_events     │
 │                        │  ③ _sync_subscription()  │
 │                        │     · org.plan = 套餐    │
 │                        │     · org.watermark      │
 │                        │     · 拉取 Stripe 订阅详情│
 │                        │     · grant(首期额度)     │
 │                        │       idem=sub-activate:<sub_id>│
 │                        │  ④ status=processed      │
 │◀── 303 回 /app/billing?flash=订阅已生效 ───────────│
 │                                                   │
 │   （一个计费周期后）                                │
 │                        │◀── webhook: invoice.paid ────────────────│
 │                        │   billing_reason=subscription_cycle      │
 │                        │   grant(周期额度)         │
 │                        │     idem=invoice:<invoice_id>            │
 │                                                   │
 │   （用户取消）                                      │
 │                        │◀── webhook: customer.subscription.deleted
 │                        │   sub.status = canceled  │
 │                        │   org.plan = free        │
 │                        │   org.watermark = true   │
```

### 6.6 不配置 Stripe 时的行为

| 功能 | 行为 |
|---|---|
| `/webhooks/stripe` | 直接返回 `{"ignored": "billing disabled"}`（200），打 `stripe_webhook_disabled` 警告 |
| `/app/billing` 页面 | 可访问（显示套餐列表） |
| 点「升级」 | 返回 `UnsupportedOperation("当前部署未开启在线支付，请联系管理员手动充值")`（HTTP 400） |
| 客户端门户 | 同上 |
| `_stripe()` 的所有调用 | 抛 `UnsupportedOperation` |
| 额度系统 | **完全正常**：注册赠送额度可用，可生成、可扣费、可对账 |
| 充值 | 只能由运维用 `credits.grant()` 手工发放 |
| 手工发放额度 | 见 [OPERATIONS.md](OPERATIONS.md) 第 4.5 节 |

手工给某个组织发放额度：

```python
import asyncio
from reelagent_cloud.config import get_settings
from reelagent_cloud.db.session import session_scope
from reelagent_cloud.domain.enums import LedgerKind
from reelagent_cloud.services import credits

async def main() -> None:
    get_settings()
    async with session_scope() as session:
        entry = await credits.grant(
            session,
            org_id="org_01M4Z76RW2WNQXHJE6Y49XNMNA",
            amount=15000,
            kind=LedgerKind.PURCHASE,          # 或 LedgerKind.ADJUSTMENT
            description="财务确认：企业客户线下转账充值",
            idempotency_key="manual-2025-11-001",   # ← 必须唯一，防重复发放
            external_ref="BANK-TXN-202511-001",
        )
        print(f"granted {entry.amount}, balance_after={entry.balance_after}")

asyncio.run(main())
```

⚠️ **`idempotency_key` 必须唯一且可追溯**（用财务单号），
否则重复执行会造成重复发放，而且**无法靠 `reconcile()` 发现**
（因为余额与账本是一致的——只是多发了一笔）。

---

## 7. 备份与恢复

### 7.1 需要备份什么

| 对象 | 重要性 | 说明 |
|---|---|---|
| **PostgreSQL 数据库** | 🔴 最重要 | 全部业务状态 |
| **对象存储** | 🔴 重要 | 产物文件（成片、关键帧、配音） |
| `REEL_SECURITY__SECRET_KEY` | 🔴 重要 | **丢了就无法解密 BYOK 密钥、所有会话失效、分享链接失效** |
| 环境变量 / Secret 清单 | 🔴 重要 | 与 SECRET_KEY 一起保管 |
| `.env` / K8s Secret | 🟠 | 同上 |
| 引擎 workspace | 🟡 可丢 | 临时工作区，任务结束后本就该删 |
| Redis | ⚪ 不必 | 只有限流计数器，丢了重启即可 |
| 日志 | 🟡 | 排障与合规需要，按保留策略 |

### 7.2 数据库备份

**逻辑备份（推荐日常使用）**：

```bash
# 全库自定义格式（支持并行恢复与选择性恢复）
pg_dump -Fc -Z 6 \
  -h "$PGHOST" -U reelagent -d reelagent \
  -f "reelagent-$(date +%Y%m%d-%H%M%S).dump"

# 校验转储完整性（不做恢复，只检查可读）
pg_restore --list "reelagent-$(date +%Y%m%d-%H%M%S).dump" >/dev/null && echo "dump OK"
```

**只备份关键三表**（快速、小体积、用于紧急对账）：

```bash
pg_dump -Fc -t ledger_entries -t webhook_events -t jobs \
  -h "$PGHOST" -U reelagent -d reelagent \
  -f "reelagent-critical-$(date +%Y%m%d-%H%M%S).dump"
```

> **这三张表为什么最重要**：
> - `ledger_entries` —— 只追加的额度事实，丢了**无法重建**（钱对不上）。
> - `webhook_events` —— 支付事件的去重记录，丢了会导致重复发放额度。
> - `jobs` —— 任务状态与租约，丢了在跑的任务会变成孤儿。
>
> `subscriptions` 也很重要（可从 Stripe 侧重建，但麻烦）。

**物理备份 / PITR（推荐生产）**：

```bash
# 用 pgBackRest / WAL-G / 云厂商的自动备份
# 关键要求：开启 WAL 归档，保留至少 7 天的 PITR 窗口
```

检查 WAL 归档是否开启：

```sql
SHOW archive_mode;        -- 期望 on
SHOW archive_command;     -- 期望非空
SHOW wal_level;           -- 期望 replica 或 logical
```

**备份策略建议**：

| 类型 | 频率 | 保留 |
|---|---|---|
| 逻辑全量 `pg_dump -Fc` | 每天 | 30 天 |
| 关键三表 | 每 6 小时 | 7 天 |
| 物理基础备份 + WAL | 每天全量 + 连续 WAL | 14 天 PITR 窗口 |
| 备份异地副本 | 每天同步 | 与主备份同周期 |

### 7.3 对象存储备份

**local 后端**：

```bash
# 用 rsync 做增量镜像（--delete 会同步删除，谨慎使用）
rsync -a --delete \
  --exclude '*/.tmp' \
  "$REEL_STORAGE__LOCAL_DIR/" \
  "backup-host:/backup/reelagent-storage/"
```

**s3 后端**：

```bash
# 用 bucket 复制（跨区域自动复制）
aws s3api put-bucket-replication --bucket reelagent-artifacts \
  --replication-configuration file://replication.json

# 或用 sync 做快照
aws s3 sync "s3://reelagent-artifacts" "s3://reelagent-artifacts-backup/$(date +%Y%m%d)/"
```

> ⚠️ **备份 bucket 必须是私有的**，且**不要在权限不同的环境之间复制**。
> 因为所有租户共享同一个 bucket，
> 一次误配的副本会一次性泄漏全部租户数据。

**版本控制**（推荐）：

```bash
aws s3api put-bucket-versioning --bucket reelagent-artifacts \
  --versioning-configuration Status=Enabled
```

配合生命周期规则，把软删产物的历史版本在 30 天后过期。

### 7.4 密钥与配置备份

**`SECRET_KEY` 是备份里最容易被忘、后果最严重的一项。**

```bash
# 把配置快照存进保险库（不要放在与数据库备份同一位置）
mkdir -p /secure/reelagent-config-$(date +%Y%m%d)

# 配置快照（密钥已打码，可用于确认「当时配了什么」）
REEL_ENV=prod python -c "
from reelagent_cloud.config import get_settings
import json; print(json.dumps(get_settings().safe_dump(), ensure_ascii=False, indent=2))
" > /secure/reelagent-config-$(date +%Y%m%d)/safe-dump.json

# ⚠️ SECRET_KEY 本身必须单独、加密地保管
#    建议用 KMS / Vault，而不是文件
```

**丢了 `SECRET_KEY` 的后果**：

| 影响 | 是否可恢复 |
|---|---|
| 所有会话 Cookie 失效 | 用户重新登录即可（**可恢复**） |
| **所有 BYOK 密钥无法解密** | ❌ **不可恢复**。用户必须重新填写自己的供应商 Key。`decrypt_byok_map` 会静默跳过解不开的条目（`except TokenError: continue`），因此**不会报错**，只会导致用错 Key 去调供应商 |
| 所有分享链接失效 | ❌ 不可恢复，需要重新分享 |
| 已签发的一次性 token 失效 | 无影响 |

### 7.5 恢复演练

**每季度至少演练一次。** 演练步骤：

```bash
# ---------- 1) 准备一个隔离的恢复环境 ----------
# 不要在生产实例上做恢复演练
createdb -h "$PGHOST" -U reelagent reelagent_restore_test
```

```bash
# ---------- 2) 恢复数据库 ----------
pg_restore -h "$PGHOST" -U reelagent \
  -d reelagent_restore_test \
  --no-owner --no-privileges --jobs=4 \
  reelagent-20251120-030000.dump

# 校验
psql -h "$PGHOST" -U reelagent -d reelagent_restore_test -c "
  SELECT 'users' AS t, count(*) FROM users
  UNION ALL SELECT 'organizations', count(*) FROM organizations
  UNION ALL SELECT 'projects', count(*) FROM projects
  UNION ALL SELECT 'jobs', count(*) FROM jobs
  UNION ALL SELECT 'shots', count(*) FROM shots
  UNION ALL SELECT 'artifacts', count(*) FROM artifacts
  UNION ALL SELECT 'ledger_entries', count(*) FROM ledger_entries
  UNION ALL SELECT 'subscriptions', count(*) FROM subscriptions
  UNION ALL SELECT 'webhook_events', count(*) FROM webhook_events
  UNION ALL SELECT 'audit_logs', count(*) FROM audit_logs
  UNION ALL SELECT 'email_outbox', count(*) FROM email_outbox
  UNION ALL SELECT 'memberships', count(*) FROM memberships
  UNION ALL SELECT 'api_keys', count(*) FROM api_keys
  UNION ALL SELECT 'usage_records', count(*) FROM usage_records
  ORDER BY t;"
```

```bash
# ---------- 3) 恢复对象存储（抽样恢复或全量同步到测试桶） ----------
aws s3 sync s3://reelagent-artifacts-backup/20251120/ s3://reelagent-artifacts-restore-test/
```

```bash
# ---------- 4) 起一个指向恢复环境的实例（不同端口） ----------
REEL_ENV=staging \
REEL_DATABASE__URL="postgresql+asyncpg://reelagent@$PGHOST/reelagent_restore_test" \
REEL_STORAGE__BACKEND=s3 \
REEL_STORAGE__S3_BUCKET=reelagent-artifacts-restore-test \
REEL_SECURITY__SECRET_KEY="<从保险库取出的同一把>" \
REEL_BASE_URL="http://127.0.0.1:8001" \
reelagent-server --host 127.0.0.1 --port 8001
```

> ⚠️ **必须用同一把 `SECRET_KEY`**，否则 BYOK 密钥解不开，
> 演练会得出「恢复失败」的错误结论。

```bash
# ---------- 5) 功能验证 ----------
# 探针
curl -s http://127.0.0.1:8001/readyz | jq

# 对账（最关键的一步：账本必须自洽）
psql -h "$PGHOST" -U reelagent -d reelagent_restore_test -c "
SELECT o.id, o.credits_balance AS balance,
       COALESCE(SUM(l.amount), 0) AS ledger_sum,
       o.credits_balance - COALESCE(SUM(l.amount), 0) AS drift
  FROM organizations o
  LEFT JOIN ledger_entries l ON l.org_id = o.id
 GROUP BY o.id, o.credits_balance
HAVING o.credits_balance <> COALESCE(SUM(l.amount), 0);"
# 期望：0 行

# 队列状态是否合理
psql -h "$PGHOST" -U reelagent -d reelagent_restore_test -c "
SELECT status, count(*) FROM jobs GROUP BY status ORDER BY status;"
```

```bash
# ---------- 6) 清理 ----------
dropdb -h "$PGHOST" -U reelagent reelagent_restore_test
aws s3 rb s3://reelagent-artifacts-restore-test --force
```

**记录演练结果**：RTO（恢复耗时）、RPO（丢失的数据时间窗口）、
遇到的问题。这些数字决定了实际能承诺的 SLA。

### 7.6 恢复生产（真事故）

**顺序很重要**：

```
① 停止写入
   把 API 与 worker 全部停掉（缩容到 0）
   ⚠️ 不要只是「暂停」——必须完全停止，否则恢复期间的新写入会丢

② 备份当前（损坏的）状态
   ⚠️ 不要直接覆盖！先把现状 dump 出来，保留排障与事后分析的证据

③ 恢复数据库
   从最近的备份 + WAL 恢复到目标时间点

④ 恢复对象存储
   如果对象存储没坏，跳过这步（只恢复数据库即可）

⑤ 恢复配置
   确认 SECRET_KEY 与备份时一致

⑥ 启动一个副本，验证
   探针 / 对账 / 队列状态 / 抽样查询几个项目

⑦ 全量启动
   先起 worker 还是先起 API？
   → 先起 API（它只读不写长任务），确认探针正常后再起 worker
```

**恢复后的必做检查**：

```sql
-- ① 额度对账（最重要）
SELECT o.id, o.credits_balance AS balance,
       COALESCE(SUM(l.amount), 0) AS ledger_sum,
       o.credits_balance - COALESCE(SUM(l.amount), 0) AS drift
  FROM organizations o
  LEFT JOIN ledger_entries l ON l.org_id = o.id
 GROUP BY o.id, o.credits_balance
HAVING o.credits_balance <> COALESCE(SUM(l.amount), 0);
-- 期望 0 行。如果不是，见 OPERATIONS 第 4 节
```

```sql
-- ② 找出恢复期间「僵尸」的 running 任务
--    （备份时它们正在跑，但 worker 已经没了）
SELECT id, org_id, project_id, locked_by, lease_expires_at, heartbeat_at
  FROM jobs
 WHERE status = 'running';
-- 处置：让 worker 起来后由租约回收自动处理；
--       或按 OPERATIONS 第 1.5 节手工重置
```

```sql
-- ③ 校验必备约束与索引都在（恢复可能用了不完整的 dump）
SELECT conname FROM pg_constraint
 WHERE conname IN ('uq_job_org_idempotency','uq_ledger_org_idempotency',
                   'uq_webhook_provider_event','ck_org_credits_non_negative')
 ORDER BY conname;
-- 期望 4 行
```

```sql
-- ④ 检查 webhook 是否需要补处理
--    恢复点之后发生的 Stripe 事件需要从 Stripe 侧重放
SELECT event_type, status, count(*), max(created_at) AS last_at
  FROM webhook_events GROUP BY 1, 2 ORDER BY 1;
```

在 Stripe Dashboard 里对比「事件发送时间」与「我们最后处理的 `created_at`」，
把缺口部分的事件重放（见 [OPERATIONS.md](OPERATIONS.md) 第 5.8 节）。

---

## 8. 升级与回滚

### 8.1 版本策略

- 使用语义化版本。`1.0.0` 是首个正式版本。
- 两个包（`reelagent` 与 `reelagent_cloud`）**共享同一个版本号**
  （`pyproject.toml` 里只有一个 `version = "1.0.0"`），
  因此它们事实上是同步发布的。
- 数据库结构变更应当走 Alembic（见 5.3），
  **但迁移目录尚未提供**——这是当前升级流程最大的风险点。

### 8.2 升级顺序

**正确的顺序：先迁移数据库，再升 API，最后升 worker。**

```
① 备份（必做）
   pg_dump -Fc ... && 确认 dump 可读

② 数据库迁移
   alembic upgrade head        ← 在独立 Job 里执行，不要并发
   ✅ 迁移应当向后兼容（同时兼容旧版与新版的代码），
      这样升级过程中两版代码并存不会出错

③ 滚动升级 API
   kubectl set image deployment/reelagent-server server=<image>:<新版本>
   kubectl rollout status deployment/reelagent-server
   ✅ maxUnavailable: 0 + maxSurge: 1 —— 先起新的就绪，再撤老的

④ 验证 API
   curl -fsS https://reel-domain/readyz | jq
   curl -fsS https://reel-domain/api/v1/system/status | jq

⑤ 升级 worker
   kubectl set image deployment/reelagent-worker worker=<image>:<新版本>
   kubectl rollout status deployment/reelagent-worker
   ⚠️ 老 worker 收到 SIGTERM 后会「停止领新任务 → 等在跑的做完 →
      超过 SHUTDOWN_GRACE_SECONDS(60s) 强退」，
      因此 rollout 会比 API 慢，需要耐心
```

**为什么先 API 后 worker**：
API 只负责创建任务与查询，升级它不会影响正在跑的任务。
如果把 worker 先升了，而新 worker 依赖新版 API 的字段，
中间窗口会出错。

### 8.3 兼容性规则

**迁移必须向后兼容**（因为升级期间新旧代码并存）：

| 变更 | 是否兼容 | 做法 |
|---|---|---|
| 加表 | ✅ | 直接 `create_table` |
| 加可空列 | ✅ | 直接 `add_column(nullable=True)` |
| 加非空列（有默认值） | ✅ | `add_column(server_default=...)`，之后再移除默认值 |
| 加非空列（无默认值） | ❌ | 分三步：加可空列 → 回填 → 改非空（跨两个发布） |
| 删列 | ❌ | 分两步：先让代码不再读写 → 下个版本删列 |
| 改列类型 | ⚠️ | 只在 PG 能安全转换时直接改（如 `VARCHAR(32)` → `VARCHAR(64)`）；否则加新列 + 回填 |
| 加索引 | ✅ | 大表用 `CREATE INDEX CONCURRENTLY` |
| 加唯一约束 | ⚠️ | 先确认没有重复数据，否则迁移会失败 |
| 改枚举取值 | ⚠️ | **本项目的枚举是 `VARCHAR + CHECK`**（`native_enum=False`）。加值需要改 CHECK 约束；删值必须先确认没有数据使用 |

**枚举变更的具体做法**（这是本项目特有的关注点）：

```python
# 加一个 PlanTier 取值时：
# 1) 改 domain/enums.py
# 2) 改 domain/plans.py 的 PLANS 字典（否则 get_plan 会 KeyError）
# 3) 迁移里更新 CHECK 约束：
op.drop_constraint('plan_tier', 'organizations', type_='check')
op.create_check_constraint('plan_tier', 'organizations',
    "plan IN ('free','starter','pro','studio','new_tier')")
```

> 因为用的是 `native_enum=False`，**不需要** `ALTER TYPE ... ADD VALUE`
> （那个操作在 PG 里不能在事务块内执行，是很多人的痛点）。
> 这是当初选 `VARCHAR + CHECK` 的收益。

### 8.4 回滚

**回滚代码容易，回滚数据难。** 按类型分别处理：

| 变更 | 代码回滚 | 数据回滚 |
|---|---|---|
| 纯应用逻辑（无迁移） | ✅ 重部署旧镜像 | 无需 |
| 加表 / 加可空列 | ✅ 重部署旧镜像（旧代码不认识新列也不受影响） | 无需 |
| 加非空列 | ⚠️ 旧代码插入时若不给值会失败 | 需要 `drop_column` 或改为可空 |
| 删列 | ❌ 旧代码会读不到列 | 需要从备份恢复 |
| 迁移中做了数据回填（如重新计算余额） | ⚠️ | **必须写反向迁移**，或从备份恢复 |
| **额度发放** | ❌ | **绝不能靠回滚代码撤销**——账本是只追加的。要发反向的 `ADJUSTMENT` 条目 |

**回滚流程**：

```bash
# ① 先把 API 回滚（快，且能立刻止血）
kubectl rollout undo deployment/reelagent-server
kubectl rollout status deployment/reelagent-server

# ② 判断数据是否需要回滚
#    只有「破坏性迁移」才需要。查看当前版本：
alembic current

# ③ 如果迁移本身可逆，回退一版
alembic downgrade -1

# ④ 回滚 worker（最后，因为它最慢）
kubectl rollout undo deployment/reelagent-worker
```

> ⚠️ **`alembic downgrade` 只在迁移脚本写了 `downgrade()` 时才可行。**
> 自动生成的迁移经常是空 `downgrade()`（尤其是 `drop_column` 类的操作）。
> **升级前请审查 `downgrade()` 是否可用**，否则出问题只能从备份恢复。

**额度相关变更的回滚规则**（最重要的一条）：

```
❌ 错误做法：alembic downgrade 之后，用户被多发的额度还在
❌ 错误做法：直接 UPDATE organizations SET credits_balance = ... 撤销
✅ 正确做法：写一条反向的 adjustment 账本条目

理由：账本是「事实」，只追加。
     修改历史会让对账失去意义，也会破坏审计能力。
```

用 `credits.adjust()` 写反向条目：

```python
# 撤销一次被误发的额度
await credits.adjust(
    session,
    org_id=org_id,
    amount=-15000,                    # 负数 = 扣回
    reason="回滚 v1.0.1 误发的额度（INC-1234）",
    created_by="ops",
)
```

### 8.5 升级前检查清单

```bash
# ① 确认当前版本与迁移版本
alembic current
curl -s https://reel-domain/health | jq '.version'

# ② 确认没有正在跑的任务（或接受它们会被中断）
psql -c "SELECT count(*) FROM jobs WHERE status = 'running';"
```

```bash
# ③ 确认没有卡在非终态的任务
psql -c "SELECT status, count(*) FROM jobs GROUP BY status;"
```

```bash
# ④ 备份
pg_dump -Fc -Z 6 -h "$PGHOST" -U reelagent -d reelagent \
  -f "pre-upgrade-$(date +%Y%m%d-%H%M%S).dump"
pg_restore --list "pre-upgrade-$(date +%Y%m%d-%H%M%S).dump" >/dev/null && echo "dump OK"
```

```bash
# ⑤ 对账基线（升级后要对比，确认升级没破坏额度）
psql -c "
SELECT count(*) AS inconsistent_orgs FROM (
  SELECT o.id FROM organizations o
    LEFT JOIN ledger_entries l ON l.org_id = o.id
   GROUP BY o.id, o.credits_balance
  HAVING o.credits_balance <> COALESCE(SUM(l.amount), 0)
) t;"
# 期望 0。如果非 0，先修好再升级
```

```bash
# ⑥ 审查迁移脚本的 downgrade() 是否可用
grep -A20 'def downgrade' reelagent_cloud/db/migrations/versions/<最新>.py
```

```bash
# ⑦ 确认新版本的配置项（新增的环境变量）
#    对比两个版本的 config.py，或看 CHANGELOG
```

### 8.6 灰度与回滚决策

保守的做法：

```
1. 先在一个非生产环境（staging）跑完整流程 + 冒烟
2. 生产只升 1 个 API 副本，观察 15 分钟
   · 看 reelagent_http_requests_total{status=~"5.."} 是否上升
   · 看 /health 是否稳定
   · 看日志有没有新的 error 事件
3. 全量升 API
4. 只升 1 个 worker，观察它跑完几个任务
   · 看 reelagent_jobs_total{status="dead"} 是否上升
   · 看 reelagent_job_lease_expired_total 是否上升
5. 全量升 worker
```

**回滚触发条件**（建议写进发布规范）：

| 信号 | 阈值 |
|---|---|
| HTTP 5xx 比例 | > 1% 持续 5 分钟 |
| `reelagent_jobs_total{status="dead"}` | 出现任何增长 |
| `reelagent_job_lease_expired_total` | 出现任何增长 |
| `/readyz` | 非 200 持续 2 分钟 |
| 额度对账 | 出现不一致（**立即回滚，无需等待**） |

---

## 9. 可观测性接入

### 9.1 日志

**生产配置**：

```bash
REEL_OBSERVABILITY__LOG_FORMAT=json
REEL_OBSERVABILITY__LOG_LEVEL=INFO
```

**JSON 日志的关键字段**：

| 字段 | 含义 |
|---|---|
| `event` | 事件名（结构化日志的消息键） |
| `timestamp` | |
| `level` | |
| `request_id` | 由中间件绑定，可与错误响应里的 `request_id` 串联 |
| `job_id` / `org_id` / `project_id` | 上下文（对应的事件里才有） |
| `duration_ms` / `slow` | HTTP 请求的耗时与慢标记 |

**自动脱敏**：`logging.py` 在处理器层面用正则擦除——
键名匹配 `api[_-]?key|apikey|secret|password|passwd|token|authorization|bearer|client[_-]?secret|webhook[_-]?secret|session|cookie|pepper|dsn`（忽略大小写），
或值匹配 `sk-...` / `Bearer ...` / `whsec_...` / `AKIA...`，
都会被替换为 `***REDACTED***`。

> 这是**在处理器层面**做的，不依赖每个调用点自觉。
> 但仍建议：**不要主动把密钥塞进日志**。

**采集建议**：

| 系统 | 方式 |
|---|---|
| Loki | Promtail 采集容器 stdout，用 `event` 与 `level` 做标签 |
| ELK | Filebeat → Logstash → Elasticsearch |
| CloudWatch | 容器日志直接接 CloudWatch Logs Insights |
| Datadog | 容器日志采集，把 JSON 字段映射为 attributes |

**慢请求被标记**（`REEL_OBSERVABILITY__SLOW_REQUEST_MS`，默认 3000ms）：

```bash
# 找出慢请求
kubectl logs -l app=reelagent-server --tail=5000 \
  | jq -r 'select(.slow == true) | "\(.duration_ms)ms \(.method) \(.path) \(.status)"' \
  | sort -rn | head -20
```

**哪些路径不打访问日志**（`QUIET_PATHS` / `QUIET_PREFIXES`）：

```
/health  /healthz  /readyz  /livez  /metrics  /favicon.ico
/static/*  /assets/*
```

（避免探针与静态资源淹没日志）

### 9.2 指标

**抓取配置示例**：

```yaml
# prometheus.yml
scrape_configs:
  - job_name: reelagent-server
    metrics_path: /metrics
    static_configs:
      - targets: ["reelagent-server.reelagent.svc.cluster.local:80"]
        labels: {component: api}

  # ⚠️ worker 当前不暴露 HTTP 端口，因此无法直接抓取。
  #    worker 侧的指标（reelagent_worker_active / worker_jobs_running /
  #    job_queue_depth / job_lease_expired_total）需要通过 API 进程的
  #    /metrics 获取——但请注意：
  #    · job_queue_depth 由 worker 的 housekeeping 更新，
  #      但指标注册表是**进程内**的，因此 worker 进程的计数器
  #      不会出现在 API 进程的 /metrics 里。
  #    → 结论：worker 的指标目前只在 worker 自己的进程里，
  #      除非你给 worker 加一个 metrics 端点（当前未实现），
  #      否则无法通过 Prometheus 抓取。
```

> ⚠️ **这是一个真实的可观测性缺口**：
> `REGISTRY = CollectorRegistry(...)` 是进程内的独立注册表。
> `reelagent_worker_active` / `reelagent_worker_jobs_running` /
> `reelagent_job_lease_expired_total` / `reelagent_job_retries_total`
> 以及 `job_queue_depth` 都由 **worker 进程**更新，
> 而 `/metrics` 端点只在 **API 进程**上。
> 因此这些指标目前**无法被 Prometheus 采集到**。
>
> 可行的规避方案（按推荐度）：
>
> 1. **用数据库做队列监控**（最简单，见 [OPERATIONS.md](OPERATIONS.md) 第 2 节）：
>    ```sql
>    SELECT status, count(*) FROM jobs GROUP BY status;
>    ```
>    把它做成一个小的导出器（exporter）。
> 2. **给 worker 加一个 metrics HTTP 端点**（需改代码）。
> 3. **用 Pushgateway**，worker 定期推送（需改代码）。
> 4. **用多进程模式的 prometheus_client**（需改代码，
>    且要注意 worker 是 asyncio 单进程 + 线程池的结构）。
>
> **API 侧的指标（`reelagent_http_requests_total` /
> `reelagent_http_request_duration_seconds` /
> `reelagent_auth_failures_total` / `reelagent_rate_limit_hits_total` /
> `reelagent_projects_created_total`）
> 可以正常抓取。**

**指标清单**（名称与标签以 `observability/metrics.py` 为准）：

| 指标 | 类型 | 标签 | 由谁更新 |
|---|---|---|---|
| `reelagent_http_requests_total` | Counter | `method, route, status` | API |
| `reelagent_http_request_duration_seconds` | Histogram | `method, route` | API |
| `reelagent_http_requests_in_flight` | Gauge | — | API |
| `reelagent_auth_failures_total` | Counter | `reason` | API |
| `reelagent_rate_limit_hits_total` | Counter | `scope` | API |
| `reelagent_projects_created_total` | Counter | — | API |
| `reelagent_jobs_total` | Counter | `kind, status` | worker（+ API 的取消路径） |
| `reelagent_job_duration_seconds` | Histogram | `kind` | worker |
| `reelagent_job_queue_depth` | Gauge | `status` | worker |
| `reelagent_job_retries_total` | Counter | `kind` | worker |
| `reelagent_job_lease_expired_total` | Counter | — | worker |
| `reelagent_provider_calls_total` | Counter | `provider, capability, status` | 引擎（云端聚合） |
| `reelagent_provider_call_duration_seconds` | Histogram | `provider, capability` | 引擎 |
| `reelagent_provider_cost_usd_total` | Counter | `provider, capability` | worker（`provider="mixed"`） |
| `reelagent_provider_circuit_open` | Gauge | `provider, capability` | 引擎 |
| `reelagent_credits_consumed_total` | Counter | `org_id` | 服务层 |
| `reelagent_shots_generated_total` | Counter | `status` | worker |
| `reelagent_worker_active` | Gauge | — | worker |
| `reelagent_worker_jobs_running` | Gauge | `worker_id` | worker |

> ⚠️ `reelagent_credits_consumed_total` 带 `org_id` 标签。
> 租户数量多时会造成 Prometheus 基数膨胀。
> 建议在采集侧做 `sum without (org_id)` 或删除该标签。

**建议的告警规则**：

```yaml
groups:
  - name: reelagent
    rules:
      - alert: ReelAgentApiDown
        expr: up{job="reelagent-server"} == 0
        for: 2m
        labels: {severity: critical}
        annotations: {summary: "ReelAgent API 不可达"}

      - alert: ReelAgentNotReady
        expr: probe_success{job="reelagent-readyz"} == 0
        for: 3m
        labels: {severity: critical}
        annotations: {summary: "ReelAgent /readyz 未就绪（数据库或存储不可用）"}

      - alert: ReelAgentHighErrorRate
        expr: |
          sum(rate(reelagent_http_requests_total{status=~"5.."}[5m]))
          / sum(rate(reelagent_http_requests_total[5m])) > 0.01
        for: 5m
        labels: {severity: warning}
        annotations: {summary: "HTTP 5xx 比例超过 1%"}

      - alert: ReelAgentSlowRequests
        expr: |
          histogram_quantile(0.95,
            sum(rate(reelagent_http_request_duration_seconds_bucket[5m])) by (le, route)
          ) > 3
        for: 10m
        labels: {severity: warning}
        annotations: {summary: "P95 响应时间超过 3 秒"}

      - alert: ReelAgentAuthFailuresSpike
        expr: sum(rate(reelagent_auth_failures_total[5m])) > 5
        for: 5m
        labels: {severity: warning}
        annotations: {summary: "认证失败速率异常，可能是撞库"}

      - alert: ReelAgentRateLimitSpike
        expr: sum by (scope) (rate(reelagent_rate_limit_hits_total[5m])) > 1
        for: 10m
        labels: {severity: info}
        annotations: {summary: "限流命中速率上升（scope={{ $labels.scope }}）"}
```

（队列与租约相关的告警需要 worker 指标可采集，见上面的缺口说明。
在缺口修复前，用数据库导出器实现。）

### 9.3 状态页与状态接口

| 端点 | 鉴权 | 内容 |
|---|---|---|
| `/status` | 无 | 公开状态页（HTML，需要模板） |
| `/api/v1/system/status` | **无** | JSON：`version` / `env` / `queues` / `config`（密钥已打码） |
| `/api/v1/system/ping` | 无 | `{"pong":"ok"}` |
| `/health` | 无 | 人类可读的健康汇总（HTML 里可嵌入） |

> ⚠️ `/api/v1/system/status` 暴露了队列深度与配置快照
> （供应商类型、是否启用 billing / redis / rate_limit）。
> 密钥已打码，但**生产环境建议在反向代理层限制它的来源**：
>
> ```nginx
> location = /api/v1/system/status {
>     allow 10.0.0.0/8;
>     deny all;
>     proxy_pass http://127.0.0.1:8000;
> }
> ```

### 9.4 Sentry（可选）

```bash
pip install "reelagent[sentry]"
REEL_OBSERVABILITY__SENTRY_DSN=https://...@sentry.io/...
```

初始化参数（`api/app.py::_init_sentry`）：

```python
sentry_sdk.init(
    dsn=...,
    environment=settings.env.value,     # dev / staging / prod
    traces_sample_rate=0.1,             # 10% 性能采样
    send_default_pii=False,             # 不把用户邮箱等 PII 发给第三方
)
```

未安装 `sentry-sdk` 时只打 `sentry_not_installed` 警告，不影响启动。

### 9.5 审计日志

`audit_logs` 表记录了 16 类动作，带 `request_id` 可与结构化日志串联：

```sql
-- 某个组织最近的审计记录
SELECT created_at, action, actor_type, actor_user_id, target_type, target_id,
       ip, request_id, meta
  FROM audit_logs
 WHERE org_id = '<org_id>'
 ORDER BY created_at DESC
 LIMIT 50;
```

```sql
-- 高风险动作（密钥与成员变更）
SELECT created_at, action, actor_user_id, target_id, ip
  FROM audit_logs
 WHERE action IN ('api_key.created','api_key.revoked','member.invited',
                  'member.removed','user.password_changed','subscription.changed')
   AND created_at > now() - interval '7 days'
 ORDER BY created_at DESC;
```

**审计写入不参与业务事务的成败**（`services/audit.py`）：
业务成功但审计失败时，宁可丢掉一条审计也不回滚用户的操作。
因此**审计日志可能有缺失**，不能当作完整的合规证据链
（如果需要严格合规，应当把审计写入改成同步阻塞 + 失败即回滚，
或者写到一个独立的、高可用的日志系统）。

---

## 10. 部署检查清单

按顺序勾选。**任何一项不通过都不要上线。**

### 10.1 配置

- [ ] `REEL_ENV=prod`
- [ ] `REEL_SECURITY__SECRET_KEY` 已生成（`secrets.token_urlsafe(48)`）**并已安全备份**
- [ ] `REEL_SECURITY__HTTPS_ONLY=true`
- [ ] `REEL_SECURITY__ALLOWED_HOSTS` 写明域名列表（**不含 `*`**）
- [ ] `REEL_BASE_URL` 是 `https://...`
- [ ] `REEL_DATABASE__URL` 是 `postgresql+asyncpg://...`（**不是 SQLite**）
- [ ] 视频供应商 Key 已配置（`REEL_PROVIDERS__VIDEO__API_KEY`）
- [ ] 图像 / LLM / TTS 供应商 Key 已配置
- [ ] `REEL_SUPPORT_EMAIL` 改成真实邮箱（默认是 `support@reelagent.local`）
- [ ] 存储：`REEL_STORAGE__BACKEND` 与 `S3_BUCKET` 一致（s3 时必须填 bucket）
- [ ] 如果多副本：`REEL_REDIS__ENABLED=true`
- [ ] 如果启用支付：`REEL_BILLING__ENABLED=true` + `SECRET_KEY` + `WEBHOOK_SECRET` + 三个 Price ID
- [ ] 连接池容量已核算：
      `(POOL_SIZE + MAX_OVERFLOW) × 进程数 < PG max_connections`
- [ ] `REEL_WORKER__SHUTDOWN_GRACE_SECONDS` < 容器 `terminationGracePeriodSeconds`
- [ ] `REEL_OBSERVABILITY__LOG_FORMAT=json`
- [ ] **API 与 worker 的所有配置完全一致**（尤其是 `SECRET_KEY` 与存储配置）

### 10.2 数据库

- [ ] Postgres 可连，版本 ≥ 13
- [ ] 14 张表全部存在
- [ ] 六个关键约束存在（`uq_job_org_idempotency` / `uq_ledger_org_idempotency` /
      `uq_webhook_provider_event` / `uq_shot_project_key` /
      `uq_membership_user_org` / `ck_org_credits_non_negative`）
- [ ] 关键索引存在（`ix_jobs_claim` / `ix_jobs_lease` / `ix_jobs_org_created`）
- [ ] `EXPLAIN` 确认领取查询走索引而不是 Seq Scan
- [ ] `max_connections` 留有余量
- [ ] WAL 归档已开启（PITR 可用）
- [ ] 备份任务已配置并**验证过一次恢复**

### 10.3 存储

- [ ] 对象存储 bucket 为**私有**
- [ ] 应用有读写权限（`/readyz` 的 storage 检查为 `ok: true`）
- [ ] 生命周期规则已配置（软删产物的历史版本过期）
- [ ] 备份 bucket 在**独立权限边界**内（不要与生产同权限）
- [ ] 如果从 local 切到 s3：**已有对象已完整同步**（key 结构一致）

### 10.4 网络与 TLS

- [ ] 域名解析正确
- [ ] 证书链完整、未过期、自动续期已配置
- [ ] HTTP 301 → HTTPS
- [ ] HSTS 响应头存在
- [ ] CSP / X-Frame-Options / nosniff 等安全头齐全
- [ ] **应用端口不对公网开放**（防 XFF 伪造绕过 IP 限流）
- [ ] 反向代理**不改写请求体**（Stripe 验签依赖）
- [ ] 反向代理**不把 5xx 重写成 200**（Stripe 重试依赖）
- [ ] 代理传递 `X-Forwarded-For`（**追加**而非覆盖）与 `X-Forwarded-Proto`
- [ ] 代理的 `client_max_body_size` ≥ `REEL_STORAGE__MAX_UPLOAD_MB`
- [ ] `/api/v1/system/status` 已限制来源 IP（建议）

### 10.5 服务

- [ ] `reelagent-server` 启动无报错（`app_ready` 日志出现）
- [ ] `reelagent-worker` 启动无报错（`worker_starting` 日志出现）
- [ ] `/livez` 返回 200
- [ ] `/readyz` 返回 200 且四项 checks 状态符合预期
- [ ] `/metrics` 可访问（或确认按需关闭）
- [ ] `scripts/smoke.py` 通过
- [ ] 端到端跑通一条成片（用真实供应商 Key）
- [ ] worker 的副本数 × concurrency 与供应商限流配额匹配

### 10.6 安全

- [ ] `REEL_SECURITY__ALLOW_SIGNUP` 按需设置（私有部署建议关闭）
- [ ] 如果是邀请制：`REEL_SECURITY__SIGNUP_INVITE_CODE` 已设置
- [ ] 确认 API Key 无法访问 `/app/*` 与管理类接口
      （用 API Key 调 `POST /api/v1/api-keys` 应当返回 403）
- [ ] 确认跨租户文件访问被拒绝（用 A 组织的 key 访问 B 组织的产物 → 403）
- [ ] 日志脱敏生效（搜一下日志里有没有 `sk-` / `whsec_` 明文）
- [ ] `SECRET_KEY` 不在代码仓库、不在日志、不在镜像里

### 10.7 可观测性

- [ ] 日志被采集系统收走
- [ ] `/metrics` 被 Prometheus 抓取
- [ ] API 侧告警规则已配置（不可达 / 未就绪 / 5xx 比例 / P95 / 认证失败）
- [ ] 队列深度监控已通过数据库导出器或手工巡检覆盖
      （**worker 指标当前无法被 Prometheus 采集**，见 9.2 节的缺口说明）
- [ ] 磁盘用量告警已配置
- [ ] Sentry（可选）已接入并收到一条测试事件

### 10.8 Stripe（如启用）

- [ ] 三个商品与价格已创建
- [ ] 三个 Price ID 已配置
- [ ] Webhook 端点已创建：`https://<domain>/webhooks/stripe`
- [ ] 订阅了那 6 个事件
- [ ] `WEBHOOK_SECRET` 已配置
- [ ] 用 Stripe CLI 或测试模式做过一次完整结账
- [ ] 确认 `webhook_events` 里有 `status='processed'` 的记录
- [ ] 确认订阅激活后额度确实到账（查 `ledger_entries`）
- [ ] 确认重复投递被识别为 `duplicate`（不重复发额度）

### 10.9 运维准备

- [ ] 值班同学已读过 [OPERATIONS.md](OPERATIONS.md)
- [ ] [OPERATIONS.md](OPERATIONS.md) 里的 SQL 已在**只读副本**上验证过
- [ ] 应急脚本已准备（对账修复、退额度、孤儿工作区清理）
- [ ] 恢复演练做过一次并记录了 RTO / RPO
- [ ] 已知缺口的应对方式已明确：
      - `reelagent-admin` 悬空 → 用 CLI / 运维脚本代替
      - Alembic `versions/` 为空 → 明确「生成首个版本」的负责人与时机
      - worker 指标不可采集 → 明确用数据库导出器替代（见 9.2 节）
      - 无产品 GC → 明确磁盘巡检频率与清理脚本（见 OPERATIONS 第 7 节）
      - `Idempotency-Key` 未生效 → 客户端自行去重，或先修此问题

---

## 相关文档

- [架构说明](ARCHITECTURE.md) —— 分层结构、14 张表、任务生命周期、双记账
- [运维 Runbook](OPERATIONS.md) —— 按症状组织的排障手册
- [REST API 参考](API.md) —— 端点与字段
- [架构决策记录](adr/) —— 为什么这样设计
- [贡献指南](../CONTRIBUTING.md) —— 开发环境与代码规范
