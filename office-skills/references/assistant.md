# 使用已启用的智能助手

本参考适用于支持助手命令的Agent分支，不能假设原v1.0.0交付已经具备这些入口。先使用安装结果中的CLI／可信MCP核对实际能力，不在员工电脑安装服务端或填写模型密钥。

如果当前AI工具可以直接完成任务，继续复用数据与管理能力即可；不要再启动另一个助手重复同一任务。只有用户希望交给内部助手、或明确选择独立Agent时，才使用相应入口。不得递归委派、自批写入或用多个Agent绕过预算与权限。

## 服务端助手

1. `assistant-capabilities`或MCP `assistant_capabilities`检查配置就绪；未启用时说明需管理员配置，不索要模型／数据库秘密。
2. 用户明确要求启动后，准备含prompt、稳定request_key及可选conversation_id、context_page的UTF-8 JSON文件，执行`assistant-start --file <文件>`，或者MCP `start_assistant_run`。全局CLI连接参数仍在子命令之前。
3. 202仅表示已受理。`assistant-run <任务ID>`读取状态和实际回执；`assistant-runs`分页查询本人的任务，默认10条，保留原筛选和游标。不要轮询刷屏或把首屏称作全部。
4. `awaiting_confirmation`时完整展示服务器保存的操作参数与预览。用户没有明确批准时不调用决定接口。拒绝或批准都使用原任务ID、最新version和原proposal_id，不替换写参数，不提交模型历史。
5. 得到本人明确决定后，`assistant-decide <任务ID> --version <版本> --proposal-id <提案ID> --decision approve|deny`，或MCP `decide_assistant_run`。决定受理后还要查实际结果，不称为已写入成功。
6. 用户要求停止时，读取当前版本，执行`assistant-cancel <任务ID> --version <版本>`，或MCP `cancel_assistant_run`。停止不撤回已完成的操作。

结果待核实、中断、网络超时不自动重开任务，不用新request_key重复提交。先核对回执和实际数据。原任务及审批仅本人可访问；管理权限不能当作读取他人提示词的授权。

## 员工独立Agent

这是可选模型运行包，原CLI不必包含它。只有本人明确希望在本机运行并已有获准的模型配置时，使用`agent --agent-config <配置> --prompt <任务>`。缺少依赖时给出安装提示，安装／全局升级仍遵守统一规则，不能自动借用AI工具的模型密钥。

调用独立Agent时仅使用安装结果的绝对cli，不叠加常规cli_args、--config或--service：独立TOML已包含service_url。先核对它与获准企业地址一致；不能为了运行模型而静默更换企业。API协议可明确选择openai-completions、openai-responses、anthropic-messages，不按模型名猜测。

独立运行的终端确认与服务端任务审批是两条不同路径，不上传本机暂停历史伪装为服务器提案。不提供自动批准或免确认选项；非交互终端写入默认拒绝。

## 会话与控制台工作区

只有用户希望服务器助手继续同一主题时，使用本人会话；当前AI宿主可直接完成任务时不再委派。`assistant-conversation-create --title <名称>`创建，`assistant-conversations`默认10项读取本人活动会话（--archived看归档）；`assistant-conversation <ID> --limit 10`有界读取，存在next时不能称为全部。MCP对应create/list/get_assistant_conversation。原assistant-start文件添加conversation_id即可续问，context_page仅允许已登记页面标签；不能提供任意URL／DOM、message_history或批准记录。

单会话有活动任务时不再发起新任务；用户可选择另一个会话。本人有效权限变化时旧轮次会隐藏，不从本地记录恢复旧内容绕过限制。归档／恢复使用`assistant-conversation-archive <ID> --version <版本>`，恢复加--restore；MCP archive_assistant_conversation。归档不删除记录、不取消活动任务。与外部AI宿主交互时，启动内部模型仍需用户明确选择，审批仍由人确认。

Web全局助手与`/console/assistant/workspace`共用这些操作；收起不取消，浏览器刷新不能自动重发任务或决定。
