# PostgreSQL部署与身份隔离

当前已实施开发数据库，不再是目录骨架。基线为`pgvector/pgvector:0.8.7-pg18-trixie`，部署与CI均固定多架构摘要`sha256:9d9c930220cb9bf2f956d10a8f909cf9973d9672ca0278aef2c1e6facccad2e0`；[compose.dev.yaml](../compose.dev.yaml)配置持久卷、初始化与loopback端口。具体镜像补丁／摘要及企业资源配置在部署时确认，不把浮动标签当不可变生产版本。

[init.sh](init.sh)只供全新卷初始化：创建业务库office和Keycloak身份库office_identity，业务owner、业务运行账号与身份账号分离；默认权限和schema按最小权限设置。不要对已有库重复执行初始化、删除卷或重新生成口令来修复问题。

- 业务表由[唯一迁移历史](../../office-data-service/migrations/README.md)管理，不在启动时使用ORM自动建表。
- 查询与写入由业务服务以office_app执行；owner仅供迁移和受控引导。客户端不直连PG。
- 正式就绪检查使用TCP `pg_isready -h 127.0.0.1`，避免把官方镜像初始化期间的临时Unix socket服务当成正式服务。
- 备份／独立恢复检查和双库协调流程见[70](../../docs/ARCHITECTURE.md)；隔离实验不替代生产异机灾备与业务一致恢复演练。

本期PG同时承担事务存储和受控多维统计，不默认启用pgvector、数仓、RLS、分区或按项目分库／分表；超过容量后的演进按实际测量确认。当前模型与查询边界见[76](../../docs/ARCHITECTURE.md)。

镜像预装pgvector 0.8.7，目标库不自动执行CREATE EXTENSION，不改变既有数据模型、角色或迁移。外部PG18可继续不安装扩展。现有实例不得直接重跑安装器隐式替换，须备份、隔离恢复核对与受控切换；详见[84](../../docs/ARCHITECTURE.md)。
