"""校验单份公开配置，生成品牌／身份／命令产物；不迁移既有实例。"""

import argparse
import hashlib
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate(path):
    if path.is_symlink():
        raise ValueError("模板配置不能是软链")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    sections = {
        "product": {"id", "name", "short_name", "description"},
        "brand": {"organization", "primary_color"},
        "client": {"command", "display_name"},
        "distribution": {"namespace"},
    }
    if (
        set(data) != {"schema_version", *sections}
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 1
    ):
        raise ValueError("模板配置字段或版本不支持")
    for section, fields in sections.items():
        if not isinstance(data[section], dict) or set(data[section]) != fields:
            raise ValueError("配置字段不完整或存在未知项：" + section)
        for value in data[section].values():
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 200
                or any(ord(c) < 32 for c in value)
            ):
                raise ValueError("配置必须为非空单行文本且不超过200字符")
    for value in [
        data["product"]["id"],
        data["distribution"]["namespace"],
        data["client"]["command"],
    ]:
        if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", value) or len(value) > 40:
            raise ValueError("技术标识必须是最长40位的小写kebab-case")
    if data["client"]["command"] in {
        "python",
        "python3",
        "pip",
        "uv",
        "sh",
        "bash",
        "git",
        "docker",
        "office-service",
    }:
        raise ValueError("命令名不能占用系统工具")
    if data["distribution"]["namespace"] == "admin":
        raise ValueError("命名空间与身份产品内置客户端冲突")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", data["brand"]["primary_color"]):
        raise ValueError("主题色必须是六位HEX颜色")
    # 文案会进入身份主题属性及HTML标题，保守拒绝HTML／属性转义控制。
    if any(c in value for section in sections for value in data[section].values() for c in "<>\\"):
        raise ValueError("配置不接受HTML标记或反斜线")
    return data


def derived(data):
    namespace = data["distribution"]["namespace"]
    return {
        "name": data["product"]["name"],
        "short_name": data["product"]["short_name"],
        "description": data["product"]["description"],
        "organization": data["brand"]["organization"],
        "primary_color": data["brand"]["primary_color"],
        "command": data["client"]["command"],
        "cli_name": data["client"]["display_name"],
        "namespace": namespace,
        "client_id": namespace + "-cli",
        "web_client_id": namespace + "-web",
        "admin_client_id": namespace + "-account-admin",
    }


def outputs(root, data):
    value = derived(data)
    encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    result = {
        p: encoded
        for p in [
            "clients/python/src/office_client/product.json",
            "office-data-service/app/common/product.json",
            "deploy/installer/product.json",
            "installers/client/product.json",
        ]
    }
    pyproject = root / "clients/python/pyproject.toml"
    text = pyproject.read_text()
    pattern = r'(?m)^[a-z][a-z0-9-]* = "office_client\.cli:main"$'
    if len(re.findall(pattern, text)) != 1:
        raise ValueError("CLI构建入口必须恰有一个，拒绝覆盖自定义结构")
    result["clients/python/pyproject.toml"] = re.sub(
        pattern, value["command"] + ' = "office_client.cli:main"', text
    )
    policy = json.loads((root / "deploy/identity/session-policy.json").read_text())
    desired = {}
    for suffix in ["cli", "web"]:
        matches = [key for key in policy["clients"] if key.endswith("-" + suffix)]
        if len(matches) != 1:
            raise ValueError("身份策略入口不唯一")
        desired[value[suffix + "_client_id" if suffix == "web" else "client_id"]] = policy[
            "clients"
        ][matches[0]]
    policy["clients"] = desired
    result["deploy/identity/session-policy.json"] = (
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n"
    )
    for locale in ["zh_CN", "en"]:
        result[f"deploy/identity/themes/office/login/messages/messages_{locale}.properties"] = (
            f"loginTitle=登录{value['name']}\nloginAccountTitle=登录工作台\nloginTitleHtml={value['short_name']} · 管理工作台\n"
        )
    result["deploy/identity/themes/office/login/resources/css/product.css"] = (
        ":root { --pf-v5-global--primary-color--100: "
        + value["primary_color"]
        + "; --pf-v5-global--primary-color--200: "
        + value["primary_color"]
        + "; }\n"
    )
    schema = json.loads((root / "contracts/openapi.json").read_text())
    schema["info"] = {
        "title": value["name"],
        "description": value["description"],
        **{
            key: item for key, item in schema["info"].items() if key not in {"title", "description"}
        },
    }
    result["contracts/openapi.json"] = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    result["template-state.json"] = (
        json.dumps(
            {
                "schema_version": 1,
                "template_version": (root / "VERSION").read_text().strip(),
                "config_sha256": hashlib.sha256(canonical).hexdigest(),
                "identity_namespace": value["namespace"],
                "command": value["command"],
                "generated_files": sorted(result),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return result


def configure(root, config, *, check=False):
    root = root.resolve()
    data = validate(config)
    content = outputs(root, data)
    for name in content:
        path = root / name
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root.parent):
            raise ValueError("生成目标不能包含软链")
    changed = [
        name
        for name, text in content.items()
        if not (root / name).is_file() or (root / name).read_text() != text
    ]
    if check:
        if changed:
            raise ValueError("生成配置漂移：" + ", ".join(changed))
        return []
    if not changed:
        return []
    # 配置变更在构建前完成；任何本地实例足迹均要求独立迁移，不猜测停用状态。
    if (root / "runtime").exists() and any((root / "runtime").iterdir()):
        raise ValueError("存在实例运行目录；请从干净模板生成新项目，禁止原地改实例身份或品牌")
    previous = {
        name: (root / name).read_bytes() if (root / name).exists() else None for name in changed
    }
    done = []
    try:
        for name in changed:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
                tmp = Path(f.name)
                try:
                    f.write(content[name].encode())
                    f.flush()
                    os.fsync(f.fileno())
                    os.replace(tmp, path)
                finally:
                    tmp.unlink(missing_ok=True)
            done.append(name)
    except BaseException:
        for name in reversed(done):
            if previous[name] is None:
                (root / name).unlink(missing_ok=True)
            else:
                (root / name).write_bytes(previous[name])
        raise
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        changed = configure(args.root, args.config or args.root / "template.toml", check=args.check)
    except (ValueError, OSError) as e:
        parser.exit(2, str(e) + "\n")
    print(
        json.dumps(
            {"status": "verified" if args.check else "configured", "changed": changed},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
