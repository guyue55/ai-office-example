# 模型配置能力复核与管理员管理方案

日期：2026-10-27，以下为当时缺口核查。**2026-10-28实现更新**：管理员配置闭环已进入实现与隔离验收，使用说明见[管理操作指南](MODEL_CONFIGURATION_OPERATIONS.md)，逐项实施与证据见[执行计划](plans/2026-10-28-model-configuration.md)。历史核查保留，表中“缺口／待实现”描述不代表更新后的代码状态；已有实例尚未升级。

## 1. 核查结论与历史证据

此前确实调研并开发了三种协议，不能将“没有设置页面”描述为“三协议从未开发”。主要提交为`6f64584 feat(agent): 支持OpenAI双协议与Anthropic自定义模型配置`，记录见AGENT_PLAN的“自定义模型配置阶段”，技术说明见[AGENT_MODEL_CONFIGURATION.md](AGENT_MODEL_CONFIGURATION.md)，真实模型证据见[AGENT_REAL_MODEL_ACCEPTANCE.md](AGENT_REAL_MODEL_ACCEPTANCE.md)。

| 层次 | 当前实现 | 缺口 |
|---|---|---|
| 协议／SDK | OpenAI Chat Completions、OpenAI Responses、Anthropic Messages | 不保证所有兼容网关或模型均已验收 |
| 配置模型 | AgentConfig，含端点、协议、模型、密钥引用、超时和Limits | 运行态校验要求approved_model_data=true，不能直接用作未批准草稿模型 |
| 员工独立Agent | TOML配置＋明确环境／文件秘密引用 | 不自动共享服务端模型密钥 |
| 服务端Agent | 通过agent_config_file在启动时加载一次，构建Runner | 没有配置版本存储、管理API、Web表单或动态应用机制 |
| 就绪检查 | installed/configured/ready及通用错误说明 | 缺少可操作的管理员诊断入口与细分原因 |
| 权限和审计 | 现有agent.run与任务／审批审计 | 无模型配置读取、管理、测试／生效的专门权限和审计闭环 |

用户说的三种“格式”实际是模型API协议，当前配置文件格式仍是TOML：

| UI建议名称 | 当前api值 | Provider实现 | 端点规则 |
|---|---|---|---|
| OpenAI · Chat Completions | openai-completions | OpenAIChatModel | OpenAI兼容base URL＋/chat/completions |
| OpenAI · Responses | openai-responses | OpenAIResponsesModel | OpenAI兼容base URL＋/responses |
| Anthropic · Messages | anthropic-messages | AnthropicModel | Anthropic兼容base URL＋/v1/messages |

model_url是base URL，不是完整请求路径。协议显式选择，不按模型名猜，也不遇错自动降级。Anthropic目前支持bearer与x-api-key两种明确选择；官方鉴权及兼容网关条件见原协议文档。继续复用现有异步SDK，不另写HTTP模型适配。

## 2. 本轮真实复验

执行clients/agent/tests/test_config.py、test_provider_settings.py、test_http_chain.py，**35项通过**。覆盖配置验证、三协议和Anthropic两种鉴权的loopback HTTP链路、工具续答、覆盖信息、预算／Token、宿主凭证隔离及失败行为。使用明确的协议响应夹具，不读取真实模型配置或密钥，不把它们算作供应商推理质量。

历史真实供应商验收仅覆盖获准第三方服务的Chat Completions及合成数据；Responses与Anthropic不能借此称为真实供应商全部通过。本轮也没有重做付费模型调用。

## 3. BeefTV参考确实存在，以及新增值得吸收的部分

本地只读核查BeefTV工作区；HEAD参考`c99cba49`，本次读取的下列路径没有未提交差异。不复制其源码、业务字段、品牌或秘密；BeefTV基于本地工作区，不能直接套用为企业多用户授权。

| 参考路径 | 已核实的做法 | 本项目如何吸收 |
|---|---|---|
| agent-host/canvas-turn.mjs | SUPPORTED_APIS三选一；api/baseUrl/modelId和密钥引用分离 | 现有6f64584已经吸收，保留当前实现 |
| backend/internal/workspace/provider_config.go | 版本比较保存、脱敏读取、保留未修改秘密、加密持久化 | 管理配置必须有版本和明确秘密动作，避免覆盖与误清除 |
| backend/internal/handler/workspace.go | 返回revision／health；版本冲突拒绝覆盖 | 复用本项目响应、业务码和服务端实时权限，不移植本地身份逻辑 |
| backend/internal/handler/workspace_model_config_test.go | 冲突／错误响应不泄漏秘密 | 增加接口负向测试，而不是只测表单可保存 |
| web/src/services/model-config-repository.ts | 区分hydrating/saving/saved/error，协调读取与写入 | 保存中、保存成功、应用成功必须分开；多标签冲突不能静默覆盖 |
| web/src/pages/settings/channel-model-settings.tsx | 模型、协议与能力分开编辑 | 本项目先收敛为通用文本Agent配置，不引入视频／画布渠道插件体系 |

## 4. 重新联网核对的管理模式

- [Open WebUI设置说明](https://docs.openwebui.com/getting-started/quick-start/settings/)区分实例管理员连接设置与用户偏好。借鉴“系统设置”入口，不让普通员工管理全企业模型密钥；不引入整套Open WebUI。
- [Open WebUI连接说明](https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/)按连接配置地址和认证；协议与具体模型兼容仍需验证。
- [LiteLLM模型管理](https://docs.litellm.ai/docs/proxy/model_management)区分数据库管理和文件配置的来源，分别表达可编辑范围；这一来源区分值得吸收。其无需重启能力是其已有实现，本项目不能仅模仿UI就宣称支持。
- [Claude官方API说明](https://platform.claude.com/docs/en/api/overview)继续作为Messages端点／鉴权依据。本项目不是另造模型API网关，不引入路由、计费或任意插件平台。

## 5. 建议的产品闭环

入口：系统设置 → 智能助手，按配置权限显隐；助手未就绪时，有管理权限者显示“前往配置”，普通员工只显示状态与联系说明。默认先管理一个有效配置，预留稳定配置ID及修订，不引入模型路由池、自动故障切换或复杂多租户模型平台。

页面分为四区：

1. 状态卡：依赖是否安装、配置来源、已保存版本、当前生效版本、启用状态及脱敏诊断。
2. 连接与模型：三协议、模型名、base URL、超时；Anthropic鉴权选择仅在相应协议出现。地址格式及完整路径误填需就地提示，不擅自探测其他地址。
3. 密钥与预算：展示“已设置／未设置”，明确保留、替换、清除；预算沿用Limits。禁止从浏览器或助手读取既有密钥值。
4. 操作：保存草稿、无模型调用的校验、明确说明费用的真实测试、启用／停用。保存不自动触发模型或把保存称为已生效。

状态必须分开：未安装、无配置、草稿、校验失败、待应用、已启用、已停用、应用失败。配置本身可用不等于远端余额／网络／模型工具能力可用。

## 6. 存储、权限和生效建议

### 不直接改挂载文件

现有服务运行根文件系统及配置挂载按只读部署；不要为了一个表单把整个配置目录开放写权限，也不要在Web请求里重写service.json或让容器操作Docker自行重启。

建议服务端采用“数据库中的非秘密配置修订＋独立受保护秘密后端”，复用PG事务、版本、审计和部署秘密机制。若采用数据库密文，必须另有持久化专用加密密钥、备份恢复与轮换方案，不能以编码代替加密或复用登录会话密钥。最终秘密存储方式是实现前需定的事项，不在本轮落库。

原文件配置保留为可识别的外部管理来源，先显示来源／只读状态；只有明确迁入托管配置后才由UI维护。禁止文件和数据库两份配置不声明优先级地互相覆盖。员工独立Agent继续使用自己的配置和秘密引用，不下发企业模型密钥。

### 权限与写入

复用现有权限项／权限集／角色组合，增加配置读取、管理和测试／应用能力，权限码在契约设计时统一登记。agent.run不包含管理模型。内置管理员的初始化／迁移授权应可追踪且不在登录时反复补权。

保存、秘密替换／清除、启停、测试、应用均核对当前权限与配置版本，记录脱敏审计。秘密字段不能出现在预览摘要、业务错误、trace、列表或响应中。测试权限也不能转成任意URL代理，端点变更需有出口策略。

### 运行时应用

当前AgentCoordinator.start受started标记限制，只加载一次。必须新增明确的配置应用生命周期，不能只保存数据库或文件然后假装热生效。

首版建议：保存新修订不改变活动任务；在没有运行／等待确认／在途写入任务时显式应用，应用前完成本地验证，失败保留原生效配置；新任务固定到生效修订及密钥版本。应用／停止与任务受理之间使用一致的并发控制，不以UI计数判断空闲。不自动取消待确认或在途写入；停用时区分停止新受理与已有任务策略。未来若支持不中断切换，再验证旧Runner与秘密版本的生命周期，不提前承诺。

## 7. 实施边界与顺序

1. 固化托管配置／外部文件来源优先级、秘密后端与迁移、配置应用和活动任务策略。
2. 复用AgentConfig／Limits／configured_model，增加管理草稿输入及只读状态模型。基础服务未安装可选Agent包时仍能显示未安装，不能因管理页导入模型SDK导致普通服务启动失败。
3. 实现受控管理应用操作、PG修订及迁移、权限／审计、配置生命周期；HTTP／Web复用应用层，不旁路原规则。
4. 使用现有共享表单、弹窗、错误／loading组件制作管理页面，不等待新React／CopilotKit前端选型。
5. 核验三协议配置→保存→校验→应用→助手受理→工具回执，以及密钥保留／替换／清除、409冲突、无权限、重启恢复和失败回退。
6. 真实供应商测试单独取得配置与费用授权；HTTP演示不作为正式密钥传输方案。最后重建同源候选，再解除发布暂停。

## 8. 验收与未决项

必须通过：普通员工API／UI拒绝管理、管理员可完成闭环、秘密查询不回显、所有写入留痕、多标签冲突可见、保存与生效版本准确、应用失败原配置可用、活动任务不被误终止、重启可恢复；三协议复用已有回归并补管理全链路。

待实现前确定：秘密后端及加密密钥初始化／备份、配置来源迁移、精确权限码与内置管理员迁移、允许模型出口范围、真实测试的数据／费用边界、停用后已有任务策略。以上不影响确认三协议已存在，也不能被“协议测试通过”替代。
