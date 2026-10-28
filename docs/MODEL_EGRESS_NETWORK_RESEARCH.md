# 模型出口与代理网络兼容调研

日期：2026-10-28。范围：服务端托管模型出口；本轮为调研与无密钥连通性验证，不代表新配置已实现或部署。

## 结论

服务端可以处理Fake-IP环境，不要求员工或部署者修改全局代理。优先保留直连校验，引入可配置的模型专用可信DNS解析器；需要企业出站代理的环境再使用显式代理适配。不能把代理解析产生的保留地址直接当公网地址放行。

此前只建议修改Clash的fake-ip-filter是网络侧解决办法，并非唯一方案。当前故障暴露的是服务端网络适配能力欠缺，不应归为模型密钥错误。

## 当前源码事实

- `model_egress.py`通过系统getaddrinfo取所有候选地址；全部通过地址策略后，连接固定IP，保留原Host与SNI及证书验证。
- 内层AsyncHTTPTransport显式trust_env=False，未传proxy；所以只设置HTTP_PROXY／HTTPS_PROXY不能使当前托管传输自动走代理。
- Transport已有resolver注入点，但仅供程序组合／测试；尚无面向部署的可信DNS配置。
- 域名、端口、路径限制、禁止重定向、原始及解压响应预算必须随网络适配保留；不允许新网络路径绕过它们。
- 本轮核对安装版本httpx2 2.13.1；官方文档与本地签名均支持AsyncHTTPTransport的proxy参数。标准HTTPX文档仅作为机制参考，不能替代实际httpx2核对。

## 无密钥实测

当前运行容器的系统DNS将已配置公网供应商域名解析到198.18.0.0/15、2001:2::/48中的地址，策略拒绝；宿主正在运行Clash Verge／Mihomo，符合Fake-IP表现。

相同容器向Cloudflare DoH查询该公开域名的A／AAAA得到正常公网地址；使用查询结果进行TLS握手、server_hostname保留原域名、系统CA校验证书，连接成功。未更改系统／Docker／Clash配置，未读取模型密钥，未调用模型API或验证模型效果。

这只证明本环境具备“可信解析→真实IP→有效TLS”的服务内解决路径，不代表完整SDK调用、代理模式或所有企业网络已经验收。公开DNS没有模型业务数据，但会获知查询域名；企业可选择自己的可信解析器，不默认外发内网域名。

## 方案比较

| 方案 | 场景 | 责任与限制 | 判断 |
|---|---|---|---|
| 系统DNS＋固定IP直连 | 普通服务器网络 | 保持现有全部地址校验 | 默认保留 |
| 模型专用可信DNS／DoH＋固定IP直连 | Fake-IP或系统DNS不适用 | 校验DNS响应、全部地址、原Host/SNI；配置固定解析器、超时和有界缓存；DNS失败不可退回不安全放行 | 当前故障首选补强 |
| 显式HTTP CONNECT代理 | 企业统一出口、必须经过代理 | 仅模型流量走指定代理；若由代理解析目标，最终IP访问控制交由可信代理ACL实施，不能继续声称应用验证了最终连接IP | 通用可选扩展 |
| 放行全部保留地址／关闭TLS | 暂时绕过报错 | 改变边界且易影响其他目标 | 不采纳 |
| 写死本次供应商IP | 临时排查 | 供应商/CDN变化后失效 | 不作为正式配置 |

HTTPX2支持HTTP代理，HTTPS目标经CONNECT隧道完成TLS。显式proxy可以与trust_env=False并存；不需要开启对所有环境变量的自动信任。单纯打开trust_env也不能消除外层先行DNS拦截，须完整适配传输。

## 建议设计边界

1. 模型协议、URL、模型名和密钥继续由模型设置页管理；出网方式和可信基础设施由实例部署配置管理，不能由模型或Agent工具改变。
2. 当前先设计并实现可选可信DNS适配；默认系统解析，不硬编码Cloudflare为所有企业的强制依赖，不静默把企业内部域名发给公共DNS。
3. 显式代理作为独立适配器；代理凭证如需要使用受限文件引用，不放页面回显或日志。不能使用未配置的任意公共代理自动兜底。
4. DNS必须异步、有超时、字节／答案数量限制、TTL上限及缓存隔离；检查查询对应、响应状态和A／AAAA，不把无答案／失败当可用。
5. DoH解析器是运维指定的信任根，HTTPS校验与重定向限制独立；防止解析器自身请求递归进入模型传输。内网模型继续按明确许可和企业DNS处理。
6. 网络故障细分：DNS失败、解析到受限地址、目标越界、重定向拒绝、代理连接／认证失败；不全部压成“出口策略拒绝”，不展示秘密或供应商正文。
7. 测试包括DNS正常／Fake-IP／混合私有地址／无答案／超时／缓存过期、TLS域名与证书、取消、代理CONNECT目标与ACL责任、三种模型协议及响应预算。真实模型调用另行确认费用和验收范围。

上述为建议，尚未增加生产配置字段或修改运行服务。

## 一手资料

- HTTPX2代理与隧道：https://pydantic.dev/docs/httpx2/advanced/proxies/
- HTTPX2源码：https://github.com/pydantic/httpx2
- HTTPX环境变量及trust_env：https://www.python-httpx.org/environment_variables/
- Cloudflare DoH接口：https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/
- OWASP SSRF防护（目标校验、重定向、应用／网络分层）：https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
- Mihomo Fake-IP过滤：https://wiki.metacubex.one/config/dns/#fake-ip-filter

官方资料支撑机制；优先顺序和项目模块边界是结合当前源码与本机实测作出的工程判断，不是上游替本项目完成的安全认证。
