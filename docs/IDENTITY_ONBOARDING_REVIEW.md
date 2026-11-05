# 员工开通与身份绑定设计复核

本轮为代码与官方资料审查，不改变登录、授权或账号托管策略。

## 结论

当前实现不是“登录即自动分配普通员工角色”，而是“管理员开通时默认分配，可取消”。新建员工成功后已经建立账号绑定及所选角色，无需再次绑定或认领。常规界面应优先保留“新建员工”和“关联已有账号”；手填登录标识不适合作为同级日常入口，待认领则应定位为未开通访问的异常处理。以下界面收敛建议尚未实施。

## 代码证据与二次复核

| 问题 | 真实实现 | 复核位置 |
|---|---|---|
| 登录是否自动开通 | 根据 issuer／subject 查已有人员；缺失、停用或安全冻结返回403，不创建人员或授予角色 | `modules/access/service.py` 的 `Access.create`；`application/service.py` 在拒绝后登记未知身份 |
| 默认角色何时授予 | 手动绑定、认领调用 `assign_role`；账号新建／关联在预览中解析角色并在执行中写入 | `modules/access/provisioning.py`、`management.py`、`account_operations.py`、`application/account_runner.py` |
| 新建员工是否已绑定 | 执行写入 Employee、IdentityLink 和 EmployeeRole；开户还包含凭证与启用等后续阶段，不能把预览或部分成功称为完成 | `application/account_runner.py` |
| 已开通员工还会待认领吗 | 队列排除已有 IdentityLink；留痕也跳过已有绑定，包括已停用员工 | `management.py` 的 `pending_identities`；`service.py` 的 `trace_pending_identity` |
| 三条绑定路径是否等价 | 搜索关联产生 `managed=True` 绑定；手动与认领不设置该标志，按模型默认属于非受管绑定 | `application/account_runner.py`、`management.py`、`infrastructure/models.py` |
| 是否能直接移除旧入口 | 账号管理是可选配置；未配置时搜索／开户不可用，但受信身份登录和管理员绑定仍可使用 | `application/account_service.py`、`entrypoints/web/console.py` |
| 队列数量是否完整 | 当前只返回最早50条，无总数和完整分页；界面条目数不能当待处理总数 | `management.py` 的 `pending_identities` |

上述源码路径均相对 `office-data-service/app/`。只看页面文案容易把“首次开通默认授予”误读为“首次登录默认授予”；本轮分别检查了验证、拒绝留痕、预览、执行和列表过滤链路。

## 官方资料与适用边界

1. [OpenID Connect Core 1.0，5.7 Claim Stability and Uniqueness](https://openid.net/specs/openid-connect-core-1_0.html#ClaimStability)：稳定身份依据为 issuer 和 subject 的组合，不应以姓名、邮箱或用户名自动合并员工。此规范支持身份关联键选择，不要求用户手填该键。
2. [Keycloak Server Administration，First Login Flow](https://www.keycloak.org/docs/latest/server_admin/index.html#_identity_broker_first_login)：支持多种首次登录策略；自动关联账号需考虑注册来源、身份资料与信任范围。文档警告在允许任意用户名／邮箱注册的环境中使用 AutoLink 的危险。此处讨论外部身份提供方与 Keycloak 的关联，是安全设计参考，不是本项目业务人员开通的直接实现。
3. [Microsoft Entra，What is automated app user provisioning?](https://learn.microsoft.com/en-us/entra/identity/app-provisioning/user-provisioning)：区分单点登录与应用人员预配，并讨论手工、自动预配、JIT及SCIM。可据此选择明确的入职和离职流程，不能据“支持SSO”推导“应对所有登录者授予业务权限”。本项目本轮未增加JIT或SCIM。

资料通过联网读取官方正文核对。企业是否允许所有统一身份账号进入本应用，属于产品与组织准入策略；上述资料不能替用户决定。

## 建议的界面收敛

- 日常路径：新建员工完成开户与基础授权；已有账号通过搜索核对后关联。角色可另行调整，不为已完成员工再要求一次认领。
- 手动绑定：从人员页同级动作移出，定位为身份接入／运维高级入口；仅在未配置账号管理连接或明确采用非受管绑定时提供。删除整个后端能力前，应先确定模板是否继续支持这种部署方式。
- 待认领：改用易懂的“未开通访问记录”或“待开通账号”，有记录时才提示；从人员页进入集中处理，避免将未知身份自动描述为正式员工或授权申请。当前记录不包含申请意图与审批流程。
- 处理入口应核对显示名、稳定身份、访问时间及角色，保留明确确认；优先复用现有预览／执行能力，但先区分是否接管账号维护，不能把非受管绑定静默转换为受管绑定。
- 若建设独立队列，应补服务端分页和准确总数，而不是直接把现有最多50条包装成完整队列。
- 若采用“所有合资格员工首次登录自动开通”，需单独定义可信员工范围、触发条件、一次性授予、撤权不补回及停用不重建；不能只删除认领UI或把默认角色授予放进每次登录。

## 验证与限制

本轮运行 `tests/test_review_regressions.py` 和 `tests/test_web_console.py` 中 pending／binding_forwards 相关离线用例：8通过、62未选中。覆盖拒绝后留痕、留痕失败不改原拒绝、已绑定身份跳过、页面转义／日期及绑定表单显式角色选择。

另外阅读了 `tests/test_pending_identity_live.py`，其用例包含未知身份拒绝、并发登记、管理员认领及默认角色开关，但本轮没有执行该活体用例。未连接真实人员账号、读取凭证、修改授权或改变在用实例。不能将本轮离线检查称为真实开户或完整身份链路验收。
