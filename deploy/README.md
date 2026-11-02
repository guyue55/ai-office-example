# 服务端部署

企业安装入口`bash deploy/install.sh`，Linux一般由有Docker权限的管理员执行。无配置且有终端时自动询问公开参数；默认HTTPS，已有证书由管理员提供。HTTP仅为显式批准的RFC1918局域网验收。

模板品牌先由根template.toml及配置工具生成，Docker构建阶段只读核验；服务地址、端口、实例目录、已有数据库等运行参数在deploy/.env中。实例秘密独立存储，员工电脑不获取数据库凭证。

默认创建专用PostgreSQL18＋pgvector、业务／身份库与分离账号，启动Keycloak、网关及服务，执行迁移、内置权限与管理员引导。仅预装vector，不默认开启向量业务。已有外部数据库使用公开配置及受限秘密引用，不额外创建PG容器。

完整配置键与示例以[scripts/delivery_config.py](../scripts/delivery_config.py)和[配置说明](config/README.md)为准。`bash deploy/configure.sh`可生成配置；缺少Docker／Compose或权限时给出说明，不自动安装系统软件或更改信任。

管理脚本默认值见[manage.env.example](manage.env.example)，复制为`deploy/manage.env`后生效；配置只含运行目录、锁、备份、超时和Docker应用名，不保存秘密。

安装器生成的实例把PostgreSQL、Keycloak与网关镜像固定到多架构索引摘要（`@sha256:...`），amd64与arm64服务器解析到各自平台，避免同名标签随时间漂移。升级基础设施镜像需同时更新`deploy/install.sh`与`deploy/installer/layout.py`的摘要，并重跑安装验收；`docker compose pull --dry-run`可先确认摘要可解析。

同配置重跑保留卷、密码和数据；普通安装不提供静默升级或换址；显式HTTP演示迁址使用文末维护入口。监听取值`lan`、`local`或明确内网IPv4，默认`lan`；IP变化时由`./manage.sh start/restart`检测并保留数据迁址。`lan`会发布到所有宿主网卡，必须由主机防火墙／Docker `DOCKER-USER`链限制办公网段，不能把容器端口配置当作完整网络隔离。正式上线应验证HTTPS、备份恢复、目标容量及实际终端。初始化密码由本人查看受限first-login.txt并首次改密，不发给AI。

开发辅助`uv run python scripts/dev_stack.py up`仅用于独立本机测试，使用固定本地端口、随机合成测试账号和独立卷，不用于员工部署。

可选内部Agent使用`OFFICE_AGENT=1`构建，模型配置、受限秘密引用、迁移与启用步骤见[智能体运行指南](../docs/AGENT_OPERATIONS.md)。默认安装不自动启用模型，原服务功能独立可用。

显式HTTP演示实例迁址维护入口及备份／恢复边界见[实例迁址维护](../docs/INSTANCE_RELOCATION.md)。普通安装重跑仍拒绝静默换址或升级；迁址不替代本人重新登录验收。
