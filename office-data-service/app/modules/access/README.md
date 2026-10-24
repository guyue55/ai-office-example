# 人员与授权

当前已实现：Keycloak身份绑定、未开通身份留痕与认领开通、内置默认角色、员工启用/停用、角色、权限集、动作权限、实时撤权、最后管理员保护及同事务审计。业务权限在本模块判断，不能按角色中文名硬编码；令牌不冻结旧授权。

- `schemas.py`：纯输入模型、查询参数与管理操作局部规范化。
- `change_contracts.py`：封闭的预览／执行输入集合。
- `changes.py`：只读预览与事务内摘要核对，复用管理实现，不调用应用总注册表。
- `service.py`：当前员工授权与授权修订锁，初始化登记。
- `catalog.py`：已实现权限的中文名称、描述和类别，初始化与页面共用。
- `management.py`：创建、列表、版本化关联替换，及重名检查；不代管身份产品的密码。
- `provisioning.py`：绑定与认领的一次性默认角色关联，内置数据缺失时阻断开通；不补权、不自动恢复撤权。
- `inspection.py`：当前页批量计数、直接关联、配置权限合集及反向使用者分页。
- `outputs.py`：上述操作的明确输出契约。

列表搜索限制100字符并转义SQL通配符；详情需要`access.read`。更新仍需要`access.manage`、当前版本及授权管理员保护。Web/API共用Application，普通员工查看本人权限不额外要求管理读取权限。

创建后的配置顺序为：权限集选择能力→角色关联权限集→员工关联角色。新建角色/权限集不会自动授予员工权限；新业务员工绑定/认领时可默认关联普通员工角色，详见docs/50。身份凭证由身份产品管理；统一账号编排可经受控身份端口完成开户、关联、资料及安全操作，业务库保留issuer／subject关联，不保存密码。

动作权限以catalog.py的DEFINITIONS为唯一目录，当前19项，覆盖人员授权、审计、姓名维护、账号生命周期、授权会话与项目记录；本人／全部范围按各操作检查。项目、字段、部门等精细范围仍按已确认设计逐步实施，不能以目录或页面展示冒充能力。更完整目标见[15 数据模型与权限设计](../../../../docs/ARCHITECTURE.md)；当前体验及验收见[43 控制台优化](../../../../docs/ARCHITECTURE.md)。

编排已位于application/service.py；协议响应位于entrypoints/errors.py。至少保留一名同时具备access.read和access.manage的启用员工，两项权限可以来自不同角色／权限集。

账号生命周期现由account_schemas/outputs/operations声明，application/account_service与account_runner编排，infrastructure/identity/keycloak提供受控异步端口；统一控制台、HTTP/MCP与SDK接入。临时密码只经再次认证后的Web安全响应展示，不进入公共工具契约。实际范围及验证边界见docs/53。
