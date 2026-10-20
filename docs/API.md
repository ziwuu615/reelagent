# REST API 参考（API.md）

> 版本：**1.0.0** ｜ 服务默认地址：`http://localhost:8000`
>
> 本文所有端点、字段名与错误码均**逐一对照源码**。
> 标注 ⚠️ 的地方是已知的坑或不一致之处，请务必阅读。

---

## 目录

- [1. 基础信息](#1-基础信息)
- [2. 鉴权](#2-鉴权)
- [3. 通用约定](#3-通用约定)
- [4. 错误码表](#4-错误码表)
- [5. 限流规则](#5-限流规则)
- [6. 系统端点](#6-系统端点)
- [7. 身份与套餐](#7-身份与套餐)
- [8. 项目](#8-项目)
- [9. 任务](#9-任务)
- [10. 额度](#10-额度)
- [11. 产物](#11-产物)
- [12. API Key 自助管理](#12-api-key-自助管理)
- [13. 审计日志](#13-审计日志)
- [14. 文件与分享](#14-文件与分享)
- [15. Webhook](#15-webhook)
- [16. HTML 页面路由](#16-html-页面路由)
- [17. 端到端示例](#17-端到端示例)

---

## 1. 基础信息

| 项 | 值 |
|---|---|
| **API 前缀** | `/api/v1` |
| **OpenAPI 文档** | `/docs`（Swagger UI）、`/redoc`、`/openapi.json` |
| **标题** | `<product_name> API`（默认 `ReelAgent API`） |
| **版本** | `1.0.0` |
| **联系邮箱** | `REEL_SUPPORT_EMAIL`（默认 `support@reelagent.local`） |
| **License** | `Apache-2.0` |
| **servers** | `[{"url": "<REEL_BASE_URL>", "description": "<REEL_ENV>"}]` |

**内容类型**：请求与响应都是 `application/json`（除了文件下载与 Webhook
的特殊说明）。

**时间格式**：所有时间是 ISO 8601 字符串，含时区偏移
（例如 `2025-11-20T03:14:07.123456+00:00`）。
可空时间字段返回 `null`。

**ID 格式**：带业务前缀的 ULID，26 位 Crockford Base32。前缀表：

| 前缀 | 实体 |
|---|---|
| `usr_` | 用户 |
| `org_` | 组织 |
| `mem_` | 成员关系 |
| `key_` | API Key |
| `proj_` | 项目 |
| `job_` | 任务 |
| `shot_` | 镜头 |
| `art_` | 产物 |
| `led_` | 账本条目 |
| `sub_` | 订阅 |
| `use_` | 用量记录 |
| `aud_` | 审计日志 |
| `wh_` | Webhook 事件 |

**ID 可反解创建时间**：取前缀后的前 10 位按 Crockford Base32 解码即得
毫秒时间戳。产物列表接口的 `created_hint` 字段就是这个值。

---

## 2. 鉴权

三种身份，能力递增且边界清晰：

| 身份 | 凭证 | 能力 |
|---|---|---|
| **匿名** | 无 | 仅公开页面与公开端点 |
| **浏览器会话** | `reelagent_session` Cookie | 完整产品能力，含计费与成员管理 |
| **API Key** | `Authorization: Bearer ...` 或 `X-API-Key` | 受 scope 限制，**不能管理计费与成员** |

### 2.1 API Key 的传递方式

两种，优先检查 `Authorization`：

```http
Authorization: Bearer reel_a1b2c3d4_XXXXXXXXXXXXXXXXXXXXXXXX
```

```http
X-API-Key: reel_a1b2c3d4_XXXXXXXXXXXXXXXXXXXXXXXX
```

> 解析逻辑（`security/deps.py::_extract_bearer`）：
> 先看 `Authorization` 头是否以 `bearer `（忽略大小写）开头，
> 是则取第 7 个字符之后的全部内容；否则回退到 `X-API-Key` 头。

**Key 的格式**：`reel_<8位十六进制 prefix>_<token_urlsafe(32)>`

- `reel_` 是固定前缀（`API_KEY_SCHEME`），便于在日志与代码仓库里做泄漏扫描。
- 第二段是 `prefix`（8 位十六进制），**明文保留在数据库里**，用于列表展示与定位。
- 第三段是秘密部分（约 256 位熵），**只存哈希**
  （`SHA-256(pepper + full_key)`）。
- **完整 Key 只在创建时返回一次**，之后无法取回。

格式校验在 `parse_api_key()`：必须恰好 3 段、第一段是 `reel`、
第二段长度 8，否则视为格式错误（记 `auth_failures_total{reason="malformed_api_key"}`）。

### 2.2 API Key 的能力边界（重要）

**API Key 一律不能通过 `require_role` 检查**：

```python
def require_role(self, minimum: MembershipRole) -> None:
    if self.kind != "session":
        raise Forbidden("该操作需要使用浏览器登录（API Key 不具备管理权限）")
```

**并且 API Key 的角色被强制降级为 `member`**，不继承创建者的角色：

```python
@property
def role(self) -> MembershipRole:
    if self.kind == "api_key":
        return MembershipRole.MEMBER
    ...
```

因此即使创建 Key 的是一位 owner，这把 Key 也只是 member。

**显式拒绝的接口**：

| 端点 | 拒绝原因 |
|---|---|
| `POST /api/v1/api-keys` | `Forbidden("不能用 API Key 创建新的 API Key，请使用浏览器登录")` |
| `DELETE /api/v1/api-keys/{key_id}` | `Forbidden("不能用 API Key 撤销密钥")` |
| `GET /api/v1/audit` | `Forbidden("审计日志仅对浏览器会话开放")` |
| 所有 `/app/*` 页面 | 需要 `SessionPrincipal` |

**允许的操作**（需要 `write` scope）：

- `POST /api/v1/projects`（创建项目并开始生成）
- `DELETE /api/v1/projects/{project_id}`
- `POST /api/v1/jobs/{job_id}/cancel`
- `POST /api/v1/artifacts/{artifact_id}/share`

**只读操作**（需要 `read` scope，默认两者都有）：
所有 `GET` 端点。

`scopes` 默认值是 `["read", "write"]`，可在创建时指定。
`has_scope()` 的实现是 `scope in self.scopes or "admin" in self.scopes`——
但 API Key 永远拿不到 `admin`（因为无法通过 `require_role`）。

> ⚠️ **不要把 `scopes` 理解为「可以授予管理权限」**。
> 它只影响读写项目这一层。详见
> [ADR 0004](adr/0004-api-key-cannot-manage-billing.md)。

### 2.3 浏览器会话

会话是一个签名 Cookie（`itsdangerous.URLSafeTimedSerializer`，
salt `reelagent.session.v1`），载荷只含三个字段：

```json
{"uid": "usr_01...", "sv": 1, "iat": 1732060800}
```

| 字段 | 含义 |
|---|---|
| `uid` | 用户 ID |
| `sv` | **会话代际**（`users.session_version`） |
| `iat` | 签发时间戳 |

**服务端每次请求都会校验 `sv` 是否等于数据库里的 `session_version`。**
改密码或「登出所有设备」会把 `session_version` +1，
所有已签发的 Cookie 立即失效。

**Cookie 属性**（由 `/login` 与 `/signup` 设置）：

| Cookie | HttpOnly | SameSite | Secure | 有效期 |
|---|---|---|---|---|
| `reelagent_session` | ✅ | `Lax` | 跟随 `https_only` | `SESSION_MAX_AGE_SECONDS`（默认 14 天） |
| `reelagent_csrf` | ❌（前端要读） | `Lax` | 跟随 `https_only` | 同上 |

**Cookie 里刻意不放角色 / 额度 / 套餐**——那些必须每次从库里读，
否则权限变更无法即时生效（注释里有明确说明）。

### 2.4 CSRF

**Cookie 认证 + 改变状态的方法**必须带 CSRF 令牌：

```http
x-csrf-token: <从 reelagent_csrf cookie 读出的值>
```

校验用常数时间比较（`hmac.compare_digest`）。

| 情况 | 是否校验 CSRF |
|---|---|
| `GET` / `HEAD` / `OPTIONS` / `TRACE` | 不校验 |
| API Key 认证 | **不校验**（浏览器不会自动携带，天然免疫 CSRF） |
| 会话认证 + `POST`/`PUT`/`PATCH`/`DELETE` | **必须校验** |

失败返回 403 `forbidden`，消息为
「CSRF 校验失败，请刷新页面后重试」，
并记 `auth_failures_total{reason="csrf_mismatch"}`。

控制台的 HTML 表单还有**第二层**：表单里带一个隐藏字段 `csrf_token`
（`console.py::_require_csrf`），失败消息为「页面已过期，请刷新后重试」。

### 2.5 多组织切换

一个用户可以属于多个组织。默认使用**最早加入的**组织
（`ORDER BY memberships.created_at` 的第一个）。

要切换到其它组织，带 `X-Org-Id` 头：

```http
X-Org-Id: org_01M4Z776T6PJMX3YCAC5VSECSJ
```

**服务端会校验成员关系**。不是成员则返回：

```json
{
  "error": {"code": "forbidden", "message": "你不是该组织的成员"},
  "request_id": "..."
}
```

> ⚠️ **注意一个行为差异**：当显式传 `X-Org-Id` 时，
> `_resolve_org()` 的 `requested` 分支**不会检查 `is_suspended`**。
> 只有默认组织路径（不传 `X-Org-Id`）才检查。
> 因此**停用组织时建议同时吊销其 API Key**（见
> [OPERATIONS.md](OPERATIONS.md) 第 3.4 节）。

---

## 3. 通用约定

### 3.1 统一错误响应

**所有**错误（包括 422 校验错误与 500 未预期异常）都是同一个结构：

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

| 字段 | 说明 |
|---|---|
| `error.code` | **稳定的机器可读错误码**。请据此分支，不要匹配 `message` |
| `error.message` | 给终端用户看的中文消息，可直接展示 |
| `error.details` | 可选。字段随错误码不同（见第 4 节） |
| `request_id` | 排障标识。**报障时请提供它** |

**请求 ID 来源**：服务端尊重上游传入的 `X-Request-ID` 头
（截断到 64 字符），没有则生成 16 位 hex。
同一个 `request_id` 也会出现在响应头 `X-Request-ID` 与所有相关日志里。

### 3.2 响应头

| 响应头 | 说明 |
|---|---|
| `X-Request-ID` | 与响应体的 `request_id` 一致 |
| `Server-Timing` | `app;dur=<毫秒>`，服务端处理耗时 |
| `Retry-After` | **仅限流错误**。值为秒数（= 限流窗口长度） |

### 3.3 幂等

**创建项目的接口支持 `Idempotency-Key` 请求头**：

```http
Idempotency-Key: 6f1c9a2e-...
```

**未提供时的兜底**：服务端用内容指纹自动生成一个键，
格式为 `auto-<sha256 前 40 位>`，指纹的输入是：

```
f"{seed_text}|{style}|{aspect_ratio}|{shots}"
```

响应里会回显实际使用的键：

```json
{"project": {...}, "job": {...}, "quote": {...}, "idempotency_key": "auto-3f2a..."}
```

> ⚠️ **已知缺口**：`POST /api/v1/projects` 读取了 `Idempotency-Key`
> 并回显，但 `projects.create_project()` **没有接收这个键**。
> 任务侧的幂等键仍然是 `gen:<project_id>`，而 `project_id` 每次都是新生成的。
>
> **实际后果：同一个素材重复提交会创建两个项目、各扣一次预扣额度。**
> 请客户端自己用 `Idempotency-Key` 或内容指纹做去重
> （服务端返回的 `idempotency_key` 可用于你自己判断）。
>
> 这个问题记录在 [CHANGELOG 已知问题](../CHANGELOG.md#一致性与安全问题)。

**其它幂等机制**（服务端内部，客户端无需关心）：

| 机制 | 幂等键 |
|---|---|
| 账本条目 | `uq_ledger_org_idempotency(org_id, idempotency_key)` |
| 任务入队 | `uq_job_org_idempotency(org_id, idempotency_key)` |
| Webhook 事件 | `uq_webhook_provider_event(provider, event_id)` |

### 3.4 分页

统一的参数名：

| 参数 | 默认 | 上限 |
|---|---|---|
| `limit` | 视端点（20 / 30 / 50） | 100（额度流水是 200） |
| `offset` | 0 | — |

响应里返回 `total`（匹配的总数，不只是本页）：

```json
{"items": [...], "total": 137, "limit": 20, "offset": 0}
```

> 注意：`GET /api/v1/jobs` 的 `total` 是**本页长度**而不是总数
> （实现是 `len(rows)`），因为它没有单独的 count 查询。
> 见 [第 9 节](#9-任务)的说明。

### 3.5 枚举取值

**项目状态**（`projects.status`）：

| 值 | 中文 | 终态 |
|---|---|---|
| `draft` | 草稿 | 否 |
| `queued` | 排队中 | 否 |
| `running` | 生成中 | 否 |
| `ready` | 已完成 | ✅ |
| `failed` | 失败 | ✅ |
| `archived` | 已归档 | ✅ |

**任务状态**（`jobs.status`）：

| 值 | 含义 | 终态 | 可被重新领取 |
|---|---|---|---|
| `pending` | 等待执行 | 否 | ✅ |
| `running` | 执行中 | 否 | 否 |
| `succeeded` | 成功 | ✅ | 否 |
| `failed` | 失败，**可重试** | ✅ | ✅ |
| `dead` | 超过重试上限，需人工介入 | ✅ | 否 |
| `cancelled` | 已取消 | ✅ | 否 |

**任务阶段**（`jobs.stage`）与进度：

| 值 | 中文 | `progress` |
|---|---|---|
| `queued` | 排队中 | 0.0 |
| `planning` | 拆解剧本 | 0.08 |
| `assets` | 生成角色与场景 | 0.18 |
| `generating` | 逐镜生成 | 0.55 |
| `evaluating` | 质量评测 | 0.82 |
| `rendering` | 合成成片 | 0.93 |
| `done` | 完成 | 1.0 |

**任务类型**（`jobs.kind`）：

| 值 | 说明 | 当前 worker 是否支持 |
|---|---|---|
| `generate_project` | 生成整条成片 | ✅ |
| `regenerate_shot` | 重跑指定镜头 | ✅ |
| `evaluate_project` | 只重跑评测 | ❌ 抛 `NotImplementedError` |
| `render_project` | 只重跑合成 | ❌ 抛 `NotImplementedError` |

**产物类型**（`artifacts.kind`）：
`video` / `gif` / `audio` / `keyframe` / `base_image` / `storyboard` /
`manifest` / `report`

**账本类型**（`ledger_entries.kind`）：

| 值 | 方向 | 触发点 |
|---|---|---|
| `signup_grant` | + | 注册赠送（500 点） |
| `plan_grant` | + | 订阅首期 / 周期续费 |
| `purchase` | + | 单独购买点数包 |
| `consume` | − | 生成消耗 / 超预估补扣 |
| `refund` | + | 失败 / 取消 / 按实际用量退还 |
| `adjustment` | ± | 人工调整 |
| `expire` | +（⚠️ 见下） | 过期作废（**无产生方**） |

> ⚠️ `LedgerKind.sign` 把除 `consume` 之外的所有类型都算作正号，
> 因此 `expire` 目前不会被记成负数。该类型在枚举里定义了，
> 但代码里没有任何地方产生它。

**风格预设**（`style`）：`cinematic`（电影感）/ `anime`（日式动画）/
`cyberpunk`（赛博朋克）/ `ink`（水墨）/ `realistic`（写实记录）

**画幅**（`aspect_ratio`）：`9:16`（默认）/ `16:9` / `1:1` / `4:3` / `3:4`

**组织套餐**（`plan.tier`）：`free` / `starter` / `pro` / `studio`

### 3.6 订阅状态

`GET /api/v1/me` 与计费相关接口里的 `status`：

| 值 | 含义 |
|---|---|
| `trialing` | 试用中 |
| `active` | 生效 |
| `past_due` | 扣款失败（**给宽限期，能力暂不收回**） |
| `canceled` | 已取消 |
| `incomplete` | 首次支付未完成 |
| `unpaid` | 未支付 |

### 3.7 额度单位

**对外只展示「点数」，不暴露供应商真实成本结构。**

```
点数 = max(1, ceil(供应商成本USD × markup_multiplier × credits_per_usd))
```

默认 `markup_multiplier = 3.0`、`credits_per_usd = 100`，
因此 **1 USD 供应商成本 ≈ 300 点**。

> ⚠️ **`GET /api/v1/me` 的 `credits.usd_rate` 字段命名有歧义**：
> 它返回的是 `credits_per_usd`（默认 `100`），
> **不是**「点数 → 美元」的换算率。真实换算率是
> `markup_multiplier × credits_per_usd = 300`。
> 请以 `credits.unit = "credits"` 与本文的公式为准。

**预估偏保守**：`POST /api/v1/credits/quote` 的估算乘了 1.15 安全系数，
避免实际超支导致用户中途失败。

---

## 4. 错误码表

来自 `reelagent_cloud/domain/errors.py` 与 `api/errors.py`。

| `code` | HTTP | `details` | 消息（默认） | 何时出现 |
|---|---|---|---|---|
| `validation_failed` | **422** | `{"fields": [{"field","message"}]}` | `请求参数不合法` | 请求体/参数不合法 |
| `invalid_state` | 409 | — | `当前状态不允许该操作` | 例如取消已终结的任务 |
| `unsupported_operation` | 400 | — | `该功能在当前部署下不可用` | 未开启 Stripe、生产环境无视频供应商、未指定重跑镜头 |
| `unauthorized` | 401 | — | `未登录或凭证无效` | 缺少/无效凭证 |
| `forbidden` | 403 | — | `没有权限执行该操作` | 权限不足、越权、租户不匹配、CSRF 失败 |
| `account_locked` | 403 | — | `账号已被临时锁定` | 连续登录失败（8 次 / 15 分钟） |
| `account_suspended` | 403 | — | `账号已被停用` | 组织被停用或账号被锁定 |
| `insufficient_credits` | **402** | `{"required","available"}` | `额度不足：本次需要 N 点，当前可用 M 点` | 余额不足 |
| `quota_exceeded` | 402 | — | `超出套餐限制` | 套餐限额 |
| `rate_limited` | **429** | `{"scope","retry_after_seconds"}` | `操作过于频繁，请 N 秒后再试` | 触发限流 |
| `not_found` | 404 | — | `资源不存在` | 资源不存在或不属于当前租户 |
| `already_exists` | 409 | — | `资源已存在` | 邮箱已注册、成员已存在 |
| `provider_failure` | **502** | `{"provider","retryable"}` | `上游生成服务暂时不可用` | 供应商调用失败 |
| `provider_circuit_open` | **503** | `{"provider","retry_after_seconds"}` | `生成服务「X」暂时不可用，请稍后重试` | 熔断打开（含 `Retry-After` 头） |
| `service_unavailable` | 503 | — | `服务暂时不可用` | — |
| `job_failed` | 500 | — | `任务执行失败` | — |
| `job_cancelled` | 409 | — | `任务已取消` | — |
| `budget_exceeded` | 402 | — | `超出预算上限` | 超出 `job_budget_usd` |
| `internal_error` | **500** | — | `服务开小差了，请稍后重试。若持续出现请联系支持并提供 request_id` | 未预期的异常 |
| `method_not_allowed` | 405 | — | `请求无法处理` | HTTP 方法不允许 |
| `http_<code>` | 其它 | — | — | Starlette 层其它 HTTP 异常 |

**错误码映射的补充说明**：

- `422` 的 `details.fields` 会列出最多 10 个字段错误，
  每个形如 `{"field": "seed_text", "message": "Field required"}`。
  `field` 已去掉 `body` / `query` / `path` 前缀。
- 未预期异常**绝不外泄细节**：对外只给通用文案 + `request_id`，
  完整堆栈只进日志（事件名 `unhandled_exception`）。
- `provider_failure` 标记为 `user_facing = False`，
  但在响应里仍然返回它的消息（上游错误不被直接透传）。

---

## 5. 限流规则

两种后端，按是否启用 Redis 自动选择：

| 后端 | 算法 | 适用 |
|---|---|---|
| Redis | 固定窗口计数器（`INCR` + `EXPIRE NX`） | 多实例共享配额（生产推荐） |
| 进程内 | 滑动窗口 deque + 惰性清理（每 5 分钟扫一次） | 单实例部署 |

> ⚠️ **多副本部署必须启用 Redis**。否则每个副本各自计数，
> 实际限额是「配置值 × 副本数」。
>
> ⚠️ **fail-open**：限流器后端故障时**放行**而不是拒绝
> （「限流器本身挂了不应该让整个站点不可用」）。
> 因此限流失效时不会有错误，只会看到配额没被遵守。

**限额表**：

| scope | 依据 | 限额 | 窗口 | 配置项 |
|---|---|---|---|---|
| `anonymous` | 客户端 IP | 30 | 60s | `REEL_RATE_LIMIT__ANONYMOUS_PER_MINUTE` |
| `user` | 用户 ID | 240 | 60s | `REEL_RATE_LIMIT__USER_PER_MINUTE` |
| `api_key` | API Key ID | 300 | 60s | `REEL_RATE_LIMIT__API_KEY_PER_MINUTE` |
| `login` | 客户端 IP | 10 | 300s | `REEL_SECURITY__LOGIN_MAX_ATTEMPTS` / `LOGIN_WINDOW_SECONDS` |
| `signup` | 客户端 IP | 5 | 3600s | **固定值**（写死在 `limits_from`） |
| `project_create` | 组织 ID | 60 | 3600s | `REEL_RATE_LIMIT__PROJECT_CREATES_PER_HOUR` |
| `upload` | — | 30 | 3600s | **固定值**（当前无调用方） |

**哪些端点实际检查限流**：

| 端点 | 检查的 scope |
|---|---|
| `POST /login` | `login` |
| `POST /signup` | `signup` |
| `POST /api/v1/projects` | `project_create`（按 org）+ 若为 API Key 再查 `api_key` |

> ℹ️ `anonymous` 与 `user` 两个 scope 在代码里**定义了但没有调用点**。
> 也就是说，除了上面三个端点，其它接口目前**不受限流保护**。
> 这是一个需要补齐的地方。

**超限响应**：

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 60
```

```json
{
  "error": {
    "code": "rate_limited",
    "message": "操作过于频繁，请 60 秒后再试",
    "details": {"scope": "project_create", "retry_after_seconds": 60}
  },
  "request_id": "..."
}
```

**客户端 IP 的判定**（`deps.py::client_ip`）：

1. 若 `X-Forwarded-For` 的第一跳是内网/回环地址 → 认为是可信代理链，取**最后一跳**。
2. 否则取 `X-Forwarded-For` 的**第一跳**。
3. 否则取 `X-Real-IP`。
4. 否则取 TCP 对端地址。

> ⚠️ **如果应用端口暴露到公网，攻击者可以伪造 `X-Forwarded-For`
> 绕过所有基于 IP 的限流**（`anonymous` / `login` / `signup`）。
> 必须确保应用只接受来自可信代理的连接（见
> [DEPLOYMENT.md](DEPLOYMENT.md) 第 4.4 节）。

---

## 6. 系统端点

这些端点**不需要鉴权**，且 `include_in_schema=False`
（除 `/api/v1/system/*`）——**它们不出现在 `/docs` 里**。

### `GET /livez` — 存活探针

**不触碰任何外部依赖**（不查数据库）。

```bash
curl -s http://localhost:8000/livez
```

```json
{"status": "alive"}
```

**用途**：K8s `livenessProbe`。
数据库抖动时**不应该**让容器被杀掉重启，因此这个探针刻意什么都不查。

### `GET /readyz` — 就绪探针

检查数据库（`SELECT 1`）与存储（真实写入并删除 `_healthcheck/probe.txt`）。

```bash
curl -s http://localhost:8000/readyz | jq
```

**正常（HTTP 200）**：

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

**不健康（HTTP 503）**：

```json
{
  "status": "not_ready",
  "checks": {
    "database": {"ok": false, "critical": true, "error": "OperationalError"},
    "storage":  {"ok": true, "critical": true, "backend": "s3"},
    "providers": {"ok": false, "critical": false, "video": "未配置", "image": "ark", "llm": "deepseek"},
    "billing":  {"ok": true, "critical": false, "enabled": false}
  }
}
```

**只有 `critical: true` 的检查失败才会导致 503。**
`providers.ok = false`（未配置视频 Key）**不会**让服务被摘除——
因为此时服务仍能正常响应查询，只是生成请求会被拒绝。

### `GET /health` — 人类可读汇总

同 `/readyz` 的检查，但额外返回 `version` 与 `env`。

```json
{
  "status": "ok",
  "version": "1.0.0",
  "env": "prod",
  "checks": { "...": "同上" }
}
```

`status` 取值：`ok`（全部 critical 通过）或 `degraded`。
HTTP 状态码：200 或 503。

### `GET /metrics` — Prometheus 指标

```bash
curl -s http://localhost:8000/metrics | head -30
```

返回 Prometheus 文本格式，`Content-Type` 为 `text/plain; version=0.0.4`。

被 `REEL_OBSERVABILITY__METRICS_ENABLED=false` 关闭时：

```
HTTP 404
metrics disabled
```

> ℹ️ 配置里的 `REEL_OBSERVABILITY__METRICS_PATH` **不生效**——
> 路由写死为 `/metrics`。

### `GET /api/v1/system/status` — 状态页数据

> ⚠️ **这个端点无鉴权**（不挂 `CurrentPrincipal`）。
> 任何人都能读到队列深度与配置快照。
> 生产环境建议在反向代理层限制来源。

```bash
curl -s http://localhost:8000/api/v1/system/status | jq
```

```json
{
  "version": "1.0.0",
  "env": "prod",
  "queues": {
    "pending": 12,
    "running": 6,
    "succeeded": 1843,
    "failed": 3,
    "dead": 2,
    "cancelled": 7
  },
  "config": {
    "env": "prod",
    "base_url": "https://reelagent.example.com",
    "database": "postgresql",
    "redis": true,
    "storage": "s3",
    "billing": true,
    "rate_limit": true,
    "providers": {
      "llm":   {"provider": "deepseek",  "configured": true},
      "image": {"provider": "ark",       "configured": true},
      "video": {"provider": "ark",       "configured": true},
      "tts":   {"provider": "dashscope", "configured": true}
    }
  }
}
```

`config` 就是 `Settings.safe_dump()`——**所有密钥已打码**。
`queues` 是**全库**的任务计数（不区分组织）。

### `GET /api/v1/system/ping` — 连通性检查

```bash
curl -s http://localhost:8000/api/v1/system/ping
```

```json
{"pong": "ok"}
```

---

## 7. 身份与套餐

### `GET /api/v1/me` — 当前身份与套餐

**鉴权**：会话 或 API Key（`read` scope）

```bash
curl -s http://localhost:8000/api/v1/me \
  -H "Authorization: Bearer reel_a1b2c3d4_XXXX"
```

```json
{
  "actor": {
    "kind": "api_key",
    "user_id": null,
    "email": null,
    "role": "member",
    "scopes": ["read", "write"]
  },
  "organization": {
    "id": "org_01M4Z76RW2WNQXHJE6Y49XNMNA",
    "name": "例子工作室",
    "slug": "example-studio"
  },
  "plan": {
    "tier": "pro",
    "name": "专业版",
    "price_cny": 199,
    "monthly_credits": 15000,
    "status": "active",
    "current_period_end": "2025-12-20T03:14:07.123456+00:00",
    "cancel_at_period_end": false,
    "watermark": false
  },
  "credits": {
    "balance": 14820,
    "unit": "credits",
    "usd_rate": 100
  },
  "usage": {
    "total": 14,
    "active": 1,
    "ready": 11
  }
}
```

**字段说明**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `actor.kind` | `string` | `session` 或 `api_key` |
| `actor.user_id` | `string \| null` | API Key 身份时为 `null` |
| `actor.email` | `string \| null` | 同上 |
| `actor.role` | `string` | `owner` / `admin` / `member`。API Key 恒为 `member` |
| `actor.scopes` | `string[]` | 已排序。会话身份为 `["admin","read","write"]` |
| `organization.*` | | 当前生效的组织 |
| `plan.*` | | `current_plan_summary()` 的输出 |
| `plan.current_period_end` | `string \| null` | 无订阅记录时为 `null` |
| `plan.status` | `string` | 无订阅记录时默认 `active` |
| `credits.balance` | `int` | 额度快照 |
| `credits.usd_rate` | `int` | ⚠️ **实际是 `credits_per_usd`（100），不是换算率** |
| `usage.total` / `active` / `ready` | `int` | 项目计数（不含软删） |

### `GET /api/v1/plans` — 套餐列表（公开）

**鉴权**：无

```bash
curl -s http://localhost:8000/api/v1/plans | jq
```

```json
{
  "items": [
    {
      "tier": "free",
      "name": "体验版",
      "tagline": "先跑通一条完整成片，再决定要不要付费",
      "price_cny": 0,
      "monthly_credits": 500,
      "max_shots_per_project": 6,
      "max_concurrent_jobs": 1,
      "max_projects": 3,
      "max_org_members": 1,
      "allow_premium_tier": false,
      "support": "社区支持",
      "highlights": [
        "每月 500 点数",
        "单条成片最多 6 个镜头",
        "同时 1 个生成任务",
        "保留 3 个项目",
        "成片带水印"
      ]
    }
  ]
}
```

**四个套餐的完整对照**（来自 `domain/plans.py`，是定价与校验的单一事实来源）：

| 字段 | 体验版 | 创作者版 | 专业版 | 工作室版 |
|---|---|---|---|---|
| `tier` | `free` | `starter` | `pro` | `studio` |
| `name` | 体验版 | 创作者版 | 专业版 | 工作室版 |
| `price_cny` | 0 | 49 | 199 | 699 |
| `monthly_credits` | 500 | 3,000 | 15,000 | 60,000 |
| `max_shots_per_project` | 6 | 8 | 12 | 12 |
| `max_concurrent_jobs` | 1 | 1 | 3 | 8 |
| `max_projects` | 3 | 20 | -1（不限） | -1（不限） |
| `max_org_members` | 1 | 2 | 5 | 20 |
| `allow_premium_tier` | ❌ | ✅ | ✅ | ✅ |
| `priority`（调度优先级） | 0 | 1 | 2 | 3 |
| `commercial_use` | ❌ | ✅ | ✅ | ✅ |
| `support` | 社区支持 | 邮件支持（48h） | 邮件支持（24h）+ API 文档 | 专属支持渠道（4h） |

`max_projects = -1` 表示**不限**。

---

## 8. 项目

项目是「一次创作」的聚合根。创建项目时会在**同一个事务**里：
校验套餐限制 → 预扣额度 → 建项目 → 入队任务。

### `POST /api/v1/projects` — 创建项目并开始生成

**鉴权**：`WritablePrincipal`（会话 或带 `write` scope 的 API Key）

**请求头**：

| 头 | 必需 | 说明 |
|---|---|---|
| `Idempotency-Key` | 否 | 幂等键（⚠️ 见 3.3 节的已知缺口） |
| `Content-Type` | ✅ | `application/json` |

**请求体**：

```json
{
  "seed_text": "深夜的便利店只剩下最后一盏灯。林晚把湿透的伞收在门口，走向货架。",
  "title": "深夜便利店",
  "style": "cinematic",
  "aspect_ratio": "9:16",
  "shots": 6,
  "auto_start": true
}
```

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `seed_text` | `string` | ✅ | 素材。也接受别名 `text`。**长度 6～20000 字符** |
| `title` | `string` | 否 | 标题。留空则取素材首行的前 24 字符 |
| `style` | `string` | 否 | 风格预设。留空用组织默认风格（`organizations.default_style`）或 `REEL_STYLE` |
| `aspect_ratio` | `string` | 否 | 留空用 `REEL_ASPECT_RATIO`（默认 `9:16`） |
| `shots` | `int` | 否 | 镜头数。留空用套餐的 `max_shots_per_project`。会被钳制到 `[1, min(套餐上限, REEL_MAX_SHOTS_PER_PROJECT)]` |
| `auto_start` | `bool` | 否 | 默认 `true`。设为 `false` 则只建项目不入队 |

**校验与拒绝**（按执行顺序）：

| 条件 | 错误 |
|---|---|
| 组织已停用 | 403 `forbidden`（「组织已被停用」） |
| `seed_text` 长度 < 6 | 422 `validation_failed`（「素材至少需要 6 个字」） |
| `seed_text` 长度 > 20000 | 422 `validation_failed`（「素材过长（上限 20000 字），请拆分成多条」） |
| 项目数已达套餐上限（`max_projects >= 0` 时） | 403 `forbidden`（「{套餐名}最多保留 N 个项目，请删除旧项目或升级套餐」） |
| 并发任务数已达套餐上限（`auto_start=true` 时） | 403 `forbidden`（「{套餐名}同时只能有 N 个生成任务在跑，请等待完成或升级套餐」） |
| 余额不足 | 402 `insufficient_credits`（带 `required` / `available`） |
| `project_create` 限流超限 | 429 `rate_limited` |
| 请求体不是合法 JSON 或不是对象 | 422 `validation_failed`（「请求体必须是合法 JSON」/「请求体必须是 JSON 对象」） |
| 缺少 `seed_text` 与 `text` | 422 `validation_failed`（「缺少 seed_text 字段」） |

**成功响应（HTTP 201）**：

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
  "idempotency_key": "auto-3f2a1b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a"
}
```

**`_project_brief` 的字段（列表与详情共用）**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `string` | `proj_...` |
| `title` | `string` | |
| `status` | `string` | 见 3.5 节 |
| `style` | `string` | |
| `aspect_ratio` | `string` | |
| `total_shots` | `int` | |
| `finished_shots` | `int` | 已产出片段的镜头数 |
| `duration_seconds` | `float` | 四舍五入到 2 位 |
| `credits_spent` | `int` | 该项目累计预扣的额度 |
| `created_at` / `updated_at` | `string` | ISO 8601 |

**`_job_view` 的字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | `string` | `job_...` |
| `kind` | `string` | 见 3.5 节 |
| `status` | `string` | |
| `stage` | `string` | |
| `progress` | `float` | 四舍五入到 3 位 |
| `attempts` / `max_attempts` | `int` | |
| `error` | `string \| null` | 空字符串时返回 `null` |
| `error_code` | `string \| null` | 同上 |
| `result` | `object` | 成功时的结果（见 9 节） |
| `created_at` | `string` | |
| `started_at` / `finished_at` | `string \| null` | |

### `GET /api/v1/projects` — 项目列表

**鉴权**：会话 或 API Key

**查询参数**：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `status` | `string` | `""` | 按状态过滤。非法值返回 422 `validation_failed`（「未知状态：X」） |
| `q` | `string` | `""` | 按**标题或素材**模糊搜索（`ILIKE`），截断到 60 字符 |
| `limit` | `int` | 20 | 1～100 |
| `offset` | `int` | 0 | ≥ 0 |

```bash
curl -s "http://localhost:8000/api/v1/projects?status=ready&q=便利店&limit=10" \
  -H "Authorization: Bearer reel_a1b2c3d4_XXXX"
```

```json
{
  "items": [ { "...": "同 _project_brief" } ],
  "total": 11,
  "limit": 10,
  "offset": 0
}
```

排序：`created_at DESC`。**软删的项目（`deleted_at` 非空）不出现在结果里。**

### `GET /api/v1/projects/{project_id}` — 项目详情

**鉴权**：会话 或 API Key

**路径参数**：`project_id`

**查询参数**：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `include_shots` | `bool` | `true` | 是否包含镜头数组 |

```bash
curl -s "http://localhost:8000/api/v1/projects/proj_01M4Z76RZGNWY5TB0NEE2MTZYV" \
  -H "Authorization: Bearer reel_a1b2c3d4_XXXX" | jq
```

**响应**（`_project_brief` 的全部字段，加上）：

```json
{
  "id": "proj_01M4Z76RZGNWY5TB0NEE2MTZYV",
  "title": "深夜便利店",
  "status": "ready",
  "style": "cinematic",
  "aspect_ratio": "9:16",
  "total_shots": 6,
  "finished_shots": 6,
  "duration_seconds": 18.5,
  "credits_spent": 780,
  "created_at": "2025-11-20T03:14:07.123456+00:00",
  "updated_at": "2025-11-20T03:22:41.654321+00:00",

  "logline": "深夜的便利店里，一把钥匙等着一个等了三年的人。",
  "clues": [
    {
      "id": "clue_主角_83b6",
      "kind": "character",
      "name": "林晚",
      "description": "二十七八岁的女性，深夜独自来到便利店",
      "appearance": "齐肩黑色直发，圆脸，左眉尾有一颗小痣，米色长风衣，深灰围巾",
      "base_image": "projects/proj_01M4Z76RZGNWY5TB0NEE2MTZYV/assets/clue_主角_83b6.png",
      "style_tags": []
    }
  ],
  "evaluation": {
    "metrics": {
      "overall": 0.9246,
      "palette_stability": 1.0,
      "cross_shot_clue_similarity": 0.6986,
      "clue_reference_coverage": 1.0,
      "prompt_clue_fidelity": 1.0,
      "consistency_strategy_active": true,
      "coverage_wilson_lower": 0.6,
      "per_clue_similarity": {"林晚": {"shots": 4, "similarity": 0.6986}},
      "coverage_detail": {"eligible": 4, "covered": 4},
      "fidelity_misses": [],
      "rhythm": {
        "distinct_shot_sizes": 3,
        "distinct_cameras": 3,
        "duration_min": 2.0,
        "duration_max": 3.5,
        "duration_std": 0.5,
        "shot_size_histogram": {"中景": 2, "近景": 2, "特写": 2}
      },
      "shots_evaluated": 6
    },
    "judge": {
      "overall": 4.25,
      "narrative": 4,
      "consistency": 4,
      "rhythm": 4,
      "fidelity": 5,
      "top_issues": ["..."],
      "suggestions": ["..."]
    },
    "judge_error": null,
    "shots": 6,
    "finished": 6,
    "failed": 0,
    "gate": {
      "passed": true,
      "failures": [],
      "thresholds": {
        "prompt_clue_fidelity": 0.95,
        "clue_reference_coverage": 0.9,
        "palette_stability": 0.5,
        "judge_overall": 3.0
      }
    }
  },
  "cost": {
    "total_usd": 0.8123,
    "by_category": {
      "image": {"usd": 0.1500, "calls": 10, "items": []},
      "video": {"usd": 0.6123, "calls": 6, "items": []},
      "tts":   {"usd": 0.0500, "calls": 6, "items": []}
    }
  },
  "shots": [
    {
      "key": "sc01_sh01",
      "order": 0,
      "status": "audio_done",
      "shot_size": "中景",
      "camera": "推",
      "duration_s": 3.0,
      "action": "林晚收伞走进便利店",
      "narration": "深夜的便利店只剩下最后一盏灯。",
      "dialogue": "",
      "image_prompt": "中景，林晚收伞走进便利店，齐肩黑色直发…（略）",
      "video_prompt": "推，林晚收伞走进便利店",
      "clue_ids": ["clue_主角_83b6", "clue_深夜的街景_bd69"],
      "tier": "standard",
      "cost_usd": 0.13847,
      "error": null
    }
  ]
}
```

**镜头对象的字段**：

| 字段 | 类型 | 来源 |
|---|---|---|
| `key` | `string` | `shots.shot_key`（`sc01_sh01` 形式） |
| `order` | `int` | `shots.order_index` |
| `status` | `string` | 引擎镜头状态：`pending` / `keyframe_done` / `clip_done` / `audio_done` / `failed` |
| `shot_size` | `string` | 景别：特写/近景/中景/全景/远景 |
| `camera` | `string` | 运镜：固定/推/拉/摇/移/跟 |
| `duration_s` | `float` | |
| `action` / `narration` / `dialogue` | `string` | 导演意图 |
| `image_prompt` / `video_prompt` | `string` | 派生的提示词（可在 Studio 里手工改写） |
| `clue_ids` | `string[]` | 绑定的一致性线索 |
| `tier` | `string` | 路由档位（`draft` / `standard` / `premium`），可能为空 |
| `cost_usd` | `float` | 四舍五入到 5 位 |
| `error` | `string \| null` | 空字符串时返回 `null` |

> ℹ️ `shots` 数组里的对象**不含** `provider` / `model` / `attempts` /
> `keyframe_key` / `clip_key` / `audio_key`（这些字段在
> `GET /api/v1/projects/{id}/artifacts` 与 `shots` 表里，但不由本接口返回）。

**错误**：项目不存在或不属于当前组织 → 404 `not_found`（「项目不存在」）。

> ℹ️ **租户隔离在这里是靠查询实现的**：
> `projects.get_project(session, org_id=..., project_id=...)` 把
> `Project.org_id == org_id` 写进 WHERE 子句。
> 因此**用别的租户的 project_id 会得到 404 而不是 403**——
> 这是刻意的（不泄漏资源是否存在）。

### `DELETE /api/v1/projects/{project_id}` — 删除项目

**鉴权**：`WritablePrincipal`

**这是软删除**：设置 `projects.deleted_at` 并把状态改为 `archived`。
产物保留以便误删恢复。

**成功响应**：`HTTP 204 No Content`（无响应体）。

**错误**：

| 条件 | 错误 |
|---|---|
| 项目不存在 / 不属于当前组织 | 404 `not_found` |
| 项目正在生成中（有 `pending` 或 `running` 的任务） | 403 `forbidden`（「项目正在生成中，请先取消任务再删除」） |

**用 curl 删除并确认**：

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X DELETE \
  "http://localhost:8000/api/v1/projects/proj_01M4Z76RZGNWY5TB0NEE2MTZYV" \
  -H "Authorization: Bearer reel_a1b2c3d4_XXXX"
# 期望 204
```

> ⚠️ **软删不会删除对象存储里的文件**。磁盘空间不会因此释放。
> 需要单独的对象清理流程（见 [OPERATIONS.md](OPERATIONS.md) 第 7 节）。

---

## 9. 任务

### `GET /api/v1/jobs` — 任务列表

**鉴权**：会话 或 API Key

**查询参数**：

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `active_only` | `bool` | `false` | 只要 `pending` / `running` / `failed` |
| `limit` | `int` | 20 | 1～100 |

```bash
curl -s "http://localhost:8000/api/v1/jobs?active_only=true&limit=5" \
  -H "Authorization: Bearer reel_a1b2c3d4_XXXX" | jq
```

```json
{
  "items": [ { "...": "同 _job_view" } ],
  "total": 5
}
```

> ⚠️ **`total` 是本页长度（`len(rows)`），不是匹配总数。**
> 这个端点的实现是 `{"items": [...], "total": len(rows)}`，
> 没有单独的 count 查询。需要真实总数请查数据库或
> `GET /api/v1/system/status` 的 `queues`（但那是全库计数，不分组织）。

排序：`created_at DESC`。

### `GET /api/v1/jobs/{job_id}` — 任务详情（**轮询进度的主接口**）

**鉴权**：会话 或 API Key

```bash
curl -s "http://localhost:8000/api/v1/jobs/job_01M4Z76S1P2Q3R4S5T6V7W8X9Y" \
  -H "Authorization: Bearer reel_a1b2c3d4_XXXX" | jq
```

**运行中**：

```json
{
  "id": "job_01M4Z76S1P2Q3R4S5T6V7W8X9Y",
  "kind": "generate_project",
  "status": "running",
  "stage": "generating",
  "progress": 0.55,
  "attempts": 1,
  "max_attempts": 3,
  "error": null,
  "error_code": null,
  "result": {},
  "created_at": "2025-11-20T03:14:07.123456+00:00",
  "started_at": "2025-11-20T03:14:09.234567+00:00",
  "finished_at": null
}
```

**成功**：

```json
{
  "id": "job_01M4Z76S1P2Q3R4S5T6V7W8X9Y",
  "kind": "generate_project",
  "status": "succeeded",
  "stage": "done",
  "progress": 1.0,
  "attempts": 1,
  "max_attempts": 3,
  "error": null,
  "error_code": null,
  "result": {
    "artifacts": [
      "org/org_01M4Z76RW2WNQXHJE6Y49XNMNA/projects/proj_01M4Z76RZGNWY5TB0NEE2MTZYV/keyframes/sc01_sh01.png",
      "org/org_01M4Z76RW2WNQXHJE6Y49XNMNA/projects/proj_01M4Z76RZGNWY5TB0NEE2MTZYV/final/final.gif"
    ],
    "shots_total": 6,
    "shots_finished": 6,
    "evaluation_gate": true,
    "settlement": {
      "reserved": 780,
      "actual": 731,
      "delta": -49,
      "refunded": 49
    }
  },
  "created_at": "2025-11-20T03:14:07.123456+00:00",
  "started_at": "2025-11-20T03:14:09.234567+00:00",
  "finished_at": "2025-11-20T03:22:41.654321+00:00"
}
```

**`result` 的字段（成功任务）**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `artifacts` | `string[]` | 已归集的存储 key 列表 |
| `shots_total` | `int` | 镜头总数 |
| `shots_finished` | `int` | 完成的镜头数 |
| `evaluation_gate` | `bool \| null` | 质量门禁是否通过（`.get("passed")`，缺失时 `null`） |
| `settlement` | `object` | 结算明细（见下） |

**`settlement` 的字段**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `reserved` | `int` | 预扣的额度 |
| `actual` | `int` | 按实际供应商成本应扣的额度 |
| `delta` | `int` | `actual - reserved` |
| `refunded` | `int` | **仅当 `delta < 0`**：退还的额度 |
| `extra_charged` | `int` | **仅当 `delta > 0` 且补扣成功**：补扣的额度 |
| `extra_uncollected` | `int` | **仅当 `delta > 0` 但余额不足**：记为欠账的额度（成片照常交付） |

**失败**：

```json
{
  "status": "dead",
  "stage": "queued",
  "progress": 0.0,
  "attempts": 3,
  "max_attempts": 3,
  "error": "provider_failure 在 3 次尝试后失败：…",
  "error_code": "provider_failure"
}
```

**轮询建议**：

| 阶段 | 建议间隔 |
|---|---|
| `queued` / `planning` | 3 秒 |
| `assets` | 5 秒 |
| `generating` | 5～10 秒（这个阶段最长） |
| `evaluating` / `rendering` | 3 秒 |
| 终态（`succeeded` / `failed` / `dead` / `cancelled`） | 停止轮询 |

**错误**：任务不存在或不属于当前组织 → 404 `not_found`（「任务不存在」）。

### `POST /api/v1/jobs/{job_id}/cancel` — 取消任务

**鉴权**：`WritablePrincipal`

```bash
curl -s -X POST "http://localhost:8000/api/v1/jobs/job_01M4Z76S1P2Q3R4S5T6V7W8X9Y/cancel" \
  -H "Authorization: Bearer reel_a1b2c3d4_XXXX" | jq
```

**响应**：与 `GET /api/v1/jobs/{job_id}` 相同的 `_job_view` 结构，
反映**取消请求之后**的状态。

**两种行为**：

| 取消时的任务状态 | 行为 | 响应中的 `status` |
|---|---|---|
| `pending` | **立即**置为 `cancelled`，`finished_at` 写入，**全额退还预扣额度**（幂等键 `cancel-refund:<job_id>`），项目状态退回 `draft` | `cancelled` |
| `running` | 只置 `cancel_requested = true`。实际停止由 worker 的下一次心跳（最多 20 秒）传给引擎，引擎在下一个阶段/镜头边界抛 `PipelineCancelled` | `running`（此时仍是） |

> ⚠️ **`running` 任务的取消不是立即的**。
> 一个正在进行的供应商 HTTP 请求会跑完才退出，
> 因此从「发起取消」到「任务真的停」可能有几十秒延迟。
> 需要确认最终结果请继续轮询 `GET /api/v1/jobs/{job_id}`，
> 直到 `status` 变为 `cancelled`。

**错误**：

| 条件 | 错误 |
|---|---|
| 任务不存在 / 不属于当前组织 | 404 `not_found` |
| 任务已是终态（`succeeded` / `failed` / `dead` / `cancelled`） | 409 `invalid_state`（「任务已结束，无法取消」） |

> ℹ️ 注意 `JobStatus.is_terminal` 把 `failed` 也算作终态，
> 但 `failed` 的任务**是可被重新领取的**（`is_claimable`）。
> 也就是说：一个 `failed` 且还在退避等待中的任务**无法被取消**。
> 如果它最终耗尽重试变成 `dead`，也无法取消。
> 需要干预时只能等它跑完或按 [OPERATIONS.md](OPERATIONS.md) 第 1.5 节手工处理。

---

## 10. 额度

### `GET /api/v1/credits` — 余额与流水

**鉴权**：会话 或 API Key

**查询参数**：

| 参数 | 类型 | 默认 | 上限 |
|---|---|---|---|
| `limit` | `int` | 30 | 200 |

```bash
curl -s "http://localhost:8000/api/v1/credits?limit=5" \
  -H "Authorization: Bearer reel_a1b2c3d4_XXXX" | jq
```

```json
{
  "balance": 14820,
  "items": [
    {
      "id": "led_01M4Z78ABCDEFGHJKMNPQRSTVW",
      "kind": "consume",
      "amount": -780,
      "balance_after": 14820,
      "description": "预扣：生成《深夜便利店》",
      "project_id": "proj_01M4Z76RZGNWY5TB0NEE2MTZYV",
      "job_id": "job_01M4Z76S1P2Q3R4S5T6V7W8X9Y",
      "created_at": "2025-11-20T03:14:07.123456+00:00"
    },
    {
      "id": "led_01M4Z77XYZABCDEFGHJKMNPQRST",
      "kind": "signup_grant",
      "amount": 500,
      "balance_after": 15600,
      "description": "注册赠送 500 点",
      "project_id": null,
      "job_id": null,
      "created_at": "2025-11-19T10:00:00.000000+00:00"
    }
  ]
}
```

**字段说明**：

| 字段 | 说明 |
|---|---|
| `balance` | 当前余额快照 |
| `items[].amount` | **正数入账、负数出账** |
| `items[].balance_after` | 本次变动**之后**的余额 |
| `items[].kind` | 见 3.5 节的账本类型表 |
| `items[].description` | 人类可读描述 |

**用户可自证的核心**：因为每条都带 `balance_after`，
用户可以自己验证「上一条的 `balance_after` + 本条 `amount` = 本条 `balance_after`」。

排序：`created_at DESC`。

> ℹ️ **没有公开的 `reconcile` 端点。**
> 对账结果只在控制台页面 `/app/usage` 上展示。
> 需要程序化对账请直接查数据库（见
> [OPERATIONS.md](OPERATIONS.md) 第 4.2 节）。

### `POST /api/v1/credits/quote` — 预估生成成本

**鉴权**：会话 或 API Key

**查询参数**：

| 参数 | 类型 | 默认 | 范围 |
|---|---|---|---|
| `shots` | `int` | 6 | 1～24 |

> ⚠️ 这是一个 **POST** 端点，但参数走**查询字符串**（`Query(...)`），
> **不接受请求体**。

```bash
curl -s -X POST "http://localhost:8000/api/v1/credits/quote?shots=8" \
  -H "Authorization: Bearer reel_a1b2c3d4_XXXX" | jq
```

```json
{
  "estimated_credits": 1040,
  "breakdown": {
    "image": 210,
    "video": 780,
    "tts": 50
  },
  "balance": 14820,
  "affordable": true,
  "max_shots": 12
}
```

| 字段 | 说明 |
|---|---|
| `estimated_credits` | 预估消耗（已含 1.15 安全系数） |
| `breakdown.image` / `.video` / `.tts` | 分项点数 |
| `balance` | 当前余额 |
| `affordable` | `balance >= estimated_credits` |
| `max_shots` | 当前套餐的 `max_shots_per_project` |

**预估公式**（`credits.quote_project`）：

```python
premium_uplift = 1.35 if plan.allow_premium_tier else 1.0
images       = shots
base_images  = min(max(2, shots // 2), REEL_MAX_BASE_IMAGES)
image_cost   = (images + base_images) * 0.03
video_cost   = shots * 0.14 * premium_uplift          # 0.14 USD/镜（默认）
tts_cost     = shots * 0.01
total_usd    = (image_cost + video_cost + tts_cost) * 1.15
credits      = max(1, ceil(total_usd * markup * credits_per_usd))
```

---

## 11. 产物

### `GET /api/v1/projects/{project_id}/artifacts` — 项目产物列表

**鉴权**：会话 或 API Key

**路径参数**：`project_id`
**查询参数**：`kind`（默认 `""`，按产物类型过滤）

```bash
curl -s "http://localhost:8000/api/v1/projects/proj_01M4Z76RZGNWY5TB0NEE2MTZYV/artifacts?kind=keyframe" \
  -H "Authorization: Bearer reel_a1b2c3d4_XXXX" | jq
```

```json
{
  "items": [
    {
      "id": "art_01M4Z77KEYFRAME00000000001",
      "kind": "keyframe",
      "filename": "sc01_sh01.png",
      "size_bytes": 184320,
      "content_type": "image/png",
      "is_public": false,
      "shot_key": "keyframes/sc01_sh01.png",
      "created_at": "2025-11-20T03:15:02.123456+00:00",
      "url": "/api/v1/artifacts/art_01M4Z77KEYFRAME00000000001/download?disposition=inline",
      "created_hint": "2025-11-20T03:15:02.123000+00:00"
    }
  ],
  "total": 13
}
```

| 字段 | 说明 |
|---|---|
| `shot_key` | 目录型产物（keyframes/clips/audio）填相对路径；`final/` 下的产物为空串 |
| `url` | 相对路径的下载/内联 URL |
| `created_hint` | 从产物 ID（ULID）反解出的创建时间 |
| `is_public` | 是否已开启公开分享 |

排序：`created_at ASC`。**软删的产物（`deleted_at` 非空）不出现在结果里。**

**错误**：

| 条件 | 错误 |
|---|---|
| 项目不存在 / 不属于当前组织 | 404 `not_found` |
| `kind` 不是合法的产物类型 | 422 `validation_failed`（「未知的产物类型：X」） |

> ℹ️ 实现会**先校验项目归属**（调 `projects.get_project`），
> 再查产物。这是「避免用别人的 project_id 探测产物」的刻意设计。

### `GET /api/v1/projects/{project_id}/final` — 取成片入口

**鉴权**：会话 或 API Key

供前端播放器直接使用。优先返回 MP4（`kind=video`），其次 GIF（`kind=gif`），
按 `created_at DESC` 取最新的一条。

```bash
curl -s "http://localhost:8000/api/v1/projects/proj_01M4Z76RZGNWY5TB0NEE2MTZYV/final" \
  -H "Authorization: Bearer reel_a1b2c3d4_XXXX" | jq
```

**已有成片**：

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

**还没有成片**（项目未完成 / 失败）：

```json
{"available": false}
```

**错误**：项目不存在 / 不属于当前组织 → 404 `not_found`。

### `GET /api/v1/artifacts/{artifact_id}/download` — 下载产物

**鉴权**：会话 或 API Key

**查询参数**：

| 参数 | 类型 | 默认 | 可选值 |
|---|---|---|---|
| `disposition` | `string` | `attachment` | `inline` / `attachment`（其它值回退为 `attachment`） |

```bash
# 内联展示（播放器用）
curl -s -o out.gif \
  "http://localhost:8000/api/v1/artifacts/art_01M4Z77FINAL0000000000001/download?disposition=inline" \
  -H "Authorization: Bearer reel_a1b2c3d4_XXXX"

# 附件下载（保存文件用）
curl -sOJ \
  "http://localhost:8000/api/v1/artifacts/art_01M4Z77FINAL0000000000001/download?disposition=attachment" \
  -H "Authorization: Bearer reel_a1b2c3d4_XXXX"
```

**响应**：文件的原始字节流。

| 响应头 | 值 |
|---|---|
| `Content-Type` | 产物的 `content_type` |
| `Content-Disposition` | `<disposition>; filename="<artifact.filename>"` |
| `Content-Length` | 文件大小（`size()` 返回 0 时省略） |
| `Cache-Control` | `private, max-age=3600` |
| `Accept-Ranges` | `none`（**不支持断点续传**） |
| `ETag` | 双引号包裹的 `sha256` 前 32 位 |

**副作用**：每次成功下载会把 `artifacts.download_count` +1。

**错误**：

| 条件 | 错误 |
|---|---|
| 产物不存在 / 不属于当前组织 / 已软删 | 404 `not_found`（「产物不存在」） |
| 登记存在但存储层文件已丢失 | 404 `not_found`（「文件不存在或已被清理」） |

### `POST /api/v1/artifacts/{artifact_id}/share` — 开启/关闭公开分享

**鉴权**：会话 或 API Key

**查询参数**：

| 参数 | 类型 | 默认 |
|---|---|---|
| `enable` | `bool` | `true` |

> ⚠️ 这也是一个参数走查询字符串的 **POST** 端点。

```bash
# 开启分享
curl -s -X POST "http://localhost:8000/api/v1/artifacts/art_01M4Z77FINAL0000000000001/share?enable=true" \
  -H "Authorization: Bearer reel_a1b2c3d4_XXXX" | jq
```

```json
{
  "is_public": true,
  "share_url": "https://reelagent.example.com/s/eyJhaWQiOiJhcnRfMDFNNFo3N0ZJTkFMMDAwMDAwMDAwMDAwMSIsImV4cCI6MTc2NDU5MjQwN30.aBcDeF"
}
```

```bash
# 关闭分享（已发出的 URL 立即失效）
curl -s -X POST "http://localhost:8000/api/v1/artifacts/art_01M4Z77FINAL0000000000001/share?enable=false" \
  -H "Authorization: Bearer reel_a1b2c3d4_XXXX" | jq
```

```json
{"is_public": false, "share_url": ""}
```

**分享链接的性质**：

| 属性 | 值 |
|---|---|
| 形式 | `{REEL_BASE_URL}/s/{token}` |
| token | itsdangerous 签名（salt `reelagent.share.v1`），载荷 `{"aid": <artifact_id>, "exp": <unix 时间戳>}` |
| **TTL** | **30 天**（`30 * 24 * 3600` 秒，硬编码） |
| 需要登录 | ❌ 不需要 |
| 可撤销 | ✅ 关闭分享（`enable=false`）后**已发出的 URL 立即失效**，因为访问时同时校验 `artifact.is_public is True` |
| 访问次数 | 不限 |

**错误**：产物不存在 / 不属于当前组织 → 404 `not_found`。

> ⚠️ `artifacts.share_token` 字段存的是 `token[:48]`——
> **只是用于展示，不是用于校验的**。
> 校验完全靠签名 token + `is_public` 标志。
> 因此不要把这个字段当作访问凭证。

---

## 12. API Key 自助管理

### `GET /api/v1/api-keys` — 列出 API Key

**鉴权**：会话 或 API Key（可以列，因为这是只读）

```bash
curl -s http://localhost:8000/api/v1/api-keys \
  -H "Authorization: Bearer reel_a1b2c3d4_XXXX" | jq
```

```json
{
  "items": [
    {
      "id": "key_01M4Z76RW2WNQXHJE6Y49XNMNA",
      "name": "CI 流水线",
      "prefix": "a1b2c3d4",
      "masked": "reel_a1b2c3d4_********",
      "scopes": ["read", "write"],
      "last_used_at": "2025-11-20T03:10:00.000000+00:00",
      "request_count": 1247,
      "expires_at": null,
      "revoked_at": null,
      "active": true
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `prefix` | 8 位十六进制 |
| `masked` | 用于展示的脱敏形式 `reel_<prefix>_********` |
| `last_used_at` | **写入有 60 秒节流**，因此不会精确到每次请求 |
| `request_count` | 同上，节流更新 |
| `active` | `revoked_at is None and not (expires_at and expires_at < now)` |

排序：`created_at DESC`。**已撤销的 Key 也在列表里**（`revoked_at` 非 `null`）。

### `POST /api/v1/api-keys` — 创建 API Key

**鉴权**：**必须浏览器会话**，且 `role.rank >= 2`（admin 或 owner）

**请求体**：

```json
{
  "name": "CI 流水线",
  "scopes": ["read", "write"],
  "expires_in_days": 90
}
```

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `name` | `string` | `"API Key"` | 最长 80 字符 |
| `scopes` | `string[]` | `["read","write"]` | ⚠️ 不能授予管理权限（见 2.2 节） |
| `expires_in_days` | `int` | `null`（永不过期） | |

**成功响应（HTTP 201）**：

```json
{
  "id": "key_01M4Z76RW2WNQXHJE6Y49XNMNA",
  "name": "CI 流水线",
  "prefix": "a1b2c3d4",
  "scopes": ["read", "write"],
  "key": "reel_a1b2c3d4_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
  "warning": "请立即保存，此密钥不会再次显示"
}
```

> ⚠️ **`key` 字段只在这一次响应里出现**。数据库只存
> `SHA-256(pepper + full_key)`，无法恢复明文。
> **丢失后只能重新创建。**

**错误**：

| 条件 | 错误 |
|---|---|
| 用 API Key 调用 | 403 `forbidden`（「不能用 API Key 创建新的 API Key，请使用浏览器登录」） |
| `role.rank < 2`（member） | 403 `forbidden`（「需要管理员权限」） |
| 免费版套餐 | 403 `forbidden`（「免费版不支持 API Key，请升级到专业版」） |
| 活跃 Key 已达 20 个 | 403 `forbidden`（「API Key 数量已达上限（20），请先撤销不再使用的 Key」） |
| 请求体不是合法 JSON | **不报错**，用全部默认值（`body = {}`） |

> ℹ️ 最后一条的实现细节：`except Exception: body = {}`。
> 因此**请求体格式错误时会静默用默认值创建一个 Key**，
> 而不是返回 422。使用时应确保发送合法 JSON。

### `DELETE /api/v1/api-keys/{key_id}` — 撤销 API Key

**鉴权**：**必须浏览器会话**

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X DELETE \
  "http://localhost:8000/api/v1/api-keys/key_01M4Z76RW2WNQXHJE6Y49XNMNA" \
  -H "Cookie: reelagent_session=...; reelagent_csrf=..." \
  -H "x-csrf-token: ..."
# 期望 204
```

**成功响应**：`HTTP 204 No Content`

**撤销是软撤销**：设置 `api_keys.revoked_at = now()`。
已撤销的 Key 立即失效（`is_active` 返回 `False`），
但记录保留在列表里可追溯。

**错误**：

| 条件 | 错误 |
|---|---|
| 用 API Key 调用 | 403 `forbidden`（「不能用 API Key 撤销密钥」） |
| Key 不存在 / 不属于当前组织 | 404 `not_found`（「密钥不存在」） |

> ⚠️ **注意这个端点没有检查 `role.rank`**（与创建端点不同）。
> 任何浏览器会话用户（包括 `member`）都能撤销自己组织的 Key。
> 若需要更严格的限制，应在路由层补 `AdminPrincipal`。

---

## 13. 审计日志

### `GET /api/v1/audit` — 审计日志

**鉴权**：**必须浏览器会话**（API Key 被显式拒绝）

**查询参数**：

| 参数 | 类型 | 默认 | 上限 |
|---|---|---|---|
| `limit` | `int` | 50 | 200 |

```bash
curl -s "http://localhost:8000/api/v1/audit?limit=20" \
  -H "Cookie: reelagent_session=..." | jq
```

```json
{
  "items": [
    {
      "id": "aud_01M4Z78ABCDEFGHJKMNPQRSTVW",
      "action": "project.created",
      "actor": "usr_01M4Z76RW2WNQXHJE6Y49XNMNA",
      "actor_type": "user",
      "target": "project:proj_01M4Z76RZGNWY5TB0NEE2MTZYV",
      "ip": "203.0.113.42",
      "meta": {"shots": 6, "style": "cinematic", "estimated_credits": 780},
      "created_at": "2025-11-20T03:14:07.123456+00:00"
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `action` | 见下表的 16 个动作类型 |
| `actor` | 操作者的 `user_id`（系统动作为 `null`） |
| `actor_type` | `user` / `api_key` / `system` |
| `target` | `"{target_type}:{target_id}"`；`target_id` 为空时是空串 |
| `ip` | 客户端 IP |
| `meta` | 动作相关的上下文 |

**动作类型**（`AuditAction` 枚举的全部取值）：

| 值 | 含义 |
|---|---|
| `user.signup` | 注册 |
| `user.login` | 登录成功 |
| `user.login_failed` | 登录失败（含 `meta.failures`） |
| `user.logout` | 登出（`meta.scope = "all_devices"` 表示登出所有设备） |
| `user.password_changed` | 改密 |
| `api_key.created` | 创建 API Key（`meta.prefix`） |
| `api_key.revoked` | 撤销 API Key |
| `project.created` | 创建项目 |
| `project.deleted` | 删除项目 |
| `shot.regenerated` | 重跑镜头（`meta.shots`） |
| `job.cancelled` | 取消任务（`meta.count`） |
| `credits.granted` | 发放额度 |
| `subscription.changed` | 订阅变更（`meta.tier` / `meta.status` / `meta.source`） |
| `member.invited` | 添加成员（`meta.role`） |
| `member.removed` | 移除成员 |
| `org.updated` | 组织信息更新 |

排序：`created_at DESC`。

**错误**：API Key 身份 → 403 `forbidden`（「审计日志仅对浏览器会话开放」）。

> ⚠️ **审计日志可能有缺失**：写入不参与业务事务的成败
> （业务成功但审计失败时宁可丢一条审计，也不回滚用户操作）。
> 需要严格合规证据链时应改用同步阻塞方案（当前未实现）。

---

## 14. 文件与分享

这两个端点**不需要 `Authorization` 头**，但 `/files/` 需要会话 Cookie。

### `GET /files/{key:path}` — 按存储 key 访问产物

**鉴权**：`CurrentPrincipal`（会话 或 API Key）
（`include_in_schema=False`，不出现在 `/docs`）

**这是租户隔离的硬边界**：请求的 key 必须属于当前组织。

```bash
curl -s "http://localhost:8000/files/org/org_01M4Z76RW2WNQXHJE6Y49XNMNA/projects/proj_01M4Z76RZGNWY5TB0NEE2MTZYV/final/final.gif" \
  -H "Cookie: reelagent_session=..." -o final.gif
```

**key 的格式**：

```
org/<org_id>/projects/<project_id>/<category>/<filename>
```

`<category>` 取值：`keyframes` / `assets` / `clips` / `audio` / `final`

**响应头**：与 `GET /api/v1/artifacts/{id}/download` 一致
（`Content-Disposition: inline`、`Cache-Control: private, max-age=3600`），
但**没有 `ETag`**（这条路径不查库，拿不到 sha256）。

**错误**：

| 条件 | 错误 |
|---|---|
| key 前缀不属于当前组织 | 403 `forbidden`（「无权访问该文件」） |
| key 属于当前组织但文件不存在 | 404 `not_found`（「文件不存在或已被清理」） |

> ⚠️ **越权尝试会被记录成安全告警**：
> 事件名 `cross_tenant_file_access_blocked`，
> 含 `org_id` / `actor`（会话用户或 API Key 前缀）/ `key` 前缀。
> 正常用户不会触发它，因此这是一个高价值的监控信号。

### `GET /s/{token}` — 公开分享链接

**鉴权**：**无**（`include_in_schema=False`）

```bash
curl -s -o shared.gif "https://reelagent.example.com/s/eyJhaWQiOi..."
```

**三个必须同时满足的条件**：

1. token 签名有效且未过期（`read_share_token`）
2. `artifact.is_public is True`
3. `artifact.deleted_at is None`

**响应头**（与 `/files/` 的差异）：

| 头 | 值 |
|---|---|
| `Cache-Control` | `public, max-age=86400`（更长，因为允许 CDN 缓存） |
| `Cross-Origin-Resource-Policy` | `cross-origin`（分享页需要能被跨源引用） |
| `ETag` | `sha256` 前 32 位 |

**错误**：任一条件不满足 → 404 `not_found`，消息为以下之一：

- 「分享链接无效或已过期」
- 「分享链接已过期」
- 「分享的内容不存在或已取消分享」

> ℹ️ token 过期的判定是双重的：
> `read_share_token` 内部先比一次 `exp`，
> 而 itsdangerous 的 `loads()` **没有传 `max_age`**，
> 因此过期完全依赖载荷里的 `exp` 字段检查。

---

## 15. Webhook

### `POST /webhooks/stripe` — Stripe 支付回调

**鉴权**：**无**（靠签名验证），`include_in_schema=False`

**请求头**：

| 头 | 必需 |
|---|---|
| `Stripe-Signature` | ✅ |
| `Content-Type` | `application/json`（Stripe 发送的原始体） |

**请求体**：Stripe 的原始事件 JSON。

> ⚠️ **验签必须用原始请求体。**
> 路由实现是 `payload = await request.body()`，
> **不挂任何 body 解析依赖**。
> 因此反向代理**不能**改写请求体（不要开压缩重写、不要开 WAF 的
> body 规范化、不要加会提前解析 body 的中间件）。

**响应**：

| 情况 | HTTP | 响应体 |
|---|---|---|
| 处理成功 | 200 | `{"received": true, "event_id": "evt_...", "outcome": "<描述>"}` |
| 重复投递 | 200 | `{"received": true, "event_id": "evt_...", "outcome": "duplicate"}` |
| 未识别的事件类型 | 200 | `outcome: "ignored"` |
| `billing.enabled = false` | 200 | `{"ignored": "billing disabled"}` |
| 请求体为空 | 400 | `{"error": "empty payload"}` |
| 签名校验失败 | 400 | `{"error": "Webhook 签名校验失败：..."}` |
| 处理过程中抛异常 | **500** | `{"error": "processing_failed", "event_id": "evt_..."}` |

> ⚠️ **500 是刻意的**：让 Stripe 重试。
> 「吞掉异常返回 200 会导致用户付了钱没到账。」
> **不要在反向代理层把 5xx 重写成 200。**

**已处理的事件类型**（`_HANDLERS` 的全部 6 个）：

#### `checkout.session.completed`

结账完成。行为：

1. 从 `client_reference_id` 或 `metadata.org_id` 取组织 ID。
   缺失时返回 `outcome: "missing org reference"`。
2. 从 `metadata.tier` 取套餐（默认 `starter`）。
3. `_sync_subscription()`：
   - 更新 `subscriptions`（plan / status / `stripe_subscription_id`）
   - 拉取 Stripe 订阅详情回填 `stripe_price_id` / 周期 / `cancel_at_period_end`
     （拉取失败只记 `stripe_subscription_fetch_failed` 警告，**不阻塞激活**）
   - 更新 `organizations.plan` 与 `organizations.watermark`（`= plan.is_free`）
   - 发放首期额度，**幂等键 `sub-activate:<subscription_id>`**
4. 写审计 `subscription.changed`

`outcome` 形如 `"activated pro"`。

#### `customer.subscription.created` / `customer.subscription.updated`

两者共用 `_handle_subscription_updated`：

1. 从 `metadata.org_id` 取组织；缺失时按 `stripe_subscription_id` 反查。
   都找不到返回 `"unknown subscription"`。
2. 套餐的确定顺序：先按 Price ID 反查（`tier_for_price`），
   失败则用 `metadata.tier`，再失败则默认 `starter`。
3. 同 `_sync_subscription()`。
4. 写审计。

`outcome` 形如 `"updated pro/active"`。

#### `customer.subscription.deleted`

1. 按 `stripe_subscription_id` 找到订阅。找不到返回 `"unknown subscription"`。
2. `sub.status = canceled`、`canceled_at = now()`、`cancel_at_period_end = False`。
3. `org.plan = free`、`org.watermark = True`。
4. **不改账本**（不回收已发放的额度）。

`outcome`: `"canceled -> downgraded to free"`。

#### `invoice.paid`

1. 取订阅 ID（先看 `obj.subscription`，再看 `obj.parent.subscription_details.subscription`）。
   缺失返回 `"not a subscription invoice"`。
2. 按订阅找到组织和套餐。
3. **如果是续费**（`billing_reason == "subscription_cycle"`）：
   - `credits.grant(plan.monthly_credits)`，**幂等键 `invoice:<invoice_id>`**
   - `org.credits_granted_this_period = plan.monthly_credits`
   - `org.period_started_at = now()`
   - `outcome`: `"granted 15000 credits"`
4. **如果是首购**：只返回 `outcome: "paid <金额>"`（额度已在激活时发过）。

#### `invoice.payment_failed`

1. 取订阅 ID。缺失返回 `"not a subscription invoice"`。
2. `sub.status = PAST_DUE`。
3. **不改 `org.plan`，不改账本**——给用户宽限期。
4. 打 `subscription_past_due` 警告。
5. `outcome`: `"marked past_due"`。

#### 其它事件

标记为 `WebhookStatus.IGNORED` 并 `processed_at = now()`，
`outcome: "ignored"`，返回 200。

### 幂等：双层保险

| 层 | 机制 |
|---|---|
| 1 | `webhook_events(provider, event_id)` **唯一约束**。先落库再处理，重复投递直接返回 `duplicate` |
| 2 | 额度发放的 `idempotency_key`（`sub-activate:<sub_id>` / `invoice:<invoice_id>`），受 `uq_ledger_org_idempotency` 保护 |

**第 2 层的价值**：即使为了重放而删掉 `webhook_events` 记录
（见 [OPERATIONS.md](OPERATIONS.md) 第 5.8 节），额度也不会重复发放。

### `payload` 字段是裁剪过的

`webhook_events.payload` 只保留排障所需的关键字段：

```json
{
  "id": "evt_...",
  "type": "invoice.paid",
  "data": {
    "object": {
      "id", "object", "status", "customer", "subscription",
      "amount_paid", "amount_due", "currency", "created",
      "current_period_start", "current_period_end",
      "cancel_at_period_end", "client_reference_id",
      "metadata", "billing_reason"
    }
  }
}
```

> ⚠️ **因此不能拿 `payload` 当重放的输入**（它不完整）。
> 重放必须从 Stripe 侧发起。

---

## 16. HTML 页面路由

这些是**浏览器页面**，不是 JSON API。
它们返回 HTML（或 303 重定向），表单错误的处理方式是
**重新渲染页面并带错误消息**，而不是返回 JSON。

> ⚠️ **重要前提**：`reelagent_cloud/web/templates/` 目录
> **在 1.0.0 时点不存在**。
> 因此以下所有页面路由在运行时都会抛
> `TemplateNotFound`。路由与视图逻辑已完整实现，
> 缺的是模板与静态资源本身。
> **JSON API（`/api/v1/*`）与 Webhook 不受影响。**

### 公开页面

| 路径 | 方法 | 处理器 | 说明 |
|---|---|---|---|
| `/` | GET | `pages.landing` | 落地页（含套餐、风格展示、FAQ） |
| `/pricing` | GET | `pages.pricing` | 定价页 |
| `/terms` | GET | `pages.terms` | 服务条款 |
| `/privacy` | GET | `pages.privacy` | 隐私政策 |
| `/status` | GET | `pages.status` | 公开状态页（队列深度 + 依赖健康） |

这些页面用 `OptionalPrincipal`，即登录与未登录都能访问。

### 认证

| 路径 | 方法 | 说明 |
|---|---|---|
| `/login` | GET | 登录页（已登录则 303 重定向到 `next`） |
| `/login` | POST | 表单 `email` / `password` / `next`。成功写 Cookie 并 303 到 `next`；失败按 401/403 重新渲染页面 |
| `/signup` | GET | 注册页（`allow_signup=false` 时渲染 `signup_closed.html` 并返回 403；已登录则 303 到 `/app`） |
| `/signup` | POST | 表单 `email` / `password` / `display_name` / `org_name` / `invite_code` / `agree` |
| `/logout` | POST | 清 Cookie，303 到 `/` |
| `/logout-all` | POST | **会话代际 +1**（所有设备立即失效），清 Cookie，303 到 `/login` |

**防开放重定向**：`next` 参数只接受站内相对路径
（以 `/` 开头且不以 `//` 开头），否则回退到 `/app`。

**注册时的套餐行为**：`org_name` 留空则用
`"{display_name} 的空间"`，`display_name` 留空则用邮箱 @ 前的部分。
注册赠送 `SIGNUP_GRANT_CREDITS = 500` 点。

### 控制台（`/app/*`，全部需要 `SessionPrincipal`）

| 路径 | 方法 | 说明 |
|---|---|---|
| `/app` 或 `/app/` | GET | 仪表盘（项目计数、活跃任务、最近账本） |
| `/app/projects` | GET | 项目列表（分页，每页 12；支持 `status_filter` / `q` / `page`） |
| `/app/projects/new` | GET | 新建项目页（含额度预估与三个示例素材） |
| `/app/projects` | POST | 创建项目（表单字段 `seed_text` / `title` / `style` / `aspect_ratio` / `shots` / `csrf_token`）。成功 303 到详情页 |
| `/app/projects/{id}` | GET | 项目详情；状态是 `queued` / `running` 时改用 `project_running.html` |
| `/app/projects/{id}/studio` | GET | Studio（分镜编辑，可看关键帧与线索） |
| `/app/projects/{id}/regenerate` | POST | 重跑指定镜头（表单 `shot_keys` 逗号分隔 + `csrf_token`）。**按镜头数扣费**，`priority=5` |
| `/app/projects/{id}/shots/{shot_key}/prompt` | POST | 保存手工改写的 `image_prompt`（最长 4000 字符） |
| `/app/projects/{id}/cancel` | POST | 取消该项目下所有 `pending` / `running` 任务 |
| `/app/projects/{id}/delete` | POST | 删除项目（软删） |
| `/app/usage` | GET | 用量与账单（含**对账结果** `reconcile`） |
| `/app/billing` | GET | 套餐与账单页 |
| `/app/billing/checkout` | POST | 创建 Stripe Checkout（表单 `tier` + `csrf_token`），303 到 Stripe |
| `/app/billing/portal` | POST | 打开 Stripe 客户门户（表单 `csrf_token`），303 到 Stripe |
| `/app/settings` | GET | 设置页（API Key 列表、成员列表、BYOK 状态） |
| `/app/settings/org` | POST | 更新组织名与默认风格（**需 role ≥ admin**） |
| `/app/settings/api-keys` | POST | 创建 API Key（**需 role ≥ admin**）。明文密钥**只在此次响应体里出现一次**，不进 URL |
| `/app/settings/api-keys/{key_id}/revoke` | POST | 撤销 API Key（**需 role ≥ admin**） |
| `/app/settings/byok` | POST | 保存 BYOK 密钥（表单 `video_key` / `image_key` / `llm_key` + `csrf_token`，**需 role ≥ admin**） |
| `/app/settings/members` | POST | 添加成员（表单 `email` / `role` / `csrf_token`，**需 role ≥ admin**） |

**CSRF**：所有 POST 表单必须带隐藏字段 `csrf_token`，
其值从 `reelagent_csrf` Cookie 读取。
失败消息为「页面已过期，请刷新后重试」。

**跳转与提示**：成功/失败通过 303 重定向的查询参数传递：

```
/app/projects/{id}?flash=已开始生成，预计 3-8 分钟完成
/app/projects/new?error=<URL 编码的错误消息>
/app/billing?flash=订阅已生效
```

### 404 处理

```python
if request.url.path.startswith(("/api/", "/webhooks/", "/files/")):
    # JSON 响应
    return JSONResponse({"error": {"code": "not_found", "message": "接口不存在"},
                         "request_id": "..."}, status_code=404)
# 其它路径返回 HTML 404 页面
return get_templates().TemplateResponse(request, "pages/404.html", {...}, status_code=404)
```

因此 JSON 路径与页面路径拿到的 404 格式是不同的。

---

## 17. 端到端示例

完整的 curl 流程：创建项目 → 轮询任务 → 取成片。

```bash
# ---------- 0) 准备 ----------
export BASE="https://reelagent.example.com"
export KEY="reel_a1b2c3d4_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

# 确认服务就绪
curl -fsS "$BASE/readyz" | jq '.status'
# "ready"

# 确认身份与余额
curl -fsS "$BASE/api/v1/me" -H "Authorization: Bearer $KEY" \
  | jq '{plan: .plan.tier, balance: .credits.balance, max_shots: .plan.monthly_credits}'

# ---------- 1) 预估成本（可选，但建议做）----------
curl -fsS -X POST "$BASE/api/v1/credits/quote?shots=6" \
  -H "Authorization: Bearer $KEY" | jq
# {
#   "estimated_credits": 780,
#   "breakdown": {"image": 150, "video": 585, "tts": 45},
#   "balance": 14820,
#   "affordable": true,
#   "max_shots": 12
# }

# ---------- 2) 创建项目 ----------
RESP=$(curl -fsS -X POST "$BASE/api/v1/projects" \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-$(date +%s)" \
  -d '{
    "seed_text": "深夜的便利店只剩下最后一盏灯。林晚把湿透的伞收在门口，走向货架。她在最底层找到一盒过期牛奶，生产日期是三年前的今天。收银台后的老人抬起头，说这盒牛奶他等了很久。",
    "title": "深夜便利店",
    "style": "cinematic",
    "aspect_ratio": "9:16",
    "shots": 6
  }')

echo "$RESP" | jq

PROJECT_ID=$(echo "$RESP" | jq -r '.project.id')
JOB_ID=$(echo "$RESP" | jq -r '.job.id')
echo "project=$PROJECT_ID job=$JOB_ID"

# ---------- 3) 轮询任务 ----------
while true; do
  JOB=$(curl -fsS "$BASE/api/v1/jobs/$JOB_ID" -H "Authorization: Bearer $KEY")
  STATUS=$(echo "$JOB" | jq -r '.status')
  STAGE=$(echo "$JOB" | jq -r '.stage')
  PROGRESS=$(echo "$JOB" | jq -r '.progress')

  echo "$(date +%H:%M:%S)  status=$STATUS  stage=$STAGE  progress=$PROGRESS"

  case "$STATUS" in
    succeeded)
      echo "--- 结算明细 ---"
      echo "$JOB" | jq '.result'
      break
      ;;
    failed)
      # 可重试的失败会自动重排，因此这里只提示
      echo "任务失败（可重试）: $(echo "$JOB" | jq -r '.error')"
      sleep 10
      ;;
    dead|cancelled)
      echo "任务终结: status=$STATUS  error=$(echo "$JOB" | jq -r '.error')"
      exit 1
      ;;
    *)
      # 生成阶段最长，放慢轮询
      if [ "$STAGE" = "generating" ]; then sleep 8; else sleep 3; fi
      ;;
  esac
done

# ---------- 4) 看项目详情与评测 ----------
curl -fsS "$BASE/api/v1/projects/$PROJECT_ID" -H "Authorization: Bearer $KEY" \
  | jq '{
      title, status,
      shots: (.shots | length),
      finished: .finished_shots,
      duration: .duration_seconds,
      gate: .evaluation.gate,
      metrics: .evaluation.metrics | {
        overall, palette_stability, cross_shot_clue_similarity,
        clue_reference_coverage, prompt_clue_fidelity
      },
      cost_usd: .cost.total_usd
    }'

# ---------- 5) 取成片 ----------
FINAL=$(curl -fsS "$BASE/api/v1/projects/$PROJECT_ID/final" -H "Authorization: Bearer $KEY")
echo "$FINAL" | jq

if [ "$(echo "$FINAL" | jq -r '.available')" = "true" ]; then
  ART_ID=$(echo "$FINAL" | jq -r '.id')

  # 下载成片
  curl -fsS -o "final_$PROJECT_ID.$(echo "$FINAL" | jq -r '.kind')" \
    "$BASE/api/v1/artifacts/$ART_ID/download?disposition=attachment" \
    -H "Authorization: Bearer $KEY"
  echo "已下载成片"

  # 生成分享链接（可选）
  curl -fsS -X POST "$BASE/api/v1/artifacts/$ART_ID/share?enable=true" \
    -H "Authorization: Bearer $KEY" | jq -r '.share_url'
fi

# ---------- 6) 看额度流水确认扣费 ----------
curl -fsS "$BASE/api/v1/credits?limit=5" -H "Authorization: Bearer $KEY" \
  | jq '{balance, items: [.items[] | {kind, amount, balance_after, description}]}'
```

**期望的额度流水（成功的完整生命周期）**：

```
consume  -780  预扣：生成《深夜便利店》
refund   +49   按实际用量退还 49 点（job_...）
```

净消耗 731 点（实际供应商成本 $2.44 ÷ 3.0 × 100…… 具体由实际成本决定）。

**如果任务失败**：

```
consume  -780  预扣：生成《深夜便利店》
refund   +780  任务失败退还（job_...）
```

净消耗 0 点。

**如果任务成功但实际成本超出预估**：

```
consume  -780  预扣：生成《深夜便利店》
consume  -120  超出预估补扣 120 点（job_...）
```

（若余额不足，补扣会失败并记入 `settlement.extra_uncollected`，
成片照常交付。见第 9 节的 `settlement` 说明。）

### 17.1 Python 客户端示例

```python
"""ReelAgent 最小客户端示例。

依赖：httpx（已在项目的 dependencies 里）
"""
import time

import httpx

BASE = "https://reelagent.example.com"
KEY = "reel_a1b2c3d4_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"

client = httpx.Client(
    base_url=BASE,
    headers={"Authorization": f"Bearer {KEY}"},
    timeout=30.0,
)

# ---------- 1) 预估 ----------
quote = client.post("/api/v1/credits/quote", params={"shots": 6}).raise_for_status().json()
print(f"预估 {quote['estimated_credits']} 点，余额 {quote['balance']} 点")
if not quote["affordable"]:
    raise SystemExit("余额不足")

# ---------- 2) 创建 ----------
created = client.post(
    "/api/v1/projects",
    headers={"Idempotency-Key": f"demo-{int(time.time())}"},
    json={
        "seed_text": "深夜的便利店只剩下最后一盏灯。林晚把湿透的伞收在门口。",
        "title": "深夜便利店",
        "style": "cinematic",
        "shots": 6,
    },
).raise_for_status().json()

project_id = created["project"]["id"]
job_id = created["job"]["id"]
print(f"已创建 project={project_id} job={job_id}")

# ---------- 3) 轮询 ----------
INTERVALS = {
    "queued": 3, "planning": 3, "assets": 5,
    "generating": 8, "evaluating": 3, "rendering": 3,
}
deadline = time.time() + 3600

while time.time() < deadline:
    job = client.get(f"/api/v1/jobs/{job_id}").raise_for_status().json()
    print(f"  {job['status']:<10} {job['stage']:<12} {job['progress']:.2f}")

    if job["status"] == "succeeded":
        print("结算：", job["result"].get("settlement"))
        break
    if job["status"] in {"dead", "cancelled"}:
        raise SystemExit(f"任务终结：{job['status']} — {job['error']}")
    # failed 是可重试的，继续等

    time.sleep(INTERVALS.get(job["stage"], 5))
else:
    raise SystemExit("超时")

# ---------- 4) 取成片 ----------
project = client.get(f"/api/v1/projects/{project_id}").raise_for_status().json()
gate = project["evaluation"]["gate"]
print(f"质量门禁：{'通过' if gate['passed'] else '未通过'} {gate['failures']}")

final = client.get(f"/api/v1/projects/{project_id}/final").raise_for_status().json()
if not final["available"]:
    raise SystemExit("还没有成片")

data = client.get(
    f"/api/v1/artifacts/{final['id']}/download",
    params={"disposition": "attachment"},
).raise_for_status().content

out = f"final_{project_id}.{final['kind']}"
with open(out, "wb") as fh:
    fh.write(data)
print(f"已保存 {out}（{len(data)} 字节）")
```

### 17.2 错误处理示例

```python
import httpx

resp = client.post("/api/v1/projects", json={"seed_text": "太短"})

if resp.status_code >= 400:
    err = resp.json()["error"]
    request_id = resp.json()["request_id"]

    match err["code"]:
        case "insufficient_credits":
            d = err["details"]
            print(f"需要 {d['required']} 点，只有 {d['available']} 点")
            # → 引导用户充值

        case "validation_failed":
            for f in err["details"]["fields"]:
                print(f"字段 {f['field']}：{f['message']}")
            # → 高亮表单字段

        case "forbidden":
            print(err["message"])
            # → 可能是套餐限制、权限不足、或租户不匹配

        case "rate_limited":
            retry_after = int(resp.headers.get("Retry-After", "60"))
            print(f"限流，{retry_after} 秒后重试（scope={err['details']['scope']}）")
            # → 退避重试

        case "provider_failure" | "provider_circuit_open":
            print(f"供应商问题：{err['message']}")
            # → 稍后重试，不要立即重试
            # 注意 provider_circuit_open 的 details 里有 retry_after_seconds

        case "unsupported_operation":
            print(err["message"])
            # → 部署配置问题（可能是没配视频供应商），不是用户的问题

        case "internal_error":
            print(f"服务错误，请提供 request_id 给支持：{request_id}")
            # → 上报，附 request_id
```

---

## 相关文档

- [架构说明](ARCHITECTURE.md) —— 数据模型与任务生命周期
- [部署指南](DEPLOYMENT.md) —— 环境变量、Stripe 配置、域名与 HTTPS
- [运维 Runbook](OPERATIONS.md) —— 排障手册
- [架构决策记录](adr/) —— 为什么 API Key 不能管计费、为什么队列放 Postgres
