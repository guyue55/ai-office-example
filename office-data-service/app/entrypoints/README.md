# 传输入口

已实现HTTP、MCP、无AI Web与服务CLI；业务操作共享application/service.py。协议错误响应集中在errors.py，业务模块只抛出common中的安全错误类型。

HTTP/MCP工具参数与输出引用模块的纯模型；不得在传输入口复制业务查询或授权规则。CLI的bootstrap为离线owner操作，没有公开HTTP引导后门。
