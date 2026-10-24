# 服务端源码

已实现的异步模块化单体，服务端与Web无AI。

- `main.py`：运行装配、生命周期、中间件与统一边界。
- `application/`：共享事务编排、操作输入／输出注册、业务码表装配。
- `common/`：基础输入／输出、错误类型、分页预算、追踪和公开元数据；不得依赖业务、协议或数据库。
- `modules/`：业务能力和规则；输入输出模型与实现分离，不反向依赖编排或传输入口。
- `infrastructure/`：纯配置、数据库工厂、持久化模型；配置不导入数据库驱动。
- `entrypoints/`：HTTP、MCP、Web、CLI及协议错误适配，复用应用层。

依赖规则由根 `scripts/check_architecture.py` 验证。迁移仍在服务端项目 migrations，跨模块测试在仓库根 tests。
