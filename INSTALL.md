# 服务端与员工端安装

## 1. 准备企业项目

通过README中的生成器建立新项目。仅修改template.toml不能假定已生效，须运行配置工具或使用new_project生成器。`python3 scripts/configure_template.py --check`必须通过。

公开企业品牌在template.toml；服务入口与数据库环境在deploy/.env；秘密由安装器随机生成或引用安全文件。不要合并为含密码的大配置，不向AI发送初始化密码。

## 2. 服务器一键安装

Linux服务器准备Docker Engine、Compose插件、Bash与flock（util-linux）。macOS验收使用Docker Desktop。缺少环境时安装入口提供提示；不自动改系统权限、组或证书信任。

```sh
sudo bash deploy/install.sh
```

macOS使用`bash deploy/install.sh`。没有deploy/.env且有终端时进入交互：确认HTTPS访问地址、监听范围、端口、实例目录和管理员信息；按回车接受默认值。监听默认`lan`，即`0.0.0.0`，当前服务器所有内网接口均可访问；`local`只允许本机访问；只有多网卡隔离时才选择固定内网IPv4。HTTP验收实例IP变化时由`./manage.sh start`或`restart`自动检测并保留数据迁址。默认HTTPS需要已有证书。只有显式选择并确认后才允许RFC1918局域网HTTP验收。

默认创建独立PostgreSQL18业务库与身份库、分离数据库账号、Keycloak、网关及服务端，运行迁移、内置权限与首管理员初始化。密码随机生成，首次登录资料在结果指示的受限credentials目录，由本人查看并首次改密。服务就绪不等于已登录。

也可先`bash deploy/configure.sh`或填写deploy/.env.example中的公开配置。已有外部PG18支持连接、账号与秘密文件引用，安装器会初始化必要数据库结构与权限，不额外启动PG容器。完整字段见deploy/.env.example；没有授权的数据库不能拿来测试。

管理脚本的运行目录、锁、活动实例、备份目录和默认超时可在`deploy/manage.env`中设置，模板为`deploy/manage.env.example`；`./manage.sh config`可查看解析后的最终值。管理配置不包含任何秘密。

重复运行同配置安装不删除卷、不重置密码。配置／镜像变化会阻断静默升级；生产升级、改址与身份标识迁移应另行规划备份和恢复，模板不提供无条件覆盖。

日常安装、构建、升级、启动、停止、重启、状态、检查、IP变化恢复、数据库备份、容器重建和容器网络清理统一使用项目根目录`./manage.sh`。新环境使用`install`，显式构建使用`build`，已存在镜像升级使用`upgrade`；`start/restart`只管理已安装实例，不会静默构建。`start/restart`自动检测活动内网地址；HTTP验收实例地址变化时保留数据迁址，多个候选会要求选择登录主地址；`status/check/plan`只读；`stop`保留全部数据；`recreate/down/upgrade`需要预览和精确确认串，且拒绝删除卷、镜像或实例数据。完整命令、退出码与安全边界见[服务统一管理](docs/MANAGEMENT.md)。

## 3. 制作员工交付包

开发机器需要uv。新生成项目先初始化Git并提交，再执行：

```sh
uv sync --frozen
uv run python scripts/prepare_release.py --tag v1.0.0 --output dist/candidate
uv run python scripts/package_client_setup.py --config deploy/.env --output dist/employee --artifact-dir dist/candidate
```

若使用已发布完整候选中的PC zip，解压后直接填写企业地址即可。包名office-client是稳定内部包名；实际员工命令由template.toml的client.command生成，安装器返回真实路径。

## 4. 员工电脑

只提供员工交付目录，不提供服务器源码、秘密或数据库连接。优先给AI提示词：

> 阅读本目录INSTALL.md，安装CLI和完整技能套件。服务器地址向我确认，不用本机IP推断；检查连接后核对身份，未登录时引导我本人浏览器授权。不要安装Docker，不修改全局AI设置，不读取服务器秘密或回显凭证。

人工也可执行`bash setup.sh`或`powershell -NoProfile -File .\setup.ps1`。脚本准备局部uv／Python和venv；缺企业地址时交互询问，非交互缺配置明确停止。安装结果在.office/installation.json，使用cli与cli_args执行whoami、capabilities；不要猜全局命令或复制令牌。

首次授权、系统钥匙串／凭证管理器确认由员工本人完成。AI宿主技能发现取决于宿主支持，安装成功不等于已自动注册所有AI工具。通知等外部动作使用宿主连接器，单独授权验收。

显式HTTP演示实例迁址维护入口及备份／恢复边界见[实例迁址维护](docs/INSTANCE_RELOCATION.md)。普通安装重跑仍拒绝静默换址或升级；迁址不替代本人重新登录验收。

监听范围、Docker防火墙边界及IP变化后的处理见[服务统一管理](docs/MANAGEMENT.md)。不要在公网网卡存在时仅凭`lan`模式宣称端口已隔离。


## 5. 选择普通服务或含 Agent 的服务

在交付 `deploy/.env` 中设置 `OFFICE_AGENT=0`（默认普通数据服务）或 `OFFICE_AGENT=1`（安装助手运行依赖）。交互向导也提供同一选项。`OFFICE_IMAGE` 留空时从源码构建对应变体；填写已发布镜像引用时，本地不存在会先拉取，再固定实际镜像 ID。Agent 镜像必须同时选 `OFFICE_AGENT=1`，否则在建库前明确停止。不要把安装运行依赖当成模型已配置或供应商已可用。

环境变量可用于未在文件设置的 `OFFICE_AGENT`／`OFFICE_IMAGE`；若与文件的非空值冲突则停止，不静默覆盖。首次解析使用固定摘要的轻量 Python 容器，不要求宿主安装 Python，不执行 `.env` 中的 Shell 表达式；首次联网需能拉取基础镜像。旧 JSON 配置不增加这两个公开字段，可用环境选择，推荐新交付使用 `.env`。

Agent 管理员在“模型设置”完成保存、测试、应用。模型根密钥由安装器单独生成，重入不更换。备份、升级和恢复须同时保留业务／身份数据库、实例配置、身份及会话秘密、模型加密根密钥；详细边界见[模型操作与恢复](docs/MODEL_CONFIGURATION_OPERATIONS.md)。不要将秘密文件放入源码、候选包或员工交付目录。
