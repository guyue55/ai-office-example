# 控制台模型配置与运维

2026-10-28，Agent 分支新增。与[协议说明](AGENT_MODEL_CONFIGURATION.md)、[实施计划](plans/2026-10-28-model-configuration.md)配套；不代表当前运行实例已升级。

## 谁在哪里配置

控制台导航“系统设置 → 智能助手配置”，路径 `/console/model-settings`。使用权限项而非角色名称：

| 权限 | 能力 |
|---|---|
| agent.config.read | 查看脱敏草稿、来源、修订和运行状态 |
| agent.config.manage | 保存草稿，保留／替换／清除草稿密钥；同时需要 read |
| agent.config.apply | 启用已保存修订／停用助手；同时需要 read |
| agent.config.test | 明确确认可能计费后执行连接测试；同时需要 read |

新库初始化管理员包含这些权限。0014迁移会一次性将新权限加入原有含access.manage的权限集，并增加版本；之后按实际权限配置生效，不在登录时补回。普通员工的agent.run不包含模型管理。更细的职责可通过现有权限集拆分。

本组管理操作只提供HTTP／Web人工入口。不会把模型密钥传入助手、MCP、员工SDK或CLI；没有在工具目录注册“读写模型密钥”能力。员工独立Agent仍用自己的TOML和秘密引用。

## 运维先完成一次

1. 备份，按原流程执行正式迁移至当前head **0014**。普通镜像仍无可选Agent依赖；需要运行模型时使用包含Agent的镜像。
2. 新安装器已生成 `service/secrets/model_encryption_key`，0600、服务UID/GID所有，独立于Web会话密钥。已有实例不会自动生成或覆盖根密钥：运维用`cryptography.fernet.Fernet.generate_key()`生成URL-safe 32字节随机根密钥，写入独立受限文件；不要粘贴到聊天／配置值／Git。
3. 在该实例`service/service.json`设置如下**文件引用与允许出口**，按原流程重启该实例；不要重装现有数据库：

```json
{
  "agent_secret_key_file": "secrets/model_encryption_key",
  "agent_model_urls": ["https://api.openai.com/v1", "https://api.anthropic.com"]
}
```

实际允许地址由企业确定；自定义兼容网关也填准确base URL。列表默认空，页面不能扩大它，避免有配置权限就能把服务变为任意地址代理。模型请求不跟随重定向，也不使用宿主代理环境；企业强制代理需要另行适配与验收。只有loopback HTTP用于隔离模型测试，企业HTTP演示开关不会放开任意内网模型地址。

根密钥、数据库密文和服务配置需要配套备份；丢失原根密钥不能恢复模型密钥。**不得通过生成新根密钥修复解密失败。** 首版不提供在线根密钥轮换；轮换需暂停任务、备份、迁移所有保留修订密文、恢复验证后再更换，不能只换文件。Fernet用于小型秘密的认证加密，依据[cryptography官方说明](https://cryptography.io/en/latest/fernet/)，不自行设计密码算法。

HTTP演示页面仍提示明文传输；真实企业密钥应经HTTPS配置，不将HTTP验收当正式安全方案。

## 管理员使用顺序

1. 选择三种协议之一、允许的base URL、准确模型标识；Anthropic显式选择Bearer或x-api-key，不自动猜协议或回退。
2. 首次选择“替换为新密钥”。已有配置默认“保留草稿已有密钥”，表单不回填原值；清除仅作用于新草稿，不修改生效修订。
3. 核对数据使用许可、超时与任务预算，保存草稿。未勾数据许可也能保存，启用／测试会拒绝。保存没有模型调用。
4. 可选“测试草稿连接”，明确勾选可能计费并确认。只发固定合成文本、无企业工具、最多1次模型请求、32输出Token、512总Token预算及有界超时，不自动重试；响应不展示供应商原文。通过不证明工具能力、余额、资料合规或全部模型兼容。开始与完成分别审计；若进程被取消或中断而只有开始事件，结果视为未知，不能自动重试。
5. 启用**已保存修订**，不是未保存的表单内容。运行中、排队或待确认任务存在时返回409，请先完成或明确停止相关任务。启用失败保留旧配置；新任务记录固定的model_revision。
6. 停用同样要求无活动任务；停止新受理，不回退外部文件。读取历史／完成已有回执不依赖模型继续可用。重新启用需要明确选择当前已保存修订。

多标签同版本保存只有一个成功；另一个409，刷新后重新核对，不自动覆盖或重试写入。首次保存使用version=0，后续使用响应中的version。版本值同时约束草稿和启停，不能用修订号猜最新version。

## 来源与恢复

- 初始source=external：原`agent_config_file`如有配置继续使用；没有文件时明确未就绪。文件只读，不通过页面重写。
- 保存草稿不改变来源或活动Runner。首次显式启用托管修订后source=managed，重启从PG的active_revision恢复；最新草稿不自动生效。
- 停用source=disabled；即使原文件仍存在也不加载。未安装Agent包仍可查看／保存草稿，应用时返回依赖不可用，不影响原数据服务。
- 配置切换和任务受理复用协调器互斥，PG版本锁保护草稿并发。模型网络等待不持数据库事务，也不阻塞查询／取消。仍要求单执行进程，不声明跨worker或多副本热切换。
- 数据库提交与内存状态之间若发生异常，后续任务校验不一致并拒绝启动，重启按已提交配置恢复。应用回退不等于数据库降级；0014含敏感修订及审计，禁止自动删表降级。
- 历史修订保留密文以便追溯与恢复；清除当前草稿不等于安全擦除历史供应商密钥。需要立即失效时同时在供应商侧撤销旧密钥；历史保留／清理策略后续按企业要求实施。

## HTTP契约

所有端点复用现有`code/message/data/meta`、权限、错误码与安全日志：

| 方法与路径（前缀/api/v2） | 请求 | 结果 |
|---|---|---|
| GET /agent/configuration | 无 | ModelStatus，只有secret_set状态，无密文或明文 |
| POST /agent/configuration/draft | version、draft、secret_action；replace时提供secret | 脱敏状态及新修订 |
| POST /agent/configuration/apply | version、revision | 生效状态 |
| POST /agent/configuration/disable | version | 已停用状态 |
| POST /agent/configuration/test | version、revision、acknowledge_cost=true | revision、passed、脱敏message |

草稿的三协议取值、预算等来自真实输入模型；完整定义以生成的`contracts/openapi.json`为准。输入错422、无权限403、旧版本或活动任务冲突409、运行依赖／秘密不可用503。测试收到有效模型响应但不通过时，HTTP200且data.passed=false，表示测试操作完成而连接未通过；不是伪装助手任务执行成功。

## 验收边界

本轮实际命令和结果追加到实施计划。独立临时PG正式迁移、HTTP/Web、浏览器和合成模型均不得指向当前业务实例。三协议真实HTTP适配沿用已有loopback回归；Responses／Anthropic真实供应商与费用验收仍单独授权。保存、启用和连接测试是三件不同的事。
