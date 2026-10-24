# MCP协议适配

已实现受保护的Streamable HTTP `/mcp`。官方SDK负责协议；工具共用Application、当前员工权限、事务、输出模型和审计，服务端无AI。

工具覆盖身份／能力、人员与权限、受控账号生命周期、本人／全企业授权、项目字段、候选／确认／更正、有界确认导出和受控聚合。实际清单以server.py、validation.py及application/capability_map.json为准，不另维护易漂移的手写工具数量。scripts/check_capability_coverage.py核对HTTP／MCP／SDK／CLI的应用操作覆盖。

管理及数据变更使用封闭预览／执行集合，摘要绑定实例、操作人、修订及内容，执行时重新鉴权；宿主落实真实人工确认，摘要不能证明真人批准。参数严格校验，不隐式丢弃额外字段，不开放任意SQL、文件路径或Shell。工具读写注解仅辅助展示，不是权限边界。

export_confirmed_records为单项目有界确认快照导出，超过条数／字节／内容节点预算即失败；aggregate_data复用通用查询内核与data适配器，单数据集／精确修订，不是任意业务指标、跨项目统一快照或无限导出。

协议测试含真实本机Keycloak／PG链路；具体AI宿主的发现、OAuth授权、确认体验及企业HTTPS需分别验收。MCP宿主独立管理凭证，不复制CLI钥匙串令牌到配置。完整边界见[76](../../../../docs/ARCHITECTURE.md)，聚合验收见[75](../../../../docs/ARCHITECTURE.md)。
