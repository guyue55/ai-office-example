# 客户端 CLI／SDK

包名office-client，Python导入office_client，公开类OfficeClient。CLI与SDK是同包的两个层次：cli.py／cli_commands.py负责命令，client.py及auth.py等负责连接、协议与安全凭证。客户端不包含数据库驱动。

实际CLI名称来自根template.toml的client.command，由配置工具生成构建入口与product.json。默认命令office；安装后以installation.json中的cli与cli_args为准。改命令后须重新构建安装，不静默覆盖已有工具。

CLI无参数或-h显示中文帮助，不联网或读取凭证；业务命令输出JSON，错误保留退出码。doctor为离线检查，connection-check匿名检查入口，whoami验证实际身份。浏览器授权由本人完成，凭证只入系统安全后端。

连接与恢复见[认证](../../office-skills/references/authentication.md)和[安装](../../INSTALL.md)。业务写入先预览确认；有界分页和聚合必须报告覆盖，不将首屏或截断结果称全量。源码修改不代表员工现有安装自动更新。
