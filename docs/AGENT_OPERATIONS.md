# 智能体运行、部署与验收

适用于`codex/agent`分支，尚未合并或发布。员工端、内部助手和外部AI宿主共用原有数据、权限和审计；这里只新增通用执行能力，不预装客户业务。

## 运行形态

| 形态 | 模型在哪里调用 | 企业操作入口 | 确认入口 |
|---|---|---|---|
| 员工独立Agent | 员工电脑 | 原SDK→HTTP | 交互终端，明确输入“确认” |
| 服务端内部助手 | 企业服务进程 | 原异步Application | 控制台本人任务页／明确授权的客户端 |
| 外部AI工具 | 该工具的运行环境 | CLI／SDK／MCP | 宿主按技能规则取得真人确认 |

内部助手只使用启动人的身份；不会取管理员凭证替员工执行。每次工具以及下一轮模型请求前核对当前身份和实际权限。无关人员的权限修改不使本人任务误失效；实际撤权、停用和版本变化仍阻断操作。内部助手的工具白名单不包含启动任务和审批任务，避免模型递归委派或自我批准。

## 员工独立运行

使用Python3.13／3.14，从源码运行：

```sh
uv sync --project clients/agent --locked
uv run --project clients/agent python -m office_agent --help
uv run --project clients/agent python -m office_agent --config /secure/agent.toml --prompt '列出我有权访问的数据容器，并说明是否读取完整'
```

先通过原CLI完成浏览器登录；配置参考`clients/agent/agent.example.toml`。模型密钥独立于企业登录，使用环境变量名或`api_key_file`引用。指定文件时以文件为准，读取失败不回退其他凭证。相对路径以配置文件所在目录为准；POSIX文件须仅所有者可访问、不能软链，Windows由部署者设置ACL。不要将秘密值填入TOML或提交Git。

## 内部助手部署

普通镜像不安装模型依赖，原功能继续独立工作。需要内部助手时显式构建：

```sh
docker build --build-arg OFFICE_AGENT=1 -f deploy/Dockerfile -t office-agent:local .
```

全新安装器本地构建也可显式选择`OFFICE_AGENT=1 bash deploy/install.sh`；使用现成`OFFICE_IMAGE`时必须先确认该镜像包含Agent依赖，安装器不会替远程镜像增装。现有安装器继续完成数据库、身份、迁移和控制台安装，**不会自动选择模型、写入密钥或批准企业资料外发**。

内部助手配置由管理员在安装后完成，不能把只构建镜像称为助手已启用：

1. 将`deploy/config/agent.example.toml`放到实例`service/agent.toml`，填写与实例完全相同的`service_url`、获准模型URL和模型名称。
2. 将模型密钥放到实例`service/secrets/model_key`；宿主Linux下按服务UID/GID（默认10001）设置所有者与0600权限，目录保持受限。密钥不交给员工或AI。
3. 管理员确认资料处理政策后设置`approved_model_data=true`。该开关不代表系统能够识别或脱敏全部敏感资料。外部模型不应接收未经许可的个人或财务数据。
4. 在实例`service/service.json`添加`"agent_config_file":"agent.toml"`。该目录已只读挂载到容器`/etc/office`，无需新开端口或把秘密写进Compose环境。
5. 按原升级流程先备份、执行正式迁移至`0012_agent_runs`，再使用已构建的Agent镜像重启本实例。不要对已有实例重新执行全新安装以冒充升级。原数据库／身份服务不因模型配置而重建。
6. 控制台访问`/console/assistant`，或调用`assistant-capabilities`核对`installed/configured/ready`。依赖、密钥引用或配置缺失时拒绝受理503007，原数据功能继续可用；`ready`不代表供应商可用、余额充足或模型效果已验收。

代码启动入口固定单工作进程；当前不支持多个独立执行器共同接管任务。不要通过自定义多worker命令绕过此约束。后续需要横向扩容时应先实施执行租约和调度所有权。

## 任务与人工确认

所有HTTP接口复用原统一响应。模型要求和写参数不能改变配置、权限或待批状态：

| 操作 | HTTP | CLI | MCP |
|---|---|---|---|
| 配置就绪情况 | GET `/api/v2/agent/capabilities` | assistant-capabilities | assistant_capabilities |
| 受理任务 | POST `/api/v2/agent/runs` | assistant-start | start_assistant_run |
| 本人分页列表 | GET `/api/v2/agent/runs` | assistant-runs | list_assistant_runs |
| 本人详情 | GET `/api/v2/agent/runs/{id}` | assistant-run | get_assistant_run |
| 决定待批操作 | POST `/api/v2/agent/runs/decision` | assistant-decide | decide_assistant_run |
| 请求停止 | POST `/api/v2/agent/runs/cancel` | assistant-cancel | cancel_assistant_run |

启动仅接受`prompt`、`request_key`，返回202代表任务持久受理。网络失败重试保留原request_key；同键不同内容或配置冲突409。列表默认10条，支持数字分页、游标、搜索、状态和时间范围；详情严格限本人，不因管理员身份允许读取其他人的提示与结果。运行权限为可配置`agent.run`，业务权限仍逐项核验。

迁移一次性将`agent.run`加入内置员工权限集及已有`access.manage`权限集，更新权限版本。它不会在登录时重新补权；管理员后续可正常撤回。管理员应知悉模型调用会产生额外费用，必要时先撤下授权再逐步开放。

确认请求只接受任务ID、当前版本、服务器生成的proposal_id和布尔决定。旧版本、重复决定、他人任务、伪造提案与额外模型历史被拒绝。CLI/MCP宿主仍须取得真人的明确同意，不能从模型文字推断批准；协议本身不证明键盘前坐着真人。

任务状态为等待、运行、待确认、结束、失败、停止、中断、结果待核实。模型文字是解释，`receipts`才是实际操作回执。确认等待占用任务总时限（默认120秒）；首版没有跨天会话、后台无限等待或自动重启续跑。

取消不撤回已提交事务。在途写入丢失回执或进程故障时保守标为结果待核实，不自动重放。重启遗留活动任务标为中断；需要人工核对原数据和审计后另发任务。业务审计附可信`agent_run_id`，任务记录保留实际结果，不保存OAuth、供应商密钥或完整模型内部历史。

## 验收边界

本分支使用显式测试模型、独立临时PG18和隔离身份夹具验证框架安全与真实数据操作；本机独立浏览器验证页面和统一确认弹窗。供应商模型、真实企业身份、生产容量、企业资料脱敏方案和模型费用另行验收。跨企业运行、任意代码执行、任意SQL／URL调用、自动审批和自动跨Agent委派均不在本次实现范围。

远端CI只有推送后才会执行，不能把工作流配置当作CI通过。原v1.0.0实例、全局CLI及AI工具配置不会因当前分支代码变化自动升级。

## 发布资产补充

Agent分支的候选构建会额外生成office-agent的wheel及sdist，并在release-manifest中声明agent_runtime。原三个包和普通员工安装包保持独立，只有选择独立模型运行时才安装Agent依赖。校验器兼容没有该标记的历史发布；有标记时要求两份Agent制品齐全并核对摘要，不接受半套文件。这里是候选构建能力，不覆盖已发布v1.0.0资产，也不自动创建Tag或发布。

## 可选员工Agent交付包

从干净、已提交且与候选同源的源码运行：

```sh
uv run python scripts/package_client_setup.py --artifact-dir /path/to/candidate --service https://office.company.example --with-agent --output /path/to/new-agent-client
```

不加--with-agent保持普通员工包；添加时验证完整候选的Agent声明、来源及摘要，按独立Agent锁文件导出带哈希依赖，带入第三份轮包与agent.example.toml。服务器助手使用者无需安装本机模型运行时。员工按原setup.sh／setup.ps1安装，只写本交付目录的虚拟环境；安装结果agent_runtime记录本地能力，实际模型配置和用户登录另行完成。普通包和Agent包使用不同目录，不能原地静默切换。
