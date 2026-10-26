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
OFFICE_QUERY_PG_TESTS=1 OFFICE_AGENT_BROWSER_TESTS=1 uv run --extra agent --frozen pytest tests/test_assistant_browser_postgres.py -q
```

PG测试创建本轮临时容器并执行正式迁移；需要Docker服务。浏览器使用Playwright独立上下文，可通过OFFICE_TEST_BROWSER_CHANNEL=chrome选择已安装Chrome，否则按原Playwright安装说明准备浏览器。以上测试使用合成数据与模型替身，不发生真实模型计费。新的会话入口须同步HTTP、SDK、CLI、MCP及contracts；历史预算、权限指纹与兼容边界见docs/AGENT_WORKSPACE_PLAN.md。
