"""独立浏览器验收入口必须可导入；不运行浏览器或访问实例。"""

import importlib.util
from pathlib import Path


def test_browser_probe_imports_are_valid(monkeypatch):
    folder = Path(__file__).parent
    monkeypatch.syspath_prepend(str(folder))
    for path in sorted(folder.glob("e2e_*.py")):
        spec = importlib.util.spec_from_file_location("import_probe_" + path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert callable(module.main)


def test_browser_page_inventory_is_complete():
    import json

    root = Path(__file__).resolve().parents[1]
    inventory = json.loads((root / "tests/ui-pages.json").read_text())
    templates = root / "office-data-service/app/entrypoints/web/templates"
    pages = {p.name for p in templates.glob("*.html") if "{% extends" in p.read_text()}
    assert pages == {row["template"] for row in inventory["pages"]}
    components = {p.name for p in templates.iterdir() if p.is_file()} - pages
    assert components == set(inventory["components"])


def test_probes_do_not_touch_instance_files_at_import():
    """探针不得在模块级读写实例文件。

    CI 没有 runtime/ 足迹，导入即崩溃会让整条 quality 门失败；本地有足迹则
    看不出问题，因此必须静态约束，而不是依赖导入时的环境差异。
    """
    import ast

    offenders = []
    for path in sorted(Path(__file__).parent.glob("e2e_*.py")):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            # 跳过函数与类定义体，只看模块级执行语句
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr in {"read_text", "read_bytes", "write_text", "mkdir"}
                ):
                    offenders.append(f"{path.name}:{child.lineno} 调用了 {child.func.attr}")
    assert offenders == [], "探针在模块级访问文件系统：" + "；".join(offenders)
