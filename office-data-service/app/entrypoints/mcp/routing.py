"""保留完整MCP中间件，但不让根挂载吞掉HTTP业务路由的405。"""

from starlette.routing import Match, Mount


class ProtocolMount(Mount):
    def matches(self, scope):
        path = scope.get("path", "")
        root = scope.get("root_path", "")
        if root and path.startswith(root + "/"):
            path = path[len(root) :]
        if path not in {"/mcp", "/mcp/"} and not path.startswith("/.well-known/"):
            return Match.NONE, {}
        return super().matches(scope)
