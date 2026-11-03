# v1.1.2 发版准备与验收

日期：2026-11-03。目标版本 1.1.2，修复 v1.1.1 的 Agent 轮包精确依赖约束；运行代码、迁移与权限不变。

## 根因

- v1.1.1 将各包版本提升到 1.1.1，但 `clients/agent/pyproject.toml` 仍写 `office-client==1.1.0`，`office-data-service/pyproject.toml` 的 `agent` 额外依赖仍写 `office-agent==1.1.0`。
- 结果：`office_agent-1.1.1` 轮包要求不存在的 `office-client==1.1.0`，Agent CI 的 `verify_install.py` 失败，镜像 checks 无法通过。

## 范围

- 两处精确约束改为 1.1.2。
- 版本同步：VERSION、五份 pyproject、Agent 模块版本、两份锁文件、OpenAPI、template-state。
- 保留 1.1.1 的迁移说明修正（0017）。

## 验证

- `verify_install.py --wheels dist/agent` 通过（本地与 CI）。
- `prepare_release.py --check --tag v1.1.2`、`configure_template.py --check`、`export_contracts.py --check` 通过。
- 候选 17 资产；verify_release（全 SHA）、SHA256SUMS、check_client_install 通过。
- 镜像 `1.1.2` / `1.1.2-agent` 公开、双架构。

## 边界

- v1.1.1 的 Agent 轮包不可用；已发布 Tag／资产不覆盖，改用本版。
- 实例升级与全局客户端更新不在本补丁范围。

## 2026-11-03 发布记录

- 附注 Tag `v1.1.2` 指向 `4fc2d9aa00d8d6b72da6de7c863ef09b2da2a004`（合并后 main）；GitHub Release 已发布并标记 Latest，17 份资产。
- 下载后复核：`verify_release.py`（全 SHA）、`SHA256SUMS`、`check_client_install`（office 1.1.2）通过。
- 镜像 `ghcr.io/guyue55/ai-office-example/data-service:1.1.2`、`:1.1.2-agent` 及对应 `sha-*` 标签公开、双架构（amd64／arm64）；OCI revision `4fc2d9a`、version `1.1.2`；base 无 `office_agent`，agent 含 `office-agent 1.1.2`。`ENABLE_IMAGE_RELEASE` 已恢复 `false`。
- 为消除 `test_model_settings_browser` 在 Linux CI 的重复超时，测试增加了保存后 `wait_for_load_state("load")` 与 `#model-reset` 启用断言；仅测试改动，未改生产 UI 代码。
