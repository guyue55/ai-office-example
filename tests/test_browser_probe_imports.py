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
