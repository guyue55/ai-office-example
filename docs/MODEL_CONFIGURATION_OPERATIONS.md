# 控制台模型配置与运维

2026-10-29更新，Agent分支。与[协议说明](AGENT_MODEL_CONFIGURATION.md)、[实施计划](plans/2026-10-28-model-configuration.md)配套；不代表当前运行实例已升级。

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

1. 备份，按原流程执行正式迁移至当前head **0016**。普通镜像仍无可选Agent依赖；需要运行模型时使用包含Agent的镜像。
2. 新安装器已生成 `service/secrets/model_encryption_key`，0600、服务UID/GID所有，独立于Web会话密钥。已有实例不会自动生成或覆盖根密钥：运维用`cryptography.fernet.Fernet.generate_key()`生成URL-safe 32字节随机根密钥，写入独立受限文件；不要粘贴到聊天／配置值／Git。
3. 在该实例`service/service.json`设置如下**文件引用与出口策略**，按原流程重启该实例；不要重装现有数据库：

```json
{
  "agent_secret_key_file": "secrets/model_encryption_key",
  "agent_model_url_policy": "public",
  "agent_model_urls": []
}
```

默认 `public` 模式，管理员直接填写公网HTTPS base URL，不必先让运维逐个加地址。`agent_model_urls` 在此模式可明确授权特定内网HTTPS／HTTP或隔离loopback端点；元数据／链路本地、多播、未指定及保留地址仍拒绝。企业需要固定供应商时，设置 `agent_model_url_policy: "allowlist"`，列表必须精确匹配。旧配置已有非空列表但未写策略时自动保持allowlist，不静默扩大权限；空列表旧配置按public默认行为。

保存只做本地格式与策略校验，不解析域名、不发模型请求。托管模型每次实际请求在有界异步调用内解析DNS，验证全部候选IP，将连接固定至通过检查的IP，保留原域名Host、TLS SNI与证书验证，拒绝跨源／base路径外请求及重定向，不使用宿主代理环境。HTTP模型须是运维明确列出的受信内网IPv4／loopback地址，企业HTTP演示开关不替代模型出口授权。此措施不代替网络出口防火墙；企业强制代理需要另行适配与验收。只影响控制台托管模型，员工独立Agent与外部文件模式保持原行为。

根密钥、数据库密文和服务配置需要配套备份；丢失原根密钥不能恢复模型密钥。**不得通过生成新根密钥修复解密失败。** 首版不提供在线根密钥轮换；轮换需暂停任务、备份、迁移所有保留修订密文、恢复验证后再更换，不能只换文件。Fernet用于小型秘密的认证加密，依据[cryptography官方说明](https://cryptography.io/en/latest/fernet/)，不自行设计密码算法。

HTTP演示页面仍提示明文传输；真实企业密钥应经HTTPS配置，不将HTTP验收当正式安全方案。

## 管理员使用顺序

1. 选择API协议，填写服务地址及密钥。已有密钥不回填，默认保留；点击“更换”输入新密钥，“清除”仅影响下一次保存。无密钥、自定义鉴权、网关参数和预算在高级设置。
2. 点击模型行旁的刷新图标，使用**当前未保存输入**获取模型；无需预先填模型名称。可搜索／键盘选择，也可直接输入准确ID。目录缺失不阻断手填；列表截断明确提示。
3. 点击模型旁“测试连接”，经一次费用确认，使用**当前未保存输入**发送短合成提示，不生成配置修订、不切换活动配置。结果就近显示；修改参数后失效。只发固定合成文本、无企业工具、最多1次模型请求、32输出Token、512总Token和最多30秒模型阶段预算；不自动重试。
4. “保存”只持久化；“保存并启用”先保存，再按原版本／权限／活动任务规则启用。数据使用许可为启用前提；目录和合成测试不要求先批准业务外发。若第二阶段失败，明确提示配置已保存、尚未启用，保留原运行配置，不重复模型调用。
5. 详细运行信息、最近测试、原已保存修订的测试／启用／停用在折叠详情中；未保存修改仍阻止误操作旧修订。临时测试标为“未保存”，不会冒充已保存或活动配置的验证证据。测试通过不证明工具能力、余额、数据合规或全部协议特性。
6. 切换服务地址而保留原密钥时，需要明确确认继续使用，或输入新密钥；浏览器不得自动把旧密钥送往新的地址。无JS保持手填表单，密钥操作通过下拉选择，输入的新密钥不会在回执页面回填。
7. 停用／启用沿用无活动任务要求；任务排队、运行或待确认时返回409，不能自动终止他人的任务。新任务固定model_revision。Web临时探针与正式管理均要求实时权限；普通员工无管理入口。

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
| POST /agent/configuration/models | version、connection、secret_action／secret、reuse_secret_at_new_url | 有界items、truncated、分类及耗时，不调用推理 |
| POST /agent/configuration/probe | version、draft、秘密动作、request_key、acknowledge_cost=true | revision=null、passed、分类、建议、耗时；同一请求标识不重发 |
| POST /agent/configuration/test | version、revision、acknowledge_cost=true | revision、passed、category、message、suggestion、elapsed_ms |

草稿的三协议取值、预算等来自真实输入模型；完整定义以生成的`contracts/openapi.json`为准。输入错422、无权限403、旧版本或活动任务冲突409、运行依赖／秘密不可用503。测试收到有效模型响应但不通过时，HTTP200且data.passed=false，表示测试操作完成而连接未通过；不是伪装助手任务执行成功。

## 验收边界

本轮实际命令和结果追加到实施计划。独立临时PG正式迁移、HTTP/Web、浏览器和合成模型均不得指向当前业务实例。三协议真实HTTP适配沿用已有loopback回归；Responses／Anthropic真实供应商与费用验收仍单独授权。保存、启用和连接测试是三件不同的事。

## 配置页交互（2026-10-29）

首屏按协议、地址、密钥、模型排列，刷新和测试就近显示，当前状态紧凑呈现。高级和旧修订操作折叠，桌面优先、窄屏可换行，无JS保留手填。可编辑模型选择组件支持键盘、Esc及自由ID输入，外部目录纯文本显示，不按名称猜模型能力。模型目录至多10页、1000个ID，每次响应最多1MiB、总等待至多15秒；截断不称全量。推理探针仍各64KiB响应预算。所有请求复用服务端受控出口，浏览器不直连模型服务。

结果绑定本次输入版本；请求期间改变连接或模型，过期回执不能把新输入标绿。模型目录不可用、认证失败、限流和实际模型调用失败分别提示，失败不清空当前模型。页面等待超时不自动重试；临时测试按actor与request_key唯一约束去重，刷新页面不会重放。共享敏感字段标记让“显示密钥”后的文本输入也排除在脏表单快照外。

当前实施与验收见[主流程计划](plans/2026-10-28-model-settings-workflow.md)，调研依据见[交互调研](MODEL_SETTINGS_UX_RESEARCH.md)。

## 测试分类与范围

`ok`为基础合成调用通过；失败类别固定为authentication、permission、endpoint_or_model、rate_limit、provider、dns、tls、connect、timeout、policy、invalid_response。页面用中文说明并给下一步建议，技术类别便于排障；不返回密钥、供应商响应正文或原异常字符串。测试元数据保存在model_connection_test，配置查询提供最近一次；revision=null表示临时配置，不与活动修订混同。历史清理策略及完整测试历史列表不在本轮范围。

实现与本轮分层验收见[连接流程计划](plans/2026-10-28-model-connection-flow.md)。当前运行实例没有自动升级；升级须先备份并迁移至当前head 0016。


## 响应预算与测试异常恢复（2026-10-28加固）

Token参数不能替代HTTP内存限制。控制台托管模型的普通请求默认原始响应、解压后响应分别不超过4MiB，可在实例service/service.json设置`agent_model_response_wire_bytes`、`agent_model_response_decoded_bytes`，各允许65536至16777216字节。合成连接探针固定进一步收紧至各64KiB，不随一般预算提高；这些是传输预算，和业务`limits.output_bytes`各自独立。员工独立Agent／外部文件模式不因本轮自动改变。

读取在SDK解析之前逐块计数，成功与错误体一致；Content-Length只能提前拒绝，不能绕过实际累计。支持identity、gzip和标准zlib deflate并做有界解压，损坏、截断、拼接压缩流及其他压缩方式明确拒绝。请求只声明接受gzip／deflate。超限返回测试分类`response_limit`，响应关闭且不自动重试；不保留供应商原始正文。

测试从开始记录提交到结果落库都纳入收尾：已获得结果时优先保存完成事实，结果未知时标interrupted。独立收尾任务最多5秒（不超过operation_timeout），请求取消／重复取消不直接取消该任务；收尾未结束会拒绝新的连接测试，避免重复计费。进程正常关闭会等待已登记收尾。

收尾仍可能因数据库不可用而失败，此时记录安全日志并返回回执未保存；不能把它解释为未发生调用。无需重启，后续配置查询／操作会恢复无实际运行任务的started为interrupted；每次最多100条，锁定记录跳过并在后续访问再核对，真实活跃测试和收尾任务不会被误恢复。恢复不调用模型、不声称恢复了已丢失结果。数据库尚不可用时无法保证立即落盘；进程强制终止后的恢复也只修正状态，不重发请求。此机制仍仅支持既定的单执行进程。

加固方案、失败证据和验证边界见[专项计划](plans/2026-10-28-model-response-lifecycle-hardening.md)。这次历史加固阶段的结构为0015；当前主流程改版新增0016，升级以当前head为准。


## 双镜像与恢复验收补充（2026-10-28）

普通镜像不含 `office_agent`；Agent 镜像以同一 Dockerfile 的 `OFFICE_AGENT=1` 构建。安装器在建库前导入预检，配置入口可见不代表可运行模型。发布矩阵按普通／Agent 分开标签、摘要和证明，本地构建不代表 GHCR 已交付。

隔离验收 `OFFICE_QUERY_PG_TESTS=1 uv run --extra agent --frozen pytest tests/test_model_backup_restore_postgres.py -q` 使用临时 PG18 的真实 custom-format dump/restore，在同一隔离集群新数据库恢复数据及 ACL。验证活动修订与最新未应用草稿分别保留；正确根密钥恢复可用，缺失／错误根密钥使助手降级，普通数据查询仍通过。该测试没有供应商调用，不替代异机身份库恢复、完整实例灾备、容量或目标环境演练。现有实例不因本轮测试而升级。

## 模型DNS自动兼容（2026-10-28更新）

新增公网模型供应商无需维护域名清单。运维在实例`service/service.json`统一配置可信DoH JSON解析器即可：

```json
{
  "agent_model_doh_url": "https://cloudflare-dns.com/dns-query",
  "agent_model_doh_scope": "auto"
}
```

上例合并到原配置，不覆盖其他字段。业务人员继续只填协议、base URL、模型和密钥；默认public出口模式不限制供应商品牌或域名。可信解析器属于部署基础设施，可用企业自建的兼容服务，不强制Cloudflare；未配置时保持原系统DNS。

自动模式先使用系统DNS：正常公网结果直接使用；遇到Fake-IP或公网域名解析失败时使用可信解析器。系统DNS返回私有、链路本地、元数据或混合不安全地址时不以兜底掩盖结果，仍按原出口授权判断。字面IP、单标签名及`.local`／`.localhost`／`.lan`／`.internal`／`.home.arpa`不触发公共DoH兜底；已授权且正常解析为私网IP的内部模型保留系统路径。不能仅凭域名推断所有企业私有名称，配置解析器时应遵守企业DNS政策。

只有主动要求限定范围的部署才使用`agent_model_doh_scope: "domains"`与`agent_model_doh_domains`精确清单；旧版非空清单未写scope时保持限定语义，不静默扩大查询范围。当前实例已明确选择auto并移除原单域名清单。

解析器须支持Cloudflare兼容的DoH JSON查询与Question／Answer格式，不声称所有RFC8484接口都支持JSON。A／AAAA异步并发，总解析超时5秒，原始和解压后各64KiB，每响应最多64记录，合并地址最多64。所有候选地址仍校验，连接固定IP、保留Host／SNI和TLS验证；不重定向、不在可信解析失败后放行Fake-IP。缓存最多64域名，TTL按记录并上限60秒，TTL=0不缓存；结束关闭客户端，取消传播至并发查询。

只影响托管模型传输，不接管身份、数据库、其他客户端、员工独立Agent或宿主代理。历史测试失败保留，不自动重试付费调用。`dns_fake_ip`表示基准测试映射地址，`dns_address`表示受限地址，`dns`表示解析失败。

## 登录隔离与通用模型接入（2026-10-28）

控制台 Cookie 按配置 public_url 的规范地址派生实例命名空间，包含端口；不依赖浏览器传来的 Host。同一主机不同实例不会清理彼此的登录事务。旧共享 Cookie 不继续用于认证，升级后需重新登录。服务内存会话仍在重启时结束；失效、超时、绑定不符和令牌交换失败提供统一本站恢复入口，不重放旧回调。命名空间防止意外覆盖，不把同一主机端口视为对抗恶意服务的浏览器安全隔离；不可信服务应使用不同主机名。

连接测试不借用 operation_timeout。模型探针最多30秒，取草稿 request_timeout、limits.seconds 与30秒最小值；页面显示已保存草稿预算。状态落库仍有独立有界收尾，不将“模型等待结束”误认为整个HTTP响应必须同刻结束。普通任务仍受自己的请求与总时限约束。

### 连接测试通过但会话失败

先对照最近测试的修订、模型ID与当前生效修订。测试草稿成功不会自动替换正在使用的配置；只有明确启用，或选择“保存并启用”，新任务才使用新配置。供应商域名也不能代替模型ID。HTTP 404可能来自模型标识或接口路径，不能仅凭状态码断定出口策略或网络故障。

2026-10-29真实实例复核发现：已通过测试的是新草稿，实际会话仍使用旧修订且把供应商域名当作模型ID。启用已测试草稿后，原权限问题完成，包含身份权限和能力查询两条真实工具回执；同会话只读追问也完成。该结果验证本次配置及会话链路，不代表所有供应商、模型或写入流程均已验收；未自动重试旧失败任务，未修改业务数据。

### 显式内网模型

公网默认 HTTPS，无需逐供应商登记。受信内网模型可由运维在 agent_model_urls 精确配置 HTTP base URL，例如 `http://192.168.1.20:11434/v1`，模型页即可保存；不要用公网 HTTP。地址解析仍逐请求校验、固定连接，HTTP 不接受公网解析结果，链路本地／元数据、假IP、多播等仍拒绝。当前员工端的内网 HTTP 使用 RFC1918 IPv4，需单独设置 `allow_model_http=true`；企业服务的 allow_lan_http_demo 不替代该授权。托管配置由服务器根据显式允许地址注入此许可。内网域名 HTTP 及IPv6 HTTP尚不作为员工端通用承诺，可使用内网IPv4或HTTPS。

### 网关高级参数

- `auth_mode="api-key"` 保持原默认；OpenAI 用 Bearer，Anthropic 可选择 Bearer／x-api-key。
- `auth_mode="none"` 明确不鉴权，不读取宿主密钥、不发送 SDK 内部占位值。已有密文可保留但不会使用。无JS表单选择无密钥时，密钥操作选“保留”或“清除”，不要选择“替换”却留空。
- `auth_mode="custom-header"` 加 `auth_header="x-gateway-key"` 将独立加密密钥放入指定头，替代默认鉴权；不与 Bearer 同发。
- `extra_headers`／`extra_query` 各为最多16项字符串映射，只放路由、版本等非秘密参数；每项值最多256个可打印ASCII字符。例如查询参数 `{"api-version":"2026-01-01"}`。不在 base URL 拼查询字符串，不放密钥。禁止凭证名称、重复大小写名称、传输层保留头，且不允许覆盖凭证头。

员工端 TOML 使用同名字段，秘密仍通过 api_key_file 或 api_key_env。服务端通过人工 Web／HTTP 配置，不开放给助手或MCP改密钥。三种标准协议继续共用一套SDK模型适配；专用供应商API、Azure身份协议或任意网关语义并不因能传参数就自动兼容。

依据：[RFC6265 §8.5](https://www.rfc-editor.org/rfc/rfc6265#section-8.5) 明确Cookie不按端口隔离；[OpenAI客户端源码](https://github.com/openai/openai-python/blob/main/src/openai/_client.py) 与 [Anthropic客户端源码](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/_client.py) 支持 default_query／default_headers。实现使用现有异步客户端、结构化查询参数和受控请求钩子，未引入新的网关框架。
