"""交付公开配置的唯一解析入口；不执行shell、不展开变量、不读取秘密。"""

import configparser
import ipaddress
import json
from pathlib import Path
from urllib.parse import urlsplit

KEYS = {
    "OFFICE_DATABASE_HOST",
    "OFFICE_DATABASE_PORT",
    "OFFICE_DATABASE_NAME",
    "OFFICE_DATABASE_OWNER",
    "OFFICE_DATABASE_OWNER_PASSWORD_FILE",
    "OFFICE_DATABASE_USER",
    "OFFICE_DATABASE_PASSWORD_FILE",
    "OFFICE_IDENTITY_DATABASE_NAME",
    "OFFICE_IDENTITY_DATABASE_USER",
    "OFFICE_IDENTITY_DATABASE_PASSWORD_FILE",
    "OFFICE_PUBLIC_URL",
    "OFFICE_DEPLOYMENT_MODE",
    "OFFICE_INSTANCE_DIR",
    "OFFICE_IMAGE",
    "OFFICE_AGENT",
    "OFFICE_LISTEN_ADDRESS",
    "OFFICE_CERTIFICATE_FILE",
    "OFFICE_PRIVATE_KEY_FILE",
    "OFFICE_CA_BUNDLE_FILE",
    "OFFICE_ADMIN_USERNAME",
    "OFFICE_ADMIN_NAME",
}
PRIVATE = tuple(
    ipaddress.ip_network(item) for item in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def read_env(path):
    parser = configparser.ConfigParser(
        interpolation=None, delimiters=("=",), comment_prefixes=("#",), strict=True
    )
    parser.optionxform = str
    parser.read_string("[delivery]\n" + Path(path).read_text(encoding="utf-8-sig"))
    if parser.sections() != ["delivery"] or parser.defaults():
        raise ValueError("交付.env不支持INI分节或默认节")
    values = dict(parser["delivery"])
    if set(values) - KEYS:
        raise ValueError("交付配置包含未知字段")
    for key, value in values.items():
        if value.startswith('"'):
            value = json.loads(value)
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        if not isinstance(value, str) or any(c in value for c in "\r\n$`"):
            raise ValueError("配置只支持单行字面量，不展开变量或执行命令")
        values[key] = value
    if values.get("OFFICE_AGENT", "0") not in {"0", "1"}:
        raise ValueError("OFFICE_AGENT须为0或1")
    mode = values.get("OFFICE_DEPLOYMENT_MODE", "https")
    if mode not in {"https", "lan-http-demo"}:
        raise ValueError("未知部署模式")
    url = values.get("OFFICE_PUBLIC_URL", "").rstrip("/")
    parsed = urlsplit(url)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path
    ):
        raise ValueError("必须填写不含路径或凭证的服务根地址")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("端口无效")
    if mode == "lan-http-demo":
        address = ipaddress.ip_address(parsed.hostname)
        if (
            parsed.scheme != "http"
            or address.version != 4
            or not any(address in subnet for subnet in PRIVATE)
        ):
            raise ValueError("HTTP演示仅支持显式RFC1918私有IPv4地址")
    elif parsed.scheme != "https":
        raise ValueError("正式模式需要HTTPS")
    return {**values, "OFFICE_PUBLIC_URL": url, "OFFICE_DEPLOYMENT_MODE": mode}


def server_settings(values):
    result = {
        "public_url": values["OFFICE_PUBLIC_URL"],
        "allow_lan_http_demo": values["OFFICE_DEPLOYMENT_MODE"] == "lan-http-demo",
        "listen_address": values.get("OFFICE_LISTEN_ADDRESS") or "lan",
        "admin_username": values.get("OFFICE_ADMIN_USERNAME") or "admin",
        "admin_name": values.get("OFFICE_ADMIN_NAME") or "企业管理员",
    }
    for variable, name in (
        ("OFFICE_CERTIFICATE_FILE", "certificate_file"),
        ("OFFICE_PRIVATE_KEY_FILE", "private_key_file"),
        ("OFFICE_CA_BUNDLE_FILE", "ca_bundle_file"),
    ):
        if values.get(variable):
            result[name] = values[variable]
    database_keys = {
        key for key in values if key.startswith(("OFFICE_DATABASE_", "OFFICE_IDENTITY_DATABASE_"))
    }
    if database_keys:
        required = {
            "OFFICE_DATABASE_HOST",
            "OFFICE_DATABASE_OWNER_PASSWORD_FILE",
            "OFFICE_DATABASE_PASSWORD_FILE",
            "OFFICE_IDENTITY_DATABASE_PASSWORD_FILE",
        }
        if not required <= values.keys():
            raise ValueError("外部数据库须完整提供主机及三个密码文件引用，不会回退新建数据库")
        result["external_database"] = {
            "host": values["OFFICE_DATABASE_HOST"],
            "port": int(values.get("OFFICE_DATABASE_PORT", "5432")),
            "name": values.get("OFFICE_DATABASE_NAME", "office"),
            "owner": values.get("OFFICE_DATABASE_OWNER", "office_owner"),
            "user": values.get("OFFICE_DATABASE_USER", "office_app"),
            "identity_name": values.get("OFFICE_IDENTITY_DATABASE_NAME", "office_identity"),
            "identity_user": values.get("OFFICE_IDENTITY_DATABASE_USER", "office_identity"),
            "owner_password_file": values["OFFICE_DATABASE_OWNER_PASSWORD_FILE"],
            "app_password_file": values["OFFICE_DATABASE_PASSWORD_FILE"],
            "identity_password_file": values["OFFICE_IDENTITY_DATABASE_PASSWORD_FILE"],
        }
    return result


def image_selection(values, environment):
    """配置与显式环境不可冲突；不把缺省0提前当成用户选择。"""
    result = {}
    for key in ("OFFICE_AGENT", "OFFICE_IMAGE"):
        configured = values.get(key, "")
        supplied = environment.get(key, "")
        if configured and supplied and configured != supplied:
            raise ValueError(key + "环境与交付配置冲突")
        result[key] = supplied or configured
    result["OFFICE_AGENT"] = result["OFFICE_AGENT"] or "0"
    if result["OFFICE_AGENT"] not in {"0", "1"}:
        raise ValueError("OFFICE_AGENT须为0或1")
    image = result["OFFICE_IMAGE"]
    if image and (image.startswith("-") or any(c.isspace() or ord(c) < 32 for c in image)):
        raise ValueError("镜像引用格式无效")
    return result


if __name__ == "__main__":
    import os
    import sys

    try:
        source = Path(sys.argv[1])
        values = (
            {}
            if source.read_text(encoding="utf-8-sig").lstrip().startswith("{")
            else read_env(source)
        )
        selected = image_selection(values, os.environ)
        print(selected["OFFICE_AGENT"])
        print(selected["OFFICE_IMAGE"])
    except (ValueError, OSError, IndexError):
        print("交付配置或镜像选择无效，请检查OFFICE_AGENT／OFFICE_IMAGE及配置格式", file=sys.stderr)
        sys.exit(2)
