# v1.2.0 发版准备与验收

日期：2026-11-13。目标版本 1.2.0，累计 81 个提交（自 v1.1.2 起），集中在 Web 工作台体验、智能助手配置页、前端质量门禁与流式响应修复。

## 范围

### 生产行为变化

- 契约：`422001 invalid_input`（`invalid_request` 在 422 语义下）与 `422012 unsupported_operation` 正式抛出，替代原先静默降级的 HTTP 500 组合。
- 智能助手：流式响应在 SDK 收尾重放时不再被误判为截断；会话标题去除控制符与方向符；模型用量结构化展示；敏感信息清洗覆盖键名变体与文本凭证。
- Web：工作台语义结构与视觉令牌重构、模型配置页信息层级简化、保存语义主次分离、服务地址变更时的密钥沿用确认、表格与对话框无障碍名称、关闭对话框时同步撤出无障碍树。
- 开发栈：自动生成登录主题、模型密钥加密根密钥与身份校验配置。

### 质量门禁

- 新增 `scripts/check_ui_styles.py`（颜色字面量、未守卫 `:hover`、非刻度圆角、中性 1px 边框）与 `scripts/check_frontend_rules.py`（内联样式、内联事件、`innerHTML`、原生弹窗、`@media` 格式与断点），基线双向锁定并接入 CI `quality` job。
- `scripts/check_capability_coverage.py` 增加输出契约与输入契约交叉校验。
- `tests/test_quality_gates.py` 为门禁脚本自身提供回归。

### 未包含

- 无新增 Alembic 迁移，迁移 head 仍为 `0017_agent_progress`。
- 无权限码、角色语义或业务模型变更。
- 未改动部署拓扑、镜像默认依赖或员工交付包结构。

## 版本同步范围

`VERSION`、五份 `pyproject.toml`（根、office-data-service、clients/python、packages/api-contracts、clients/agent）、`clients/agent/src/office_agent/__init__.py` 的 `__version__`、两份 `uv.lock`（根与 clients/agent）、`contracts/openapi.json` 的 `info.version`（由包版本派生，不手改）、`template-state.json` 的 `template_version`、`README.md` 的当前发布行。

`scripts/configure_template.py` 在存在 `runtime/` 实例足迹时拒绝原地执行。按 AGENTS.md「跨环境发版与验收经验」第 2 条，本轮在隔离源码副本运行原生成器，仅回填预期版本差异后执行 `--check`，未删除实例足迹、未改身份或品牌。

## 验证

### 已实跑

- `pytest -m 'not live'`：863 passed / 147 deselected。
- 隔离浏览器套件（CI 命令同源，6 个文件）：46 passed。
- Agent 包测试：104 passed。
- `ruff check .`、`ruff format --check .`（493 files）、`mypy office-data-service/app --check-untyped-defs`（111 source files）通过。
- `check_architecture.py`、`check_capability_coverage.py`（60 操作）、`export_contracts.py --check`、`check_ui_styles.py`、`check_frontend_rules.py` 通过。
- Python 3.13 与 3.14 两个版本矩阵；CI 的 `quality`（3.13／3.14）、`Agent`（ubuntu 3.13／3.14、macos 3.13、windows 3.13）、`source-safety`、`windows-installer`、`macos-ops` 全绿。

### 未验证

- 隔离数据库（PG18 + pgvector）套件：本机无法拉取 `pgvector/pgvector:0.8.7-pg18-trixie` 镜像，9 项在本地跳过；依赖 CI 或具备镜像的环境复跑，不记为通过。
- Safari／Firefox 的 `@view-transition` 跨文档过渡与 `allow-discrete` 退出动效行为。
- 真实读屏器体验；触屏按压反馈；深色模式（全仓尚无 `prefers-color-scheme` 规则，本版不声称支持）。
- 真实供应商之外模型的连通性与计费；本版只以 DeepSeek 完成端到端连接测试。
- 企业真实数据容量、目标压测与全部 AI 宿主宿体验收。

## 已知问题

- 窄屏（约 390px）顶部滚动位置，浮动「智能助手」入口可能压住下拉控件的箭头处点击区。既有判定标准（`tests/test_ui_catalog_browser.py` 只要求滚到底部不压控件）通过；尝试缩成图标后更差已回退。记录为可改进项，未在本版修复。
- 模型配置页在 `#model-url` 上按 Enter 不触发提交；`form.checkValidity()` 为 true 但存在 `:invalid` 元素，根因未定位，点击提交路径不受影响。

## 发布步骤（按 docs/OPERATIONS.md）

1. 更新版本元数据并提交。
2. `uv lock` 与 `uv lock --project clients/agent`，审核差异仅含版本变化。
3. `uv run python scripts/export_contracts.py` 重新生成 OpenAPI。
4. `prepare_release.py --check --tag v1.2.0`。
5. `prepare_release.py --tag v1.2.0 --output <新目录>` 构建候选。
6. `check_client_install.py --wheel-dir <候选>`、`verify_release.py <候选> --tag v1.2.0 --commit <SHA>`。
7. PR 合并到 main 后打附注 Tag 并推送，创建 Release。
