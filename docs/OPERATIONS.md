# 模板常用操作与发版手册

本手册是操作顺序，不是自动执行授权。先确认仓库、分支、目标提交、目标实例与本轮授权。已明确覆盖同一目标和范围的授权可复用；准备源码、候选、远端推送、正式发布、实例升级和全局工具更新是不同范围。

## 1. 日常开发与验证

1. 阅读 AGENTS.md、CONTRIBUTING.md 和受影响模块说明；检查 `git status --short`、当前分支及 `git remote -v`，保留无关修改。
2. 使用 Python 3.13／3.14 与 `uv sync --frozen`。不自动升级锁定依赖或替换现有全局 CLI。
3. 按影响做针对性验证；完整代码门禁参见 CONTRIBUTING.md 与 .github/workflows/ci.yml。文档改动核对链接、命令、配置和差异即可，不能为文档检查启动真实业务操作。
4. 运行 `python3 scripts/configure_template.py --check`，核对品牌生成物和许可／NOTICE；发布源码与全历史不得带入客户、旧品牌或内部记录。
5. 核对暂存清单，使用 `type(scope): 中文说明` 提交；受保护 main 走分支／PR 和必需 CI，不绕过保护直接推送。

公共链路的核心检查入口：

```bash
uv run python scripts/check_architecture.py
uv run python scripts/check_capability_coverage.py
uv run python scripts/check_skills.py
uv run python scripts/export_contracts.py --check
```

改模型／业务码后，先运行不带 `--check` 的 export_contracts.py，从真实注册表生成并审核差异。数据库结构改动需配套迁移和已有数据的兼容检查；不能只修改 ORM 模型。

`scripts/verify_foundation.py` 提供编排验证，结果写入忽略的 runtime；它不是纯只读命令。`--live`、`--docker` 分别涉及隔离实例和临时容器，只在确认测试目标与相应环境后使用。不能把未运行、跳过或排除项算作通过。

## 2. 发版前准备

1. 确定版本、交付范围、兼容影响、迁移与回退策略。区分产品版本、API 路径、响应契约版本及数据库迁移号，不联动盲改。
2. 更新 VERSION、根项目和三个工作区 pyproject.toml 的产品版本及 `releases/v<版本>.md`；运行 `uv lock` 并审核锁文件差异，再 `uv sync --frozen` 更新本地包元数据，最后用 `scripts/export_contracts.py` 从真实模型生成 OpenAPI。版本校验以 prepare_release.py 的 PROJECTS 和实际发行名为准，不手改契约快照；锁定依赖发生非预期升级时先查明原因。
3. 完成相关门禁并提交全部发布源码；最终目标提交应通过远程 CI。运行 `python3 scripts/configure_template.py --check`，核对品牌生成物和许可／NOTICE；发布源码与全历史不得带入客户、旧品牌或内部记录。
4. 检查已有 Tag／Release；已经发布的版本不覆盖、不移动 Tag、不替换同名资产，修复使用新版本。检查远端失败时区分不存在、权限错误与网络错误。
5. Release 说明写清能力、变更、迁移／兼容、已知限制和验证范围；不粘贴凭证、客户日志、回调、内部目录或未脱敏截图。

以下本地命令以 Bash 为例，先读取实际版本，禁止依赖脚本的默认 v1.0.0：

```bash
task_tag="v$(cat VERSION)"
uv run python scripts/prepare_release.py --check --tag "$task_tag"
```

`--check` 仅核验版本相关一致性，不构建、不打 Tag、不发布。正式候选构建前再次确认源码已提交、无未跟踪程序、工作区来源明确。

## 3. 构建与验收候选

```bash
task_candidate="dist/candidate-$(date +%Y%m%d-%H%M%S)"
uv run python scripts/prepare_release.py --tag "$task_tag" --output "$task_candidate"
uv run python scripts/check_client_install.py --wheel-dir "$task_candidate"
```

候选目录必须是新的，不能覆盖旧制品。核对 release-manifest.json 与 build-manifest.json 的 source_commit 均为待发布 SHA；文件名、版本、源码与轮包内容及 SHA256SUMS 必须匹配。不要将 dist 下其他版本或额外交付配置混入资产。

再用 `uv run python scripts/verify_release.py "$task_candidate" --tag "$task_tag" --commit "$(git rev-parse HEAD)"` 核对资产白名单、来源及归档边界；下载后也执行同样核验。

在候选目录校验摘要：Linux 可用 `sha256sum --check SHA256SUMS`；macOS 可用 `shasum -a 256 --check SHA256SUMS`；Windows 用 Python hashlib 或对应的受验证校验工具。不能只验证文件存在。

确认三个工作区发行包均齐全，其中契约库实际名为 `office-api-contracts`。源码包、员工包、技能包的用途不同，员工电脑不交付服务器秘密。许可证范围按本仓库来源和第三方声明核查。

隔离客户端安装检查仅验证候选入口与内容，不证明真人登录、系统钥匙串、AI 宿主发现或业务上线。`scripts/install_client.py` 不指定 `--tool-root` 时会使用用户工具目录；不得当成无副作用的自检。

手动 Actions 候选流程是 release-candidate.yml。获准触发时，显式指定目标分支与版本；它先运行 CI，再构建候选 Artifact，不会创建 Tag／Release，也不会部署服务器。运行 ID 与最终结论单独记录。

## 4. Tag、Release 和镜像发布

仅在发布授权覆盖目标版本、提交及资产后执行。PR 合并可能改变 SHA：必须基于最终 main 的明确提交重新构建候选并等待该 SHA 的 CI，不能把合并前分支制品挂到合并后 Tag。

建议顺序：

1. 核实最终 SHA、通过的 CI、来源一致的候选和已审核发布说明。
2. 在该 SHA 创建附注 Tag，推送确切 Tag；先核验不存在，不使用强制移动。
3. 用 `gh release create` 的 `--verify-tag --draft --notes-file` 建立草稿。按已核对清单上传制品及 SHA256SUMS，不使用未经审核的 dist 通配符，也不使用覆盖上传。
4. 核对草稿 Tag、目标提交、说明、资产及仓库可见性。授权已覆盖正式发布时转为正式 Release，否则保留草稿。
5. 从实际 Release 下载到新目录，重新核验摘要、清单来源与安装入口。上传成功不等于下载验收已通过。

当前工作流不以 Tag push 自动发布；不必为了跑候选提前打 Tag。镜像发布是独立的 image-release.yml：仅 main 手动运行，须传入指向当前提交的已存在附注 Tag。先复用 CI，再构建 linux/amd64 和 linux/arm64，发布产品版本及提交 SHA 两个镜像标签，附 OCI 来源、SBOM 和 provenance。首次发布还需核实 GHCR 包已公开，完成匿名拉取及真实隔离容器运行后才能宣告镜像验收完成。发布镜像还需显式启用 ENABLE_IMAGE_RELEASE=true，并核验 image-release 环境审批；默认关闭。

Release、GHCR 镜像和企业服务升级彼此独立。发布制品不会自动更新员工电脑或企业服务器；不把 CI 通过写成 CD 已执行。

## 5. 新环境安装与员工交付

以 [INSTALL.md](../INSTALL.md) 和真实安装器为准：服务器与员工 PC 独立安装，不预设共享源码、虚拟环境或数据库账号。

- 服务器使用 deploy/install.sh；Linux 一般为 `sudo bash deploy/install.sh`，macOS Docker Desktop 验收为 `bash deploy/install.sh`。缺公开配置时按交互补齐，不猜服务地址，不读取已有秘密。
- 默认初始化专用数据库、分离账号、身份服务、迁移和必要权限；已有外部数据库只有显式配置时接入，不能扫描后自动认领。
- 同配置续跑不是升级；配置或镜像变化被阻断时，不删除标记、卷或凭证绕过。
- 员工交付使用完整 PC 包，或用 package_client_setup.py 从批准的 deploy/.env 与指定候选目录派生企业包。仅派发员工所需文件，先检查是否带入额外企业资料。
- 员工优先由 AI 阅读交付包 INSTALL.md，也支持 setup.sh／setup.ps1。用 `.office/installation.json` 记录的 cli 与 cli_args，不假定全局命令存在；换址先核对管理员提供的 URL 和匿名连接检查。
- 登录、首次改密及系统安全后端授权由本人完成。核对 whoami、capabilities；MCP 宿主与 CLI 不假定共享凭证。

## 6. 已有实例升级、故障与恢复

没有通用的一键无损升级承诺。先识别实例使用一键安装器还是手工 Compose 路径，不能将一种路径的维护命令套在另一种实例上。

1. 记录现有源码／镜像摘要、配置版本、迁移版本、服务入口和待升级目标；核验本轮维护窗口及影响范围。
2. 按实例类型准备业务库、身份库、公开配置与受限秘密的恢复材料，演练到隔离目标；不在现有库做破坏性试验。两个库各自的快照不自动构成跨库原子一致备份。
3. 审核迁移耗时、锁表、兼容及停写要求，按实际部署流程执行迁移与服务切换。应用回滚不等于数据库可降级；不盲目执行 downgrade。
4. 验证版本与就绪、浏览器登录、客户端连接／授权、实时权限与撤销、审计、关键受控读写及必要 UI；关键依赖未验证不得只凭健康检查宣称成功。
5. 失败时先保留现场，核对最后成功阶段及重复执行风险；按已演练方案回退，不能盲目重放写入、清库或删卷。

backup_check.py 与 recovery_drill.py 是本机开发环境辅助，后者有 `--backup-dir` 等参数；会创建备份／恢复实例或合成测试操作，不能当成对客户生产环境无副作用的通用备份命令。生产异机恢复和容量需另行验收。

## 7. 操作完成后的交付说明

分别报告源码／提交、CI、候选构建、Tag／Release、镜像、服务器部署和员工安装是否完成，以及实际证据和未验证项。只写已发生的步骤，不把上一次或其他提交的检查算到本次。

本仓库为公开通用模板；新企业实例默认保持私有。模板更新、公开范围变更和历史重建须单独明确范围，禁止导入旧来源仓库的历史。

显式HTTP演示实例迁址维护入口及备份／恢复边界见[实例迁址维护](INSTANCE_RELOCATION.md)。普通安装重跑仍拒绝静默换址或升级；迁址不替代本人重新登录验收。
