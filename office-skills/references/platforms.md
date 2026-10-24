# 平台与安装边界

支持Python3.13／3.14。完整员工交付包包含CLI／SDK、通用技能、安装器与锁定依赖。macOS／Linux执行bash setup.sh，Windows执行powershell -NoProfile -File .\setup.ps1；AI优先阅读交付INSTALL.md。

安装器建立目录内.office/venv并返回CLI路径、cli_args和连接结果，不修改全局AI配置，不安装Docker或数据库。企业CLI名称由模板配置生成；示例office不保证是企业实际命令，应使用installation.json中的路径。

认证需要系统安全凭证后端。macOS使用钥匙串，Windows使用Credential Manager，Linux需要可用Secret Service；不以明文文件替代。员工本人完成浏览器登录和系统权限确认。

服务端地址由管理员提供，不推断成员工电脑IP。正式部署使用HTTPS和可信证书；局域网HTTP仅供明确批准的验收，仍有明文风险。客户端不自动安装企业CA或关闭证书校验。

离线doctor不读取凭证、不联网，成功不等于登录成功。安装连接核验、浏览器授权、目标AI宿主发现和真实MCP调用分别验收；测试矩阵配置不等于Windows实机已验证。
