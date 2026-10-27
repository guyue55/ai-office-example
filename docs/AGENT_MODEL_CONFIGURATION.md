# 自定义模型协议与配置

2026-10-26。员工独立Agent与服务端内部Agent使用同一个配置模型、供应商工厂及执行引擎；企业业务授权与模型密钥继续分离。协议适配完成不等于某个供应商、模型或企业网关已经真实验收。

## 能力状态（2026-10-27复核）

三协议／文件配置已有实现，管理员Web设置与动态应用闭环尚未实现。不能将协议支持等同于控制台可配置。历史提交、BeefTV参考、35项本轮复验及补齐方案统一见[模型配置管理复核](MODEL_CONFIGURATION_MANAGEMENT.md)。本文继续作为运行协议与文件格式说明，不重复定义管理接口。

## 选型依据

Pydantic AI提供[OpenAI Chat／Responses模型](https://pydantic.dev/docs/ai/models/openai/)、[兼容接口接入](https://pydantic.dev/docs/ai/models/compatible-apis/)和[Anthropic模型](https://github.com/pydantic/pydantic-ai/blob/main/docs/models/anthropic.md)。本项目固定Pydantic AI 2.61.0，实际核对安装签名并使用官方异步SDK，不自行重写模型请求与工具消息转换。

本地BeefTV的`agent-host/canvas-turn.mjs`将`api`、`baseUrl`、`modelId`与密钥引用分开，明确支持`openai-completions`、`openai-responses`、`anthropic-messages`；同时提醒不同兼容服务对reasoning、消息角色等支持不同。本项目吸收配置与协议分离的做法，没有复制其源码、引入Pi／Node运行时，或推断其供应商配置可以直接通用。

采用三种显式协议，而不是按模型名猜接口或在报错后自动降级。旧配置没有`api`时保持原来的Chat Completions行为；同一模型名出现在不同网关时，也由配置者决定协议和端点。

| api | SDK模型 | model_url的含义 | 请求路径 |
|---|---|---|---|
| openai-completions | OpenAIChatModel | OpenAI兼容base URL，通常含/v1 | base URL下的/chat/completions |
| openai-responses | OpenAIResponsesModel | Responses兼容base URL，通常含/v1 | base URL下的/responses |
| anthropic-messages | AnthropicModel | Anthropic兼容base URL，官方为根地址 | base URL下的/v1/messages |

`model_url`不是完整接口URL；不要填到`/chat/completions`、`/responses`或`/v1/messages`。自定义反向代理有路径前缀时按其文档填写，不自动删路径或探测其他主机。模型URL支持HTTPS和本机loopback HTTP；企业服务的局域网HTTP演示开关不能扩大模型出口。loopback指实际执行Agent的环境：员工电脑上是该电脑，服务器容器中是该容器，不是另一台员工电脑或宿主机地址。

## 最小配置

OpenAI Chat Completions：

```toml
service_url = "https://office.company.example"
api = "openai-completions"
model_url = "https://api.openai.com/v1"
model = "your-tool-capable-model"
api_key_env = "OFFICE_MODEL_API_KEY"
request_timeout = 30
approved_model_data = false
```

OpenAI Responses只需显式改协议，地址也可以换成确实实现该协议的网关：

```toml
api = "openai-responses"
model_url = "https://api.openai.com/v1"
```

Anthropic Messages替换为：

```toml
api = "anthropic-messages"
model_url = "https://api.anthropic.com"
model = "your-tool-capable-claude-model"
anthropic_auth = "bearer"
```

这些是同一份完整配置的替换片段，不能把多段同名键拼进一个TOML。示例模型名是占位符，须换成目标端点实际支持且具备工具调用能力的模型。确认该模型端点获准处理任务资料后才将`approved_model_data`设为true；它不代表系统已经完成全部PII脱敏。

服务器通常使用独立受限文件：

```toml
api_key_file = "secrets/model_key"
```

指定文件时不回退环境变量；路径相对配置文件，POSIX权限要求见[运行指南](AGENT_OPERATIONS.md)。SDK只接收这份配置指定的密钥，不把企业OAuth令牌当模型密钥，不借用AI工具的订阅、Keychain或配置目录。

## Anthropic鉴权与兼容网关

本轮锁定Anthropic Python SDK 2.3.0。[官方2026-10-21迁移说明](https://platform.claude.com/docs/en/api/migration-2026-10-21)将API Key的默认请求头改为`Authorization: Bearer`；[官方鉴权说明](https://platform.claude.com/docs/en/manage-claude/authentication)仍允许旧`x-api-key`。需要旧头的网关可显式设置`anthropic_auth="x-api-key"`，该选项仅适用于Messages协议。不同时发送两种鉴权，也不因401自动切换。

SDK可能读取宿主环境的自定义头与组织信息。独立HTTP客户端关闭环境代理与自动重定向，请求出口过滤未知头，并重新绑定本配置的模型鉴权。OpenAI不继承宿主组织／项目；Anthropic的内容遥测与trace传播显式关闭，不由环境变量开启。这不替代企业网络、供应商保留政策或网关本身的安全审核。

## 预算、存储与失败行为

- `request_timeout`默认30秒，允许大于0且不超过300秒；实际请求超时不超过任务总时限。任务总时限和请求／工具／Token预算仍由`limits`控制。
- 三种协议的SDK重试均关闭；307、401、429、503等错误不自动重试、不切协议或换供应商，不回显上游错误正文中的敏感内容。
- Responses显式设置`store=false`，不依赖远端response ID恢复上下文。完整历史由本次有界执行持有；该参数不等于供应商的完整零保留承诺。
- 当前接口为文本和注册工具的有界任务，不因此宣称支持所有多模态、Realtime、任意远端工具、文件上传、供应商特有thinking参数或自动模型选择。
- 基础服务和普通员工包不安装模型SDK；选择可选Agent运行时才引入三种协议依赖。新增Anthropic及docstring-parser两项依赖，版本见两份锁文件。

## 验证证据与边界

三种协议及Anthropic两种鉴权头通过独立loopback HTTP实测：模型工具请求→真实OfficeClient→统一契约→模型续答，保留分页覆盖并核对Token累计。另测试重定向不跟随、错误不重试、不换协议、不回显秘密，以及宿主鉴权／组织信息隔离、客户端关闭和遥测禁用。

这些响应是明确的协议夹具，不是实际供应商推理。获准模型的真实效果、费用、限额、企业证书／网关兼容仍需按具体部署验收；不会把更换base URL等同于所有兼容服务可用。
