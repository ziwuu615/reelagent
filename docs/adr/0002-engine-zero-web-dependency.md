# ADR 0002：引擎层零 Web 依赖，与云层严格分离

- **状态**：已接受
- **日期**：1.0.0 发布前
- **决策者**：引擎 / 平台
- **相关文件**：`reelagent/`（整包）、`reelagent/pipeline/runner.py`、`reelagent_cloud/services/pipeline.py`、`pyproject.toml`

---

## 背景

产品同时要满足两类使用方式：

1. **单机创作**：一个创作者 clone 仓库，不配任何 API Key，
   用内置的离线供应商跑通全流程，看到真实的 GIF/音视频产物。
2. **多租户 SaaS**：同一个生成能力被包装成有账号、额度、计费、
   并发限制与审计的服务。

如果只有一套代码，会立刻出现两难：

- 服务化需要的（数据库 schema、租户过滤、鉴权、限流、Stripe）
  会渗进生成逻辑，使「clone 即跑」不再可能——因为要连数据库、要配密钥。
- 生成逻辑需要的（PIL 渲染、同步阻塞的供应商 HTTP 调用、线程池并发）
  放进 Web 进程会卡死事件循环。

在项目起始阶段还有一个现实约束：**生成算法会频繁迭代**（prompt、
一致性策略、路由规则、评测阈值），而计费与鉴权必须稳定。
两者的变更节奏与风险等级完全不同。

## 决策

**把项目拆成两个包，依赖方向严格单向**：

```
reelagent_cloud  ──依赖──▶  reelagent
      （云层）                 （引擎）
```

### 引擎 `reelagent/` 的边界

- **零 Web 依赖**。不导入 FastAPI / Starlette / SQLAlchemy / Stripe / Redis。
  全部依赖只有 `numpy` + `Pillow`（+ 标准库）。
- **不感知租户**。没有 `org_id` 概念，没有身份，没有权限。
- **自己管状态**：单文件 SQLite（`<workspace>/reelagent.db`），
  通过 `harness/state.py::Store` 访问。这是引擎唯一的持久化。
- **数据模型用 dataclass 而非 Pydantic**（见 ADR 的「备选方案」一节与
  `reelagent/schema.py` 的 docstring）：
  「核心零第三方依赖，任何机器 clone 即跑，同时 `to_dict / from_dict`
  保证了完整的 JSON 往返能力——这是断点恢复的基础。」
- **通过宿主回调暴露扩展点**，而不是自己实现宿主能力：

  ```python
  def run(
      self,
      project_id: str,
      *,
      on_stage: Callable[[str, str], None] | None = None,   # 上报进度
      should_cancel: Callable[[], bool] | None = None,      # 响应取消
      ...
  ) -> dict[str, Any]:
  ```

  docstring 明确写了：「引擎本身不认识数据库、队列与 HTTP，
  这些能力全部由宿主注入。」

### 云层 `reelagent_cloud/` 的职责

- 所有 Web / 多租户 / 计费 / 队列 / 存储相关的能力。
- **不许把业务逻辑写进 Agent**：云层只能通过
  `services/pipeline.py` 这一层与引擎交互。
- 引擎桥接层负责补偿两边的不匹配（四个工程问题，见下文「后果」）。

### 一个刻意的反向约束

`reelagent_cloud/services/pipeline.py` 的 docstring 里有一条产品红线：

> **没有配置视频供应商时，生产环境直接拒绝执行**，
> 而不是悄悄用离线渲染顶上——让用户花钱买到占位图是最坏的产品事故。

也就是说，引擎的 mock 能力**不允许**在云层被当作降级手段使用。
引擎可以 mock，云层生产环境不可以。

## 后果

### 正面

- **引擎可以被单机使用，也可以被服务化**。切换宿主不需要改任何 Agent 代码。
- **引擎测试不需要数据库、网络、API Key**。48 个引擎用例 clone 即跑
  （云层另有 164 个 pytest 用例，需要 `[dev]` 依赖）。
- **失败域隔离**。计费/鉴权/限流的 bug 不会污染生成逻辑；
  生成算法的改动也不会波及账务。
- **供应商与存储可替换**。Agent 只依赖 `Runtime`，不感知具体模型与供应商，
  因此换模型、换供应商、换存储都不需要改 Agent 代码。
  同一套 `ToolRegistry` 还能零改动地被 MCP Server 暴露
  （`specs()` 就是 `tools/list` 结构）。

### 负面 / 代价

- **需要一层桥接代码**，而且这层要解决四个非平凡的问题
  （`services/pipeline.py` 的 docstring 列举）：

  | 问题 | 对策 |
  |---|---|
  | 长任务不能长期占用数据库连接 | 进出各开一个短事务，中间完全不持有连接 |
  | 引擎同步阻塞 vs worker 异步 | `asyncio.to_thread()` + `run_coroutine_threadsafe` 回调写进度 |
  | 引擎在本地临时目录工作 | 跑完搬进对象存储并在 `artifacts` 表登记 |
  | 预估与实际成本有偏差 | 用引擎汇总的真实成本 `settle()` 多退少补 |

- **引擎两套配置来源**。引擎自己读扁平的 `REEL_MODE` / `REEL_LLM_API_KEY`
  等环境变量；云层用嵌套的 `REEL_PROVIDERS__VIDEO__API_KEY`。
  桥接层必须显式转换（`build_engine_settings()`）。
  这也是 `.env.example` 只覆盖引擎变量、不覆盖云层变量的历史原因
  （见「已知缺口」）。
- **云层的「断点恢复」能力被削弱**。引擎的 checkpoint 在
  `<workspace>/projects/<engine_project_id>/reelagent.db`，
  云层给每个 job 一个独立工作区（`engine_workspace / job_id`），
  且任务结束后 `cleanup_workspace()` 会删除它。
  因此云层当前的断点恢复主要体现在「单次执行内的重试」与
  「单镜重跑」，而不是跨进程的完整续跑。
- **类型重复**。引擎的 `ProviderConfig` 与云层的 `ProviderSettings`
  是两套结构，需要人工保持同步。

### 需要长期注意

- **禁止让引擎 import 云层**。这是架构的核心不变量，
  code review 时应当作为硬性检查项。
- 引擎新增依赖时必须评估：这个依赖是否破坏了「clone 即跑」。

## 备选方案

### 方案 A：单一包，用配置开关区分模式

```python
if settings.is_saas:
    ...
```

- **否决理由**：条件分支会渗透到每一层，最终没有任何一处能确定自己
  运行在哪种模式下。这是「配置地狱」的经典成因。
  而且 SaaS 的依赖（FastAPI、SQLAlchemy、Stripe、argon2）
  仍然会出现在 `dependencies` 里，破坏「核心零依赖」。

### 方案 B：引擎作为独立的 pip 包 / 独立仓库

- **优点**：边界最硬（必须通过版本化接口交互），
  可以被其它产品复用。
- **否决理由**：1.0.0 阶段引入发布流程与版本对齐成本，
  对单人/小团队是净负担。`pyproject.toml` 的
  `include = ["reelagent*", "reelagent_cloud*"]` 保留了在同一仓库里
  协作的便利。**注意**：两个包已经共享同一个版本号（`1.0.0`），
  意味着它们事实上是同步发布的——这与「独立仓库」的设想有张力，
  是将来拆分时首先要解决的问题。

### 方案 C：引擎也用 Pydantic

- **优点**：自动校验、序列化优化、与云层模型统一、
  可以复用 FastAPI 的 schema 生成。
- **否决理由**：Pydantic 是第三方依赖，会破坏「clone 即跑」。
  数据模型简单（`Project` / `Script` / `Scene` / `Shot` / `Clue`）时，
  dataclass + 手写 `to_dict / from_dict` 这笔交易划算。
  `Serializable.from_dict` 还刻意做了「只接受已知字段」的容错
  （`known = {f.name for f in fields(cls)}`），
  这使得旧版本的 JSON 在新版本代码里仍能加载。

### 方案 D：把引擎做成独立进程，通过 RPC 交互

- **优点**：进程边界即安全边界，资源隔离彻底。
- **否决理由**：1.0.0 阶段引入的复杂度远大于收益；
  当前的 `asyncio.to_thread()` 已经解决了事件循环阻塞问题。
  如果将来需要按供应商配额做资源隔离，这是合理的演进方向。

---

## 参考

- 架构细节：[ARCHITECTURE.md](../ARCHITECTURE.md) 第 2、3、4 节
- 贡献约束：[CONTRIBUTING.md](../../CONTRIBUTING.md)（「引擎层不得引入 Web 依赖」）
