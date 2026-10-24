# 企业数据与管理服务

异步FastAPI／SQLAlchemy／psycopg服务，HTTP与MCP复用application操作层，Web仅提供管理与人工操作，无AI调用。身份由Keycloak管理，实时权限、版本、确认状态和审计在PostgreSQL业务库。

app/common为基础模型与元数据；modules为权限、数据及查询能力；application为编排；infrastructure为数据库、配置、外部适配；entrypoints为HTTP、MCP、Web与运维CLI。模块依赖由根scripts/check_architecture.py检查。

项目模型是字段修订和记录的数据容器，不附带行业含义。查询按当前权限和确认状态控制；未确认数据不能冒充事实；更正经重新确认后替代，历史保留。权限在服务端执行，Web显隐不是授权边界。

部署使用根deploy/install.sh，参阅[安装指引](../INSTALL.md)。服务账号不具备迁移和结构写入权限；迁移使用单独账号。配置与秘密分离，不提供静态管理员令牌或SQLite替代。
