# Agent 分支调研与证据

调研日期：2026-10-25。基线：main `94942ce`；工作分支：`codex/agent`。本文区分外部事实、项目判断与未验收项，不将文档可用等同于产品可用。

范围更新：用户已确认服务端和员工端均支持AI，初轮只做员工端的优先实验不再是整体交付范围。补充的Pi／BeefTV、AG-UI、A2A取舍及新架构见[智能体总体方案](AGENT_PLATFORM_DESIGN.md)。

## 1. 要解决的问题

现有模板把企业数据、身份权限、审计及控制台连接给员工的 AI 工具；技能负责告诉宿主如何调用 CLI／SDK／MCP。它没有独立的模型执行循环，因此缺少在无既有 AI 宿主时执行自然语言任务的入口。新增 Agent 的价值是提供可复用、可测试的任务运行能力，而不是替换原有服务、建立另一套权限或增加行业业务。

当前源码核对：

- `clients/python/src/office_client/client.py` 已有浏览器授权、系统凭证、读操作、预览、提交、错误恢复和有界聚合。
- `office-data-service/app/application/` 是操作编排入口；HTTP／MCP共用服务端实时权限及审计。
- `office-skills/RULES.md` 已明确外部内容不可信、数字由程序算、写前确认、未知结果不重放、凭证不得进入模型。
- 现有明确约束是服务端和Web没有AI。改变这个边界需要单独明确，不因创建分支而静默改变。

不改变方案：继续让现成 AI 工具承载执行，维护成本最低，对已经使用这类工具的员工仍有效。修好技能和工具覆盖无法替代独立模型运行时；反过来，独立运行时也不必取代技能。

## 2. 一手来源与版本

以下版本由 GitHub release API、PyPI JSON 和官方文档交叉读取。Python范围为包元数据声明，不能证明所有平台已实际运行。GitHub星数只用于发现，没有作为选择评分。调研没有执行外部仓库脚本。

| 项目 | 本轮版本证据 | Python声明 | 特点与适配判断 |
|---|---|---|---|
| [Pydantic AI](https://github.com/pydantic/pydantic-ai) | v2.61.0，2026-10-24 | >=3.11 | 类型化参数、依赖注入、多模型、延迟工具和用量限制，与已有Pydantic/Python栈接近；优先验证slim及所需provider，避免全量依赖 |
| [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) | v0.24.0，2026-10-15 | >=3.10 | 轻量执行循环、审批、会话、测试和追踪；可接其他模型，但默认追踪出口和各provider兼容须显式核验 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 1.2.17，2026-10-24 | >=3.10 | 持久状态图、暂停恢复很强；跨多步骤长流程确有需求时有价值，目前引入图和checkpoint生命周期成本较高 |
| [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) | Python core 1.23.0；仓库latest为dotnet-1.26.0，不能混称Python版本 | >=3.10 | Python/.NET与多Agent编排，覆盖广；当前纯Python员工端没有要求采用其全部栈 |
| [smolagents](https://github.com/huggingface/smolagents) | v1.26.0，2026-05-29 | >=3.10 | 代码执行Agent有优势；企业数据管理并不需要默认开放生成代码和shell，相关隔离成本不应默认引入 |
| [Deep Agents](https://github.com/langchain-ai/deepagents) | 0.7.32，2026-10-23 | >=3.11,<4 | 规划、子Agent、文件与上下文管理的完整harness；当前并无这些能力同时必需的证据，默认采用容易扩大权限与运行面 |
| [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) | v0.2.174，2026-10-24 | >=3.10 | 可复用成熟Agent宿主，但供应商／宿主依赖强；不适合作为本项目中立核心的默认绑定 |

包依赖条目含optional extras和平台条件，不能直接拿条目数比较安装重量。真正选型还需要锁定解析、构建和安装验收。当前倾向Pydantic AI只是项目判断，不是任何官方对本项目的背书。

## 3. 应吸收的机制

### 3.1 受控工具优于任意执行

模型只提出工具名和结构化参数；注册表限制可用操作，服务端再次验证身份、权限、结构、版本和预算。不给任意SQL、HTTP目标、shell、Python执行器、动态代码导入和自动安装工具。工具注解只是提示，不是权限边界。

[MCP安全最佳实践](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)明确讨论token转传、混淆代理和SSRF。复用既有SDK认证，不把企业token当作模型参数，不允许模型从工具响应选择新服务地址。MCP作为已有受控入口继续保留，不必为了新增Agent再创建代理层。

### 3.2 人工确认必须位于模型之外

[Pydantic AI deferred tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)提供暂停等待审批机制，并指出客户端提交的审批不能成为对不可信客户端的服务端授权边界。[OpenAI HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)同样覆盖暂停和恢复。项目应复用服务端预览摘要、版本与幂等：模型不可自己提供批准；终端展示具体变更，真人批准同一目标和内容后才执行。拒绝不执行；批准后内容改变须重新确认；结果未知先回读，不能自动再执行。

### 3.3 持久化不自动等于安全恢复

[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)区分线程checkpoint与长期store；内存checkpoint不跨重启。[interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)说明恢复需要稳定线程标识和持久checkpointer。项目必须区分会话文本、已批准意图与真实业务结果，不能通过重放checkpoint重做写入。默认会话只在进程内保留；未来持久化需另评估加密、用户隔离、过期、版本迁移和清理，不能随意落完整业务数据。

### 3.4 预算、取消和错误属于主链路

[Pydantic AI agents](https://pydantic.dev/docs/ai/core-concepts/agent/)提供请求和工具调用限制；还应设输入、工具结果、输出、墙钟和并发上限。token计数不等于费用上限，服务商未返回usage时更不能声称精确成本。429、模型拒绝、坏参数、工具错误、401/403、版本冲突、用户取消与未知写入结果分别处理，不靠无限重试自愈。

### 3.5 提示词与真实权限分离

[OWASP提示注入说明](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)指出RAG和提示词不能消除提示注入。工具响应、员工资料和上传文件都是数据；禁止其中的命令改变服务地址、凭证、工具清单或审批策略。读取结果进入模型本身也可能泄露企业数据，因此模型目的地、最小化范围和脱敏必须在任务前确定，不能仅依赖模型自行判断。

### 3.6 可观测性默认不外传

[OpenAI tracing](https://openai.github.io/openai-agents-python/tracing/)提供内置追踪及敏感字段控制。无论选哪个框架，都不默认配置外部trace exporter，不记录prompt全文、原始工具数据、令牌或回调。最小事件只含run_id、操作名、阶段、时长、用量和受控错误分类；服务端审计仍为操作事实来源。

### 3.7 技能与运行时是互补关系

[Agent Skills规范](https://agentskills.io/specification)定义SKILL.md及按需资源。技能是说明与能力材料，不是安全沙箱、审批系统或模型执行循环。继续让已有AI宿主安装office-skills；独立Agent复用同一规则和工具语义，不创建第二套业务指南，也不自动运行下载技能中的脚本。

## 4. 候选结论和反证

初次调研时的历史实验方向（已被用户后续两端AI要求替代）：独立员工端Python Agent包，Pydantic AI作为可替换的运行时适配，复用office-client。当前服务端内部助手与外部接入方向见AGENT_PLATFORM_DESIGN。原技能路径继续工作。先单Agent，确有独立上下文／权限／并行收益后再考虑多Agent。

反证：如果需求实际是Web对话、24小时共享调度、集中模型密钥、多人审批与跨设备恢复，则员工端进程不是充分方案，应重新评估独立Agent服务、身份委托、任务持久化和部署。不能把它偷偷放入已有数据服务进程。

开放验证项：精确版本API与Python3.13/3.14兼容、依赖审计、安装体积、供应商兼容、无外发默认、真审批／取消、工具覆盖、独立打包、服务端拒绝链路和真实模型端到端效果。测试模型只证明程序控制流，不证明真实模型效果。

本轮实验补充：Python3.13.7隔离安装`pydantic-ai-slim[openai]==2.61.0`，解析26个包，`uv pip check`通过，`pip-audit==2.9.0`对实际固定依赖扫描未发现已知漏洞。已验证暂停不写、拒绝不写、批准执行一次和请求上限。未验证Python3.14、Windows、真实provider或企业实例。当前版本不再接受旧示例中的`Agent(instrument=False)`；实验使用实际签名，不配置遥测exporter。无已知漏洞只代表本轮审计数据库覆盖，不能保证不存在漏洞。

部分文档旧路径经web读取不可达；已用官方重定向后的文档及Context7固定v2.0.0示例辅助核对。该示例不能冒充v2.61.0运行验证，采用前必须核对实际安装版本源码。

## 自定义协议补充（2026-10-26）

用户要求支持OpenAI Chat／Responses及Anthropic配置。本轮核对Pydantic AI官方模型与兼容API文档、固定2.61.0实际类签名、本地BeefTV的agent-host/canvas-turn.mjs，以及Anthropic官方2026-10-21鉴权迁移说明，采用三种显式api而不自动猜测／失败切换。具体来源、取舍、配置与验收边界统一见[模型配置](AGENT_MODEL_CONFIGURATION.md)。新增Anthropic2.3.0与docstring-parser0.18.0；未调用真实供应商或读取宿主凭证。
