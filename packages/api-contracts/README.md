# office-api-contracts

通用接口契约库：统一业务JSON响应、严格类型、数字业务码及HTTP/MCP薄适配。核心只依赖Pydantic；不导入业务、数据库、身份产品或AI，不执行权限、事务、登录、任务或自动重试。

发行名：`office-api-contracts`；导入名：`office_contracts`。Python3.13–3.14，当前验证组件Pydantic2.14.1、FastAPI0.143.3、HTTPX0.28.1、MCP2.3.0。其他版本不以元数据范围代替测试。自有源码采用 Apache-2.0；第三方依赖保留其原许可。发布状态以 GitHub Release 为准。

## 安装与依赖边界

```bash
uv pip install /path/to/office_api_contracts-1.0.0-py3-none-any.whl
uv pip install '/path/to/office_api_contracts-1.0.0-py3-none-any.whl[fastapi,httpx,mcp]'
```

按需选择extra；核心不需要FastAPI、HTTPX、MCP或SQLAlchemy。wheel包含类型标记和通用码表；应用自己的码表由应用分发。应用锁依赖；库声明兼容范围。未承诺所有 AI 宿主或生产环境可用。

## 核心：业务模型不随外壳变化

以下是合成库存示例，用于说明跨项目复用。

```python
from pydantic import BaseModel, ConfigDict, Field
from office_contracts import Meta, Success, decode, prepare_success


class InventoryItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    sku: str
    quantity: int = Field(ge=0)


prepared = prepare_success(
    InventoryItem(sku="demo-item", quantity=12),
    InventoryItem,
    Meta(trace_id="demo-001"),
)
result = decode(prepared.body, Success[InventoryItem])
assert result.data.quantity == 12
```

完整响应为：

```json
{
  "code": 0,
  "message": "操作成功",
  "data": {"sku": "demo-item", "quantity": 12},
  "meta": {"trace_id": "demo-001", "contract_version": "1"}
}
```

四字段在线上必须存在；成功data可以明确为null、false或0。Python构造可使用默认成功码／wire版本，但codec和生成Schema要求线上字段完整。核心字段严格校验；bool、浮点和字符串不能冒充数字业务码。接收器使用JSON模式，避免把日期等合法JSON类型误当Python类型拒绝。

`prepare_success`复核独立字段值、执行JSON序列化与语义往返、有限值和预算检查，再保存不可变字节。拒绝被修改实例、无效copy／construct及不符合声明的输出。别名按线上名称输出；自定义序列化器必须纯函数并符合自己的线模型。需要静态类型明确的复杂联合，可声明Pydantic RootModel，或显式使用TypeAdapter；动态联合准备物保留object类型，不假称能静态推导所有运行时类型。

## 注册表、业务码与冻结

```python
from office_contracts import CodeSpec, ContractError, make_registry

registry = make_registry(
    "inventory", [CodeSpec(900001, "inventory", "missing", "库存项不存在", frozenset({404}))]
)
# 业务层可抛受控错误；不把原始异常或请求资料放入消息。
# raise ContractError(900001)
```

`make_registry`合成通用码与应用扩展并冻结；重复数字、重复定义键、错误／警告状态冲突会失败。`failure`验证出站登记码及HTTP映射。嵌套ErrorInfo／Warning必须传入对应Registry准备，并校验类别；警告不能充当失败。

应用内数字唯一，通用码不可覆盖；不同应用各持独立Registry，不共享全局“当前项目”。`registry.export()`产生带SHA256修订的快照，`CodeRegistry.from_export()`验证指纹。不得手改生成快照或按数字前缀推测重试／权限。未知远端码保留数字并安全降级。

## FastAPI：先声明路由，再安装边界

```python
from fastapi import FastAPI
from office_contracts.integrations.fastapi import ContractRouter, install_fastapi

app = FastAPI()
api = ContractRouter(registry=registry, prefix="/api/v2")


@api.get("/inventory", data_model=InventoryItem, errors=[900001])
async def inventory():
    return InventoryItem(sku="demo-item", quantity=12)


app.include_router(api)
install_fastapi(app, registry=registry, api_prefixes=("/api/v2",))
```

也支持FastAPI原有`response_model`参数名，二者不能重复声明。保留显式函数参数、Depends／Security和重复响应头；同步端点进入线程池。受管路由禁止直接返回Response；文件、流、重定向、HEAD和204／205／304保留普通原生路由。依赖不能临时改成未声明状态。

安装范围外的既有处理器保留。纯ASGI边界限制输入、拒绝重复JSON键和非有限值，生成独立追踪；响应开始后出错不再发送第二份JSON，取消继续传播。调用方将CORS等需要覆盖错误的中间件置于适当外层。默认不缓存；ETag、Range和业务快照由应用明确实现。泛化4xx／5xx使用OpenAPI范围条目，避免逐状态膨胀。

事务写入口须在同一工作单元内调用`prepare_success`，提交成功后仅发布该准备物；库不创建事务。若准备失败要回滚；提交结果未知不能声称未写入。已有结果重放仍重新授权；201的已知重放以200/meta.replayed表示，业务负责证明重放。

## 同步与异步HTTPX

```python
import httpx
from office_contracts.integrations.httpx import request_json, arequest_json

with httpx.Client(base_url="http://127.0.0.1:8800", timeout=15) as client:
    result = request_json(client, "GET", "/api/v2/inventory", InventoryItem, registry=registry)
    # 在真实应用中由调用方提供已认证Client或请求头。
```

`arequest_json`使用调用方AsyncClient，返回相同泛型。两者以流读取限制解压后的JSON大小，不每次创建Client；读取结束／异常都关闭流，不自动重试、不跳转、不取得令牌。压缩响应不会二次解压。连接／读／写／池超时保留本地分类；写入未知时不得直接重放。异步调用使用原生可取消总期限；同步调用使用有界后台名额和调用线程总deadline，名额在底层请求实际退出前保持占用，详见文末。不能宣称自定义同步传输一定可被强制终止，或任意解压器的峰值内存已被限制。

- `RemoteError`：合法远端Failure，保留数字码、HTTP状态和安全信息。
- `ProtocolError`：非JSON、字段／类型／版本错误、状态冲突或超预算。
- `TransportFailure`：本地网络失败，无伪造远端code，保守记录write_outcome。
- `NativeResult`：显式原生无正文结果，调用方仍需判断status；HEAD错误不等于成功。

`decode_response`只处理已有响应，不能撤销此前的缓冲。媒体类型、必需字段、trace和HTTP/code关系都会检查；已登记错误用本地受控提示，不默认显示远端原文。接收协议对象可兼容新增字段，业务T继续遵循自身规则。下载直接用调用方原生流接口。

## MCP、批次与任务

`integrations.mcp.output_schema(T)`生成Success[T]/Failure的联合Schema；`tool_success`／`tool_failure`返回官方CallToolResult，structuredContent与TextContent JSON同源，工具执行失败标isError。JSON-RPC、认证握手和协议错误不重包。当前已验证官方SDK模型与Schema；实际AI宿主兼容另验。

`collections.Page[T]`没有虚构total；`BatchResult[T]`以唯一id、逐项成功／失败／未执行及一致父状态表达独立批次，`check_request_ids`验证请求对应。原子写批次仍由应用事务实现，不能用独立批次假装原子。

`execution.TaskResult[T]`区分待执行、执行中、取消请求及终态；成功结果、失败ErrorInfo、部分逐项结果分别必需。`Artifact.availability`与任务终态分开。`check_transition`保护终态；这些仅为表示，不提供持久队列、补偿或跨进程幂等。

## 预算、验证与演进

默认请求JSON1MiB、结果JSON2MiB、深度32、节点100000；Issue最多20项／路径8段，批次最多100项。预算明确可配置，业务容量需实际校准；大结果分页／导出，不放开无限缓冲。核心库本身不做数据库查询或任意SQL代理。

源码仓库检查：

```bash
uv run pytest packages/api-contracts/tests
uv run ruff check packages/api-contracts
uv run mypy packages/api-contracts/src packages/api-contracts/tests/typing --check-untyped-defs
uv build --package office-api-contracts
```

库版本、HTTP资源版本和meta.contract_version分别演进。已发布数字不重用，兼容字段新增不能重定义现有语义。包内业务中立；应用负责金额单位、权限、确认、指标、缓存与重放证据。迁移采用显式v2入口，既有v1／MCP不自动切换。

### 同步调用总deadline与容量

`request_json`复用传入的HTTPX连接池，使用最多8个同步在途名额；调用线程到deadline即返回TransportFailure，停止继续消费响应，不自动重试。已开始但尚未结束的请求保留名额至实际退出，防止超时后无限扩线程。自定义传输若忽略底层网络超时可能继续占用名额，后续超限请求明确拒绝。`arequest_json`继续使用原生asyncio总deadline，无同步线程。
