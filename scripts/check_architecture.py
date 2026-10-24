"""检查本项目已确认的模块依赖方向；包含函数内导入，不以延迟导入规避规则。"""

import argparse
import ast
from importlib.util import resolve_name
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSPORTS = ("fastapi", "starlette", "mcp", "httpx", "sqlalchemy", "psycopg")


def within(name, prefix):
    return name == prefix or name.startswith(prefix + ".")


def imports(path, module):
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            for item in node.names:
                yield item.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = resolve_name("." * node.level + base, package)
            yield base, node.lineno
            for item in node.names:
                if item.name != "*":
                    yield base + "." + item.name, node.lineno


def cycles(graph):
    """Tarjan强连通分量；返回实际循环组而非所有可达节点。"""
    indices, low, stack, active, found = {}, {}, [], set(), []

    def visit(node):
        indices[node] = low[node] = len(indices)
        stack.append(node)
        active.add(node)
        for target in sorted(graph[node]):
            if target not in indices:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in active:
                low[node] = min(low[node], indices[target])
        if low[node] == indices[node]:
            group = []
            while True:
                member = stack.pop()
                active.remove(member)
                group.append(member)
                if member == node:
                    break
            if len(group) > 1 or node in graph[node]:
                found.append(sorted(group))

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    return found


def inspect(root: Path):
    app = root / "office-data-service/app"
    sources = {}
    for path in app.rglob("*.py"):
        parts = path.relative_to(app).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        sources[".".join(("app", *parts))] = path
    graph = {name: set() for name in sources}
    problems = set()
    for name, path in sources.items():
        for dependency, line in imports(path, name):
            if dependency in graph:
                graph[name].add(dependency)
            denied = []
            if within(name, "app.common"):
                denied += [
                    "app.application",
                    "app.modules",
                    "app.entrypoints",
                    "app.infrastructure",
                    *TRANSPORTS,
                ]
            if within(name, "app.modules"):
                denied += ["app.application", "app.entrypoints"]
            if within(name, "app.modules") and path.stem in {
                "schemas",
                "outputs",
                "change_contracts",
                "catalog",
            }:
                denied += ["app.infrastructure", *TRANSPORTS]
                if (
                    dependency.startswith("app.modules.")
                    and len(dependency.split(".")) >= 4
                    and dependency.split(".")[3]
                    not in {
                        "schemas",
                        "outputs",
                        "change_contracts",
                        "catalog",
                    }
                ):
                    denied.append(dependency)
            if name == "app.infrastructure.config":
                denied += ["app.application", "app.modules", "app.entrypoints", *TRANSPORTS]
            if any(within(dependency, prefix) for prefix in denied):
                problems.add(f"{path.relative_to(root)}:{line}: {name} 不得依赖 {dependency}")
    for package, prefixes in [
        ("clients/python/src/office_client", ("app", "sqlalchemy", "psycopg")),
        ("packages/api-contracts/src/office_contracts", ("app", "office_client")),
    ]:
        for path in (root / package).rglob("*.py"):
            parts = path.relative_to(root / package).with_suffix("").parts
            if parts[-1] == "__init__":
                parts = parts[:-1]
            module = ".".join((Path(package).name, *parts))
            for dependency, line in imports(path, module):
                if any(within(dependency, prefix) for prefix in prefixes):
                    problems.add(f"{path.relative_to(root)}:{line}: 独立包不得依赖 {dependency}")
    for group in cycles(graph):
        problems.add("循环依赖：" + " -> ".join(group))
    return sorted(problems)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    problems = inspect(args.root)
    if problems:
        parser.exit(1, "\n".join(problems) + "\n")
    print("ARCHITECTURE_BOUNDARIES_OK")


if __name__ == "__main__":
    main()
