# 配置与身份边界

唯一品牌输入为根目录template.toml。修改后运行`python3 scripts/configure_template.py`，或用new_project生成独立项目；`--check`只读核验一致性。Docker构建与候选发布均拒绝生成物漂移。

| 配置项 | 用途 |
|---|---|
| product.id | 交付源码与员工zip的文件名前缀，小写kebab-case |
| product.name | 控制台标题、登录页名称、OpenAPI与MCP服务名称 |
| product.short_name | 控制台导航及登录页品牌简称 |
| product.description | API说明及公开产品元数据 |
| brand.organization | 控制台组织归属说明 |
| brand.primary_color | 控制台主色与身份登录主题主色，六位HEX |
| client.command | 实际CLI命令及帮助／版本显示；构建后安装生效 |
| client.display_name | CLI帮助显示名称 |
| distribution.namespace | 初始OAuth客户端与本机凭证隔离前缀 |

身份客户端派生为namespace-cli、namespace-web及namespace-account-admin；本机凭证还按服务URL隔离。保留稳定的内部office-client／office_client、office-data-service、数据库账号、realm、scope、权限码与API路径；这些是协议与升级边界，不随企业展示品牌反复变化。当前自有契约库已统一为 office-api-contracts／office_contracts；第三方组件的版权和许可声明必须保留，后续企业配置不再改动内部包名。

生成的product.json分布于客户端、服务端、安装器，但均由同一配置生成，不是四份人工配置。template-state.json记录配置摘要、版本与受控文件清单；API模型和错误码仍以真实注册表为准，不允许品牌配置改写业务模型。

配置值不作为shell代码执行。标识严格校验，拒绝未知键、控制字符、HTML、路径穿越、保留命令冲突、符号链接和覆盖已有项目。输出按文件原子替换，普通异常回退；断电等跨文件中断由check门阻断，不宣称跨文件事务。

生成新项目不复制Git历史、runtime、dist、环境秘密或凭证。已存在runtime足迹时拒绝原地改配置；从干净模板生成新项目。既有实例迁址、换命名空间、升级、改数据库账号须走独立迁移，不能当换Logo式操作。

服务地址、端口、数据库连接等运行参数仍在deploy/.env；密码仅在安全文件或系统安全后端，不能合并进品牌配置。图形Logo／favicon尚不是现有UI的配置能力，模板使用可配置文字品牌；需要图形资产时应增加受验证的资源机制，不能将任意HTML或SVG注入名称字段。

源码术语project是字段修订及记录的数据容器，不等同于CRM项目管理；没有行业指标、财务算法或业务审批成品。新企业按字段／权限／技能扩展，而不修改身份底层。
