"""技能结构、套件内引用与真实CLI参数检查；不冒充模型行为评测。"""

import contextlib
import io
import json
import re
from pathlib import Path

import yaml
from office_client.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "office-skills"
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def inside(path, root):
    return path.resolve().is_relative_to(root.resolve())


def check(suite=SUITE):
    contract = json.loads((suite / "contracts.json").read_text(encoding="utf-8"))
    actual = {p.relative_to(suite).as_posix() for p in suite.rglob("SKILL.md")}
    assert actual == set(contract["skills"]), "技能登记与文件不一致"
    names = set()
    for relative in contract["skills"]:
        path = suite / relative
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---\n"), relative
        metadata = yaml.safe_load(text.split("---", 2)[1])
        name = metadata["name"]
        assert name == path.parent.name and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
        assert len(name) <= 64 and name not in names
        assert 1 <= len(metadata["description"]) <= 1024
        assert "<" not in metadata["description"] and ">" not in metadata["description"]
        assert set(metadata) <= {
            "name",
            "description",
            "metadata",
            "license",
            "compatibility",
            "allowed-tools",
        }
        assert all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in metadata.get("metadata", {}).items()
        )
        if "compatibility" in metadata:
            assert (
                isinstance(metadata["compatibility"], str) and len(metadata["compatibility"]) <= 500
            )
        names.add(name)
    # 只校验操作入口和参考；README的开发者仓库导航不属于离线运行依赖。
    paths = [suite / p for p in contract["skills"]]
    paths += [suite / "RULES.md", *suite.rglob("references/*.md")]
    for path in paths:
        for link in LINK.findall(path.read_text(encoding="utf-8")):
            if link.startswith(("https://", "http://", "#")):
                continue
            target = path.parent / link.split("#", 1)[0]
            assert inside(target, suite) and target.is_file(), f"套件引用不可用：{path.name}→{link}"
    parser = build_parser()
    for argv in contract["cli_examples"]:
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                parser.parse_args(argv)
            except SystemExit as error:
                assert error.code == 0, "命令示例与解析器不符"
    ids = set()
    for case in contract["behavior_cases"]:
        assert case["id"] not in ids
        ids.add(case["id"])
        assert set(case["expected_routes"]) <= names
        assert all(case[key] for key in ("prompt", "required_actions", "forbidden_side_effects"))
    return {
        "skills": len(names),
        "cli_examples": len(contract["cli_examples"]),
        "behavior_cases_declared": len(ids),
        "ai_behavior_verified": False,
    }


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False))
