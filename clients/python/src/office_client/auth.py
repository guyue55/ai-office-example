"""浏览器授权码 + PKCE；令牌只保存在操作系统凭证管理器。"""

import base64
import hashlib
import hmac
import ipaddress
import json
import math
import secrets
import time
import webbrowser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import keyring

from .credential_lock import CredentialLockError, credential_guard
from .product import PRODUCT


class ClientError(Exception):
    pass


class TokenStore(Protocol):
    def load(self) -> dict | None: ...
    def save(self, value: dict) -> None: ...
    def clear(self) -> None: ...


class KeyringStore:
    def __init__(self, service_url: str):
        self.service = PRODUCT["namespace"] + ":" + service_url
        self.coordination_key = self.service

    @staticmethod
    def check():
        backend = keyring.get_keyring()
        if type(backend).__module__ not in {
            "keyring.backends.macOS",
            "keyring.backends.SecretService",
            "keyring.backends.Windows",
        }:
            raise ClientError("未检测到支持的系统安全凭证库；请配置系统钥匙串，不会退回明文文件")

    def load(self):
        self.check()
        try:
            value = keyring.get_password(self.service, "oauth")
            return json.loads(value) if value else None
        except (keyring.errors.KeyringError, ValueError):
            raise ClientError("系统凭证库不可读或记录损坏；请在本机修复，不会回退明文") from None

    def save(self, value):
        self.check()
        try:
            keyring.set_password(self.service, "oauth", json.dumps(value))
        except keyring.errors.KeyringError:
            raise ClientError("系统凭证库写入失败，登录尚未安全保存；不会回退明文") from None

    def clear(self):
        self.check()
        try:
            keyring.delete_password(self.service, "oauth")
        except keyring.errors.PasswordDeleteError:
            pass
        except keyring.errors.KeyringError:
            raise ClientError("系统凭证库清理未确认，请在本机核对") from None


def trusted_url(url: str, *, loopback_http=True, allow_lan_http_demo=False):
    if type(allow_lan_http_demo) is not bool:
        raise ClientError("演示开关必须为布尔值")
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ClientError("服务地址缺少有效主机名")
    if parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ClientError("服务地址不能含凭证、查询或片段")
    demo = False
    if allow_lan_http_demo:
        try:
            address = ipaddress.ip_address(parsed.hostname)
            demo = address.version == 4 and any(
                address in ipaddress.ip_network(net)
                for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
            )
        except ValueError:
            pass
    if parsed.scheme != "https" and not (
        parsed.scheme == "http"
        and (demo or (loopback_http and parsed.hostname in {"127.0.0.1", "localhost"}))
    ):
        raise ClientError("服务需要 HTTPS；仅本地开发地址允许 HTTP")
    return url.rstrip("/")


def safe_seconds(value: object) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= 253402300799
        and math.isfinite(value)
    ):
        return float(value)
    return None


def credential_expiry(tokens: object) -> float | None:
    if (
        not isinstance(tokens, dict)
        or not isinstance(tokens.get("access_token"), str)
        or not tokens["access_token"]
    ):
        return None
    return safe_seconds(tokens.get("expires_at"))


class OAuth:
    def __init__(
        self,
        service_url: str,
        store: TokenStore,
        browser_open=webbrowser.open,
        *,
        allow_lan_http_demo=False,
    ):
        self.service = trusted_url(service_url, allow_lan_http_demo=allow_lan_http_demo)
        self.allow_lan_http_demo = allow_lan_http_demo
        self.store, self.browser_open = store, browser_open
        self.http = httpx.Client(timeout=10, follow_redirects=False, trust_env=False)
        self._config: dict | None = None

    @contextmanager
    def coordinated(self):
        try:
            with credential_guard(self.store):
                yield
        except (CredentialLockError, OSError):
            raise ClientError("本机凭证协调未完成，请检查权限或稍后重试") from None

    def config(self):
        if self._config is None:
            response = self.http.get(self.service + "/auth/config")
            response.raise_for_status()
            config = response.json()
            if not isinstance(config, dict) or not all(
                isinstance(config.get(key), str) and bool(config[key])
                for key in ("issuer", "client_id", "redirect_uri", "scope")
            ):
                raise ClientError("授权发现响应无效")
            trusted_url(config["issuer"], allow_lan_http_demo=self.allow_lan_http_demo)
            if (
                urlparse(config["issuer"]).scheme == "http"
                and self.allow_lan_http_demo
                and urlparse(config["issuer"]).netloc != urlparse(self.service).netloc
            ):
                raise ClientError("HTTP演示的身份入口必须与服务同源")
            self._config = config
        assert self._config is not None
        return self._config

    def status(self):
        """仅本机凭证元信息，不联网、不刷新、不清除，也不证明远端身份有效。"""
        tokens = self.store.load()
        expiry = credential_expiry(tokens)
        state = (
            "missing"
            if tokens is None
            else "invalid"
            if expiry is None
            else "expired"
            if expiry <= time.time()
            else "present"
        )
        refresh_expiry = (
            safe_seconds(tokens.get("refresh_expires_at")) if isinstance(tokens, dict) else None
        )
        refresh_state = (
            "reauthentication_required"
            if isinstance(tokens, dict) and tokens.get("refresh_outcome") == "invalid"
            else "uncertain"
            if isinstance(tokens, dict) and tokens.get("refresh_outcome") == "pending"
            else "unavailable"
            if not isinstance(tokens, dict) or not tokens.get("refresh_token")
            else "unknown"
            if refresh_expiry is None
            else "expired"
            if refresh_expiry <= time.time()
            else "available_locally"
        )
        return {
            "service": self.service,
            "credential_state": state,
            "expires_at": expiry,
            "refresh_state": refresh_state,
            "refresh_expires_at": refresh_expiry,
            "online_verified": False,
            "notice": "访问令牌到期不等于授权失效；本机刷新期限也不证明远端有效，当前员工与权限请用whoami核对。",
        }

    def login(self, timeout: float = 120, *, on_authorize=None, open_browser=True):
        config = self.config()
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        state = secrets.token_urlsafe(32)
        redirect = urlparse(config["redirect_uri"])
        if redirect.scheme != "http" or redirect.hostname != "127.0.0.1" or not redirect.port:
            raise ClientError("当前客户端只接受已登记的本机回调地址")
        result = {}

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # 授权码不得出现在 HTTP 日志。

            def do_GET(self):
                incoming = urlparse(self.path)
                query = parse_qs(incoming.query)
                supplied = query.get("state", [""])[0]
                valid = (
                    incoming.path == redirect.path
                    and hmac.compare_digest(supplied, state)
                    and len(query.get("state", [])) == 1
                )
                if not valid:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write("授权回调校验失败".encode())
                    return
                if "code" in query and len(query["code"]) == 1:
                    result["code"] = query["code"][0]
                else:
                    result["error"] = "浏览器未完成授权"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write("授权已接收，可以关闭此窗口。".encode())

        params = {
            "client_id": config["client_id"],
            "redirect_uri": config["redirect_uri"],
            "response_type": "code",
            "scope": config["scope"],
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        url = config["issuer"] + "/protocol/openid-connect/auth?" + urlencode(params)
        try:
            server = HTTPServer(("127.0.0.1", redirect.port), Callback)
        except OSError:
            raise ClientError("本机授权回调端口不可用，请结束旧登录进程后再试") from None
        with server:
            server.timeout = 0.5
            if on_authorize is not None:
                on_authorize(url)
            if open_browser:
                try:
                    opened = self.browser_open(url)
                except (OSError, webbrowser.Error):
                    opened = False
                if opened is False and on_authorize is None:
                    raise ClientError(
                        "无法打开浏览器，请使用office login --no-browser获取同机授权链接"
                    )
            deadline = time.monotonic() + timeout
            while not result and time.monotonic() < deadline:
                server.handle_request()
        if "code" not in result:
            raise ClientError(result.get("error", "浏览器授权超时，请重新登录"))
        response = self.http.post(
            config["issuer"] + "/protocol/openid-connect/token",
            data={
                "grant_type": "authorization_code",
                "client_id": config["client_id"],
                "redirect_uri": config["redirect_uri"],
                "code": result["code"],
                "code_verifier": verifier,
            },
        )
        if not response.is_success:
            raise ClientError("授权码兑换失败，请重新登录")
        self._save(response.json())

    def _save(self, tokens, *, previous_refresh=None):
        with self.coordinated():
            self._persist(tokens, previous_refresh=previous_refresh)

    def _persist(self, tokens, *, previous_refresh=None):
        # 先验证再替换本机状态；错误响应或缺字段不得污染可恢复凭证。
        if not isinstance(tokens, dict):
            raise ClientError("身份服务响应无效，凭证未更新")
        lifetime = safe_seconds(tokens.get("expires_in"))
        if (
            not isinstance(tokens.get("access_token"), str)
            or not tokens["access_token"]
            or lifetime is None
            or not 0 < lifetime < 253402300799 - time.time()
            or (
                "refresh_token" in tokens
                and (not isinstance(tokens["refresh_token"], str) or not tokens["refresh_token"])
            )
        ):
            raise ClientError("身份服务凭证响应无效，凭证未更新")
        safe = {"access_token": tokens["access_token"], "expires_at": time.time() + lifetime}
        refresh = tokens.get("refresh_token", previous_refresh)
        if refresh:
            safe["refresh_token"] = refresh
        refresh_lifetime = tokens.get("refresh_expires_in")
        if refresh_lifetime is not None:
            parsed = safe_seconds(refresh_lifetime)
            if parsed is None or parsed > 253402300799 - time.time():
                raise ClientError("刷新凭证期限响应无效，凭证未更新")
            if parsed > 0:
                safe["refresh_expires_at"] = time.time() + parsed
        elif previous_refresh is not None:
            previous = self.store.load()
            if isinstance(previous, dict) and "refresh_expires_at" in previous:
                safe["refresh_expires_at"] = previous["refresh_expires_at"]
        safe["credential_id"] = secrets.token_hex(16)
        self.store.save(safe)

    def access_token(self):
        with self.coordinated():
            tokens = self.store.load()
            if not tokens:
                raise ClientError("尚未登录，请使用相同--service执行office login")
            expiry = credential_expiry(tokens)
            if expiry is None:
                raise ClientError("本机凭证记录异常，请使用相同--service重新浏览器授权")
            if tokens.get("refresh_outcome") == "invalid":
                raise ClientError("登录授权已失效，请重新浏览器授权")
            if tokens.get("refresh_outcome") == "pending":
                raise ClientError("上次登录续期结果未确认，请重新浏览器授权；不会重用旧刷新凭证")
            if expiry > time.time() + 15:
                return tokens["access_token"]
            if not tokens.get("refresh_token"):
                raise ClientError("访问令牌已到期且没有刷新凭证，请重新登录")
            config = self.config()
            pending = {
                **tokens,
                "refresh_outcome": "pending",
                "refresh_attempt": secrets.token_hex(16),
            }
            # 先安全登记再发送。进程退出或响应保存失败，也不让另一进程重用可能已消费的凭证。
            self.store.save(pending)
            try:
                response = self.http.post(
                    config["issuer"] + "/protocol/openid-connect/token",
                    data={
                        "grant_type": "refresh_token",
                        "client_id": config["client_id"],
                        "refresh_token": tokens["refresh_token"],
                    },
                )
            except httpx.HTTPError:
                raise ClientError(
                    "身份续期结果未确认，请重新浏览器授权；旧记录已保留但不会重用"
                ) from None
            current = self.store.load()
            if (
                not isinstance(current, dict)
                or current.get("refresh_attempt") != pending["refresh_attempt"]
            ):
                raise ClientError("本机登录已变化，请重新确认当前账号")
            if not response.is_success:
                if response.status_code >= 500 or response.status_code == 429:
                    try:
                        failure = response.json()
                    except ValueError:
                        failure = {}
                    if isinstance(failure, dict) and failure.get("error") in {
                        "temporarily_unavailable",
                        "server_error",
                    }:
                        self.store.save(tokens)
                        raise ClientError("身份服务暂不可用；本机凭证已保留，请稍后重试")
                    # 无明确OAuth失败回执时，不能推断轮换未发生。
                    raise ClientError(
                        "身份服务暂不可用；本机凭证已保留但续期结果不确定，请重新授权"
                    )
                if response.status_code in (400, 401):
                    try:
                        failure = response.json()
                    except ValueError:
                        failure = {}
                    if isinstance(failure, dict) and failure.get("error") in {
                        "invalid_client",
                        "unauthorized_client",
                        "invalid_scope",
                    }:
                        raise ClientError("身份客户端配置不匹配，请联系管理员；本机记录未清除")
                    if isinstance(failure, dict) and failure.get("error") == "invalid_grant":
                        self.store.save({**pending, "refresh_outcome": "invalid"})
                        raise ClientError("登录已失效，请使用相同--service重新登录")
                raise ClientError("身份续期未确认，请重新授权，不会清除其他登录")
            try:
                refreshed = response.json()
                self._save(refreshed, previous_refresh=tokens["refresh_token"])
            except (ValueError, ClientError):
                raise ClientError(
                    "新凭证未能安全保存，请重新授权；不会重复使用旧刷新凭证"
                ) from None
            return refreshed["access_token"]

    def logout(self):
        with self.coordinated():
            self._logout()

    def _logout(self):
        tokens = self.store.load()
        try:
            if tokens and tokens.get("refresh_token"):
                config = self.config()
                response = self.http.post(
                    config["issuer"] + "/protocol/openid-connect/logout",
                    data={
                        "client_id": config["client_id"],
                        "refresh_token": tokens["refresh_token"],
                    },
                )
                if not response.is_success:
                    raise ClientError("身份服务注销未确认；本机凭证已清除")
        finally:
            self.store.clear()

    def close(self):
        self.http.close()
