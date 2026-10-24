"""固定身份来源的异步管理客户端；不记录上游正文、凭证或原始异常。"""

import asyncio
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from app.common.async_work import WorkCapacityExceeded
from app.common.errors import DomainError
from app.infrastructure.secrets import read_secret
from app.modules.access.identity_ports import LoginAccount


class KeycloakAdmin:
    def __init__(self, issuer: str, client_id: str, secret_file: Path, *, transport=None):
        url = urlsplit(issuer)
        root, marker, realm = url.path.rpartition("/realms/")
        if not marker or not realm or "/" in realm or realm == "master":
            raise ValueError("身份管理必须绑定专用企业realm")
        self.issuer = issuer
        self.base = f"{url.scheme}://{url.netloc}{root}/admin/realms/{quote(realm, safe='')}"
        self.client_id, self.secret_file = client_id, secret_file
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(10, connect=3),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
            follow_redirects=False,
            transport=transport,
        )
        self._token, self._deadline = "", 0.0
        self._refresh = asyncio.Lock()
        self._capacity = asyncio.Semaphore(4)
        self._secret_capacity = asyncio.Semaphore(1)

    async def aclose(self):
        self._token = ""
        await self.http.aclose()

    async def _access_token(self):
        async with self._refresh:
            if self._token and time.monotonic() < self._deadline:
                return self._token
            try:
                # 文件读入后只用于换取令牌；不把秘密存入配置或诊断输出。
                secret = await read_secret(self.secret_file, self._secret_capacity)
                response = await self.http.post(
                    self.issuer + "/protocol/openid-connect/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": secret,
                    },
                )
                if response.status_code != 200:
                    raise DomainError(
                        503,
                        "identity_unavailable",
                        "账号管理连接暂不可用，请联系管理员检查服务配置",
                    )
                payload = response.json()
                if not isinstance(payload, dict):
                    raise TypeError("令牌响应无效")
                token, ttl = payload.get("access_token"), payload.get("expires_in")
                if not isinstance(token, str) or not token or type(ttl) is not int or ttl <= 0:
                    raise ValueError("令牌响应无效")
                self._token = token
                self._deadline = time.monotonic() + max(0, ttl - 15)
                return token
            except WorkCapacityExceeded:
                raise DomainError(503, "authentication_busy", "身份校验繁忙，请稍后重试") from None
            except (httpx.HTTPError, OSError, ValueError, TypeError):
                raise DomainError(503, "identity_unavailable", "账号管理连接暂不可用") from None

    async def _request(self, method: str, path: str, *, payload=None, params=None):
        async with self._capacity:
            token = await self._access_token()
            try:
                response = await self.http.request(
                    method,
                    self.base + path,
                    json=payload,
                    params=params,
                    headers={"Authorization": "Bearer " + token},
                )
            except httpx.HTTPError:
                # 写入超时可能已经生效，由持久化操作层核实，适配器不自动重试。
                raise DomainError(
                    503, "identity_unavailable", "身份服务响应异常，操作结果需核实"
                ) from None
        if response.status_code == 404:
            raise DomainError(404, "not_found", "登录账号不存在")
        if response.status_code == 409:
            raise DomainError(409, "constraint_conflict", "登录账号已存在，请核对或关联已有账号")
        if response.status_code in (400, 422):
            raise DomainError(
                422, "invalid_request", "身份服务拒绝此操作，请核对账号资料或安全策略"
            )
        if response.status_code in (401, 403):
            self._token, self._deadline = "", 0
            raise DomainError(503, "identity_unavailable", "账号管理连接权限不足，请联系管理员")
        if response.status_code not in (200, 201, 204):
            raise DomainError(503, "identity_unavailable", "身份服务暂时不可用，请稍后核实结果")
        return response

    @staticmethod
    def _account(row: dict) -> LoginAccount:
        try:
            identifier, username = row["id"], row["username"]
            if (
                not isinstance(identifier, str)
                or not identifier
                or not isinstance(username, str)
                or not username
                or type(row["enabled"]) is not bool
            ):
                raise ValueError("账号响应无效")
            attributes = row.get("attributes") or {}
            if not isinstance(attributes, dict):
                raise TypeError("账号属性无效")
            operation = attributes.get("officeOperation", [])
            actions = row.get("requiredActions") or []
            if (
                not isinstance(operation, list)
                or not all(isinstance(value, str) for value in operation)
                or not isinstance(actions, list)
                or not all(isinstance(value, str) for value in actions)
            ):
                raise TypeError("账号属性无效")
            name = str(row.get("firstName") or username)
            return LoginAccount(
                identifier,
                username,
                name,
                str(row.get("email") or ""),
                row["enabled"],
                tuple(actions),
                str(operation[0]) if operation else "",
            )
        except (KeyError, TypeError, ValueError):
            raise DomainError(
                503, "identity_unavailable", "身份服务返回的账号资料无法核验"
            ) from None

    @staticmethod
    def _json(response):
        try:
            return response.json()
        except ValueError:
            raise DomainError(503, "identity_unavailable", "身份服务返回内容无法核验") from None

    async def search(self, query: str, limit: int = 20) -> list[LoginAccount]:
        response = await self._request(
            "GET",
            "/users",
            params={"search": query, "max": min(limit, 20), "briefRepresentation": "false"},
        )
        rows = self._json(response)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise DomainError(503, "identity_unavailable", "账号查询结果无法核验")
        return [self._account(row) for row in rows if not row.get("serviceAccountClientId")]

    async def get(self, identifier: str) -> LoginAccount:
        response = await self._request("GET", "/users/" + quote(identifier, safe=""))
        row = self._json(response)
        if not isinstance(row, dict) or row.get("serviceAccountClientId"):
            raise DomainError(404, "not_found", "不支持管理此登录账号")
        return self._account(row)

    async def find_username(self, username: str) -> LoginAccount | None:
        response = await self._request(
            "GET",
            "/users",
            params={
                "username": username,
                "exact": "true",
                "max": 2,
                "briefRepresentation": "false",
            },
        )
        rows = self._json(response)
        if not isinstance(rows, list) or len(rows) > 1:
            raise DomainError(503, "identity_unavailable", "登录账号唯一性无法核验")
        return await self.get(str(rows[0]["id"])) if rows else None

    async def create(self, username: str, name: str, email: str, operation_id: str) -> LoginAccount:
        payload = {
            "username": username,
            "firstName": name,
            "enabled": False,
            "requiredActions": ["UPDATE_PASSWORD"],
            "attributes": {"officeOperation": [operation_id]},
        }
        if email:
            payload["email"] = email
        await self._request("POST", "/users", payload=payload)
        # 不信任上游Location作为可请求URL；固定realm内按唯一登录名回读。
        account = await self.find_username(username)
        if account is None or account.operation_id != operation_id:
            raise DomainError(503, "identity_unavailable", "账号可能已建立，归属标记需核实")
        return account

    async def update(
        self,
        identifier: str,
        *,
        name: str | None = None,
        email: str | None = None,
        enabled: bool | None = None,
    ):
        payload: dict[str, object] = {}
        if name is not None:
            payload["firstName"] = name
        if email is not None:
            payload.update(email=email, emailVerified=False)
        if enabled is not None:
            payload["enabled"] = enabled
        await self._request("PUT", "/users/" + quote(identifier, safe=""), payload=payload)

    async def temporary_password(self, identifier: str, password: str):
        await self._request(
            "PUT",
            "/users/" + quote(identifier, safe="") + "/reset-password",
            payload={"type": "password", "value": password, "temporary": True},
        )

    async def password_email(self, identifier: str):
        await self._request(
            "PUT",
            "/users/" + quote(identifier, safe="") + "/execute-actions-email",
            payload=["UPDATE_PASSWORD"],
        )

    async def end_sessions(self, identifier: str):
        await self._request("POST", "/users/" + quote(identifier, safe="") + "/logout")

    async def end_session(self, identifier: str, session_id: str):
        """只清理固定企业内、经账号归属核实的会话；不接受调用方提供URL。"""
        try:
            response = await self._request(
                "GET", "/users/" + quote(identifier, safe="") + "/sessions"
            )
        except DomainError as error:
            if error.status == 404:
                return
            raise
        rows = self._json(response)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise DomainError(503, "identity_unavailable", "身份会话归属无法核验")
        matches = [row for row in rows if row.get("id") == session_id]
        if not matches:
            return
        if len(matches) != 1 or matches[0].get("userId") != identifier:
            raise DomainError(503, "identity_unavailable", "身份会话归属冲突，需人工核实")
        try:
            await self._request("DELETE", "/sessions/" + quote(session_id, safe=""))
        except DomainError as error:
            if error.status != 404:
                raise
