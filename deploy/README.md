# ReelAgent 部署指南

面向一个已经跑通、现在要正式上线的 ReelAgent。三种方式按「省心程度」排序：

| 方式 | 适合场景 | 大概成本 | 你会得到 |
|---|---|---|---|
| [一、Docker Compose 单机](#一docker-compose-单机部署推荐起步) | 自建服务器、内网部署、先上线再优化 | 一台 2C4G 起 | 一条命令拉起全部服务，数据都在自己机器上 |
| [二、Fly.io 云托管](#二flyio-云托管不想运维机器) | 不想管机器、要自动 HTTPS 与扩容 | 按用量 | 自动 TLS、多区域、无需自己打补丁 |
| [三、Kubernetes](#三kubernetes简述有集群时) | 已有集群、要与企业基础设施对接 | 集群成本 | 精细的探针/扩缩容/发布策略 |

无论哪种方式，**部署顺序都是同一套**（这点比选平台重要）：

```
准备配置 → 起依赖(Postgres/Redis) → 跑数据库迁移 → 起 API → 起 Worker → 验证探针 → 配域名与 HTTPS → 配 Stripe Webhook → 配备份
```

最后三步单独成节，请务必看完：**没配 HTTPS 等于会话 Cookie 裸奔，没配 Webhook 等于收不到钱，没配备份等于数据只有一份。**

---

## 零、上线前的一次性准备

1. 生成两个随机密钥（**每个环境一份，绝不共用、绝不入库**）：

   ```bash
   openssl rand -base64 48     # REEL_SECURITY__SECRET_KEY
   openssl rand -base64 48     # REEL_SECURITY__API_KEY_PEPPER
   # 没有 openssl 也可以用 Python：
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

2. 从模板复制配置，只改 config.py 认识的变量名：

   ```bash
   cp .env.production.example .env
   # 搜索 CHANGE_ME，一共 9 处（模板里都标了 ⚠）
   ```

3. 记住生产环境的**启动校验**——`REEL_ENV=prod` 时以下检查会真的拦住启动
   （已实测复现，报错信息会逐条列出）：

   - `REEL_DATABASE__URL` 不能是 SQLite；
   - `REEL_SECURITY__HTTPS_ONLY=true`；
   - `REEL_SECURITY__ALLOWED_HOSTS` 不能是 `["*"]`，必须写真实域名；
   - `REEL_BASE_URL` 必须是 `https://`；
   - `REEL_PROVIDERS__VIDEO__API_KEY` 必须配置（没有视频 Key 就产不出成片）。

   > ⚠ **有一个坑必须知道**：`REEL_SECURITY__SECRET_KEY` 缺失**不会**被拦下。
   > config.py 里那条检查实际上永远不会触发，因为 `SecuritySettings` 会先自动
   > 生成一个随机密钥，父级再检查时它已经"非空"了。
   > 后果是多副本 api 各自持有一份不同密钥 → 用户在负载均衡后面随机掉登录，
   > 且每次重新部署都会全站登出。
   > **上线前请人工确认 `.env` 里 `REEL_SECURITY__SECRET_KEY` 有值。**

   改完配置先自检一次，比等容器起不来再看日志快得多：

   ```bash
   docker compose run --rm --entrypoint python migrate \
     -c "from reelagent_cloud.config import get_settings; print(get_settings().safe_dump())"
   ```

   > 注意：`safe_dump()` 输出里所有密钥已打码，可以安全贴进工单。

---

## 一、Docker Compose 单机部署（推荐起步）

### 1.1 服务器准备

- **配置**：起步 2C4G / 40G 磁盘；`worker.concurrency=2` 时 ffmpeg 合成峰值吃满 2 核。
  如果同时要跑多个大项目，直接上 4C8G。
- **系统**：Ubuntu 22.04+ / Debian 12+，装好 Docker Engine 与 Compose v2：

  ```bash
  curl -fsSL https://get.docker.com | sh
  docker compose version    # 需要 v2.x
  ```

- **防火墙**：只对外开 `22`、`80`、`443`。**不要**对外开放 8000/5432/6379——
  compose 里 api 默认只绑 `127.0.0.1:8000`，正是为了逼你走反向代理。

### 1.2 起服务

```bash
git clone <你的仓库地址> reel-agent && cd reel-agent
cp .env.production.example .env
vim .env                                   # 改掉 9 处 CHANGE_ME

docker compose up -d --build
docker compose ps                          # 五个服务：postgres/redis/migrate/api/worker
```

启动顺序由 compose 的 `depends_on` 条件保证，不需要你手动等待：

```
postgres(healthy) ─┐
                   ├─→ migrate(跑完退出) ─→ api
redis(healthy) ────┘                     └→ worker
```

- `migrate` 是一次性容器：先 `scripts/wait_for_db.py` 等库可连，再 `alembic upgrade head`，成功后退出；
- `api` 与 `worker` 用的是 `service_completed_successfully`——**迁移没成功，应用根本不会启动**，
  从根上避免"新代码 + 旧表结构"这种最难查的故障。

> **首次部署的重要提醒**：当前仓库里**还没有** `alembic.ini` 与 `reelagent_cloud/db/migrations/`。
> 这意味着 `migrate` 走的是降级分支：
> - `REEL_ENV` 不是 `prod` → 自动建表（等价于 `create_all()`），首次部署能直接跑通；
> - `REEL_ENV=prod` → **明确报错退出**，不会静默改线上表结构。
>
> 所以正式上线前请先补上迁移（本地执行一次即可）：
>
> ```bash
> python -m alembic init reelagent_cloud/db/migrations
> # 编辑 alembic.ini / env.py，把 target_metadata 指向 reelagent_cloud.db.base.Base，
> # 并把 sqlalchemy.url 改成从环境变量读取 REEL_DATABASE__URL
> python -m alembic revision --autogenerate -m "init schema"
> git add alembic.ini reelagent_cloud/db/migrations && git commit -m "chore: 接入 Alembic"
> ```
>
> `alembic.ini` 存在后，compose 与 K8s 的 migrate 都会自动改走 `alembic upgrade head`，无需再改任何配置文件。

### 1.3 验证

```bash
curl -s localhost:8000/livez     # {"status":"alive"}    —— 进程活着
curl -s localhost:8000/readyz    # {"status":"ready",...} —— 数据库+存储都可用
curl -s localhost:8000/health    # 人类可读的详细诊断
curl -s localhost:8000/metrics | head
```

三个探针的语义差别（**K8s/负载均衡里搞错会导致流量打到没准备好的实例**）：

| 路径 | 查什么 | 用在哪 |
|---|---|---|
| `/livez` | 什么都不查，进程能响应就 200 | Docker HEALTHCHECK、K8s liveness。**绝不能**在这里查库 |
| `/readyz` | 数据库可连 + 存储可写 | K8s readiness、负载均衡摘除。不通过返回 **503** |
| `/health` | 同上，但返回详细诊断 | 人看的排障入口 |
| `/metrics` | Prometheus 抓取 | 监控系统 |

> `/readyz` 每次调用都会往存储里 put + delete 一个探针文件。用 s3 后端时，
> 探针周期就是"每 Pod 每周期 2 次 S3 请求"，所以 K8s 里 readiness 周期取了 30s。

### 1.4 日常运维

```bash
docker compose logs -f api worker          # 跟随日志（structlog JSON，可直接给采集器）
docker compose up -d --build               # 更新代码（会重跑 migrate）
docker compose up -d --scale worker=3      # worker 横向扩容（租约抢占，天然安全）
docker compose restart worker              # 重启 worker（会触发优雅停机）
docker compose down                        # 停服务，保留数据卷
docker compose down -v                     # ⚠ 连数据一起删，先备份！
```

**优雅停机**是这套编排里最容易被忽略的细节，这里说明白：

- worker 收到 `SIGTERM` 后**不会立刻退出**：它停止领新任务，把在跑的做完（最久
  `REEL_WORKER__SHUTDOWN_GRACE_SECONDS`，默认 60s），超过才强杀；
- 因此 compose 里 `stop_grace_period: 120s` **必须大于**那个值，否则正在合成的成片
  会被 `SIGKILL` 掐断，任务要等租约过期（15 分钟）才能重试，用户会以为系统坏了；
- 镜像用 `tini` 作为 1 号进程，保证信号能正确转发到 Python（否则 worker 可能收不到信号）。

### 1.5 反向代理与 HTTPS

`api` 只绑 `127.0.0.1`，所以必须有反向代理终止 TLS。**推荐 Caddy**（自动申请续期证书，零配置）：

```caddyfile
# /etc/caddy/Caddyfile
reelagent.example.com {
    encode gzip
    # 上传体积要与 REEL_STORAGE__MAX_UPLOAD_MB 对齐，否则素材上传会被拦成 413
    request_body {
        max_size 32MB
    }
    # 关键：让后端拿到真实客户端 IP 与协议。
    # 不转发 X-Forwarded-* 的话，限流会把所有请求算到代理这一个 IP 上，等于限流失效。
    reverse_proxy 127.0.0.1:8000 {
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-Proto {scheme}
        # 成片下载是长连接，超时放宽
        transport http {
            read_timeout 300s
        }
    }
    log {
        output file /var/log/caddy/reelagent.log
    }
}
```

```bash
sudo systemctl reload caddy
curl -sI https://reelagent.example.com/livez    # 期望 200
```

用 Nginx 的话（等价配置）：

```nginx
server {
    listen 443 ssl http2;
    server_name reelagent.example.com;

    ssl_certificate     /etc/letsencrypt/live/reelagent.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/reelagent.example.com/privkey.pem;
    # 用 certbot 申请：certbot --nginx -d reelagent.example.com

    client_max_body_size 32m;          # 与 MAX_UPLOAD_MB 对齐
    proxy_read_timeout 300s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Stripe Webhook 验签用的是原始请求体，代理不要做任何 body 改写
        proxy_request_buffering off;
    }
}
server {
    listen 80;
    server_name reelagent.example.com;
    return 301 https://$host$request_uri;
}
```

配置好之后，把 `.env` 里的 `REEL_BASE_URL`、`REEL_SECURITY__ALLOWED_HOSTS`
都改成真实域名，再 `docker compose up -d`。

---

## 二、Fly.io 云托管（不想运维机器）

Fly.io 的特点：一个镜像 + 两个 **process group**（`app` 跑 API、`worker` 跑任务），
证书与 HTTPS 全自动。其他平台（Railway / Render）映射关系一样，末尾给了对照。

### 2.1 准备

```bash
curl -L https://fly.io/install.sh | sh
fly auth login
fly launch --no-deploy        # 会生成 fly.toml，把它改成下面的内容
```

`fly.toml`（关键点都在注释里）：

```toml
app = "reelagent"
primary_region = "hkg"          # 选离用户近的区域（香港/新加坡对国内延迟较低）

[build]
  dockerfile = "Dockerfile"

# 一个镜像，两个进程组：app 跑 HTTP，worker 跑任务队列
[processes]
  app    = "reelagent-server --host 0.0.0.0 --port 8000 --workers 2"
  worker = "reelagent-worker"

# 非敏感配置直接写这里；密钥用 `fly secrets set`（见下）
[env]
  REEL_ENV = "prod"
  REEL_BASE_URL = "https://reelagent.fly.dev"
  REEL_OBSERVABILITY__LOG_FORMAT = "json"
  REEL_SECURITY__HTTPS_ONLY = "true"
  REEL_SECURITY__ALLOWED_HOSTS = '["reelagent.fly.dev"]'
  REEL_WORKSPACE_DIR = "/app/workspace"
  # worker 参数：Fly 的 shared-cpu-2x 大约能撑 2 并发
  REEL_WORKER__CONCURRENCY = "2"

# 只有 app 进程组需要对公网开放
[http_service]
  processes = ["app"]
  internal_port = 8000
  force_https = true
  auto_stop_machines = "stop"     # 没流量时停机省钱
  auto_start_machines = true
  min_machines_running = 1        # 至少留 1 台，否则冷启动慢

  [[http_service.checks]]
    # 与容器探针一致：走 /livez，不查数据库
    interval = "30s"
    timeout = "5s"
    grace_period = "20s"
    method = "GET"
    path = "/livez"

[[vm]]
  size = "shared-cpu-2x"
  memory = "2gb"

# worker 需要更长的停机宽限：它要把在跑的任务做完（见 REEL_WORKER__SHUTDOWN_GRACE_SECONDS）
[experimental]
  # Fly 在停止 machine 时发送 SIGTERM 并等待这个时长
  # （按平台文档配置；如不支持该字段，请把 SHUTDOWN_GRACE_SECONDS 调小到 30s 以内）
  kill_signal = "SIGTERM"
  kill_timeout = 120
```

> Fly 的 `kill_timeout` 与 `REEL_WORKER__SHUTDOWN_GRACE_SECONDS` 必须满足
> `kill_timeout > SHUTDOWN_GRACE_SECONDS`，否则任务会被强杀 —— 和 compose 里
> `stop_grace_period > SHUTDOWN_GRACE_SECONDS` 是同一条规则。

### 2.2 依赖：Postgres 与 Redis

```bash
fly postgres create --name reelagent-db --region hkg
fly postgres attach reelagent-db -a reelagent       # 会注入 DATABASE_URL
fly redis create --name reelagent-redis             # 或 upstash redis
```

**注意坑点**：`fly postgres attach` 注入的是 `postgres://...`，而本项目要求
**异步驱动**，必须显式设置带 `+asyncpg` 的连接串：

```bash
fly secrets set -a reelagent \
  REEL_DATABASE__URL="postgresql+asyncpg://<用户>:<口令>@<host>:5432/<库>" \
  REEL_REDIS__URL="redis://:<口令>@<host>:6379/0"
```

> 云上建议**不要**把 `?sslmode=require` 直接拼在 asyncpg 的 URL 里，
> asyncpg 用的是 `?ssl=require`；不确定时先不加，用内部网络连接通常无需 TLS。

### 2.3 密钥

```bash
fly secrets set -a reelagent \
  REEL_SECURITY__SECRET_KEY="$(openssl rand -base64 48)" \
  REEL_SECURITY__API_KEY_PEPPER="$(openssl rand -base64 48)" \
  REEL_PROVIDERS__LLM__API_KEY="sk-..." \
  REEL_PROVIDERS__IMAGE__API_KEY="..." \
  REEL_PROVIDERS__VIDEO__API_KEY="..." \
  REEL_PROVIDERS__TTS__API_KEY="..." \
  REEL_STORAGE__S3_ACCESS_KEY_ID="..." \
  REEL_STORAGE__S3_SECRET_ACCESS_KEY="..." \
  REEL_BILLING__SECRET_KEY="sk_live_..." \
  REEL_BILLING__WEBHOOK_SECRET="whsec_..."
```

### 2.4 存储与迁移

- **存储**：云托管下 `local` 存储会在每次部署/重启后丢文件，**必须**用 S3 兼容存储
  （AWS S3 / 阿里云 OSS / Cloudflare R2 都行）：
  ```bash
  fly secrets set -a reelagent REEL_STORAGE__BACKEND=s3 REEL_STORAGE__S3_BUCKET=reelagent-prod
  ```
- **迁移**：Fly 没有 compose 那种 `depends_on`，把迁移作为**发布的前置步骤**手工执行：

  ```bash
  # 用一次性 machine 跑迁移，跑完即销毁
  fly machine run --app reelagent --rm \
    --env REEL_ENV=prod \
    ghcr.io/<owner>/reel-agent:1.0.0 \
    --command "sh -c 'python scripts/wait_for_db.py && alembic upgrade head'"

  fly deploy          # 迁移成功后再发布新版本
  ```

  更稳的做法是在 CI（`.github/workflows/release.yml`）里加一个 migrate 步骤，
  发布镜像之后、`fly deploy` 之前执行。

### 2.5 验证与扩容

```bash
fly status                         # 两个进程组的 machine 状态
fly logs -a reelagent              # JSON 日志
curl -s https://reelagent.fly.dev/readyz
fly scale count app=2              # API 扩容
fly scale count worker=3           # worker 扩容（同样靠数据库租约，不冲突）
```

### 2.6 其它平台的对照关系

| 概念 | Fly.io | Railway | Render |
|---|---|---|---|
| API 进程 | process group `app` | Service（Web） | Web Service |
| Worker 进程 | process group `worker` | Service（Worker，无端口） | Background Worker |
| Postgres | `fly postgres` | PostgreSQL 插件 | Render Postgres |
| Redis | `fly redis` | Redis 插件 | Render Key Value |
| 环境变量 | `[env]` + `fly secrets` | Variables | Environment |
| 停机宽限 | `kill_timeout` | 部署设置里的 drain | 无显式配置，靠平台默认 |

---

## 三、Kubernetes（简述，有集群时）

清单在 [`k8s/`](k8s/) 下，`kubectl apply -f` 逐个应用即可（顺序不能反）：

```bash
kubectl create namespace reelagent

# 1) 配置与密钥（先把 configmap 里的 CHANGE_ME 与 secret 里的占位符换掉）
kubectl apply -f k8s/configmap.yaml
kubectl create secret generic reelagent-secrets -n reelagent \
  --from-literal=REEL_SECURITY__SECRET_KEY="$(openssl rand -base64 48)" \
  --from-literal=REEL_SECURITY__API_KEY_PEPPER="$(openssl rand -base64 48)" \
  --from-literal=REEL_DATABASE__URL="postgresql+asyncpg://用户:口令@数据库:5432/reelagent" \
  --from-literal=REEL_REDIS__URL="redis://:口令@redis:6379/0" \
  --from-literal=REEL_PROVIDERS__VIDEO__API_KEY="..." \
  --from-literal=REEL_PROVIDERS__LLM__API_KEY="..." \
  --from-literal=REEL_PROVIDERS__IMAGE__API_KEY="..." \
  --from-literal=REEL_PROVIDERS__TTS__API_KEY="..." \
  --from-literal=REEL_STORAGE__S3_ACCESS_KEY_ID="..." \
  --from-literal=REEL_STORAGE__S3_SECRET_ACCESS_KEY="..."
# （secret.example.yaml 只是模板，真实值不要提交到 Git）

# 2) 迁移：**先跑完再发应用**
kubectl apply -f k8s/job-migrate.yaml
kubectl wait --for=condition=complete job/reelagent-migrate-1-0-0 -n reelagent --timeout=300s

# 3) 应用与入口
kubectl apply -f k8s/deployment-api.yaml -f k8s/deployment-worker.yaml
kubectl apply -f k8s/service.yaml -f k8s/ingress.yaml -f k8s/hpa.yaml

# 4) 本地存储方案才需要（默认是 S3，不需要）
# kubectl apply -f k8s/pvc.yaml

kubectl get pods -n reelagent -w
```

几个已经踩好的决策（清单里都有注释，这里只列结论）：

- **探针**：liveness 用 `/livez`（不查库，库抖动不该导致重启），readiness 用 `/readyz`（查库+存储）；
- **worker 探针**：没有端口，用 `scripts/worker_healthcheck.py` 扫 `/proc` 判断进程是否还在；
- **宽限期**：worker `terminationGracePeriodSeconds: 120` > `SHUTDOWN_GRACE_SECONDS: 60`；
- **用 `args` 而不是 `command`**：保留镜像 ENTRYPOINT 的 tini，信号才能正确转发；
- **只读根文件系统**：`readOnlyRootFilesystem: true` + 挂 `emptyDir` 到 `/tmp` 与 `/app/workspace`；
- **存储默认 S3**：`local` 后端要求 api 与 worker 共享同一块盘，多副本跨节点必然 404；
  非要用 `local`，请同时看 [`k8s/pvc.yaml`](k8s/pvc.yaml) 的约束（单副本 + 同一节点，或 RWX 存储类）；
- **HPA 只给 api**：worker 的瓶颈是队列深度而不是 CPU，CPU 打不满时 HPA 不会扩；
  要自动扩 worker 请上 KEDA 的 postgresql scaler（按任务表待处理行数）；
- **改完 ConfigMap/Secret 要手动滚动**：`kubectl rollout restart deployment/reelagent-api -n reelagent`。

---

## 四、域名与 HTTPS（三种方式通用）

1. **解析**：把 `reelagent.example.com` 的 A 记录指到入口 IP
   （compose：服务器公网 IP；Fly：`fly ips list`；K8s：Ingress Controller 的 LoadBalancer 地址）。
   先确认解析生效（`dig +short reelagent.example.com`）再申请证书，否则 HTTP-01 校验会一直失败。
2. **证书**：
   - compose：Caddy 自动申请；Nginx 用 `certbot --nginx -d reelagent.example.com`；
   - Fly：`fly certs add reelagent.example.com`（自动签发，按提示加好 DNS 记录）；
   - K8s：`ingress.yaml` 里的 `cert-manager.io/cluster-issuer` 会自动签发。
3. **强制 HTTPS**：`REEL_SECURITY__HTTPS_ONLY=true`（生产校验强制要求），
   并在代理层把 80 端口 301 到 443。
4. **真实客户端 IP**：代理必须转发 `X-Forwarded-For` / `X-Forwarded-Proto`
   （Caddy/Nginx 配置见 1.5；K8s 的 `ingress.yaml` 已开 `use-forwarded-headers`）。
   不转发的话限流会把所有请求算成一个 IP，**等于没有限流**。
5. **上传体积**：代理的 `client_max_body_size` / `proxy-body-size` 必须 ≥
   `REEL_STORAGE__MAX_UPLOAD_MB`（默认 32MB），否则用户传素材会拿到 413。
6. **防火墙**：只开 22/80/443；数据库与 Redis 永远不要暴露公网。

---

## 五、Stripe Webhook 配置

1. **地址**（固定路径，不要改）：

   ```
   https://reelagent.example.com/webhooks/stripe
   ```

2. **在 Stripe 后台配置**：Developers → Webhooks → Add endpoint，
   粘贴上面的地址，订阅这些事件（与 `reelagent_cloud/services/billing.py` 的处理器一一对应）：

   | 事件 | 作用 |
   |---|---|
   | `checkout.session.completed` | 支付完成，发放套餐额度 |
   | `customer.subscription.created` | 订阅建立 |
   | `customer.subscription.updated` | 升降级、状态变化 |
   | `customer.subscription.deleted` | 退订（到期后收回权益） |
   | `invoice.paid` | 续费成功，发放周期额度 |
   | `invoice.payment_failed` | 扣款失败，标记 past_due |

3. **拿签名密钥**：创建后页面会给出 `whsec_...`，写进配置：

   ```bash
   # compose
   REEL_BILLING__WEBHOOK_SECRET=whsec_...
   # Fly
   fly secrets set -a reelagent REEL_BILLING__WEBHOOK_SECRET="whsec_..."
   # K8s
   kubectl create secret generic reelagent-secrets -n reelagent \
     --from-literal=REEL_BILLING__WEBHOOK_SECRET="whsec_..." --dry-run=client -o yaml \
     | kubectl apply -f -
   ```

   同时确认 `REEL_BILLING__ENABLED=true` 且 `REEL_BILLING__SECRET_KEY` 已配置
   （启用计费但没配密钥会导致启动失败）。

4. **本地联调**：Stripe CLI 转发事件并打印签名密钥：

   ```bash
   stripe listen --forward-to localhost:8000/webhooks/stripe
   stripe trigger checkout.session.completed
   ```

5. **必须知道的三个行为**（代码里就是这么实现的，排障时能省很多时间）：

   - 端点**不做业务鉴权**，靠 `Stripe-Signature` 验签 → 代理**不能改写请求体**
     （Nginx 记得 `proxy_request_buffering off`，别在中间件里解析 body）；
   - `billing.enabled=false` 时该端点会**返回 200 并在日志里记 `stripe_webhook_disabled`**，
     看起来"正常"但什么都没发生——收不到额度先查这个开关；
   - 处理失败会返回 **5xx** 让 Stripe 重试（这是有意为之），
     所以监控里看到 5xx 不要急着改成 200，那会导致用户付了钱没到账。

---

## 六、备份与恢复

**优先级排序**：数据库（丢了就是全部用户与账目）> 产物文件（丢了用户投诉）> Redis（基本可以不备）。

### 6.1 数据库

```bash
# 每天 03:00 全量备份，保留 14 天（放到 crontab -e）
0 3 * * * docker exec reelagent-postgres pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" \
  > /backup/reelagent-$(date +\%F).dump 2>>/var/log/reelagent-backup.log

# 清理 14 天前的备份
30 3 * * * find /backup -name 'reelagent-*.dump' -mtime +14 -delete
```

要求：

- 备份**不要和被备份的数据放在同一块盘**（`/backup` 最好是另一块盘或直接推到对象存储）：

  ```bash
  # 推到 S3 兼容存储，顺带做异地
  aws s3 cp /backup/reelagent-$(date +%F).dump s3://reelagent-backups/db/ --storage-class STANDARD_IA
  ```

- **每季度做一次恢复演练**，否则备份只能算"以为有备份"：

  ```bash
  # 还原到临时库验证可读，绝不直接覆盖生产库
  docker exec -i reelagent-postgres pg_restore -U "$POSTGRES_USER" -d reelagent_restore --clean < backup.dump
  ```

- 托管数据库（Fly Postgres / 云 RDS）请额外打开**自动快照 + 时间点恢复（PITR）**，
  它能把"误删一张表"的恢复粒度从"一天"降到"一分钟"。

### 6.2 产物文件

- **用了 S3（推荐）**：打开**版本控制**（误删可恢复）与**生命周期规则**
  （例如 90 天后转低频存储、365 天后删除），跨区域复制按合规要求决定。
- **用了 local 存储**：备份 `artifacts` 卷：

  ```bash
  # 备份（restic 支持增量与加密，比 tar 更适合长期跑）
  restic -r s3:s3.amazonaws.com/reelagent-backups/artifacts backup /var/lib/docker/volumes/reelagent_artifacts/_data

  # 恢复
  restic -r s3:s3.amazonaws.com/reelagent-backups/artifacts restore latest --target /tmp/restore
  ```

  K8s 的 local 方案则用 `VolumeSnapshot`（需要 CSI 快照支持）：

  ```yaml
  apiVersion: snapshot.storage.k8s.io/v1
  kind: VolumeSnapshot
  metadata:
    name: reelagent-workspace-20250101
    namespace: reelagent
  spec:
    volumeSnapshotClassName: csi-snapclass
    source:
      persistentVolumeClaimName: reelagent-workspace
  ```

### 6.3 Redis

Redis 里只有限流计数与缓存，**丢了不影响业务正确性**，所以不需要备份。
但建议开 AOF（compose 里 `--appendonly yes` 已经开了），重启后计数不会清零即可。

### 6.4 配置与密钥

- `.env` **不要提交到 Git**（`.gitignore` 与 `.dockerignore` 都已排除）；
  把密钥存进密码管理器或 Vault，团队里只共享"取值方式"而不是"值本身"。
- `REEL_SECURITY__SECRET_KEY` 轮换会让所有用户重新登录，属于计划内操作；
  轮换 `REEL_SECURITY__API_KEY_PEPPER` 会让已发出的 API Key 失效，
  所以它才和 `SECRET_KEY` 分开配置。
- 每次改完配置，用 `safe_dump()` 自检一次（见第零节），确保打码输出里没有意外的空值。

### 6.5 顺手要配的监控（比备份更能救你）

- **探针类**：`/readyz` 返回 503 的持续时间（超过 5 分钟基本就是真故障）；
- **业务类**：`/metrics` 里的任务失败率、任务耗时、队列深度
  （队列只涨不跌 = worker 挂了或供应商限流）；
- **账务类**：Stripe Webhook 的 5xx 数量 —— 这是"用户付了钱没到账"的唯一早期信号；
- **资源类**：容器内存使用率（ffmpeg 合成把内存打满会导致 OOMKilled，表现为任务莫名失败）。
