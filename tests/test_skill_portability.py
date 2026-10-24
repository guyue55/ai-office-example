"""跨平台技能登记路径测试。"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_skill_registry_uses_portable_paths(monkeypatch):
    from pathlib import PureWindowsPath

    spec = importlib.util.spec_from_file_location(
        "office_test_skill_checker", ROOT / "scripts/check_skills.py"
    )
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    original = Path.relative_to

    def relative(path, *args, **kwargs):
        return PureWindowsPath(original(path, *args, **kwargs))

    # Linux/Mac同样能检测把反斜线文件路径误当成合同标识的缺陷。
    monkeypatch.setattr(Path, "relative_to", relative)
    assert checker.check()["skills"] == 2
