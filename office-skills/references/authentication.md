# 认证、连接与环境恢复

## 准备与边界

Windows／macOS环境与安装见[平台说明](platforms.md)。首次接入可执行`office --service https://office.company.example doctor`做离线环境诊断，不读取凭证或联网；退出0不代表已登录或服务可达。

使用企业管理员提供的可信服务地址、客户端与契约库两份wheel。无MCP且无命令执行能力的宿主不能直接用本技能操作服务；说明前置，不绕到未受控浏览器操作。

CLI全局参数在子命令前：`office --service https://office.company.example <子命令>`。所有步骤使用相同地址。默认仅本机开发允许HTTP；正式跨终端须确认企业HTTPS及证书。仅经明确批准的局域网HTTP演示例外，使用交付包安装结果中的cli及cli_args（--config指向自动生成的连接配置），不得自行关闭保护或迁移地址；该例外只允许RFC1918 IPv4且明文传输，不用于正式业务。不要把示例域名当实际部署。

本地诊断：

```bash
office --help
office --service https://office.company.example auth-status
```

auth-status只访问当前服务对应的系统钥匙串，返回credential_state=missing/present/expired/invalid及online_verified=false，不联网、刷新或清理。present只说明本机记录未按本地时间过期；服务端已停用或撤权仍可能拒绝。缺少安全钥匙串时命令失败，不能建议文件／环境变量降级。

在线身份：

```bash
office --service https://office.company.example whoami
office --service https://office.company.example capabilities
```

whoami可能正常刷新令牌并由服务端登记当前授权，不描述为“绝无状态变化”。只想看本地状态时不要改用whoami。403不能用反复登录或管理员账号解决。

## 浏览器登录

默认：`office --service https://office.company.example login`。命令显示一行authorization_pending JSON，含authorization_url和等待秒数，然后尝试打开浏览器；授权成功后输出当前员工JSON。pending不是成功，最终需退出码0及员工结果。

支持后台进程／流式输出的宿主，可以：

```bash
office --service https://office.company.example login --no-browser --timeout 120
```

从本次进程输出提取authorization_url，展示给员工；保持该进程运行，员工须在**同一台电脑**的浏览器完成登录，成功后继续原始任务。URL含state和PKCE challenge等公开协议参数，不含访问令牌；只展示本次可信进程生成的URL，不保存到长期日志、仓库或自动复用过期链接。

**这不是跨设备／设备码授权。** 命令仍等待127.0.0.1回调；不要给远程电脑打开，也没有--code、--device-code或--no-wait参数。禁止要求员工把回调URL、code、密码或令牌粘回对话。

宿主无法保留后台进程、透传中间输出，或浏览器与进程不在同一机器时：不要发起会一直阻塞的登录。让员工在运行技能的同机终端完成正常login，或使用该宿主已验证的MCP OAuth连接；之后读回whoami。不自建后台daemon，不改身份服务器开启新协议。

端口被占用、用户拒绝、浏览器失败或超时后说明具体原因；确认旧进程已结束再发起新的授权，不能连续创建多个等待进程。未完成认证前不继续业务写入。

## 退出与权限

明确用户要退出当前服务后执行`office --service https://office.company.example logout`。它尝试结束业务授权、身份会话并清本机凭证；部分失败须核对授权列表，不能把清本机钥匙串等同全部远端已清理。

授权管理读data-access的手册；设备备注只是备注，不是硬件证明。同次浏览器登录的客户端可能共同失效。401走授权恢复；403按当前员工缺少的实际业务权限联系管理员，不扩scope、不冒用共享账号。

## 安装与更新

两份wheel由管理员交付；先核对来源、版本和摘要，再由获准的人执行安装。SDK必须存在于实际运行客户端程序的Python3.13/3.14环境；独立uv tool中的office命令不代表其他Python能import SDK。本套件没有自动更新器或在线安装端点，不编造升级命令。工作流结束后再按明确授权升级、重新自检。

## 客户端续期与本机凭证状态（2026-10-22）

`auth-status`只看本地，不会联网续期。`expires_at`是访问令牌到期时间，`credential_state=expired`不等于整个授权失效；结合`refresh_state`读取：available_locally表示本地记录有尚未到期的刷新期限，unknown表示没有期限信息，uncertain表示上次轮换结果不确定，reauthentication_required表示身份服务明确拒绝刷新。以上都不证明远端有效。

正常业务调用由SDK按需刷新，不需要员工手工更新令牌。不为保活定时运行whoami、capabilities或在线诊断。uncertain／reauthentication_required走正常浏览器重新授权，禁止读取凭证、清空其他登录或重复提交刷新请求来修复。

同一系统用户、同一服务地址的CLI共用安全凭证及协调锁；登录完成后的替换、刷新和退出均受协调保护。不同机器不共用锁，MCP宿主仍单独管理凭证。必须先更新所有共用该凭证的旧CLI／SDK，再启用严格轮换；不能只更新Skills文本。七天空闲／三十天最长是候选部署策略，只有身份服务实际配置并验证后才可如此说明。Web本地会话期限独立，不能用客户端期限解释它。


## 服务地址缺失、不可达与自然语言切换

没有可信服务地址／连接配置时，先询问企业实际根地址，AI填写公开.env或读取安装结果中的cli_args；不得默认连接localhost，也不能把员工PC的IP当服务器IP。当前CLI业务命令未提供--service／--config会明确停止并提示配置；doctor仍是离线环境检查，不证明连接。

首次接入、换地址或连接失败时使用connection-check：匿名核验就绪、授权发现和公开issuer，不读取系统凭证，不写入数据。失败后根据HTTP状态、网络或配置问题解释并收集必要信息，不自动换服务器、降级HTTP或关闭证书校验。通过后再用whoami核验身份和权限，普通业务请求不逐条重复检查。

自然语言“修改服务地址”按交付包INSTALL.md执行：先呈现差异及目标，检查新地址；通过setup_client.py --stage-connection仅从核验过的公开制品生成独立连接目录，安装并核验，再正常登录。AI负责文件操作，员工不必手改。旧连接、凭证与会话不复制／不静默清理，未获明确意图不撤销旧授权。宿主不能执行终端时说明能力边界。
