# 服务端部署

企业安装入口`bash deploy/install.sh`，Linux一般由有Docker权限的管理员执行。无配置且有终端时自动询问公开参数；默认HTTPS，已有证书由管理员提供。HTTP仅为显式批准的RFC1918局域网验收。

模板品牌先由根template.toml及配置工具生成，Docker构建阶段只读核验；服务地址、端口、实例目录、已有数据库等运行参数在deploy/.env中。实例秘密独立存储，员工电脑不获取数据库凭证。

默认创建专用PostgreSQL18＋pgvector、业务／身份库与分离账号，启动Keycloak、网关及服务，执行迁移、内置权限与管理员引导。仅预装vector，不默认开启向量业务。已有外部数据库使用公开配置及受限秘密引用，不额外创建PG容器。

完整配置键与示例以[scripts/delivery_config.py](../scripts/delivery_config.py)和[配置说明](config/README.md)为准。`bash deploy/configure.sh`可生成配置；缺少Docker／Compose或权限时给出说明，不自动安装系统软件或更改信任。

同配置重跑保留卷、密码和数据；不提供静默升级、换址或身份迁移。正式上线应验证HTTPS、备份恢复、目标容量及实际终端。初始化密码由本人查看受限first-login.txt并首次改密，不发给AI。

开发辅助`uv run python scripts/dev_stack.py up`仅用于独立本机测试，使用固定本地端口、随机合成测试账号和独立卷，不用于员工部署。
