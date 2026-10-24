# 无秘密配置样例

`service.example.json` 只描述配置形态，地址和数据库主机均为占位值，不能直接作为可用部署。复制到实例自己的配置目录后，按部署环境填写地址，并通过 `OFFICE_CONFIG` 指向它；禁止将填写后的真实配置和秘密提交到 Git。

- 两个秘密字段只存文件引用，不存口令。相对路径以**解析后的配置文件所在目录**为基准；绝对路径保持原语义。配置软链先解析到真实文件，再确定目录。不展开环境变量，不按调用者工作目录寻找秘密。
- `secrets/app_password` 为运行库 office_app 的口令；文件权限仅当前服务用户可读写（0600）。迁移／离线引导使用独立 owner 配置及 owner 密钥，不得以 owner 启动服务。
- `secrets/web_session_secret` 为至少32字节的随机会话密钥，权限0600；由部署流程生成，不粘贴到聊天或版本库。若不启用 Web，可将 `web_session_secret_file` 设置为 null。
- 配置加载会检查会话密钥；数据库口令在显式创建引擎时读取。连接池和会话工厂位于 `app/infrastructure/database.py`，配置定义本身不导入数据库或HTTP/MCP实现。
- 连接预算按进程累计。本样例不代表多worker共享会话、内网域名、证书、反向代理或生产备份已经验收；CLI仍默认只监听本机，容器显式传入serve --host 0.0.0.0；可信Host仅增加public_url的主机名，不允许任意域名。

本机开发继续用 `scripts/dev_stack.py` 生成忽略目录中的实例配置与密钥；不将开发绝对路径复制成客户部署模板。


可选账号管理连接以独立的秘密引用配置；不配置则不开启管理连接，不影响原有登录：

```json
{"identity_management":{"client_id":"company-account-admin","client_secret_file":"secrets/identity_admin_secret","email_enabled":false}}
```

仅允许固定issuer下的企业realm，禁止master和通用上游代理。服务账号权限与初始化由技术管理员配置，不把管理口令发给员工；0600秘密文件不入库。email_enabled只有在SMTP及对应设密流程实际验收后才开启。实例密码、SMTP及管理连接不得写入此示例。

容器运行与维护秘密必须分目录只读挂载，运行容器不挂载owner配置；具体UID、权限、TLS及迁移顺序见[63](../../docs/ARCHITECTURE.md)。

资源配置包含数据库连接池／语句及锁超时、轻量登录保护和Web近期认证期限；具体字段及约束以app/infrastructure/config.py为准，不在文档重复维护默认值。身份期限修改须先完成客户端升级和独立验收，不在服务启动时自动覆盖身份服务策略。

本目录秘密文件0600等检查针对Linux服务部署，不适用于要求Windows员工安装服务端或模拟POSIX权限。Windows客户端凭证使用OS安全后端；企业私有CA及强制代理仍需分别核对浏览器和SDK信任，见[平台说明](../../office-skills/references/platforms.md)。
