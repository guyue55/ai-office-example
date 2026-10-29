# 企业办公服务统一管理

入口：项目根目录`./manage.sh`。服务器日常启动、停止、重启、状态、检查、地址和日志均通过这一脚本，不再维护单独的重启入口。

## 命令

| 命令 | 作用 | 会修改状态 |
|---|---|---|
| `start` | 启动Docker、同步地址、启动容器并等待健康 | 是 |
| `stop` | 停止全部容器，保留数据库、卷、镜像和配置 | 是 |
| `restart` | 同步地址后完整重启全部容器 | 是 |
| `status` | 展示实例、Docker、地址、容器、端口、健康 | 否 |
| `check` | 核验配置、容器、监听、健康和OIDC issuer | 否 |
| `address` | 展示活动内网地址和登录主地址 | 否 |
| `logs` | 查看指定容器日志 | 否 |
| `instances` | 列出发现的服务器实例及活动标记 | 否 |
| `plan down` | 只读预览删除容器和网络的影响 | 否 |
| `plan recreate` | 只读预览无状态容器重建影响 | 否 |
| `recreate` | 保留所有卷，只强制重建gateway和service | 是 |
| `down` | 删除容器和项目网络，保留卷、镜像、配置和秘密 | 是 |
| `backup` | 备份两个数据库、配置和必要秘密 | 是，仅写备份目录 |

```bash
./manage.sh status
./manage.sh check
./manage.sh start
./manage.sh stop
./manage.sh restart
./manage.sh address
./manage.sh logs --service gateway --tail 200
./manage.sh instances
./manage.sh plan down
./manage.sh plan recreate
./manage.sh backup
```

## 通用选项

```text
--instance <目录>  指定实例；也可设置 OFFICE_INSTANCE_DIR
--ip <IPv4>        指定当前活动的登录主地址；用于start/restart/address
--service <名称>   logs目标：service、gateway、identity、postgres
--tail <行数>      logs显示行数，默认100
--follow           logs持续输出
--timeout <秒>     启动、重启与健康等待上限，默认120
--confirm <值>     down/recreate确认串，必须为“项目名:动作”
--output <目录>    backup父目录，必须是绝对路径
-h, --help         查看总帮助
```

每个命令也可查看专用说明：

```bash
./manage.sh help restart
./manage.sh status --help
./manage.sh logs --help
```

## 实例选择

脚本扫描`runtime`中包含`installation.json`、`compose.json`和服务端配置的实例，不扫描迁移备份、客户端安装目录或隐藏准备目录。

- 优先使用`--instance`或`OFFICE_INSTANCE_DIR`；
- 其次读取`runtime/.active-server-instance`；
- Docker可用时选择正在运行的唯一gateway实例；
- 只有一个已安装实例时自动选择；
- 多个实例且无法唯一判断时，交互显示目录并选择；非交互环境必须传`--instance`。

选择结果写入`runtime/.active-server-instance`，该文件只保存本机实例路径，不是凭证。

只有`start`、`stop`、`restart`、`recreate`、`down`会更新活动实例记录。`status`、`check`、`address`、`instances`、`logs`严格只读，不创建或修改该文件。`plan`只读；`backup`只写指定备份目录。

## 启动、停止和重启

`start`先检查Docker引擎，再检测活动IPv4和刷新gateway配置。实例已经完整运行且健康时保持原状，不无故重启容器；只有配置更新、健康异常或容器缺失时才启动或重载。

容器启动使用Docker Compose原生`up -d --wait --wait-timeout`等待运行和健康条件，之后继续执行外部`/health/ready`、端口和OIDC核验。自定义等待只用于最终业务入口检查。

`stop`只停止容器，不关闭Docker Desktop，不删除卷、数据库、镜像或配置。停止后`status`显示停止状态，并以非零退出码提示当前不健康。

`restart`执行相同的地址同步，然后重启postgres、identity、gateway和service。若HTTP验收实例IP发生变化，会在普通重启前先走[实例迁址维护](INSTANCE_RELOCATION.md)。

`start`、`stop`、`restart`使用`runtime/.manage.lock`目录锁。第二个管理进程会明确失败，不能并发停止或重启同一套服务；命令正常退出或收到INT/TERM时释放锁。锁内PID已不存在时自动清理失效锁，避免异常退出永久阻断后续操作。

## 维护、重建与数据保护

管理脚本不提供`purge`、`delete`、`delete-data`或`uninstall`。请求这些命令、`down --volumes`、`-v`、`--rmi`、`--delete-data`时直接拒绝并返回2，持久数据、用户、权限、审计、密钥和备份不会被删除。

`down`固定执行不带`-v`和`--rmi`的Compose删除：删除容器和项目网络，保留命名卷、镜像、实例目录、秘密和备份。执行前显示容器、网络和数据库卷，执行后再次核对数据库卷仍存在；所有运行镜像也必须仍可检查。

`recreate`只强制重建`gateway`和`service`。它要求postgres和identity正在运行，不重启数据库；执行前后核对数据库卷标识，任何变化都会立即报错。配置或环境变量变化优先使用本命令。

`down`和`recreate`都必须经过二次确认：先执行`plan`查看影响，再输入精确的项目名加动作，或非交互显式传入确认串：

```bash
./manage.sh plan down
./manage.sh down --confirm office-93e42ff1ec92:down
./manage.sh plan recreate
./manage.sh recreate --confirm office-93e42ff1ec92:recreate
```

`backup`把业务库、身份库、实例配置、网关配置、必要秘密和数据库卷标识写入权限为700的目录，文件禁止组和其他用户访问；两个数据库dump通过`pg_restore --list`校验，并提供SHA-256清单。备份不会自动覆盖、轮换或删除旧目录。

## 地址检测与多地址

macOS从当前活动网络接口读取IPv4；Linux优先使用`ip -4`。只保留RFC1918内网地址，排除loopback、Docker网桥、容器虚拟接口、常见VPN和链路本地地址。

- 当前登录IP仍活动时保持不变，避免无意义重启Keycloak或结束会话；
- 只有一个新地址时自动选择；
- 检测到多个新地址且旧地址消失时，列出接口和IP要求选择；
- 非交互环境必须用`--ip`；脚本不会猜；
- gateway监听`0.0.0.0`，全部当前内网地址都能接收连接；
- Keycloak一个realm只固定一个规范登录地址，其余地址由gateway308转到主地址。

因此`localhost`、`127.0.0.1`和未选为登录主地址的网卡地址可以进入入口，但会转到同一登录来源，避免Cookie、OIDC回调和令牌issuer不一致。

## Docker恢复

macOS下第一次连接Docker失败时，脚本会启动Docker Desktop。等待后仍不可用，则终止Docker Desktop与backend桌面进程并重新打开；镜像、容器、卷和实例秘密保留。

Linux下仅当当前用户是root且存在systemctl时尝试启动系统Docker，不自动重启已运行的Docker服务。

脚本不能修复磁盘损坏、镜像缺失、未完成迁址、数据库损坏或应用启动错误；这些情况保留现场并报告检查失败。

## 状态与检查

`status`只读。它不自动启动Docker，适合操作员快速查看；有容器或健康异常时返回1。

`check`只读并核验：

1. 实例配置文件；
2. Docker引擎；
3. Compose配置；
4. postgres、identity、gateway、service是否运行；
5. gateway是否发布在`0.0.0.0:端口`；
6. `/health/ready`；
7. HTTP验收实例的OIDC issuer是否与配置一致。

全部通过返回0；任一项失败返回1。参数、实例或环境错误返回2。

`--timeout`只接受正整数秒，默认120。超过上限时启动或健康检查失败并保留容器现场，便于查看日志。

## 输出与日志

输出按“实例、地址、Docker与容器、检查结果”分节，成功、注意和失败使用明确中文前缀。容器命令的原始进度保留在对应步骤下，避免与配置摘要混在一起。

日志默认读取service最近100行：

```bash
./manage.sh logs
./manage.sh logs --service identity --tail 50
./manage.sh logs --service gateway --follow
```

日志可能包含业务请求的追踪号和错误信息；不要把终端日志直接粘贴到外部系统。配置文件、数据库密码、模型密钥不会由管理脚本主动打印。
