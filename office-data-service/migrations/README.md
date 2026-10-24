# 唯一数据库迁移历史

已实施Alembic迁移，不再是预留目录。当前versions/包含0001至0011，唯一head为0011；业务ORM登记18张表，不包含Keycloak独立管理的身份库表和Alembic版本表。模型权威在app/infrastructure/models.py，不手工同步第二套建表脚本。

迁移通过env.py显式加载OFFICE_CONFIG，强制database_user为office_owner；业务服务使用office_app。首次建库、角色授权、迁移和引导步骤见[25](../../docs/ARCHITECTURE.md)及[部署规程](../../docs/ARCHITECTURE.md)。不要在导出契约或导入纯模型时读取秘密或执行迁移。

新增结构须同时核对模型、迁移链、现有数据升级／回退风险与运行账号权限。当前没有pgvector、分区或分库迁移，不因数据库理论能力擅自增加结构。CI在全新独立PG18库验证迁移与运行账号，恢复演练另按双库规程验收；生产升级仍需备份和回滚计划。

2026-10-23：部署镜像预装pgvector，仍不新增CREATE EXTENSION迁移；head保持0011。启用向量业务时另行确认字段、权限、模型版本与索引。见[84](../../docs/ARCHITECTURE.md)。
