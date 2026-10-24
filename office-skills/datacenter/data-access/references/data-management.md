# 数据管理与报表取数

只调用capabilities声明的功能。所有变更必须先展示完整before/after并等待用户确认。项目管理需要project.manage；候选修改/归档需要对应数据管理权限和读取权限。并发版本过期、归档项目、已确认冻结等由服务端同一业务层拒绝。

## 项目/字段/候选管理

文件示例（均是封闭操作，不能换成任意HTTP路径/SQL）：

```json
{"operation":"project.create","payload":{"name":"<项目名称>","idempotency_key":"<本次创建固定UUID>"}}
```

```json
{"operation":"project.update","payload":{"identifier":"<项目ID>","payload":{"version":1,"name":"<新名称>"}}}
```

```json
{"operation":"schema.publish","payload":{"identifier":"<项目ID>","payload":{"expected_revision":0,"fields":[{"id":"amount","label":"金额","type":"decimal","required":true,"description":"必须经业务确认的口径和单位"}]}}}
```

```json
{"operation":"record.update","payload":{"identifier":"<记录ID>","payload":{"version":1,"values":{"amount":"10.00"}}}}
```

```json
{"operation":"record.archive","payload":{"identifier":"<记录ID>","version":1}}
```

新增项目优先使用 `project-preview <项目名称>`（SDK：`preview_project`），工具自动生成请求标识。如果已安装的旧版CLI没有 `project-preview`，使用既有 `data-preview`，在JSON中先生成并固定UUID作为key；不要要求员工手填。把返回的 `change` 保存为变更文件，再展示给用户确认并用该文件执行 `data-apply`。MCP使用现有数据预览／确认工具，并在 `project.create.payload` 提供本次固定 `idempotency_key`。同一次重试必须保留key和内容；改名后复用旧key会409，不得遇到超时就换key另建项目。旧版不带key的调用保持可用，但不承诺创建重试幂等。

其他变更执行：`data-preview --file <文件>` → 展示完整内容/影响 → 用户确认 → `data-apply --file <同一文件> --confirm-digest <服务端摘要>`。MCP对应`preview_data_change/apply_data_change`。

preview在数据库保存点内模拟既有业务调用并回滚，不保存业务变更；创建预览不返回真实ID。apply重新预演核对摘要，再执行真实变更。摘要绑定实例、用户、权限版本、操作及对象旧值。409重新查询、预览和确认，不覆盖；网络不确定先回读，不自动重建项目。该预演只允许纯数据库操作，不用于身份产品调用或通知。

字段发布版本与项目version、记录version各有用途，不能混用；旧记录按原schema_revision解释。同名字段不能假定同义。已确认值不直接编辑或归档，改走更正→重新确认。

## 已确认数据导出

`export-confirmed <项目ID> --max-records 1000`；MCP：`export_confirmed_records(project_id,max_records)`。

返回scope（own/all）、取数起止时间、最新确认快照列表。数据库单条查询只选每记录最新确认版本，更正待确认时仍返回旧确认值。每项目默认1000、最大5000条；超过限额报错，不静默截断。它不是无限批量导出或多维统计引擎。

单项目查询内一致不等于多个项目统一快照。当前多项目报表必须保留各源取数窗口、确认版本，源间窗口超出业务允许范围时应阻断；正式统一时点由客户确认后再实施。取数时间不能代替业务stat_date，不能推断字段口径。

导出同时限制记录内容1.5MiB及99000个内容节点；服务端小批流式读取。超过条数、字节或节点预算返回HTTP422／业务码422015，不返回部分数据，不能用截断输入制作报表。当前没有分段导出接口。
