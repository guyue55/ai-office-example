# 模块权威归属

access管理人员、角色、权限集、实时权限、账号与授权；data管理数据容器、字段修订、候选、确认、更正与历史；query负责受控条件、聚合与预算，data/query是已确认记录适配器。

不预建空业务模块。不复制身份、权限或数据模型；新增领域规则才建立独立模块。schemas／outputs／change_contracts只放模型，不导入数据库或服务实现；模块不反向依赖application或entrypoints。依赖由scripts/check_architecture.py核验。
