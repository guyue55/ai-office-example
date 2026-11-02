"""连接测试只输出固定类别，不返回供应商异常正文。"""

import ssl

from app.infrastructure.model_errors import ModelEgressError

MESSAGES = {
    "response_limit": (
        "模型响应超过传输预算",
        "请核对模型网关及响应格式；不要直接扩大业务输出预算。",
    ),
    "authentication": ("模型认证失败", "核对密钥是否有效以及鉴权方式。"),
    "permission": ("供应商拒绝此调用", "核对模型访问权限及供应商账号状态。"),
    "endpoint_or_model": ("地址或模型不匹配", "核对base URL、协议及准确模型标识。"),
    "rate_limit": ("供应商限流或配额不足", "核对配额后再手动测试，不要连续重试。"),
    "provider": ("供应商暂时不可用", "查看供应商状态，稍后手动测试。"),
    "dns_fake_ip": (
        "模型域名解析到代理Fake-IP地址",
        "请配置服务端模型专用可信DNS；无需更换密钥或关闭出口校验。",
    ),
    "dns_address": ("模型域名解析到受限地址", "请运维核对DNS；确属内网模型时需明确允许目标。"),
    "dns": ("模型域名解析失败", "请运维检查服务器DNS。"),
    "tls": ("模型TLS验证失败", "检查证书及服务器信任链，不要关闭证书验证。"),
    "connect": ("无法连接模型服务", "检查地址、端口与服务器出口网络。"),
    "timeout": ("模型测试超时", "核对网络与模型响应时间，必要时调整超时。"),
    "policy": ("模型请求被出口策略拒绝", "核对目标和解析结果；内网地址需运维明确允许。"),
    "invalid_response": ("未收到兼容的模型响应", "核对协议、模型与供应商兼容性。"),
}


def failure(exc):
    category = "invalid_response"
    current, seen = exc, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = getattr(current, "status_code", None)
        if isinstance(current, ModelEgressError):
            category = current.category
            break
        if isinstance(current, (TimeoutError,)) or "Timeout" in type(current).__name__:
            category = "timeout"
        elif isinstance(current, ssl.SSLError):
            category = "tls"
        elif isinstance(status, int) and status >= 400:
            category = {
                401: "authentication",
                403: "permission",
                402: "rate_limit",
                408: "timeout",
                429: "rate_limit",
            }.get(status, "provider" if status >= 500 else "endpoint_or_model")
        elif "Connect" in type(current).__name__:
            category = "connect"
        current = current.__cause__ or current.__context__
    message, suggestion = MESSAGES[category]
    return {"passed": False, "category": category, "message": message, "suggestion": suggestion}
