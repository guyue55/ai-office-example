# Agent交付与发版链路核查

核查日期：2026-10-28。源码基线：3acee55ee9f9921acf2a58c5c104555d3af2d527，codex/agent；VERSION=1.1.0。本轮只研究及归档，不修改功能代码、部署、推送、发版或调用模型。历史构建与旧候选不作为本提交的验收证据。

## 1. 已确认的实现与缺口

| 项目 | 当前真实实现 | 缺口与结论 |
|---|---|---|
| 镜像构建 | deploy/Dockerfile以OFFICE_AGENT=0/1分别安装普通／Agent依赖，运行时非root；非新建Dockerfile需求 | 复用同一Dockerfile，补双变体CI及发布，不复制构建系统 |
| 镜像发布 | image-release.yml固定main、附注Tag与当前SHA一致，双架构、SBOM、provenance=min | 没有传OFFICE_AGENT=1，不发布Agent变体；保留现有正确保护 |
| 安装选择 | deploy/install.sh可通过进程环境OFFICE_AGENT=1源码构建；OFFICE_IMAGE也可显式指定 | scripts/delivery_config.py未接受OFFICE_AGENT，交付.env不能配置此能力；镜像覆盖发生在构建／初次解析之后，缺统一优先级和能力检查 |
| 镜像获取 | 安装器对基础PG／Keycloak／网关有inspect或pull，再固定实际ID | 业务镜像入口直接inspect；全新服务器指定未拉取业务镜像会失败，没有对应pull |
| 能力匹配 | 普通镜像无Agent是有意设计 | 显式请求Agent但传普通镜像时没有安装前校验；不能把容器启动成功称为助手可用 |
| 容器CI | ci.yml容器job构建默认普通镜像，docker_smoke验证迁移、初始化、健康、401、Host及关闭 | 没有Agent容器变体矩阵；源码中Agent PG／浏览器测试不等于制品容器中模型配置全链路 |
| 备份恢复 | backup_check.py有开发库dump至同实例新库恢复及基础计数；模型测试有原根密钥解密、错误根密钥拒绝、重启恢复 | 未发现数据库密文＋根密钥配套恢复至独立环境的完整模型配置验收；现成backup_check绑定开发实例，不可直接用于本轮独立验收 |
| 发布说明 | releases/v1.1.0.md描述可选Agent和旧文件配置 | 明确写迁移到0013，落后当前head0015；会遗漏模型管理表和权限，需修正。RELEASE_READINESS_1.1.0.md主要是10月27日旧SHA证据，需要追加当前范围 |
| 远端CI | 本次gh run list按完整基线SHA查询返回空列表 | 当前没有该SHA的远端成功证据；不从本机通过推断远端通过 |

## 2. 本轮真实验证及边界

1. 从deploy/install.sh提取原始IMAGE选择和inspect片段，用只记录调用的Docker替身运行：
   - OFFICE_AGENT=1、OFFICE_IMAGE指向未下载镜像：仅image inspect，退出1，未pull。
   - OFFICE_AGENT=1、OFFICE_IMAGE指向替身普通镜像：仅image inspect，退出0并选中普通镜像，无能力检查。
   这是原脚本片段的控制流验证，不是新服务器Docker部署。
2. 用真实scripts/delivery_config.read_env读取临时配置（.env.example加OFFICE_AGENT=1）：实际抛出“交付配置包含未知字段”。
3. 运行test_image_release_workflow.py及test_release_verifier.py：18项通过。覆盖Tag／来源及制品核验，不证明双变体发布或新环境安装已通过。
4. 未运行backup_check.py：其默认目标是既有开发栈，不符合本轮隔离边界。未读取实际根密钥、模型凭证或账户资料。

## 3. 推荐落地方案

### A. 双变体交付，保留兼容

同一GHCR包、同一源码SHA，普通标签<version>／sha-<SHA>，Agent标签<version>-agent／sha-<SHA>-agent。工作流小矩阵分别传OFFICE_AGENT=0/1，制品元数据记录变体、构建参数、版本、来源SHA、digest与平台。两变体不得互相覆盖SHA标签。保留现有main／Tag校验、批准环境、双架构、SBOM和来源证明，不引入新CI平台或部署框架。

### B. 安装配置先收敛，再启动部署

复用OFFICE_AGENT，不另造第二种服务类型变量：加入统一交付配置解析和示例／交互。保留“未设置”和“显式0”的差别，尤其macOS转发环境不能在读配置前默认补0。显式参数／环境与文件冲突应明确报错，不静默覆盖。

将纯配置解析从依赖httpx的server.py安装阶段分离，复用标准库delivery_config。不能用source执行.env；不要求仅具备Docker的服务器预装Python，可考虑固定摘要的轻量bootstrap容器，具体命令须另行实装验证。先决定来源与变体，再拉取或构建业务镜像，复用已有inspect-or-pull逻辑，最后固定实际镜像ID。

部署数据库前核对镜像版本和运行能力（Agent依赖可导入），普通镜像与Agent请求不匹配直接拒绝。对已有实例保留镜像变化阻断及显式升级路径，不通过自动pull改变既有安装。

### C. 制品级验收

普通／Agent镜像分别在独立临时实例验证；Agent容器与临时PG及合成HTTP供应商连接，按正式HTTP/Web入口完成保存、测试、启用、任务、停用、重启恢复。验证非root根密钥可读、网络出口、响应预算、取消收尾；普通镜像无Agent依赖时数据与管理服务继续工作，不能假称模型已安装。

跨平台镜像构建与每个平台实际运行分别报告；amd64模拟成功不能代替原生负载能力。最终main SHA重新构建候选，仓库外安装轮包／交付包，下载后的Release与镜像再次核对来源及摘要。源码editable测试不替代制品消费验收。

### D. 独立备份恢复

仅用本轮新建PG、合成数据和合成根密钥：设置草稿／生效修订／测试记录，按正式方式dump，在全新库恢复，再分别配套正确、缺失、错误根密钥启动。核对生效修订、历史、权限和解密状态；缺失或错误根密钥时原数据服务可用，助手不可用，不能生成新钥匙冒充恢复；未完成任务不自动重发。验收目标是恢复业务行为，不只是行数相等。

备份清单涵盖业务库、身份库及受限配置／秘密；密钥文件不由pg_dump代备。不得默认读取现有客户资料或覆盖目标库。

### E. 文档与发布门禁

先修正v1.1.0说明的迁移head=0015，补上控制台配置／独立根密钥／出口策略及双变体安装说明，再追加当前验证证据。推送后核对完整SHA的多系统CI；main合并后重新构建同源候选，通过原verify_release再做Tag／Release。镜像可见性、匿名拉取、双架构及实际运行分别核查；发布不自动升级实例或全局客户端。

## 4. 官方依据与适用边界

- Docker多平台构建：https://docs.docker.com/build/ci/github-actions/multi-platform/ 。沿用现有Buildx双架构流程；增加软件变体不意味着要更换构建平台。
- Docker SBOM／provenance：https://docs.docker.com/build/ci/github-actions/attestations/ 。现有sbom=true及provenance=min不是缺失；max可按公开构建参数审查后选择，不作为本轮强制重构。秘密不可放build args。
- GHCR：https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry 。首次包默认私有，公开源码不等于镜像可匿名拉取；同一包内增加标签通常无需创建第二个包，但必须核实实际可见性。
- PG18 SQL dump：https://www.postgresql.org/docs/18/backup-dump.html 。dump是数据库快照，目标创建、恢复权限及库外文件需另外管理。
- Fernet：https://cryptography.io/en/latest/fernet/ 。引用密钥丢失不能解密的稳定语义，不据latest页面的开发版本升级当前锁定依赖。

结论：优先修复安装入口一致性、Agent镜像发布及制品级恢复证据；不新增业务功能、多执行进程、密钥在线轮换或新平台。当前未证明完整交付通过，完成上述门禁后再推进发版。
