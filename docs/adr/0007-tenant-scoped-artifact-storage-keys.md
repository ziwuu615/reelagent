# ADR 0007：产物存储 key 与租户绑定

- **状态**：已接受
- **日期**：1.0.0 发布前
- **决策者**：安全 / 存储
- **相关文件**：`reelagent_cloud/services/storage.py`、`reelagent_cloud/api/routes/files.py`、`reelagent_cloud/services/pipeline.py`、`reelagent_cloud/db/models.py`（`Artifact`）

---

## 背景

生成出来的产物（关键帧 PNG、片段 GIF/MP4、配音 WAV、分镜九宫格、
最终成片）需要被存储并提供给用户下载播放。这里有一个很容易被忽略的
安全问题：

**产物的访问控制发生在两层，而它们可能不一致。**

- **第一层：数据库**。`artifacts` 表有 `org_id`，查询时可以加租户过滤。
- **第二层：存储**。一旦拿到了存储层的 key（例如一个本地路径或一个
  S3 object key），读取文件本身不经过任何权限检查。

如果只有第一层做过滤，那么第二层就是一个「无鉴权的文件系统」。
攻击者只要能构造出（或猜到）一个 key，就能读到别人的片子。
真实世界里的经典漏洞形态：

```
GET /files/proj_01H8X.../keyframes/sc01_sh01.png     ← 把 URL 里的 ID 换成别人的
```

或者更隐蔽的形态：S3 上的预签名 URL 被分享出去后，
由于 key 本身不携带租户信息，无法在生成 URL 时校验归属。

此外还有几个相关的需求：

1. **存储后端要可替换**。默认 local 文件系统（自托管友好），
   也可切换到 S3 / OSS / R2 / MinIO（S3 兼容协议）。
2. **要能分享单条产物**。用户需要把成片发给客户看，但不能因此
   把整个存储桶暴露出去。
3. **要能整租户清理**。账号注销时必须能一次性删掉该组织的所有对象。
4. **文件名必须安全**。用户素材的标题可能含路径分隔符、`..`、
   中文、emoji，不能直接当路径用。

## 决策

**存储 key 的第一段强制是 `org/{org_id}/`，并且每一次按 key 的读取
都必须先过归属校验。**

### key 的构造

```python
def build_key(*, org_id: str, project_id: str | None, category: str, filename: str) -> str:
    parts = ["org", sanitize_filename(org_id, fallback="unknown")]
    if project_id:
        parts += ["projects", sanitize_filename(project_id, fallback="unknown")]
    parts += [sanitize_filename(category, fallback="misc"), sanitize_filename(filename)]
    return "/".join(parts)
```

产生的 key 形如：

```
org/org_01M4Z76RW2WNQXHJE6Y49XNMNA/projects/proj_01M4Z76RZGNWY5TB0NEE2MTZYV/keyframes/sc01_sh01.png
org/org_01M4Z76RW2WNQXHJE6Y49XNMNA/projects/proj_01M4Z76RZGNWY5TB0NEE2MTZYV/final/final.gif
```

**租户信息是 key 的结构性前缀，不是附带的元数据。**
这是一个刻意的设计：它让归属校验可以是一个纯字符串前缀判断，
不需要查库、不需要缓存、不会有「缓存过期导致的越权」问题。

### 归属校验（唯一的硬边界）

```python
def key_belongs_to_org(key: str, org_id: str) -> bool:
    """租户隔离的硬校验。任何按 key 的读取都必须先过这一关。"""
    normalized = key.replace("\\", "/").lstrip("/")
    return normalized.startswith(f"org/{sanitize_filename(org_id, fallback='unknown')}/")
```

注意 `normalize` 做了两件事：把反斜杠统一成正斜杠（防 Windows 路径混淆），
去掉前导斜杠（防绝对路径）。

### 在路由里强制执行

`GET /files/{key:path}` 的第一件事就是校验：

```python
@router.get("/files/{key:path}", include_in_schema=False)
async def get_file(key: str, request, settings, principal: CurrentPrincipal) -> Response:
    # 租户隔离的硬边界：key 必须属于当前组织
    if not storage_svc.key_belongs_to_org(key, principal.org_id):
        log.warning(
            "cross_tenant_file_access_blocked",
            org_id=principal.org_id,
            actor=principal.actor_label,
            key=key[:120],
        )
        raise Forbidden("无权访问该文件")
    ...
```

两个细节值得注意：

1. **越权尝试会被记录成告警日志**，字段包含攻击者的 org、actor 标签
   （会话用户或 API Key 前缀）与被尝试的 key 前缀。
   因此这是一条**可监控的安全事件**，不只是 403。
2. **它是所有按 key 读取路径的入口**。`/api/v1/artifacts/{id}/download`
   走的是另一条路（按 Artifact ID 查库，查询里带 `Artifact.org_id == principal.org_id`），
   但它拿到 `artifact.storage_key` 后交给同一个 `_stream()` 函数，
   因此不会绕过这一层的语义。

### 文件名的清洗

```python
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._\-]+")

def sanitize_filename(name: str, *, fallback: str = "file") -> str:
    """把任意文件名清洗成安全的存储段，杜绝路径穿越与诡异字符。"""
    base = Path(name or "").name            # 先剥掉目录部分
    cleaned = _SAFE_SEGMENT.sub("-", base).strip(".-")   # 非法字符换 -，去掉首尾的 . 和 -
    if not cleaned:
        cleaned = fallback
    return cleaned[:120]
```

三个动作：`Path(name).name` 剥掉任何目录成分（`../../etc/passwd` → `passwd`）、
白名单化（只允许 `A-Za-z0-9._-`）、`strip(".-")` 防止生成 `.` / `..` / 隐藏文件。
中文文件名会被替换成 `-`，因此产物名以 shot_id 这类 ASCII 标识为主。

### local 后端的第二重保险

```python
def _path(self, key: str) -> Path:
    # 双重保险：拼接后再确认仍在根目录内
    candidate = (self.root / key).resolve()
    if not str(candidate).startswith(str(self.root.resolve())):
        raise Forbidden("非法的存储路径")
    return candidate
```

即使 `build_key` 的清洗出了问题（例如被绕过），
`resolve()` 之后的路径检查仍会拦住穿越。

`put_bytes()` 还用了一个原子替换：

```python
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_bytes(data)
tmp.replace(path)      # 原子替换：读端永远看不到半截文件
```

### 分享：不暴露存储层，而是签发独立 token

分享**不是**「把 key 变成公开」，而是：

1. `POST /api/v1/artifacts/{id}/share?enable=true` 把 `artifact.is_public = True`
   并生成一个 HMAC 签名的 token（TTL 30 天）：
   `issue_share_token(settings, artifact_id=..., expires_at=now + 30*24*3600)`。
2. 返回 `settings.public_url(f"/s/{token}")`。
3. `GET /s/{token}` **无需登录**，但要同时满足三个条件：
   - token 签名有效且未过期（`read_share_token`）
   - `artifact.is_public is True`
   - `artifact.deleted_at is None`

```python
artifact = (
    await session.execute(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.is_public.is_(True),        # ← 关闭分享后 token 立即失效
            Artifact.deleted_at.is_(None),
        )
    )
).scalar_one_or_none()
```

**关键性质**：撤销分享（`enable=false`）会把 `is_public` 置回 `False`
并把 `share_token` 置 `None`，此时**已发出的 URL 立即失效**——
不需要维护黑名单，因为授权状态在服务端而不在 URL 里。

分享响应额外带 `Cross-Origin-Resource-Policy: cross-origin` 与
`Cache-Control: public, max-age=86400`（普通产物是 `private, max-age=3600`），
因为分享页需要能被跨源引用。

### S3 后端的特殊处理

`presigned_url()` 优先返回 `s3_public_base_url`（若配置了 CDN 前缀），
否则用 `generate_presigned_url` 生成有时效的直连 URL。
**预签名 URL 只在「用户已有权访问」的路径上生成**——
当前代码里 `presigned_url()` 没有实际调用方，
下载统一走 API 层代理（`/files/{key}` 与
`/api/v1/artifacts/{id}/download`），因此归属校验始终生效。

### 整租户清理

```python
async def purge_org_storage(settings: Settings, org_id: str) -> int:
    """删除某组织的全部对象（账号注销时调用）。"""
    root = storage.local_path(f"org/{sanitize_filename(org_id, fallback='unknown')}")
    ...
```

key 的结构化前缀让「删掉一个租户的全部数据」变成一次目录删除。

## 后果

### 正面

- **越权读取在结构上不可能**。攻击者必须同时知道目标 key 的精确内容
  **并且**让自己所属组织的 `org_id` 等于 key 里的那一段——
  这是做不到的（`org_id` 来自服务端解析的凭证，不是请求参数）。
- **归属校验是 O(1) 的字符串比较**，不需要查库、不需要缓存、
  不引入新的失败模式。这在下载这种高频路径上很重要。
- **两种存储后端的安全语义一致**。local 与 S3 用同一套 key 与同一套校验，
  切换后端不会引入新的越权面。
- **分享可撤销且不需要黑名单**。授权状态存在数据库的 `is_public` 里，
  URL 只是它的一次性证明。
- **越权尝试可监控**。`cross_tenant_file_access_blocked` 是一条高价值的
  安全信号——正常用户不会触发它。
- **删除租户数据是可实现的**。这是合规（数据删除权）的基本要求，
  很多 SaaS 在后期才发现自己的存储布局无法做这件事。

### 负面 / 代价

- **key 变长且含内部 ID**。URL 里会出现 `org_01M4Z76RW...`，
  用户看到会觉得奇怪，也略微暴露了内部 ID 格式（ULID）。
  这在「日志可读性」与「信息暴露」之间是一个取舍。
  **`region` 与 `bucket` 仍然是 `config` 字段，没有按租户分桶。**
- **`org_id` 出现在文件名段里，重命名组织是不可能的**（slug 可以改，
  `org_id` 不能改）。这是可接受的：ULID 主键本来就是不可变的。
- **不同租户的对象混在同一个 bucket / 目录里**。因此：
  - 无法按租户做存储配额。
  - 无法按租户授予独立的存储凭证（例如给大客户一个只能访问自己前缀的
    IAM policy）。
  - 单个 bucket 的权限配置错误会一次性影响所有租户。
  这些都是当前方案的固有限制。
- **中文文件名被替换成 `-`**。用户下载时看到的文件名因此不总是原始标题
  （但 `Artifact.filename` 字段保留了原始名，`Content-Disposition`
  用的是这个字段，所以下载后的文件名是对的）。
- **`sanitize_filename` 对 `org_id` 也做了清洗**。当前所有 `org_id`
  都是 `org_` + ULID（纯 ASCII），清洗是恒等变换；
  但如果将来引入自定义 org 标识，清洗后的值与原始值不同会导致
  `key_belongs_to_org` 判定失败。这是一处需要注意的隐式耦合。
- **`is_public` 是布尔值，没有「有效期」与「访问次数限制」**。
  分享链接在 30 天内可以被无限次访问、转发给任何人。
  需要更细粒度控制（密码保护、一次性链接）时当前做不到。
- **`purge_org_storage()` 目前没有调用方**。账号注销流程尚未实现，
  因此「删除权」在代码里有了工具但还没有入口。

### 需要长期注意

- **任何新的按 key 读取路径都必须先调 `key_belongs_to_org`**。
  这条规则应当作为 code review 的硬性检查项。
  更好的做法是把校验下沉到 `Storage` 抽象里
  （例如 `get_bytes(key, *, org_id)`），让「忘记校验」在类型层面不可能发生。
  **这是当前设计可以改进的地方。**
- **预签名 URL 一旦开始使用，就必须确认生成前已经校验过归属**。
  它的有效期是 `presign_ttl_seconds`（默认 3600），期间无法撤销。
- **备份时不要把整个 bucket 复制到不同权限的环境**。
  因为所有租户共享 bucket，误配的副本会一次性泄漏全部数据。

## 备选方案

### 方案 A：按租户分桶 / 分目录挂载，用存储层权限隔离

```
s3://reelagent-org-01M4Z76RW.../projects/.../keyframes/xxx.png
```

- **优点**：隔离最强——即使应用层有 bug，一个租户的存储凭证
  也无法访问别人的桶。可以按租户做配额与计费。
- **否决理由**：
  1. **运维成本爆炸**。每个新注册的组织都要创建 bucket、
     配置 IAM policy、处理配额。用户注册是高频操作，bucket 不是。
  2. **S3 兼容存储的分桶能力差异大**。MinIO / R2 的 bucket 数量限制
     与 AWS 不同，本地文件系统后端更是没有「桶」的概念
     （会变成一堆挂载点或盘符）。
  3. 违反「可一键部署」的定位——自托管用户不可能接受
     「注册一个用户就要建一个桶」。
  - **不是永远否决**：对超过某个规模的企业客户，
    提供「专属存储桶」作为企业版特性是合理的演进方向。

### 方案 B：key 里不绑租户，靠数据库查询校验归属

```python
# 每次下载都查 artifacts 表，按 storage_key 找到记录，比对 org_id
artifact = SELECT * FROM artifacts WHERE storage_key = ? AND org_id = ?
```

- **优点**：key 更干净（可以是 `projects/<id>/...`），
  且存储布局与多租户解耦。
- **否决理由**：
  1. **依赖存储 key 上的唯一索引**（`storage_key` 确实是 UNIQUE），
     查询可行，但每次下载多一次数据库往返。
  2. **它假设「所有存在的对象都在 artifacts 表里」**。
     一旦出现孤儿对象（产物上传成功但登记失败 —— 这个窗口在
     `_collect_artifacts()` 里真实存在：先 `put_file` 再 `add_all(rows)`），
     这些对象就变成了「查不到记录 → 404」，
     而不是「不属于你 → 403」。前者会让运维难以区分
     「文件真的不存在」与「登记丢了」。
  3. **`/files/{key:path}` 这条快速路径就无法实现了**，
     所有访问都必须绕经数据库。
  - **折中方案**：两者结合（key 带租户前缀 **且** 校验数据库归属）。
     当前只做了前者，因为它在成本与安全性之间的性价比最高。

### 方案 C：给 key 签名（key 本身携带 HMAC）

例如 `projects/xxx/a.png?sig=...`，校验签名而非租户前缀。

- **优点**：可以为单个对象生成短期可分享的链接，不需要 `is_public` 状态。
- **否决理由**：
  1. 签名不支持「撤销」——签名有效期内无法作废，
     除非维护黑名单（而黑名单正是我们想避免的）。
  2. 当前的分享功能已经用独立 token 解决了这个问题，
     且支持立即撤销。
  3. 签名会泄漏在日志与 Referer 里，与分享 token 的风险相同，
     但缺少「一次性/可撤销」的表达能力。

### 方案 D：所有产物经 API 代理，存储桶完全私有且不可寻址

（存储层用不可猜测的随机 key，且不暴露任何 key 结构）

- **优点**：key 不可枚举，攻击面最小。
- **否决理由**：
  1. **无法整租户清理**（清理需要能按租户枚举对象）。
  2. **无法做存储层的人工排障**（运维看到一堆 UUID 目录无从下手）。
  3. **可读性损失**：日志里的 key `org/org_x/projects/proj_y/keyframes/sc01_sh01.png`
     能直接告诉排障者「哪个租户的哪个项目的哪个镜头」，
     这在事故处理中的价值很高。
  4. 当前方案的安全性已经足够（归属校验是结构性的），
     没有为了「不可枚举」而放弃可运维性的必要。

### 方案 E：把 key 的租户校验下沉到 `Storage` 抽象层

（每个方法都要求传 `org_id`，内部强制校验）

- **优点**：让「忘记校验」在类型层面不可能发生。
- **否决理由（暂缓，而非否决）**：这是当前设计**最值得做的改进**，
  但它会改动 `Storage` 的所有方法签名（8 个抽象方法 + 若干辅助方法），
  影响面较大。1.0.0 阶段用「路由里强制 + code review」的方式控制，
  并把下沉改造列入后续演进。
  **注意**：这不是「不好」，而是「还没做」。

---

## 参考

- 安全模型全景：[SECURITY.md](../../SECURITY.md)（「租户隔离」一节）
- 存储代码：`reelagent_cloud/services/storage.py`
- 下载与分享路由：`reelagent_cloud/api/routes/files.py`
- 产物登记：`reelagent_cloud/services/pipeline.py::_collect_artifacts()`
