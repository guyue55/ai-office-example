"""离线客户端环境诊断；不读取凭证、不联网、不登录或续期。"""

import platform
import sys
from importlib.metadata import PackageNotFoundError, version

import keyring

from .auth import ClientError, KeyringStore, trusted_url
from .build_info import installed_identity


def diagnose(service: str | None, *, allow_lan_http_demo=False) -> dict:
    if service:
        trusted_url(service, allow_lan_http_demo=allow_lan_http_demo)
    checks = {"python": (3, 13) <= sys.version_info[:2] < (3, 15)}
    packages = {}
    for name in ("office-client", "office-api-contracts", "httpx", "keyring"):
        try:
            packages[name] = version(name)
            checks[name] = True
        except PackageNotFoundError:
            checks[name] = False
    try:
        KeyringStore.check()
        checks["secure_backend_selected"] = True
    except (ClientError, keyring.errors.KeyringError, OSError, ImportError):
        # 系统后端异常不回显本机路径或秘密；实际读写须另行登录验收。
        checks["secure_backend_selected"] = False
    identities = {}
    for name in ("office-client", "office-api-contracts"):
        try:
            identities[name] = installed_identity(name)
            checks[name + "_content_readable"] = identities[name] is not None
        except (OSError, ValueError, ImportError):
            identities[name] = None
            checks[name + "_content_readable"] = False
    return {
        "ok": all(checks.values()),
        "platform": platform.system(),
        "python": platform.python_version(),
        "packages": packages,
        "content_identity": identities,
        "checks": checks,
        "service_configured": bool(service),
        "online_verified": False,
        "credential_io_verified": False,
        "notice": "仅检查解释器、依赖与安全后端选择；未读取凭证，未验证网络、浏览器或凭证库读写",
    }
