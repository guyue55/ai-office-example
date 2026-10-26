# 智能体运行、部署与验收

适用于`codex/agent`分支，尚未合并或发布。员工端、内部助手和外部AI宿主共用原有数据、权限和审计；这里只新增通用执行能力，不预装客户业务。

自定义模型的协议选择、端点、超时与鉴权头见[模型配置](AGENT_MODEL_CONFIGURATION.md)。

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

任务状态为等待、运行、待确认、结束、失败、停止、中断、结果待核实。模型文字是解释，`receipts`才是实际操作回执。确认等待占用任务总时限（默认120秒）；当前支持持久会话和有界续问，但活动任务没有后台无限等待或自动重启续跑。

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

## 全局助手与会话（Agent分支新增）

控制台顶栏入口打开非模态侧栏；窗口宽度小于1200时进入`/console/assistant/workspace`全页。侧栏／全页共用同一界面与服务端会话。原`/console/assistant`和任务详情保留为独立任务记录及兼容入口。普通服务未安装或未配置Agent时显示未启用，不影响原工作台。

发送第一条消息时创建会话；后续消息使用该会话。会话内一次一个活动任务；归档前需结束或停止活动任务，恢复后可继续。当前最多100个未归档会话；列表默认10项，可继续加载或通过接口使用有界数字分页。轮次默认最近10项，可加载更早消息，界面最多50轮并明确提示；这是有界展示，不表示已读取全部。会话单页最多500000字节；单轮超过128000字节、4000节点或24层时明确标记detail_truncated，界面提供独立详情链接，不能在未展示的预览上批准。

页面标签仅提供讨论位置，支持仪表盘、人员、角色、权限集、权限、审计、授权及助手；没有自动上传整页、表格或选中对象。可取消关联；每次发送固定页面标签，不随正在处理时的导航变化。草稿仅在当前页面内存，离开页面有未发送内容时浏览器会提示，未发送草稿不写浏览器存储。

关闭／收起不停止服务器任务；“停止任务”单独确认。刷新恢复会话ID，审批仍从服务器重读，不从浏览器恢复批准。等待真人决定仍受原任务时限约束。无回执写入不自动重发；审批冲突刷新后需重新核对。发送失败留存当前页面的稳定request_key，手动重试同一内容复用它；跨刷新后先看已受理任务，不盲目重发。

服务端最多读取前三个已结束的同指纹轮次（保留失败／中断／结果未知标记），以最多6000字节且不超过输入预算一半的文本参考补充本轮；不回放模型工具历史或审批。当前员工有效权限／安全纪元变化时，旧正文与回执隐藏，活动会话任务在下次工具／模型前阻断；无关员工权限调整不影响当前会话。该指纹基于当前基座动作权限及tokens_after；未来若新增项目级数据范围，必须把范围变化纳入此边界，不能仅增加前端筛选。

自动更新只查询状态，单次一个请求，活动任务2秒起、失败退避最多15秒；连续4次失败或120次读取后暂停，隐藏标签／收起停止定时，重新加载可继续。没有SSE、WebSocket或逐字流式。源代码只增加共用原生JS／CSS组件，无外部CDN、NPM运行依赖或云会话服务。

### 会话API与多端入口

| 操作 | HTTP | SDK | CLI／MCP |
|---|---|---|---|
| 创建 | POST `/api/v2/agent/conversations` | assistant_conversation_create | assistant-conversation-create／create_assistant_conversation |
| 列表 | GET同上 | assistant_conversations | assistant-conversations／list_assistant_conversations |
| 详情 | GET `/api/v2/agent/conversations/{id}` | assistant_conversation | assistant-conversation／get_assistant_conversation |
| 改名 | POST `/api/v2/agent/conversations/rename` | assistant_conversation_rename | assistant-conversation-rename／rename_assistant_conversation |
| 归档／恢复 | POST `/api/v2/agent/conversations/archive` | assistant_conversation_archive | assistant-conversation-archive／archive_assistant_conversation |

`assistant-start`文件可选添加conversation_id和context_page，原prompt／request_key用法保留。API／MCP仍拒绝额外message_history等字段。本人会话权限沿用agent.run，不能读取其他员工会话。新增Alembic0013向前升级，无会话旧任务保持可查；不要直接降级删表。会话归档保留执行与审计证据。

实现依据、任务范围与验收记录见[工作区计划](AGENT_WORKSPACE_PLAN.md)。

### 聊天交互补充

从头部“历史会话”打开可搜索浮层，列表展示标题、更新时间和归档标识，默认10项、按需继续加载。按标题搜索（q，最多100字符，按字面匹配，%／_不会当通配符）；更多会话操作中提供改名、归档／恢复、任务记录、展开工作区和手动同步。改名使用本人当前版本，冲突后核对最新版本，不自动覆盖。归档和搜索不物理删除历史。

输入框自动增高至180px；Enter换行，Ctrl／⌘+Enter发送，中文输入法组合期间不触发发送。每会话分别保留页面内存草稿；处理中允许起草，受理时不会清空后来输入的新内容。草稿不自动排队、不跨刷新保留。页面内其他会话有草稿时，离开也会提示。

侧栏宽度可拖动或聚焦分隔线后用左右键调整，Home／End选择最小／最大；360～640px，主工作区至少预留800px。保存的只是宽度偏好；小屏仍进入全页。消息更新保留未变化的任务节点；查看历史、选择文字或展开详情时不强制滚动，使用“回到最新”恢复跟随。

无需拖动时，在“更多会话操作”选择侧栏宽度：紧凑360、标准420、宽640像素，受当前视口上限约束；任意拖动值显示自定义。全页工作区不显示侧栏宽度设置。混合输入设备接入键盘后，图标聚焦仍显示中文提示，Esc可关闭。实施与验收边界见[可访问性补强](AGENT_CHAT_ACCESSIBILITY_FOLLOWUP.md)。

模型说明支持有限Markdown，包括列表、代码和表格。解析器markdown-it 15.0.3随服务端固定交付，无CDN；关闭HTML／图片，按允许标签创建DOM，仅接受HTTP(S)链接，不加载模型嵌入。正文超过32000字符或展示预算超限时回退纯文本。表格／代码在自身区域滚动。复制的是原始说明；浏览器不支持或拒绝剪贴板时打开共用详情弹窗，由本人选择复制，不伪装自动复制成功。

预览区区分账号、权限、数据确认／更正与授权操作，直接展示服务端提供的before／after摘要，最多16字段；未提供值明确标为未提供，不推断删除。完整预览与参数仍可展开，批准仍绑定原ticket和版本。状态仅来自queued／running／pending／write_inflight／cancel_requested与回执，不展示模型思维、百分比或无依据阶段。

普通只读工具回执默认折叠；覆盖不完整、还有后续页、排除记录和已知警告直接展示。写入回执直接显示，不以模型总结代替实际结果。结果未知时提示核实，不提供盲目重试写入。正常同步消息仅供读屏，异常和暂停继续可见；重新连接只读状态，不重发操作。

关联页面使用输入区标签，可移除或重新关联；只传页面枚举。新会话示例仅填入草稿。更多、收起、复制与说明采用共享图标及中文Tooltip；键盘聚焦同样显示说明，触屏无需悬停即可操作。Esc先关闭当前浮层并返回触发器；确认／详情继续使用统一弹窗。

详见[交互计划与验证](AGENT_CHAT_UX_PLAN.md)和[视觉实施账本](AGENT_CHAT_VISUAL_IMPLEMENTATION.md)。
