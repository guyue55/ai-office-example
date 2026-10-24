# HTTP 适配

已实现受保护v2业务接口、统一契约响应、严格输入和错误转换。

- `routes.py`：身份、管理、项目和记录路由，调用统一Application。
- `public.py`：健康检查与授权发现，运行时使用真实应用依赖。
- `schema.py`：只登记上述真实路由并导出公开规格；不读取实例配置、秘密或初始化数据库／身份产品。
- `limits.py`：非v2路径的请求体预算兜底；v2由通用契约库边界处理。

根 `scripts/export_contracts.py` 生成公开OpenAPI和码表，`--check`只读检查漂移。契约不能手写成第二套实现。受控聚合已接入真实业务路由及统一Application，见[查询内核](../../modules/query/README.md)。批量业务和任意查询未实施，不能从目录、PG或契约库结果模型外推。
