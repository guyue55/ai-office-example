# 企业 AI 办公基座模板

将企业AI工具与内部数据／管理服务连接起来的独立部署基座。员工可通过AI工具、技能及CLI／SDK访问受控服务。`codex/agent`分支正升级为员工端与服务端均支持AI的智能体基座：已实现员工端独立运行、服务端持久任务与Web确认入口，以及外部CLI／SDK／MCP接入。客户端和模型不直连数据库。

## 具备的能力

- 浏览器PKCE授权、系统安全凭证存储、续期协调与授权停用；员工、角色、权限集及实时权限判断。
- 人员、账号、角色、权限、客户端授权与审计控制台；共用筛选、分页、弹窗及权限显隐。
- 项目数据容器、动态字段修订、候选上报、确认冻结、更正版本、历史、归档及受控批量操作。
- 有界条件查询、多维聚合及已确认数据导出，结果附结构与覆盖信息；没有行业指标和跨数据集语义映射。
- Docker一键部署、数据库初始化及迁移；员工独立安装器，macOS／Linux与Windows入口。
- 通用CLI／SDK和数据访问技能；统一接口契约、错误码与HTTP／MCP适配。

PostgreSQL18＋pgvector镜像仅预装vector扩展，不默认启用向量表或模型。Keycloak负责身份，业务库负责实时权限。模板不包含特定行业流程、指标口径、模型服务或成品业务应用。

## 为新企业生成项目

修改根目录[template.toml](template.toml)，公开品牌和CLI命令仅在该文件配置。用Python3.13／3.14运行：

```sh
python3 scripts/new_project.py --output /path/to/new-enterprise
```

目标目录必须不存在。也可通过`--config /path/to/enterprise.toml`使用另一份配置。工具仅导出已提交模板源码，不复制Git历史、运行数据或凭证；允许template.toml本次配置修改，其他源码变更须先提交。新项目先执行`git init`并提交源码，才能使用来源可追溯的交付打包。

直接复制本目录使用也可：不要复制.git、.venv、runtime、dist或任何秘密；修改template.toml后执行`python3 scripts/configure_template.py`。有实例运行足迹时拒绝原地变更，必须从干净模板生成新项目。`--check`用于只读核验。

## 安装与使用

服务器人工安装、员工AI引导和脚本安装见[安装指引](INSTALL.md)。服务端依赖Docker／Compose，默认创建专用数据库并初始化；员工电脑不需要Docker。正式部署使用HTTPS；HTTP仅用于明确批准的局域网验收。

[配置与身份边界](docs/CONFIGURATION.md) · [架构与扩展](docs/ARCHITECTURE.md) · [开发与验证](CONTRIBUTING.md) · [来源和许可](docs/SOURCE.md) · [验收记录](docs/ACCEPTANCE.md)

当前验证证据以验收记录为准，不以原项目或CI配置代替模板验收。模板源码按公开发布流程维护；企业实例、业务资料和秘密应留在私有环境，不随模板发布。

AI 参与源码开发或企业项目初始化时，先阅读 [AGENTS.md](AGENTS.md)；员工执行任务仍从技能入口和安装指南开始。

GitHub 仓库与 CI／候选制品／镜像发布边界见 [自动化说明](docs/GITHUB.md)。

## 许可证与商用

自有源码及文档采用 [Apache-2.0](LICENSE)，允许商用与修改。对外分发时保留许可证、适用的版权／归属声明与 NOTICE，修改过的文件需显著注明变更；可在产品文档的第三方声明中注明来源：[ai-office-example](https://github.com/guyue55/ai-office-example)。不强制界面展示 Logo 或广告，也不授予商标使用权。第三方依赖遵守其各自许可，具体边界见 [来源说明](docs/SOURCE.md) 和 [第三方声明](THIRD_PARTY_NOTICES.md)。

常用开发、发版准备、安装升级与恢复操作见 [操作手册](docs/OPERATIONS.md)。

Agent分支：[总体方案](docs/AGENT_PLATFORM_DESIGN.md) · [调研](docs/AGENT_RESEARCH.md) · [员工端运行设计](docs/AGENT_DESIGN.md) · [执行计划](docs/AGENT_PLAN.md) · [可选运行时安装](clients/agent/README.md) · [自定义模型配置](docs/AGENT_MODEL_CONFIGURATION.md)。部署及操作见[运行指南](docs/AGENT_OPERATIONS.md)，全局侧栏／会话方案与验收见[助手工作区计划](docs/AGENT_WORKSPACE_PLAN.md)。Agent分支支持控制台全局入口、共享侧栏／全页会话、服务端有界续问和原审批闭环；[聊天交互计划与验收](docs/AGENT_CHAT_UX_PLAN.md)补充会话草稿隔离、受限Markdown、复制降级、搜索改名和可调侧栏。[视觉实施与验收](docs/AGENT_CHAT_VISUAL_IMPLEMENTATION.md)记录紧凑头部、可搜索历史浮层、共享图标／焦点、输入与回执分级，以及独立浏览器到临时PG的真实审批写入测试。这不会自动升级既有交付；框架隔离验收不等于真实供应商已经验收，原v1.0.0交付不因分支开发而自动升级。
