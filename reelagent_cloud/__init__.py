"""ReelAgent Cloud —— 多租户 SaaS 服务层。

分层约定（依赖方向单向向下，禁止反向 import）：

    api/        HTTP 边界：路由、中间件、鉴权依赖、错误映射
    services/   业务用例：账号、项目、任务、额度、计费、存储
    workers/    异步任务执行：领取 Job、跑流水线、回写结果
    db/         持久化：ORM 模型、会话管理、迁移
    security/   认证授权：口令、会话、API Key
    domain/     领域模型：枚举、DTO、状态机
    observability/ 可观测：日志、指标、链路上下文

引擎（`reelagent` 包）是纯计算层，不感知数据库、HTTP 与租户；
Cloud 层通过 `services.pipeline` 调用它。
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
