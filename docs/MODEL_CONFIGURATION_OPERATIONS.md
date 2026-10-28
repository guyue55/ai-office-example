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

1. 备份，按原流程执行正式迁移至当前head **0015**。普通镜像仍无可选Agent依赖；需要运行模型时使用包含Agent的镜像。
2. 新安装器已生成 `service/secrets/model_encryption_key`，0600、服务UID/GID所有，独立于Web会话密钥。已有实例不会自动生成或覆盖根密钥：运维用`cryptography.fernet.Fernet.generate_key()`生成URL-safe 32字节随机根密钥，写入独立受限文件；不要粘贴到聊天／配置值／Git。
3. 在该实例`service/service.json`设置如下**文件引用与出口策略**，按原流程重启该实例；不要重装现有数据库：

```json
{
  "agent_secret_key_file": "secrets/model_encryption_key",
  "agent_model_url_policy": "public",
  "agent_model_urls": []
}
```

默认 `public` 模式，管理员直接填写公网HTTPS base URL，不必先让运维逐个加地址。`agent_model_urls` 在此模式可明确授权特定内网HTTPS或隔离loopback端点；元数据／链路本地、多播、未指定及保留地址仍拒绝。企业需要固定供应商时，设置 `agent_model_url_policy: "allowlist"`，列表必须精确匹配。旧配置已有非空列表但未写策略时自动保持allowlist，不静默扩大权限；空列表旧配置按public默认行为。

保存只做本地格式与策略校验，不解析域名、不发模型请求。托管模型每次实际请求在有界异步调用内解析DNS，验证全部候选IP，将连接固定至通过检查的IP，保留原域名Host、TLS SNI与证书验证，拒绝跨源／base路径外请求及重定向，不使用宿主代理环境。只有运维明确列出的loopback HTTP用于隔离模型测试，企业HTTP演示开关不会放开任意内网HTTP模型。此措施不代替网络出口防火墙；企业强制代理需要另行适配与验收。只影响控制台托管模型，员工独立Agent与外部文件模式保持原行为。

根密钥、数据库密文和服务配置需要配套备份；丢失原根密钥不能恢复模型密钥。**不得通过生成新根密钥修复解密失败。** 首版不提供在线根密钥轮换；轮换需暂停任务、备份、迁移所有保留修订密文、恢复验证后再更换，不能只换文件。Fernet用于小型秘密的认证加密，依据[cryptography官方说明](https://cryptography.io/en/latest/fernet/)，不自行设计密码算法。

HTTP演示页面仍提示明文传输；真实企业密钥应经HTTPS配置，不将HTTP验收当正式安全方案。

## 管理员使用顺序

1. 选择三种协议之一、base URL、准确模型标识；Anthropic显式选择Bearer或x-api-key，不自动猜协议或回退。 OpenAI常见base URL以`/v1`结尾；Anthropic SDK自动追加`/v1/messages`，通常填写根地址。自定义网关按其说明填写，避免重复`/v1`。
2. 首次选择“替换为新密钥”。已有配置默认“保留草稿已有密钥”，表单不回填原值；清除仅作用于新草稿，不修改生效修订。
3. 核对数据使用许可、超时与任务预算，保存草稿。未勾数据许可也能保存及执行合成连接测试，启用仍拒绝。保存没有模型调用。
4. 可选“保存并测试”，先保存新修订，再测试；或单独“测试草稿连接”。通过一次项目确认弹窗明确可能计费，无重复复选框。只发固定合成文本、无企业工具、最多1次模型请求、32输出Token、512总Token预算及有界超时，不自动重试。失败不撤销已保存草稿，也不改变正在生效的旧修订。页面展示分类原因、建议、耗时、协议、模型与修订，不展示供应商正文／原异常；“基础调用通过”不证明工具能力、余额、资料合规或全部模型兼容。测试开始／完成持久化并审计；重启将未完成记录标为interrupted，不自动重发。测试期间并发保存不会把原结果绑定到新草稿；旧修订结果明确标记需重测。撤回发起者权限后仍保存已发生的测试结果，但响应和后续读取重新核验权限。保存并测试若第二阶段无法开始，会明确提示草稿已保存、测试未完成。
5. 启用**已保存修订**，不是未保存的表单内容。运行中、排队或待确认任务存在时返回409，请先完成或明确停止相关任务。启用失败保留旧配置；新任务记录固定的model_revision。
6. 停用同样要求无活动任务；停止新受理，不回退外部文件。读取历史／完成已有回执不依赖模型继续可用。重新启用需要明确选择当前已保存修订。

多标签同版本保存只有一个成功；另一个409，刷新后重新核对，不自动覆盖或重试写入。首次保存使用version=0，后续使用响应中的version。版本值同时约束草稿和启停，不能用修订号猜最新version。

## 来源与恢复

- 初始source=external：原`agent_config_file`如有配置继续使用；没有文件时明确未就绪。文件只读，不通过页面重写。
- 保存草稿不改变来源或活动Runner。首次显式启用托管修订后source=managed，重启从PG的active_revision恢复；最新草稿不自动生效。
- 停用source=disabled；即使原文件仍存在也不加载。未安装Agent包仍可查看／保存草稿，应用时返回依赖不可用，不影响原数据服务。
- 配置切换和任务受理复用协调器互斥，PG版本锁保护草稿并发。模型网络等待不持数据库事务，也不阻塞查询／取消。仍要求单执行进程，不声明跨worker或多副本热切换。
- 数据库提交与内存状态之间若发生异常，后续任务校验不一致并拒绝启动，重启按已提交配置恢复。应用回退不等于数据库降级；0014／0015含敏感修订及测试留痕，禁止自动删表降级。
- 历史修订保留密文以便追溯与恢复；清除当前草稿不等于安全擦除历史供应商密钥。需要立即失效时同时在供应商侧撤销旧密钥；历史保留／清理策略后续按企业要求实施。

## HTTP契约

所有端点复用现有`code/message/data/meta`、权限、错误码与安全日志：

| 方法与路径（前缀/api/v2） | 请求 | 结果 |
|---|---|---|
| GET /agent/configuration | 无 | ModelStatus，只有secret_set状态，无密文或明文 |
| POST /agent/configuration/draft | version、draft、secret_action；replace时提供secret | 脱敏状态及新修订 |
| POST /agent/configuration/apply | version、revision | 生效状态 |
| POST /agent/configuration/disable | version | 已停用状态 |
| POST /agent/configuration/test | version、revision、acknowledge_cost=true | revision、passed、category、message、suggestion、elapsed_ms |

草稿的三协议取值、预算等来自真实输入模型；完整定义以生成的`contracts/openapi.json`为准。输入错422、无权限403、旧版本或活动任务冲突409、运行依赖／秘密不可用503。测试收到有效模型响应但不通过时，HTTP200且data.passed=false，表示测试操作完成而连接未通过；不是伪装助手任务执行成功。

## 验收边界

本轮实际命令和结果追加到实施计划。独立临时PG正式迁移、HTTP/Web、浏览器和合成模型均不得指向当前业务实例。三协议真实HTTP适配沿用已有loopback回归；Responses／Anthropic真实供应商与费用验收仍单独授权。保存、启用和连接测试是三件不同的事。

## 配置页交互补充（2026-10-28）

桌面端编辑与运行状态／生效操作并列，窄屏或助手并排时按可用宽度堆叠。超时和任务预算收在高级设置，错误字段会展开以便定位。切换协议只展示适用鉴权；保留或清除密钥时不展示新密钥输入。无JS时保留完整表单及原服务器验证。

有未保存修改时会显示状态，并暂时阻止本页测试／启停已保存修订；可先保存或“还原修改”，不会把未保存内容当成已保存配置。最终确认提交后显示等待文案并防重复操作，取消确认不进入等待态。方案及验收见[UI专项记录](plans/2026-10-28-model-settings-ui.md)。


## 测试分类与范围

`ok`为基础合成调用通过；失败类别固定为authentication、permission、endpoint_or_model、rate_limit、provider、dns、tls、connect、timeout、policy、invalid_response。页面用中文说明并给下一步建议，技术类别便于排障；不返回密钥、供应商响应正文或原异常字符串。测试元数据保存在model_connection_test，响应只提供最近一次；修订不同即过期。历史清理策略及完整测试历史列表不在本轮范围。

实现与本轮分层验收见[连接流程计划](plans/2026-10-28-model-connection-flow.md)。当前运行实例没有自动升级；升级须先备份并迁移至0015。


## 响应预算与测试异常恢复（2026-10-28加固）

Token参数不能替代HTTP内存限制。控制台托管模型的普通请求默认原始响应、解压后响应分别不超过4MiB，可在实例service/service.json设置`agent_model_response_wire_bytes`、`agent_model_response_decoded_bytes`，各允许65536至16777216字节。合成连接探针固定进一步收紧至各64KiB，不随一般预算提高；这些是传输预算，和业务`limits.output_bytes`各自独立。员工独立Agent／外部文件模式不因本轮自动改变。

读取在SDK解析之前逐块计数，成功与错误体一致；Content-Length只能提前拒绝，不能绕过实际累计。支持identity、gzip和标准zlib deflate并做有界解压，损坏、截断、拼接压缩流及其他压缩方式明确拒绝。请求只声明接受gzip／deflate。超限返回测试分类`response_limit`，响应关闭且不自动重试；不保留供应商原始正文。

测试从开始记录提交到结果落库都纳入收尾：已获得结果时优先保存完成事实，结果未知时标interrupted。独立收尾任务最多5秒（不超过operation_timeout），请求取消／重复取消不直接取消该任务；收尾未结束会拒绝新的连接测试，避免重复计费。进程正常关闭会等待已登记收尾。

收尾仍可能因数据库不可用而失败，此时记录安全日志并返回回执未保存；不能把它解释为未发生调用。无需重启，后续配置查询／操作会恢复无实际运行任务的started为interrupted；每次最多100条，锁定记录跳过并在后续访问再核对，真实活跃测试和收尾任务不会被误恢复。恢复不调用模型、不声称恢复了已丢失结果。数据库尚不可用时无法保证立即落盘；进程强制终止后的恢复也只修正状态，不重发请求。此机制仍仅支持既定的单执行进程。

加固方案、失败证据和验证边界见[专项计划](plans/2026-10-28-model-response-lifecycle-hardening.md)。数据库结构保持0015，本轮无需新迁移；此前未升级实例仍须先完成原0015迁移。


## 双镜像与恢复验收补充（2026-10-28）

普通镜像不含 `office_agent`；Agent 镜像以同一 Dockerfile 的 `OFFICE_AGENT=1` 构建。安装器在建库前导入预检，配置入口可见不代表可运行模型。发布矩阵按普通／Agent 分开标签、摘要和证明，本地构建不代表 GHCR 已交付。

隔离验收 `OFFICE_QUERY_PG_TESTS=1 uv run --extra agent --frozen pytest tests/test_model_backup_restore_postgres.py -q` 使用临时 PG18 的真实 custom-format dump/restore，在同一隔离集群新数据库恢复数据及 ACL。验证活动修订与最新未应用草稿分别保留；正确根密钥恢复可用，缺失／错误根密钥使助手降级，普通数据查询仍通过。该测试没有供应商调用，不替代异机身份库恢复、完整实例灾备、容量或目标环境演练。现有实例不因本轮测试而升级。

## 模型专用可信DNS（2026-10-28修复）

服务端托管模型可使用运维配置的DoH JSON解析器，不要求修改Clash或系统DNS。在实例`service/service.json`中同时指定：

```json
{
  "agent_model_doh_url": "https://cloudflare-dns.com/dns-query",
  "agent_model_doh_domains": ["model.example.com"]
}
```

上例需合并到原配置，不覆盖其余字段；域名替换为获准模型的精确主机名，不含协议、路径或通配符。默认两字段为空、继续系统解析；只有清单中的模型目标使用该解析器。解析器须支持Cloudflare兼容的DoH JSON查询与Question／Answer格式；不将该JSON接口称作所有RFC8484服务都支持的格式。企业可选自建兼容解析器，不强制公共DNS。内网域名不默认查询公共服务。

A／AAAA异步并发查询；总解析超时5秒，原始和解压后各64KiB，每响应最多64记录，合并地址最多64。校验问题、状态、截断、别名链和地址族；所有候选地址仍通过原出口规则，连接固定IP并保留Host／SNI和证书验证。不重定向、不自动降级回不安全解析，不写死供应商IP。单传输生命周期内缓存最多64域名，TTL遵循记录并上限60秒，TTL=0不缓存；结束时关闭HTTP客户端，取消会传播到并发DNS查询。

此配置只影响托管模型传输，不改数据库、身份、普通HTTP客户端、员工独立Agent或宿主代理。`dns_fake_ip`提示代理映射地址；`dns_address`提示受限解析结果；原`dns`表示查询失败。修复后历史测试记录仍保留旧失败，不自动重试付费调用；需用户手动重新测试。
