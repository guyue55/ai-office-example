"""核对应用操作、MCP、SDK、CLI及封闭预览集合，不连接实例或读取凭证。"""

import ast
import json
from pathlib import Path

from app.application.commands import OPERATION_INPUTS
from app.entrypoints.mcp.validation import TOOL_INPUTS
from app.modules.access.change_contracts import Change
from app.modules.data.change_contracts import DataChange
from office_client.cli import build_parser
from office_client.client import OfficeClient
from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parents[1]


def check():
    path = ROOT / "office-data-service/app/application/capability_map.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert set(rows) == set(OPERATION_INPUTS), set(rows) ^ set(OPERATION_INPUTS)
    tree = ast.parse(
        (ROOT / "office-data-service/app/entrypoints/mcp/server.py").read_text(encoding="utf-8")
    )
    tools = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "tool"
            for d in node.decorator_list
        )
    }
    assert tools == set(TOOL_INPUTS), tools ^ set(TOOL_INPUTS)
    parser = build_parser()
    commands = next(action.choices for action in parser._actions if action.dest == "action")
    for operation, row in rows.items():
        assert row["mcp"] in tools, operation
        assert callable(getattr(OfficeClient, row["sdk"], None)), operation
        assert row["cli"] in commands, operation
    for union, prefix in ((Change, "access"), (DataChange, "data")):
        schema = TypeAdapter(union).json_schema()
        operations = {
            item["properties"]["operation"]["const"]
            for item in schema["$defs"].values()
            if "operation" in item.get("properties", {})
        }
        declared = {
            op
            for op, row in rows.items()
            if row["mode"] == "preview-confirm" and row["cli"] == prefix + "-apply"
        }
        assert declared == operations, (declared, operations)
    return rows


if __name__ == "__main__":
    rows = check()
    print(f"CAPABILITY_COVERAGE_OK operations={len(rows)}")
