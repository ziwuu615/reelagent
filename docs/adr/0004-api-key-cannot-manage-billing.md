# ADR 0004：API Key 不能管理计费与成员

- **状态**：已接受
- **日期**：1.0.0 发布前
- **决策者**：安全 / 平台
- **相关文件**：`reelagent_cloud/security/deps.py`、`reelagent_cloud/security/tokens.py`、`reelagent_cloud/api/routes/api.py`、`reelagent_cloud/api/routes/console.py`

---

## 背景

产品需要两种访问途径：

1. **浏览器会话**（Cookie）：给人在控制台里操作。
2. **API Key**：给程序化集成——客户把生成能力接进自己的流水线，
   或嵌进内部工具。

两种凭证面临完全不同的威胁模型：

| | 浏览器会话 | API Key |
|---|---|---|
| 存放位置 | HttpOnly Cookie，JS 读不到 | CI 变量、`.env`、脚本、第三方 SaaS 的连接器配置 |
| 生命周期 | 14 天，可被服务端立即吊销 | 通常长期有效，可能几年不轮换 |
| 泄漏概率 | 中（XSS、设备失窃） | **高**（提交进 Git、贴进工单、日志泄漏、第三方被拖库） |
| 泄漏后的检测 | 用户会立刻发现被登出 | 通常**毫无感知** |
| 使用场景 | 交互式，伴随人判断 | 自动化，无人在环审核 |

如果 API Key 拥有与浏览器会话相同的权限，那么一次 API Key 泄漏的后果是：
攻击者可以**改订阅套餐、取消订阅、把 owner 踢出组织、创建新的 API Key**
（后者可以自持久化，即使原 Key 被吊销也能重新拿到访问权）。

这个后果的严重性远超「项目数据被读」——**它是财务与账号控制权的损失**，
不可逆且难以察觉。

## 决策

**API Key 被限制在「读写项目」范围内，明确不具备任何管理权限。**

### 实现方式

`Principal.require_role()` 的第一行就是硬拦截：

```python
def require_role(self, minimum: MembershipRole) -> None:
    if self.kind != "session":
        raise Forbidden("该操作需要使用浏览器登录（API Key 不具备管理权限）")
    if self.role.rank < minimum.rank:
        raise Forbidden(f"需要 {minimum.value} 及以上权限")
```

也就是说，**无论 `api_keys.scopes` 里写了什么**，
只要经过 `require_role`（进而 `require_admin` / `require_owner` /
`require_billing`），API Key 一律 403。

### 依赖层的划分

`security/deps.py` 暴露了一组语义明确的依赖：

| 依赖 | 允许的身份 | 用途 |
|---|---|---|
| `CurrentPrincipal` | 会话 + API Key | 只读操作 |
| `OptionalPrincipal` | 匿名 + 会话 + API Key | 公开页面（落地页、定价页） |
| `WritablePrincipal` | 会话 + API Key（需 `write` scope） | 建项目、取消任务、删项目 |
| `SessionPrincipal` | **仅会话** | 控制台页面、计费、成员 |
| `AdminPrincipal` | 仅会话 + role ≥ admin | 计费、成员、密钥、BYOK |
| `OwnerPrincipal` | 仅会话 + role = owner | 高风险操作 |

`require_session()` 的实现同样直接：

```python
def require_session(principal: Principal) -> Principal:
    """需要浏览器会话（禁止 API Key），用于计费与成员管理。"""
    if principal.kind != "session":
        raise Forbidden("该操作需要使用浏览器登录")
    return principal
```

### API Key 被刻意降级为 `member` 角色

```python
@property
def role(self) -> MembershipRole:
    if self.kind == "api_key":
        return MembershipRole.MEMBER
    return self.membership.role if self.membership else MembershipRole.MEMBER
```

即使创建 Key 的是一位 `owner`，这把 Key 的角色也只是 `member`。
「权限不随创建者继承」是一条刻意的防线：否则管理员离职前创建的 Key
会永久保留管理员能力。

### 显式的接口级拒绝

即使某个接口没有走 `require_role`，也做了显式判断：

```python
# POST /api/v1/api-keys
if principal.kind == "api_key":
    raise Forbidden("不能用 API Key 创建新的 API Key，请使用浏览器登录")
if principal.role.rank < 2:
    raise Forbidden("需要管理员权限")

# DELETE /api/v1/api-keys/{key_id}
if principal.kind == "api_key":
    raise Forbidden("不能用 API Key 撤销密钥")

# GET /api/v1/audit
if principal.kind == "api_key":
    raise Forbidden("审计日志仅对浏览器会话开放")
```

审计日志也被挡住。理由：审计日志包含 IP、操作者、成员变更历史，
是攻击者做侦察（了解组织结构、找到高权限用户）的理想信息源。

### 其余约束

- **免费版不允许创建 API Key**（`accounts.create_api_key()` 里
  `if plan.is_free: raise Forbidden("免费版不支持 API Key，请升级到专业版")`）。
  API 能力是付费功能的边界。
- **每组织最多 20 个活跃 Key**（`revoked_at IS NULL` 计数）。
- **Key 只存哈希**：`SHA-256(pepper + full_key)`，明文仅在创建时返回一次。
- **Key 有过期时间**（`expires_at`，可选）。

## 后果

### 正面

- **API Key 泄漏的影响面被限制在「数据」而非「财务与账号」**。
  攻击者最多能读项目、创建新项目（消耗额度）、删除项目。
  他无法改套餐、无法取消订阅、无法踢人、无法创建新 Key 来自持久化。
- **用户对长期凭证的顾虑降低**。因为「把 Key 放进 CI」的后果是有限的，
  这降低了用户在集成时的摩擦，是个正向的产品效果。
- **权限模型可以被一句话讲清楚**：会话能管钱和人，Key 只能管项目。
  这比「按 scope 细粒度授权」更容易被用户理解，也更容易被审计。

### 负面 / 代价

- **某些合法的自动化场景无法实现**。例如：
  - 「每月自动续费前检查余额并自动充值」——需要计费权限，只能人工或
    用会话凭证做（而会话凭证不适合自动化）。
  - 「CI 里自动创建临时 API Key 用于测试」——做不到，必须人工在控制台建。
  - 「SaaS 连接器自动拉取用量报表」——`usage_records` 没有公开接口，
    只有 `GET /api/v1/credits`（可读，因为它是只读）。
  这些限制是**刻意的取舍**，不是遗漏。若将来确实需要，
  正确的做法是引入**独立的、可细粒度授权、可审计的 OAuth 应用模型**，
  而不是放宽 API Key 的权限。
- **`scopes` 字段的语义被削弱**。既然 `require_role` 会一刀切拒绝，
  `api_keys.scopes` 实际上只影响「读 vs 写项目」这一层
  （`Principal.has_scope()` 里 `"admin" in self.scopes` 的分支
  对 API Key 永远不成立，因为它无法通过 `require_role`）。
  这个字段当前的作用是：`require_write` 检查 `write`，
  `api_keys.scopes` 默认 `["read","write"]`。
  **文档与界面不应承诺 `scopes` 能授予管理能力。**

### 需要长期注意

- 新增「写」类接口时要显式选择依赖：只读用 `CurrentPrincipal`，
  写项目用 `WritablePrincipal`，管理与计费用 `SessionPrincipal` /
  `AdminPrincipal`。**不要图省事统一用 `CurrentPrincipal`。**
- 未来的「组织级服务账号」应当作为**独立的身份类型**引入，
  而不是给 API Key 加权限。

## 备选方案

### 方案 A：API Key 拥有与创建者相同的角色

- **优点**：最直观，用户不会遇到「我明明是 owner，为什么 Key 改不了套餐」。
- **否决理由**：把长期凭证的风险面扩大到财务与账号控制。
  一次 CI 变量泄漏 = 账单和团队控制权失守。**否决。**

### 方案 B：用 scope 系统细粒度授权（`billing:write`、`members:write` 等）

- **优点**：灵活，用户可以自行决定每把 Key 的能力；
  能给「自动充值」类场景放行。
- **否决理由**：
  1. **默认值问题**。绝大多数用户不会读 scope 列表，
     会直接用默认值。如果默认包含 `billing:write`，等于没做限制；
     如果默认不含，那和现在的方案效果一致，但多了一层复杂度。
  2. **`billing:write` 的语义很危险**。「改套餐」这件事没有
     「部分授权」的中间态——能改就是能改。
  3. **实现成本**。需要一套 scope 常量、中间件校验、
     界面提示、以及「scope 变更后旧 Key 怎么办」的迁移策略。
     1.0.0 阶段的收益不足。
  - **不是永远否决**：如果将来出现真实的、无法用会话凭证解决的
    自动化计费需求，应当重新评估，并优先考虑 OAuth 应用模型。

### 方案 C：API Key 完全只读

- **优点**：泄漏后果最小。
- **否决理由**：程序化集成的核心价值就是「把生成能力接进自己的流水线」，
  也就是**创建任务**。只读的 API 无法实现这个价值。
  「读写项目」是能支撑产品定位的最小权限集。

### 方案 D：要求 API Key 请求走单独的、功能受限的端点前缀

（例如 `/api/v1/keys/*` 只提供项目能力，管理端点只在 `/admin/*` 且仅会话可用）

- **优点**：物理隔离，比运行时检查更难绕过。
- **否决理由**：当前已经是「逻辑隔离 + 依赖层强制」，
  对于这个小规模的路由表足够清晰。如果路由数量增长到几十个，
  物理分组会是更好的选择（可读性与可审计性都更好）。

---

## 参考

- 安全模型全景：[SECURITY.md](../../SECURITY.md)
- 鉴权实现细节：[ARCHITECTURE.md](../ARCHITECTURE.md) 第 4.3 节
- 代码：`reelagent_cloud/security/deps.py::Principal`、
  `reelagent_cloud/api/routes/api.py`
