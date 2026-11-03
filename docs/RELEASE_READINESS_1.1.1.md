# v1.1.1 发版准备与验收

日期：2026-11-03。目标版本 1.1.1，是 v1.1.0 的文档修正补丁；产品代码、契约（除版本号）、迁移与权限不变。

## 范围

- 迁移 head 说明由 0015 修正为 0017（发布说明、AGENT_OPERATIONS、RELEASE_READINESS 1.1.0）。
- README 更新为 v1.1 已发布状态；INSTALL 示例改为 `v$(cat VERSION)` 与时间戳目录。
- 版本号同步：VERSION、五份 pyproject、Agent 模块版本、两份锁文件、OpenAPI 与 template-state。

## 验证

- `prepare_release.py --check --tag v1.1.1`、`configure_template.py --check`、`export_contracts.py --check` 通过。
- 候选 17 份资产；`verify_release.py`（全 SHA）、`SHA256SUMS`、`check_client_install` 通过。
- 镜像：`image-release` 运行因 Agent checks 失败被取消，`1.1.1`／`1.1.1-agent` **未发布**（匿名拉取确认不存在）。

## 发布后的缺陷与替代

- `clients/agent/pyproject.toml` 与 `office-data-service/pyproject.toml` 的本地包精确约束仍为 1.1.0，导致 `office_agent-1.1.1` 轮包不可安装（`verify_install.py` 失败）。
- 按规范不覆盖已发布 Tag 与资产，改用 **v1.1.2** 修复；本版 Release 描述已加提示。

## 边界

- 本补丁不改变运行代码、数据库结构或权限；已部署 v1.1.0 的实例无需升级数据库。
- 实例升级、全局客户端更新与真实供应商验收不在本补丁范围。
