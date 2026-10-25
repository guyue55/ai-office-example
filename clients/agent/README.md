# 独立员工端 Agent

可选的Python任务运行时，复用`office-client`，不连接数据库、不改变服务端权限。
本目录独立锁定模型依赖；普通客户端和服务器不需要安装它。Python3.13／3.14。

从完整模板源码中安装和验证：

```sh
uv sync --project clients/agent --locked
uv run --project clients/agent python -m office_agent --help
uv run --project clients/agent pytest clients/agent/tests -q
```

将`agent.example.toml`复制到本机受控目录，填写企业地址、获准的模型端点、模型名和模型密钥的环境变量名。
确认企业数据政策后，才将`approved_model_data`设为true。配置文件不保存密钥；不要把企业OAuth令牌放进模型变量。
先通过原客户端的浏览器登录授权，再执行：

```sh
uv run --project clients/agent python -m office_agent --config /path/to/agent.toml --prompt '列出我可以访问的项目，说明结果是否完整'
```

任务可读取数据、受控聚合和提出变更；写入前终端展示冻结内容，真人输入“确认”才执行。
非交互终端默认拒绝所有写入，不提供自动批准开关。每次命令是一个独立任务；进程退出后不保留会话或待批提案。
最终`explanation`是AI解释，`receipts`是工具回执；候选不等于确认、账号处理中不等于完成。

默认最多8次模型请求、20次工具调用、120秒及32000个已计量Token。暂停恢复累计预算，不自动重放写入。
超时或取消不能撤回已经发出的HTTP请求；SDK线程会等待在途请求收尾并关闭，程序退出可能略晚于任务时限。
身份或权限变化后旧提案无效。所有操作仍由服务端实时授权和审计。

首个适配器支持OpenAI Chat Completions协议端点，模型须支持工具调用。协议兼容不代表所有供应商已验收。
不配置外部trace exporter，不保存prompt／工具全文；已知秘密字段被屏蔽，但不承诺自动识别所有敏感业务信息。
模型出口必须经企业批准；不默认复用其他AI工具的订阅或凭证。

完整设计与验收边界见根目录`docs/AGENT_DESIGN.md`、`docs/AGENT_PLAN.md`。
