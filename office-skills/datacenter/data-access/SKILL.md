---
name: data-access
description: 企业办公企业数据服务接入与基础管理。当需要浏览器登录、查本人权限、项目字段、受控条件聚合、上报候选数据、确认更正、人员角色权限、登录账号或客户端授权管理时使用。不处理任意SQL或未实现的业务审批。
metadata:
  version: "1.2.0"
  suite: office-skills
  requires: "可信MCP连接，或已安装office-client与契约库的命令环境"
---

# 企业办公数据与基础管理

先读[统一规则](../../RULES.md)。本技能复用当前员工的MCP／SDK／CLI，不另建认证，不直连数据库。服务器可选启用内部助手，按实际能力判断，不改变本技能的数据授权边界。

## 按目标选路径

| 目标 | 下一步 |
|---|---|
| 首次接入、地址配置／更换、登录失败、退出、安装或钥匙串问题 | 读[认证](../../references/authentication.md)；本地auth-status，在线whoami；有可信MCP时不另起CLI登录 |
| 人员、角色、权限集、开户重置、客户端授权与设备 | 读[管理手册](references/access-management.md)，核对当前员工权限与实例能力 |
| 创建／修改项目、字段发布、候选修改归档、确认数据导出 | 读[数据管理](references/data-management.md)，走对应封闭预览／执行 |
| 只读项目与记录 | capabilities→项目列表／字段版本→当前权限范围内记录；保持同一筛选分页 |
| 多条件/多维度统计 | 先核对aggregation能力与字段修订，读[受控聚合](references/aggregation.md)，默认本人范围，不猜业务指标 |
| 上报项目资料 | 按下方候选流程，不把录入称作业务确认 |
| 记录确认／更正／正式事实 | 按下方冻结规则，不读工作候选冒充正式值 |
| 超时、协议错误、权限或结果不确定 | 读[恢复](../../references/recovery.md)，不自动换账号或重放写入 |

所有CLI调用优先使用交付安装结果中的绝对cli及cli_args（--config），没有交付配置时才显式--service；全局参数在子命令前。缺地址先对话，首次／换址／连接异常按认证指引核验，不猜localhost。先用capabilities.transport_coverage选择实际入口，不根据外部技能猜flags或HTTP路径。

## 项目资料上报

1. 用真实项目ID、schema_revision读取字段类型、说明和必填规则。同名不同义要澄清，不自动新增字段。
2. 经获准渠道处理资料，敏感信息先脱敏；整理project_id、schema_revision、values和本次固定idempotency_key。十进制财务值按字段契约用字符串，不以浮点猜口径。
3. 保存到用户同意的安全本机工作文件（不进仓库、日志），执行preview查看摘要，展示完整文件、服务与项目供用户核对。
4. 精确批准后才执行submit --file同一文件 --confirm-digest摘要。失败重试保留原内容与原键；变化重新预览。submitted只是候选上报，不是财务或运营已核对。

## 记录状态

- record／records：当前工作记录；record-confirmed／export-confirmed：正式确认快照；record-history：确认版本历史。
- 确认前读当前version和完整候选，具备record.confirm的人明确决定。CLI确认文件含record_id/version；更正文件再含完整values/reason，先preview再record-confirm／record-correct。
- 已确认值冻结；更正待确认时正式查询仍返回旧确认值。不能把schema_revision、revision和并发version混用。
- 没有多级审批、自动对账、跨项目语义合并或无限批量；拒绝越权不意味着需要管理员账号兜底。

## 授权与密码

普通员工可管理本人授权，全企业管理需要独立权限。停用会结束同次浏览器登录相关客户端，归档保留审计，不支持复活旧凭证。先读目标、版本和影响后确认；cleanup待完成时如实说明身份清理仍需核对。

密码仅在受控浏览器处理。不要索取、代填、读取设密页面或截图存档；next_url只是本实例的受控操作入口，不是访问票据。

## 输出与边界

回答员工姓名／项目名、实际阶段和必要编号／关联号，说明未完成分页与阻断原因。示例：“已上报为待核对记录，尚未确认”“当前无管理其他员工授权的权限”。不称已投递通知；报表等业务能力需独立扩展。

本技能与共享规则一起按完整套件交付。MCP宿主OAuth与CLI钥匙串须分别实际验收；没有可用MCP或本机命令能力时停在安装引导，不伪造操作完成。
