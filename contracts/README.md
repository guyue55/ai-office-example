# 公开契约产物

OpenAPI从实际HTTP路由与模块模型生成，业务码表从应用注册表导出；本目录不是第二套手写模型。HTTP已采用统一包装与独立Python契约库，通用实现位于packages/api-contracts，项目装配位于app/application。

```bash
uv run python scripts/export_contracts.py
uv run python scripts/export_contracts.py --check
```

生成过程不读取OFFICE_CONFIG、实例秘密或连接数据库，不需启动服务。先构造全部产物，再逐文件原子替换；普通写入异常恢复本进程已替换的文件。跨文件无法保证断电级原子性，check门禁会阻断残留漂移。--check不更新文件。客户端码表副本也是生成物，禁止单独手改。

MCP通过tools/list公布工具并共享同一应用与模型，不开放任意SQL。项目动态字段由项目结构接口发布；单数据集、精确字段修订下的受控多维聚合已实现，见[75](../docs/ARCHITECTURE.md)。通用契约库的批次／任务表示不等于本项目已提供批量业务接口或执行引擎。
