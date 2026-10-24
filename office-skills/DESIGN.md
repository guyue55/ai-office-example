# 技能目录契约

根SKILL.md负责路由；datacenter/data-access是唯一通用数据与管理入口，名称保留用于兼容CLI场景。企业专属业务建立独立技能目录并登记contracts.json，不预建空业务技能。

RULES.md唯一维护操作规则，认证、恢复、聚合和权限说明按需读取references。现有SDK拥有HTTP、认证、凭证协调和分页，技能不复制第二份实现。业务确定性计算程序属于对应技能，不混入服务权限模块。

发布单位为完整office-skills目录。安装器返回实际CLI路径与cli_args；命令示例office仅为默认名称，企业修改命令后必须使用返回路径，不猜测全局命令。

check_skills.py检查目录、内部链接、登记与CLI参数；behavior_cases是宿主回放用例，声明用例不代表AI已通过。客户端或授权变更需要行为回归与独立浏览器验证。
