# 一键检测 IP 并重启实例

入口：从项目根目录执行`./restart.sh`，等价于`bash deploy/restart.sh`。

## 行为

1. 检查Docker引擎；macOS引擎停止或卡住时先启动Docker Desktop，仍未恢复则重启其桌面进程。镜像、容器、卷和实例秘密保留。
2. 自动寻找有`installation.json`、`compose.json`及服务端配置的实例；优先选择当前正在运行的gateway实例。多个实例运行时要求选择，非交互环境用`--instance`指定。
3. 检测当前活动的RFC1918 IPv4，排除loopback和常见Docker、虚拟网卡。
4. HTTP验收实例若旧登录IP仍活动则继续使用；只有一个新地址时自动选择；多个新地址且旧地址消失时列出接口并要求选择登录主地址。
5. IP变化时调用现有迁址工具：预检、两库备份、更新Keycloak/issuer/受众、更新端口为`lan`、结束旧来源会话并验证新入口。
6. IP未变化时不改写身份；刷新网关、启动缺失容器并重启gateway/service。
7. 等待`/health/ready`通过，输出本机和主地址。

```bash
./restart.sh
./restart.sh --instance runtime/ui-live-20261026/instance
./restart.sh --ip 172.16.170.83
```

`--ip`只接受当前活动内网IPv4。非交互脚本在发现多个地址且旧地址已消失时必须提供该参数，不能自动猜测。

## 多地址与Keycloak

gateway监听`0.0.0.0`，所以所有当前内网地址的端口均可达。Keycloak一个realm只有一个规范issuer，不能把多个IP同时当成等价登录来源而保留hostname校验。因此：

- 脚本选择一个登录主地址；
- 新生成网关会把访问非主地址的请求308跳转到主地址；
- 主地址用于Keycloak、回调、Cookie和客户端；
- 旧主地址消失时，脚本必须选择一个仍活动的新地址；不能使用已消失地址。

这样用户通过本机、localhost或其他当前网卡进入时会转到同一登录来源。动态信任任意Host头会削弱Keycloak防护，不采用。

## Docker恢复边界

macOS等待约45秒仍无法连接Docker引擎后，脚本会终止Docker Desktop与backend桌面进程并重新打开。它不删除镜像、卷、实例或数据库。若macOS拒绝该操作，脚本明确退出，需人工在Docker Desktop的Troubleshoot中重启。

Linux服务器仅在root且系统有systemctl时尝试`systemctl start docker`，不自动重启已运行的系统Docker服务。

脚本不能修复磁盘损坏、镜像缺失、数据库损坏、未完成迁址或服务自身启动错误；这些会保留现场并报告健康检查失败。
