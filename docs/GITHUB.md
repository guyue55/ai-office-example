# GitHub 托管与自动化

模板仓库：[guyue55/ai-office-example](https://github.com/guyue55/ai-office-example)，默认分支 main，开启 GitHub Template。模板公开不代表企业实例应公开，企业项目与业务资料仍建议保持私有。

## CI

.github/workflows/ci.yml 在 main、codex/** 推送及 PR 时运行。source-safety 使用固定版本和摘要的 Gitleaks 检查完整可达历史；质量矩阵检查 Python 3.13／3.14 的格式、类型、架构、技能、契约、依赖漏洞、非活体测试及构建。另有 Linux 容器／临时 PostgreSQL、Windows 客户端及员工安装验收。

远程运行结果以对应提交的 Actions 记录为准，不代表客户生产上线或真人授权已验收。Actions 默认只读令牌，发布 job 才单独申请 packages:write；不启用自动合并或 Actions 自动批准 PR。Docker 构建记录额外上传关闭，常规日志及候选包按工作流保留期管理。

## 候选与发布

release-candidate.yml 手动运行，先执行 CI，再构建并校验候选，上传短期 Artifact；不自动创建 Tag 或 Release。

image-release.yml 仅手动运行、仅 main。CI 通过后，还要求仓库变量 ENABLE_IMAGE_RELEASE 为 true；默认关闭。管理员需核验 image-release 环境审批和 GHCR 权限／可见性再启用；变量开关不等于人工审批。

本流程不连接客户服务器，不构成自动部署。生产部署、Tag、Release 和镜像发布需要各自明确授权。

## GitHub 设置

公开后的目标保护为：main 禁止强推／删除，要求 PR 及对应的 CI 状态；镜像发布由 image-release 环境审核。单维护者场景不强制第二位同事审批源码 PR，但仍需 CI 通过。具体已生效设置以 GitHub API 或仓库 Settings 为准，工作流文件不能证明平台保护已经启用。

仓库可见性或平台套餐变化后，应重新核查保护设置。安全报告入口在启用 GitHub private vulnerability reporting 后才生效，见 SECURITY.md。

## 模板副本

复制源码不会复制 GitHub 的分支规则、变量、环境审批、报告入口和密钥保护设置。新企业仓库的维护者须自行核验；不得默认继承模板仓库的保护。
