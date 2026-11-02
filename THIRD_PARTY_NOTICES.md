# 第三方组件与许可边界

本项目的许可证（如已由权利人确定）仅适用于有权授权的自有源码和文档，不替代第三方依赖的许可。未复制参考仓库源码的声明不能代替来源和权利核实。

## Python 依赖

版本以 uv.lock 为准；[许可元数据快照](docs/dependency-licenses.json) 来自锁定开发工作区的已安装分发包，包含开发与测试依赖。更新锁文件时重新核查许可证及其变更，不能把快照视为永久结论。

- FastAPI、SQLAlchemy、Pydantic、MCP SDK 等声明 MIT。
- HTTPX、Starlette、Uvicorn、Jinja2 等声明 BSD 类许可。
- Psycopg 和 psycopg-binary 声明 LGPL-3.0-only；独立依赖不因此统一改为本项目许可。交付二进制或容器时保留其许可、版权及适用的源代码／替换等要求，不能只附本项目 LICENSE。
- certifi 声明 MPL-2.0；开发依赖 pathspec 也声明 MPL。文件级许可义务不能用根许可证覆盖。
- cryptography、packaging 等含备选或组合许可；具体文本以所交付版本的 dist-info/licenses 等上游材料为准。

Agent分支新增的可选运行依赖单独核对：Pydantic AI slim／graph 2.61.0、genai-prices及logfire-api声明MIT，OpenAI Python SDK和Anthropic Python SDK（本轮2.3.0）声明Apache-2.0，docstring-parser声明MIT，HTTPX2声明BSD-3-Clause，griffelib声明ISC。版本分别见根和clients/agent的锁文件；没有复制上述框架或本地参考项目的源码，也没有默认启用外部遥测。基础镜像不安装模型运行时，可选Agent镜像和独立轮包仍须保留实际依赖的许可材料。原许可快照不作为新增依赖已经被逐项记录的证明。

该清单不是每个 wheel 内嵌本地库的完整清单。尤其 psycopg-binary 的内嵌库，及其他本地扩展、浏览器和操作系统组件，需要在实际制品层保留上游许可文件并核查适用义务。

## 独立服务与构建工具

PostgreSQL、pgvector、Keycloak、网关及容器基础系统分别按其上游许可证提供；镜像版本与摘要见 deploy 配置。源码仓库没有将这些服务整体重新许可。

Gitleaks 是独立执行的密钥扫描工具，当前固定 v8.30.1；构建只下载并校验官方二进制，不将其源码合并为本项目。GitHub Actions 按固定提交引用，许可仍由各上游项目决定。

## 官方核对入口

- [Psycopg 源码与许可](https://github.com/psycopg/psycopg)
- [Psycopg 安装与二进制说明](https://www.psycopg.org/psycopg3/docs/basic/install.html)
- [certifi 许可](https://github.com/certifi/python-certifi/blob/master/LICENSE)
- [Keycloak 许可](https://github.com/keycloak/keycloak/blob/main/LICENSE.txt)
- [PostgreSQL 许可](https://www.postgresql.org/about/licence/)
- [pgvector 许可](https://github.com/pgvector/pgvector/blob/master/LICENSE)
- [Gitleaks](https://github.com/gitleaks/gitleaks)

对外重新分发源码、wheel、完整安装包和容器是不同交付范围，应按实际交付材料核查，不声称本文已经完成所有二进制分发的合规认证。


## 助手本地Markdown解析器

Agent分支服务端包含markdown-it 15.0.3浏览器UMD包（MIT），位于 `office-data-service/app/entrypoints/web/static/markdown-it-15.0.3/`。来源npm固定版本官方tarball，经dist SHA-512校验，SOURCE.json记录来源、完整性和实际包SHA-256；不是从CDN动态加载。该目录保留主LICENSE及包所含entities 8.1.0（BSD-2-Clause）、linkify-it 6.1.0（MIT）、mdurl 2.1.0（MIT）、punycode.js 2.3.1（MIT）、uc.micro 3.0.0（MIT）许可；依赖版本按上游15.0.3的package-lock核对。运行不需要Node或NPM。重新构建或替换此文件时须同步来源、哈希、许可和恶意输入回归，不只替换版本号。
