"""专属新实例配置、受限秘密与Compose；不接管已有目录或数据。"""

import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import ssl
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from product import PRODUCT


def write(path, value, uid=10001, gid=10001):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (
        json.dumps(value, ensure_ascii=False, indent=2) + "\n" if isinstance(value, dict) else value
    )
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, uid, gid)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def settings(value):
    allowed = {
        "external_database",
        "public_url",
        "listen_address",
        "certificate_file",
        "private_key_file",
        "admin_username",
        "admin_name",
        "ca_bundle_file",
        "allow_lan_http_demo",
    }
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("安装配置包含未知字段")
    url = urlsplit(value.get("public_url", ""))
    listen = value.get("listen_address") or "lan"
    if not isinstance(listen, str):
        raise TypeError("监听范围必须是 lan、local 或内网IPv4")
    listen_mode = {"lan": "0.0.0.0", "local": "127.0.0.1"}.get(listen)
    if listen_mode is None:
        if listen == "0.0.0.0":
            raise ValueError("监听所有网卡请使用 lan，不直接填写 0.0.0.0")
        address = ipaddress.ip_address(listen)
    else:
        address = ipaddress.ip_address(listen_mode)
    demo = value.get("allow_lan_http_demo", False)
    if type(demo) is not bool:
        raise ValueError("演示模式必须是布尔值")
    if demo:
        host = ipaddress.ip_address(url.hostname or "")
        if (
            url.scheme != "http"
            or host.version != 4
            or not any(
                host in ipaddress.ip_network(net)
                for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            )
        ):
            raise ValueError("明文演示仅允许RFC1918私有IPv4")
    if (
        (url.scheme != "https" and not demo)
        or not url.hostname
        or url.path not in {"", "/"}
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("必须提供不含路径或凭证的企业HTTPS地址")
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", url.hostname) or url.hostname in {
        "localhost",
        "127.0.0.1",
    }:
        raise ValueError("访问地址须为企业DNS域名")
    try:
        ipaddress.ip_address(url.hostname)
    except ValueError:
        public_is_ip = False
    else:
        public_is_ip = True
    if public_is_ip and not demo:
        raise ValueError("正式模式必须使用稳定DNS名称，不能把IP写死为登录地址")
    if address.version != 4 or not (address.is_private or address.is_loopback):
        raise ValueError("监听地址必须是内网IPv4、loopback或 lan/local 模式")
    if listen_mode is None and address.is_unspecified:
        raise ValueError("监听所有网卡请使用 lan 模式")
    bind_mode = (
        "lan" if listen_mode == "0.0.0.0" else "local" if listen_mode == "127.0.0.1" else "address"
    )
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", value.get("admin_username", "admin")):
        raise ValueError("管理员登录名格式无效")
    admin_name = value.get("admin_name", "企业管理员")
    if (
        not isinstance(admin_name, str)
        or not 1 <= len(admin_name.strip()) <= 100
        or any(ord(char) < 32 for char in admin_name)
    ):
        raise ValueError("管理员显示名称须为1至100个可见字符")
    external = value.get("external_database")
    if external is not None:
        from database import validate_external

        validate_external(external)
    return {
        **value,
        "public_url": value["public_url"].rstrip("/"),
        "port": url.port or (80 if demo else 443),
        "allow_lan_http_demo": demo,
        "hostname": url.hostname,
        "listen_address": str(address),
        "bind_mode": bind_mode,
        "admin_username": value.get("admin_username", "admin"),
        "admin_name": admin_name.strip(),
    }


def prepare(root, host_root, source, value, image, pg_uid, pg_gid, kc_uid, kc_gid, images=None):
    """先完成独立准备目录，再发布；准备失败时不让数据库看到半成品秘密。"""
    config = settings(value)
    public = {
        key: item
        for key, item in config.items()
        if key not in {"certificate_file", "private_key_file", "ca_bundle_file"}
    }
    public["custom_ca"] = bool(config.get("ca_bundle_file"))
    installer = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*")):
        if path.is_file() and path.suffix in {".py", ".sh"}:
            installer.update(path.name.encode() + path.read_bytes())
    intent = {
        "settings": public,
        "image": image,
        "source": source,
        "infrastructure": images,
        "installer_sha256": installer.hexdigest(),
    }
    marker = root / "installation.json"
    pending = root / ".preparation.json"
    stage = root / ".preparation"
    if marker.exists():
        original = json.loads(marker.read_text(encoding="utf-8"))
        if any(original.get(key) != item for key, item in intent.items()):
            raise ValueError("已有实例配置或镜像不同，拒绝静默升级")
        return original
    if pending.exists():
        if json.loads(pending.read_text()) != intent or stage.is_symlink():
            raise ValueError("未完成安装与本次配置不同")
    else:
        if any(path.name != ".install.lock" for path in root.iterdir()):
            raise ValueError("实例目录非空且无安装标记，拒绝接管")
        root.chmod(0o700)
        write(pending, intent, 0, 0)
    if not (stage / "installation.json").exists():
        # 尚未发布Compose，不会启动数据库；只清理本安装器的准备区。
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(mode=0o700)
        _prepare(stage, host_root, source, value, image, pg_uid, pg_gid, kc_uid, kc_gid, images)
        prepared = json.loads((stage / "installation.json").read_text())
        write(stage / "installation.json", {**prepared, **intent}, 0, 0)
    for path in stage.iterdir():
        if path.name != "installation.json":
            path.replace(root / path.name)
    (stage / "installation.json").replace(marker)
    stage.rmdir()
    pending.unlink()
    return json.loads(marker.read_text())


def _prepare(root, host_root, source, value, image, pg_uid, pg_gid, kc_uid, kc_gid, images):
    config = settings(value)
    marker = root / "installation.json"
    public = {
        key: item
        for key, item in config.items()
        if key not in {"certificate_file", "private_key_file", "ca_bundle_file"}
    }
    # 证书由管理员提供，不关闭验证、不生成员工自签信任。
    if not config["allow_lan_http_demo"]:
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(
            "/input/fullchain.pem", "/input/privkey.pem"
        )
    project = "office-" + hashlib.sha256(host_root.encode()).hexdigest()[:12]
    for folder, uid, gid in (
        ("service", 10001, 10001),
        ("maintenance", 10001, 10001),
        ("postgres", pg_uid, pg_gid),
        ("identity", kc_uid, kc_gid),
        ("gateway", 0, 0),
        ("credentials", 0, 0),
    ):
        path = root / folder
        path.mkdir(mode=0o700)
        os.chown(path, uid, gid)
        (path / "secrets").mkdir(mode=0o700)
        os.chown(path / "secrets", uid, gid)
    passwords = {
        name: secrets.token_urlsafe(32)
        for name in (
            "postgres",
            "owner",
            "app",
            "identity",
            "identity_admin",
            "first_admin",
            "web_session",
        )
    }
    for name in ("postgres", "owner", "app", "identity"):
        write(root / "postgres/secrets" / (name + "_password"), passwords[name], pg_uid, pg_gid)
    write(root / "identity/secrets/database_password", passwords["identity"], kc_uid, kc_gid)
    write(root / "identity/secrets/admin_password", passwords["identity_admin"], kc_uid, kc_gid)
    write(
        root / "credentials/first-login.txt",
        "登录名: "
        + config["admin_username"]
        + "\n初始密码: "
        + passwords["first_admin"]
        + "\n仅在浏览器输入，首次登录后修改，不发给AI。\n",
        0,
        0,
    )
    write(root / "credentials/initial_password", passwords["first_admin"], 0, 0)
    write(root / "service/secrets/app_password", passwords["app"])
    write(root / "service/secrets/web_session_secret", passwords["web_session"])
    write(
        root / "service/secrets/model_encryption_key",
        base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    )
    write(root / "maintenance/secrets/owner_password", passwords["owner"])
    external = config.get("external_database")
    if external:
        for key, target, uid, gid in (
            ("owner_password_file", "maintenance/secrets/owner_password", 10001, 10001),
            ("app_password_file", "service/secrets/app_password", 10001, 10001),
            ("identity_password_file", "identity/secrets/database_password", kc_uid, kc_gid),
        ):
            secret = Path("/input/" + key)
            if secret.stat().st_mode & 0o077:
                raise ValueError("外部数据库凭证文件必须仅所有者可读写")
            value = secret.read_text().strip()
            if not value or "\n" in value or "\r" in value:
                raise ValueError("外部数据库凭证格式无效")
            write(root / target, value, uid, gid)
    base = {
        "allow_lan_http_demo": config["allow_lan_http_demo"],
        "database_host": external["host"] if external else "postgres",
        "database_port": external["port"] if external else 5432,
        "database_name": external["name"] if external else "office",
        "public_url": config["public_url"],
        "issuer": config["public_url"] + "/identity/realms/office",
        "audience": config["public_url"] + "/mcp",
        "client_id": PRODUCT["client_id"],
        "web_client_id": PRODUCT["web_client_id"],
        "redirect_uri": "http://127.0.0.1:8765/callback",
    }
    write(
        root / "service/service.json",
        {
            **base,
            "database_user": external["user"] if external else "office_app",
            "database_password_file": "secrets/app_password",
            "web_session_secret_file": "secrets/web_session_secret",
            "agent_secret_key_file": "secrets/model_encryption_key",
            "agent_model_url_policy": "public",
            "agent_model_urls": [],
        },
    )
    write(
        root / "maintenance/owner.json",
        {
            **base,
            "database_user": external["owner"] if external else "office_owner",
            "database_password_file": "secrets/owner_password",
            "web_session_secret_file": None,
        },
    )
    if not config["allow_lan_http_demo"]:
        write(root / "gateway/fullchain.pem", Path("/input/fullchain.pem").read_text(), 0, 0)
        write(root / "gateway/privkey.pem", Path("/input/privkey.pem").read_text(), 0, 0)
    if config.get("ca_bundle_file"):
        write(root / "service/ca.pem", Path("/input/ca.pem").read_text())
    write(root / "postgres/init.sh", Path("/input/database-init.sh").read_text(), pg_uid, pg_gid)
    write(root / "gateway/nginx.conf", nginx(config), 0, 0)
    result = {"format": 1, "project": project, "image": image, "settings": public}
    write(root / "compose.json", compose(host_root, source, config, image, project, images), 0, 0)
    # 在秘密及配置全部完成后提交标记；失败目录留存，禁止自动重建口令。
    write(marker, result, 0, 0)
    return result


def nginx(config):
    port = config["port"]
    demo = config.get("allow_lan_http_demo", False)
    scheme = "http" if demo else "https"
    authority = config["hostname"] if port in (80, 443) else f"{config['hostname']}:{port}"
    canonical = f"{scheme}://{authority}"
    tls = (
        ""
        if demo
        else "ssl_certificate /etc/nginx/fullchain.pem; ssl_certificate_key /etc/nginx/privkey.pem; ssl_protocols TLSv1.2 TLSv1.3;"
    )
    listen = str(port) if demo else str(port) + " ssl"
    return f"""events {{}}
http {{
  access_log off;
  resolver 127.0.0.11 valid=10s ipv6=off;
  server {{
    listen {listen};
    server_name {config["hostname"]};
    if ($host != "{config["hostname"]}") {{ return 308 {canonical}$request_uri; }}
    {tls}
    client_max_body_size 2m;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto {scheme};
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Port {port};
    location /identity/ {{ set $identity identity:8080; proxy_pass http://$identity; }}
    location / {{
      set $app service:8800; proxy_pass http://$app;
      proxy_buffering off; proxy_read_timeout 180s;
    }}
  }}
}}
"""


def compose(host, source, config, image, project, images=None):
    def mount(local, target):
        return {
            "type": "bind",
            "source": local,
            "target": target,
            "read_only": True,
            "bind": {"create_host_path": False},
        }

    def bind(relative, target):
        return mount(host + "/" + relative, target)

    logging = {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}
    services = {
        "postgres": {
            "image": "pgvector/pgvector:0.8.7-pg18-trixie@sha256:9d9c930220cb9bf2f956d10a8f909cf9973d9672ca0278aef2c1e6facccad2e0",
            "entrypoint": ["bash", "/opt/office/start.sh"],
            "environment": {
                "POSTGRES_USER": "postgres",
                "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres_password",
            },
            "volumes": [
                "database:/var/lib/postgresql",
                *[
                    bind(
                        "postgres/secrets/" + name + "_password",
                        "/run/secrets/" + name + "_password",
                    )
                    for name in ("postgres", "owner", "app", "identity")
                ],
                bind("postgres/init.sh", "/opt/office/init.sh"),
                mount(source + "/deploy/installer/start-postgres.sh", "/opt/office/start.sh"),
            ],
            "healthcheck": {
                "test": [
                    "CMD-SHELL",
                    'PGPASSWORD="$$(cat /run/secrets/postgres_password)" psql -h 127.0.0.1 -U postgres -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname=\'office_identity\'" | grep -qx 1',
                ],
                "interval": "3s",
                "timeout": "3s",
                "retries": 120,
            },
        },
        "identity": {
            "image": "quay.io/keycloak/keycloak:26.8.1",
            "environment": {
                "KC_DB": "postgres",
                "KC_DB_URL": "jdbc:postgresql://postgres:5432/office_identity",
                "KC_DB_USERNAME": "office_identity",
                "KC_BOOTSTRAP_ADMIN_USERNAME": "setup-admin",
                "KC_HOSTNAME": config["public_url"] + "/identity",
                "KC_HTTP_ENABLED": "true",
                "KC_HTTP_RELATIVE_PATH": "/identity",
                "KC_PROXY_HEADERS": "xforwarded",
            },
            "entrypoint": ["bash", "/opt/office/start.sh"],
            "volumes": [
                bind("identity/secrets/database_password", "/run/secrets/identity_password"),
                bind("identity/secrets/admin_password", "/run/secrets/admin_password"),
                mount(source + "/deploy/installer/start-identity.sh", "/opt/office/start.sh"),
                mount(source + "/deploy/identity/themes/office", "/opt/keycloak/themes/office"),
            ],
            "depends_on": {"postgres": {"condition": "service_healthy"}},
        },
        "gateway": {
            "image": "nginx:1.28-alpine",
            "ports": [f"{config['listen_address']}:{config['port']}:{config['port']}"],
            "volumes": [
                bind("gateway/nginx.conf", "/etc/nginx/nginx.conf"),
                bind("gateway/fullchain.pem", "/etc/nginx/fullchain.pem"),
                bind("gateway/privkey.pem", "/etc/nginx/privkey.pem"),
            ],
            "networks": {"default": {"aliases": [config["hostname"]]}},
        },
        "service": {
            "image": image,
            "user": "10001:10001",
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "tmpfs": ["/tmp:size=64m,mode=1777"],
            "environment": {"OFFICE_CONFIG": "/etc/office/service.json"},
            "volumes": [bind("service", "/etc/office")],
        },
        "setup": {
            "image": image,
            "user": "0:0",
            "profiles": ["setup"],
            "entrypoint": ["python", "/installer/server.py"],
            "volumes": [
                {"type": "bind", "source": host, "target": "/state"},
                mount(source + "/deploy/installer", "/installer"),
                mount(source + "/scripts", "/helpers"),
                mount(source + "/deploy/identity/session-policy.json", "/policy.json"),
            ],
            "environment": {"OFFICE_CONFIG": "/state/maintenance/owner.json"},
            "healthcheck": {"disable": True},
        },
    }
    if config.get("allow_lan_http_demo"):
        services["gateway"]["volumes"] = [bind("gateway/nginx.conf", "/etc/nginx/nginx.conf")]
        services["gateway"].pop("networks", None)
    external = config.get("external_database")
    if external:
        services.pop("postgres")
        services["identity"].pop("depends_on")
        services["identity"]["environment"].update(
            {
                "KC_DB_URL": f"jdbc:postgresql://{external['host']}:{external['port']}/{external['identity_name']}",
                "KC_DB_USERNAME": external["identity_user"],
            }
        )
    for name, reference in (images or {}).items():
        if name in services:
            services[name]["image"] = reference
    if config.get("ca_bundle_file"):
        services["service"]["environment"]["SSL_CERT_FILE"] = "/etc/office/ca.pem"
    for name, service in services.items():
        service["logging"] = logging
        if name != "setup":
            service["restart"] = "unless-stopped"
    return {"name": project, "services": services, "volumes": {} if external else {"database": {}}}
