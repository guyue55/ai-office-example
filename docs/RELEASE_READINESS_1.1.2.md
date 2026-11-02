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
