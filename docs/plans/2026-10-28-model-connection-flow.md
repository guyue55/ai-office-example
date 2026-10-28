# 模型直接配置与连接测试实施计划

目标：管理员直接填写模型地址，在同页保存并测试，再明确启用；保留现有业务授权、模型SDK、密钥存储与任务切换机制。

## 设计决策

1. 默认public出口策略：允许管理员填写HTTPS公网模型地址。严格allowlist为可选运维策略；旧配置已有非空白名单而未写策略时保持严格模式，避免升级静默扩大出口。已有列表在public模式可明确授权特定内网HTTPS／隔离loopback地址，元数据、链路本地、广播等危险地址仍拒绝。内网HTTP模型不在本轮新增范围。
2. 保存只验证地址格式及策略，不做网络探测。实际模型请求每次DNS解析、检查所有候选地址并把请求连接固定到经过验证的IP，保留原Host和TLS SNI／证书校验，拒绝跨源和重定向，不读宿主代理环境。应用层措施不能代替部署网络出口控制。
3. 只增强服务端托管模型出口；员工独立Agent原配置／HTTP客户端行为保持。通过现有模型工厂可选transport注入实现，不复制SDK，不写私有连接池字段。
4. 测试是固定合成调用、无企业工具，不要求提前批准业务资料外发；启用仍严格要求approved_model_data。一次确认费用，不重复复选框＋弹窗，不自动重试。先保存再测试为两阶段，测试失败不撤销已保存草稿，且不影响正在生效的旧修订。
5. 连接测试记录落PG：开始、完成或重启中断；持久化脱敏分类、建议、耗时、协议、模型与修订。最新测试与草稿修订不同即标过期；不复用旧密钥／旧地址测试。测试期间并发保存也能留下原修订结果。进程中断不自动重发模型请求。
6. 结果区明确“基础调用通过”，不冒称工具／多模态／余额全验证。失败按认证、权限、地址或模型、限流、供应商异常、DNS、TLS、连接、超时、出口策略、无效响应分类；禁止原始异常／供应商正文回显。
7. Web新增save-test组合动作，仍调用已登记save与test；原生多按钮formaction及确认信息由共享提交处理器正确保留。普通保存／启用独立，Enter默认普通保存，避免隐式付费。

## 执行顺序

- [x] 固化出口策略／兼容和负向测试，确认当前HTTPX2及HTTPcore2公开transport接口与SNI扩展。
- [x] 实现受控transport及DNS固定，合成TLS验证Host／SNI／证书、重定向和私网拒绝；保留客户端三协议测试。
- [x] 扩展结果契约与0015测试记录迁移；恢复中断状态、版本关联、审计及并发结果可见性。
- [x] 实现结构化结果和“保存并测试”；服务端逐阶段重新授权，错误不丢保存回执，不在数据库事务内调用模型。
- [x] 直接地址输入、可选建议地址、策略提示、单层费用确认、结果面板和阶段按钮；复用原样式／确认／loading。
- [x] 单元、独立PG迁移、三协议HTTP、受控传输及浏览器完整验收，生成契约、更新操作指南、登记CI、本地提交。

## 验收边界

使用合成账号／密钥、临时PG、loopback HTTP／TLS供应商替身；公网DNS只用确定性解析夹具。当前实例、真实模型调用、全局客户端、推送及发布不在本轮范围。

参考：[OWASP SSRF防护](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)、[HTTPX transport](https://www.python-httpx.org/advanced/transports/)，并核查当前锁定HTTPX2源码及HTTPcore2的sni_hostname扩展，不把不同库名称或最新版说明当作已兼容。


## 实现与验收记录（2026-10-28）

已实现：public／allowlist策略及旧配置保守兼容、受控异步transport、0015连接测试留痕、固定错误分类、合成测试／业务外发许可分离、同页保存并测试、修订结果展示和全站URL输入框样式。原有三协议工厂通过可选transport注入复用，员工独立配置默认不变。启用／停用、真实权限及独立秘密仍沿用原机制。

关键负向验证：混合DNS候选含私网即拒绝；重绑定后不连接；跨源／重定向拦截；本地TLS保留Host和SNI且拒绝不可信证书；三协议通过受控transport真实请求loopback HTTP，单次仅一条合成请求。测试失败保留草稿和旧生效修订，供应商异常原文不回显；网络阶段并发保存、撤权后落盘、重启中断不重发已在独立PG验证。

执行记录：

- `uv run --extra agent --frozen pytest -m 'not live' -q`：624通过，123个live场景排除（不算通过）；之后共享提示调整又定向复核82项通过。
- `uv run --project clients/agent --frozen pytest clients/agent/tests -q`：54通过。
- `OFFICE_QUERY_PG_TESTS=1 uv run --extra agent --frozen pytest tests/test_model_configuration_postgres.py tests/test_model_configuration_upgrade.py -q`：2通过，独立新建PG18，含0013至head升级／数据保留；后续新增负向场景另行定向通过。
- `OFFICE_QUERY_PG_TESTS=1 OFFICE_AGENT_BROWSER_TESTS=1 OFFICE_TEST_BROWSER_CHANNEL=chrome uv run --extra agent --frozen pytest tests/test_model_settings_browser.py -q`：真实独立服务与浏览器完整流程。覆盖取消不保存、多按钮formaction、保存并测试、单层费用确认、无JS、普通用户403、窄屏及助手并排；补入Enter默认无费用保存、空建议列表直接配置、失败提示、URL圆角一致。
- 共享交互回归：全页面目录／助手／表单／账号流程5项通过；共享视觉基础及全页面目录2项通过。
- Ruff全仓检查／408文件格式、mypy127文件、架构、能力覆盖58操作、技能、模板一致性及契约生成核对通过。CI已纳入出口／TLS／三协议测试；本轮未推送，远端CI未执行。

首次浏览器运行因当前Playwright无对应缓存执行文件而未启动，改用已安装Chrome完成隔离验收，未改业务环境。测试发现并修复：合成探针需仅在临时运行配置中满足既有SDK数据许可校验（不持久化，不放松启用）；Anthropic追加路径与OpenAI不同；URL输入类型未包含于全站圆角／焦点选择器，现已统一并增加断言。

运行边界：选择一个合格IP连接，不对其他IP或供应商自动重试；不支持企业强制代理的新配置；网络出口防火墙仍由部署方控制。历史测试列表／自动清理、所有模型能力及真实供应商费用不属于本轮验收。当前实例未迁移／重启、全局客户端未改、未推送／发布。
