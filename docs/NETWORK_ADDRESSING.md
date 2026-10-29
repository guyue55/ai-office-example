# 网络监听、稳定入口与IP变化

日期：2026-10-29。状态：当前安装器和迁址工具已按本文实现，测试与真实实例验收另行记录。适用于企业内网单机Docker部署。

## 1. 设计结论

服务监听范围与用户登录地址必须拆开。监听决定“哪些本机网卡可接收入站连接”；登录地址决定Keycloak issuer、Web回调、Cookie来源、客户端受众和令牌身份。把服务器IP同时用于两者，会让一次普通DHCP变化同时破坏端口发布、登录回调和已安装客户端。

- 默认监听`lan`，实际映射到`0.0.0.0`，覆盖服务器当前和以后取得的局域网网卡；不记录或依赖某一个IP。
- `local`仅映射到`127.0.0.1`，用于只在服务器本机访问的维护场景。
- 显式私网IPv4继续保留，供多网卡、隔离网络或明确安全边界使用。
- HTTPS正式入口必须使用稳定内部DNS名称，不能把字面IPv4设为长期登录地址。
- 对外URL、issuer和受众保持稳定；IP变化时只更新DNS的A记录，不重建容器，也不改Keycloak或员工配置。

## 2. 为什么不能只改`0.0.0.0`

Docker Compose在没有`host_ip`时默认绑定所有接口。[Docker官方文档](https://docs.docker.com/reference/compose-file/services/#ports)同时警告：这会把服务暴露到宿主机所有网卡，并可能绕过宿主防火墙；如果服务器另有公网网卡，不能把它当成本机私有开关。

因此本项目仍然只发布gateway，不暴露PostgreSQL、Keycloak和业务服务容器。默认`lan`的可访问范围由Docker发布决定，实际企业边界由以下控制共同形成：

1. 服务器只接办公网或通过VLAN分段；
2. 主机防火墙／Docker `DOCKER-USER`链只允许办公网段访问入口端口；
3. 云主机安全组或上游网络ACL同步限制；
4. 服务自身的身份、CSRF、Host校验、权限与审计继续生效。

只改监听不会修复Keycloak。Keycloak默认不能信任任意Host头动态生成自己的公开URL；[官方hostname文档](https://www.keycloak.org/server/hostname)明确要求固定hostname，完全动态模式会关闭hostname验证能力。

## 3. 配置模型

正式HTTPS示例：

```dotenv
OFFICE_DEPLOYMENT_MODE=https
OFFICE_PUBLIC_URL=https://office.company.internal
OFFICE_LISTEN_ADDRESS=lan
OFFICE_CERTIFICATE_FILE=/secure/tls/fullchain.pem
OFFICE_PRIVATE_KEY_FILE=/secure/tls/privkey.pem
```

`OFFICE_LISTEN_ADDRESS`接受：

|值|发布地址|用途|
|---|---|---|
|`lan`（默认）|`0.0.0.0:端口`|企业单机、单一办公网段|
|`local`|`127.0.0.1:端口`|仅服务器本机访问|
|内网IPv4|`指定IP:端口`|多网卡或严格接口绑定|

不直接接受`0.0.0.0`字面量，避免把“全接口”误填成普通服务器地址；应显式选择`lan`。

## 4. 本机与localhost

`lan`模式在网络层同时接受loopback连接，因此服务器浏览器访问`127.0.0.1:端口`可以到达端口。但Keycloak登录身份仍属于`OFFICE_PUBLIC_URL`，不是`localhost`。

稳定域名部署中，推荐服务器本机也访问`https://office.company.internal`，并通过公司DNS、本机hosts或split-horizon DNS把它解析到`127.0.0.1`。这样本机和局域员工使用同一登录来源。

不能把`localhost`设为全员公开URL；也不能让浏览器在局域网IP与`localhost`之间无提示切换，因为issuer、Cookie和OAuth回调会随之改变。

## 5. IP变化后的标准处理

使用稳定DNS：

1. 给服务器配置DHCP保留或静态地址，减少无谓变化；
2. IP变化后更新内部DNS `office.company.internal -> 新IP`；
3. 等待DNS缓存过期，验证浏览器与新入口、OIDC发现和CLI匿名连接检查；
4. 不重装、不重建数据库、不改已安装员工包。已登录会话通常保留，因为浏览器访问的公开来源没有变化。

没有稳定DNS的HTTP验收实例仍受历史限制：公开URL是IP，更换IP必须使用显式迁址工具同时更新服务配置、Keycloak和旧会话。迁址能力保留作为兜底，不作为正式架构。当前HTTP演示也仅接受RFC1918 IPv4，不冒充HTTPS稳定入口。

## 6. 验收边界

需要分别验证：

- `lan`模式在服务器当前各IPv4和loopback均可连接，`local`只允许loopback；
- 正式HTTPS拒绝字面IP公开地址；
- Keycloak issuer、OIDC发现、Web回调及CLI受众始终使用稳定名称；
- IP改变后仅更新DNS即可访问，不要求重跑安装器；
- 显式迁址仍能保留用户、角色、数据和权限，并结束旧来源的会话；
- 防火墙从外部验证端口只对批准网段开放；容器端口配置不是网络隔离的唯一证据；
- 本机访问、局域网访问、员工客户端授权和刷新后的身份保持一致。

本地单测只能证明生成配置正确；必须在隔离实例验证容器端口、DNS、TLS、Keycloak发现和真实无密钥连接，不能把单测通过当成企业网络已经上线。
