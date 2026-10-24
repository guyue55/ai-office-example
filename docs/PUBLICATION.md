# 公开模板维护规范

## 公开范围

本仓库仅包含通用企业办公基座的当前源码、文档、配置模板与测试。采用独立的干净 Git 根提交，不导入来源项目或早期模板的提交历史、PR 引用、Actions 日志及制品。

客户资料、业务技能、运行数据、账号与凭证不得进入模板。企业实例仓库仍建议私有；公开模板不等于公开客户部署。

## 许可与标识

自有代码采用 Apache-2.0，保留 LICENSE、NOTICE 及适用的来源声明；第三方依赖保留自己的许可，见 THIRD_PARTY_NOTICES.md。office-api-contracts／office_contracts 为中立的契约组件名称，企业展示与工具标识按 template.toml 配置。

不从第三方组件中删除适用的版权、许可和商标声明；自有模板命名中立化也不等于授予其他企业商标权。源码公开和重新分发完整二进制制品是不同的许可核查范围。

## 每次变更的检查

1. 核对源码及所有发布分支，排除客户名称、特有业务字段与口径、沟通记录、本机路径和秘密。
2. 使用固定版本及摘要的 Gitleaks 扫描历史；日志完全脱敏，不公开原始秘密报告。自动工具不能识别所有商业语义或未知秘密。
3. 运行当前提交的 CI，检查契约、权限、安装、迁移与制品一致性；实际结果见 Actions。
4. 候选只从明确提交构建，核验源码、wheel、员工／技能包的摘要与许可证；不混入旧版本制品。
5. 推送、Tag、Release、镜像发布和企业部署分别按其授权执行；公开源码不自动触发客户服务器部署。
6. 仓库保护及私密报告能力通过 GitHub API／Settings 复查，模板副本不会继承平台设置。

## 数据保护与恢复

重建历史前，源码与 Git 引用应保存在仓库外的受限备份中。备份不属于模板源码或公开交付包，不得上传到本仓库。

重写分支无法清理 GitHub 的全部 PR 隐藏引用，因此需要无历史模板时应创建独立仓库根历史，不把旧仓库作为 fork 或导入历史。对已经由他人下载或缓存的内容，仓库维护者无法承诺追回或销毁。

## 官方依据

- [GitHub 仓库可见性与 Actions 日志影响](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility)
- [Apache-2.0 正文](https://www.apache.org/licenses/LICENSE-2.0)
- [GitHub Actions 安全使用](https://docs.github.com/en/actions/reference/security/secure-use)
- [GitHub 私密漏洞报告](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository)
