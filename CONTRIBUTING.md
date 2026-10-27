# 开发与验证

使用Python3.13／3.14及uv。运行`uv sync --frozen`建立本仓库环境。中文文档／注释，提交type(scope): 中文说明；不提交.env、凭证、runtime、dist或客户材料。

品牌仅修改template.toml并运行`python3 scripts/configure_template.py`；`--check`核验生成物。已有runtime足迹禁止原地变更；新企业使用new_project生成器。生成器不复制Git历史，新项目自行初始化并提交源码。

主要门禁：

```sh
uv run ruff check office-data-service clients/python scripts tests packages/api-contracts deploy/installer installers
uv run ruff format --check office-data-service clients/python scripts tests packages/api-contracts deploy/installer installers
uv run mypy office-data-service/app clients/python/src --check-untyped-defs
uv run python scripts/check_architecture.py
uv run python scripts/check_capability_coverage.py
uv run python scripts/check_skills.py
uv run python scripts/export_contracts.py --check
uv run python scripts/configure_template.py --check
uv run pytest -m 'not live' -q
```

修改业务码／响应模型后从真实注册表导出契约，不手改指纹。生成产品配置后须重新导出OpenAPI标题并核验。新增业务放明确扩展模块／技能，复用身份权限、数据版本和接口契约，不复制第二套认证。

真实PG／浏览器测试显式隔离并标live；没有真实实例时不能据本地测试宣称上线可用。CI含Linux／Windows与容器门，工作流未运行不等于通过。公开模板与企业私有实例分开；发布和镜像推送须有对应授权。

## 独立活体验收

避免占用其他项目的默认端口。首次开发环境可设置`OFFICE_DEV_PORT=18890 uv run python scripts/dev_stack.py up`；随后provision、migrate，按owner配置执行bootstrap，再用运行配置启动`office-service serve --port 18890`。既有配置保留，不因环境变量改变而迁址。

真实测试设置`OFFICE_LIVE_TESTS=1 OFFICE_TEST_URL=http://127.0.0.1:18890`，入口必须与专用config.json一致。先`uv run playwright install chromium`，也可用PLAYWRIGHT_CHROMIUM_EXECUTABLE明确指定已安装浏览器路径。测试辅助仅允许本机HTTP根地址，不探测、停用或接管其他应用。

开发环境端口、测试账号和卷不属于员工部署路径；生产仍使用一键安装器及独立实例参数。

## 许可与公开安全

贡献者须具备所提交材料的相应权利；自有新增贡献按仓库 Apache-2.0 提供，不提交客户资料或不兼容的第三方源码。保留 LICENSE、NOTICE 及第三方声明；企业项目品牌配置不改动这些版权归属。CI 使用固定摘要的 Gitleaks 扫描可达历史，不上传含原始秘密的扫描报告，发现问题时先私密处理。

日常操作、候选构建、Tag／Release、升级与回退顺序见 [操作手册](docs/OPERATIONS.md)。

## 可选Agent会话与工作区回归

安装可选依赖后运行`uv run --extra agent --frozen pytest -m 'not live' -q`，独立模型协议运行`uv run --project clients/agent --frozen pytest clients/agent/tests -q`。会话／审批变更还须分别执行下列隔离检查；不得指向在用企业库或用户浏览器会话：

```sh
OFFICE_QUERY_PG_TESTS=1 uv run --extra agent --frozen pytest tests/test_agent_conversations_postgres.py tests/test_agent_records_postgres.py tests/test_agent_runner_postgres.py tests/test_agent_hosted_postgres.py tests/test_postgres_readiness.py -q
OFFICE_AGENT_BROWSER_TESTS=1 uv run --extra agent --frozen pytest tests/test_assistant_workspace_browser.py tests/test_agent_browser.py -q
OFFICE_AGENT_BROWSER_TESTS=1 uv run --extra agent --frozen pytest tests/test_shared_tooltip_browser.py -q
OFFICE_AGENT_BROWSER_TESTS=1 uv run --extra agent --frozen pytest tests/test_visual_foundation_browser.py -q
OFFICE_AGENT_BROWSER_TESTS=1 uv run --extra agent --frozen pytest tests/test_form_validation_browser.py tests/test_account_flow_browser.py -q
OFFICE_QUERY_PG_TESTS=1 OFFICE_AGENT_BROWSER_TESTS=1 uv run --extra agent --frozen pytest tests/test_assistant_browser_postgres.py -q
```

PG测试创建本轮临时容器并执行正式迁移；需要Docker服务。浏览器使用Playwright独立上下文，可通过OFFICE_TEST_BROWSER_CHANNEL=chrome选择已安装Chrome，否则按原Playwright安装说明准备浏览器。以上测试使用合成数据与模型替身，不发生真实模型计费。新的会话入口须同步HTTP、SDK、CLI、MCP及contracts；历史预算、权限指纹与兼容边界见docs/AGENT_WORKSPACE_PLAN.md。

## 全页面视觉及流程回归

`tests/ui-pages.json`登记所有页面及组件。`test_ui_catalog_browser.py`用合成上下文渲染真实模板，在1440／1024／768／390／320宽度验证布局、字段名称、减少动态效果及工作区输入位置，截图仅写入忽略的runtime。它不替代权限、数据库或身份系统功能验收。

```sh
OFFICE_AGENT_BROWSER_TESTS=1 OFFICE_TEST_BROWSER_CHANNEL=chrome uv run --extra agent --frozen pytest tests/test_ui_catalog_browser.py tests/test_account_flow_browser.py tests/test_form_validation_browser.py tests/test_assistant_workspace_browser.py -q
OFFICE_AGENT_BROWSER_TESTS=1 OFFICE_TEST_BROWSER_CHANNEL=chrome uv run --extra agent --frozen pytest tests/test_ui_state_browser.py -q
```

`OFFICE_TEST_BROWSER_CHANNEL=chrome`仅选择本机已安装Chrome；也可省略并安装Playwright匹配版本的Chromium。浏览器缺失属于测试环境未就绪，不能记为功能通过。

`test_ui_state_browser.py`仅启动独立合成HTTP应用，验证保存后外壳与助手绑定、其他表单及请求期间草稿、完整页面授权保存、成功／冲突／网络失败后的控件恢复，以及本人取消离页后任务轮询继续；不修改真实授权，不调用模型。设计与执行结果见[UI状态整改](docs/UI_STATE_REMEDIATION_PLAN.md)。

该入口还覆盖侧栏／完整助手页的站内草稿确认、原生刷新取消、分页恢复、圆角原生picker键盘和Esc层级、触摸／无脚本回退，见[导航与控件方案](docs/UI_NAVIGATION_CONTROLS_PLAN.md)。未收到真正离页前不能清理任务轮询；增强样式不能替代原生select的表单与键盘语义。

独立实例的匿名身份登录布局检查：

```sh
OFFICE_AGENT_BROWSER_TESTS=1 OFFICE_TEST_BROWSER_CHANNEL=chrome OFFICE_UI_PUBLIC_URL=http://<独立验收服务器>:<端口> uv run --extra agent --frozen pytest tests/test_identity_login_layout_live.py -q
```

该入口仅打开登录页，不填写或读取凭证，不创建账号；检查刷新后布局位置和五种宽度，不能据此宣称所有瞬态布局偏移为零。开户／关联／授予角色流程使用隔离替身验证，不修改真实用户授权。


模型管理变更额外验收（仅创建临时PG，不使用已有实例）：

```bash
OFFICE_QUERY_PG_TESTS=1 uv run --extra agent --frozen pytest tests/test_model_configuration_postgres.py tests/test_model_configuration_upgrade.py -q
OFFICE_QUERY_PG_TESTS=1 OFFICE_AGENT_BROWSER_TESTS=1 uv run --extra agent --frozen pytest tests/test_model_settings_browser.py -q
```

第一项包含0013已有库升级、重复执行权限不重复增加与运行账号不能修改历史配置；第二项含真实Web保存、确认取消、密钥不回填、合成模型测试及启停。浏览器与模型身份仍为隔离夹具，不读取真实密钥或调用付费模型。Linux CI的Agent任务已登记这两层，远端结果需实际触发后确认。
