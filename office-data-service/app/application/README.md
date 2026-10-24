# 统一应用层

- `service.py`：每操作独立异步会话／事务，实时身份与权限、超时、输出校验、日志和业务分派。
- `commands.py`：总操作白名单与模型注册；管理输入复用access模块局部规范化。
- `contracts.py`：业务输出注册和事务内输出校验。
- `codebook.py/json`：项目业务码表及通用契约库的装配。

- capability_map.json：已实现应用操作与MCP／SDK／CLI的覆盖映射，由真实能力声明及检查脚本使用。
- data_changes.py：封闭数据管理预览／执行编排；session_service.py：授权会话操作与身份清理编排。
- account_service.py／account_runner.py：受控账号命令、状态恢复与异步身份调用，不是通用任务队列。

模块只向下依赖业务与基础设施，业务不反向调用总操作注册表。HTTP／MCP／Web共用Application与实时权限；错误类型在common，响应适配在entrypoints/errors.py。

受控聚合也经本层调用data适配器，授权、事务、超时和输出校验不另开旁路。具体模块和查询合同见[模块台账](../modules/README.md)，跨入口覆盖由scripts/check_capability_coverage.py检查。
