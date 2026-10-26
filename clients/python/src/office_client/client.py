import json
from urllib.parse import quote
from uuid import uuid4

import httpx
from office_contracts import ProtocolError, RemoteError, TransportFailure

from .auth import ClientError, KeyringStore, OAuth, trusted_url
from .contracts import ContractClient
from .pagination import collect_pages

# v2 契约恢复动作 → 客户端提示键映射；未列出的动作保守回落 check_service。
LIB_ACTION_MAP = {
    "reauthenticate": "login",
    "check_permissions": "check_permissions",
    "refresh_and_correct": "refresh_and_correct",
    "replay_same_key": "replay_same_key",
    "query_operation": "query_before_retry",
    "retry_later": "check_service",
    "correct_request": "refresh_and_correct",
    "refresh": "refresh_and_correct",
    "contact_admin": "check_service",
    "none": "check_service",
}

RECOVERY_HINTS = {
    "login": "请重新登录授权。",
    "check_permissions": "请核对当前权限，必要时联系管理员。",
    "refresh_and_correct": "请刷新当前数据或字段定义，修正内容后重新确认。",
    "retry_read": "读取未完成，可稍后重新查询。",
    "replay_same_key": "上报重试须保留原文件内容和原幂等键，不要另建一次提交。",
    "query_before_retry": "请先查询当前状态并核对版本，再决定是否重新操作。",
    "check_service": "请检查服务状态，稍后再试。",
}


class ServiceError(ClientError):
    """受控服务错误；只提供恢复建议，不自动重放调用。"""

    def __init__(
        self,
        message,
        *,
        code,
        status=None,
        trace_id=None,
        outcome_unknown=False,
        recovery="check_service",
        details=None,
    ):
        super().__init__(message)
        self.details = details if isinstance(details, list) else []
        self.code, self.status, self.trace_id = code, status, trace_id
        self.outcome_unknown, self.recovery = outcome_unknown, recovery

    def user_message(self):
        lines = [str(self), RECOVERY_HINTS[self.recovery]]
        if self.outcome_unknown:
            lines.append("执行结果尚未确认，请勿直接重复写入。")
        if self.trace_id:
            lines.append("排查关联号：" + self.trace_id)
        return "\n".join(lines)


def read_only_request(method, path):
    return method.upper() in {"GET", "HEAD"} or (
        method.upper() == "POST"
        and (
            path
            in {
                "/api/v2/access-changes/preview",
                "/api/v2/account-operations/preview",
                "/api/v2/data-changes/preview",
                "/api/v2/data/aggregate",
            }
            or (path.startswith("/api/v2/account-operations/") and path.endswith("/resume-preview"))
        )
    )


def recovery_action(method, path, status=None):
    """无元数据时的纯状态回退；仅用于传输层故障等拿不到服务端恢复指示的场景。"""
    if status == 401:
        return "login"
    if status == 403:
        return "check_permissions"
    if status in {400, 409, 413, 415, 422}:
        return "refresh_and_correct"
    if read_only_request(method, path):
        return "retry_read"
    if method.upper() == "POST" and path == "/api/v2/records":
        return "replay_same_key"
    return "query_before_retry"


class OfficeClient:
    def __init__(
        self, service_url: str, *, store=None, browser_open=None, allow_lan_http_demo=False
    ):
        self.url = trusted_url(service_url, allow_lan_http_demo=allow_lan_http_demo)
        self.store = store if store is not None else KeyringStore(self.url)
        self.oauth = OAuth(
            self.url,
            self.store,
            allow_lan_http_demo=allow_lan_http_demo,
            **({"browser_open": browser_open} if browser_open else {}),
        )
        self.http = httpx.Client(
            base_url=self.url, timeout=15, follow_redirects=False, trust_env=False
        )
        self.contracts = ContractClient(self)
        self._schemas: dict[tuple[str, int], dict] = {}

    def client_authorizations(self, **filters):
        """本人授权默认20条；全企业范围需要相应权限。"""
        return self.request("GET", "/api/v2/client-authorizations", params=filters)

    def describe_device(self, device_name="", platform=""):
        return self.request(
            "POST",
            "/api/v2/client-authorizations/current",
            json={"device_name": device_name, "platform": platform},
        )

    def change_authorization(self, identifier, version, action, reason):
        """调用前人工确认影响范围；不自动重试或打印凭证。"""
        return self.request(
            "POST",
            "/api/v2/client-authorizations/change",
            json={"identifier": identifier, "version": version, "action": action, "reason": reason},
        )

    def request(self, method: str, path: str, *, data_model: type = object, **kwargs):
        """便捷解包仍通过唯一严格解码器；不自动重放任何操作。"""
        if isinstance(kwargs.get("params"), dict):
            kwargs["params"] = {
                key: value for key, value in kwargs["params"].items() if value is not None
            }
        previewing = method.upper() == "POST" and read_only_request(method, path)
        writing = not read_only_request(method, path)
        try:
            return self.contracts.request(method, path, data_model, **kwargs).data
        except RemoteError as error:
            failure = error.failure
            recovery = failure.meta.recovery
            action = recovery.action if recovery else None
            raise ServiceError(
                f"{error.status} {failure.code}: {failure.message}",
                code=failure.code,
                status=error.status,
                trace_id=failure.meta.trace_id,
                details=[issue.model_dump(mode="json") for issue in failure.details],
                outcome_unknown=writing
                and (recovery is None or recovery.write_outcome not in {"not_applied", "applied"}),
                recovery="retry_read"
                if previewing and error.status >= 500
                else LIB_ACTION_MAP.get(action or "", recovery_action(method, path, error.status)),
            ) from None
        except TransportFailure as error:
            raise ServiceError(
                "网络请求未完成，请按恢复建议核对结果",
                code=-1,
                outcome_unknown=writing and error.write_outcome == "unknown",
                recovery=recovery_action(method, path),
            ) from None
        except ProtocolError as error:
            # 已收到但不可信的响应不能证明写入未发生，也不能标为成功码0。
            raise ServiceError(
                "服务响应不符合契约，请核对版本和操作结果",
                code=500002,
                status=error.status,
                trace_id=error.trace_id,
                outcome_unknown=writing,
                recovery=recovery_action(method, path),
            ) from None

    def logout(self):
        with self.oauth.coordinated():
            return self._logout()

    def _logout(self):
        """整个撤销与清理共用凭证锁，不能误清另一进程的新登录。"""
        failure = None
        try:
            if self.store.load():
                current = self.request("POST", "/api/v2/client-authorizations/current", json={})
                result = self.change_authorization(
                    current["id"], current["version"], "revoke", "客户端主动退出"
                )
                if result["cleanup"] == "done":
                    # 远端会话已结束，无需再用失效的刷新令牌重复注销。
                    self.store.clear()
                    return
        except (ClientError, httpx.HTTPError) as error:
            failure = error
        try:
            self.oauth.logout()
        except (ClientError, httpx.HTTPError) as error:
            failure = failure or error
        if failure:
            raise ClientError("退出未完全确认；请核对本机凭证与控制台授权状态") from None

    def auth_status(self):
        return self.oauth.status()

    def login(self, timeout=120, *, on_authorize=None, open_browser=True):
        self.oauth.login(timeout, on_authorize=on_authorize, open_browser=open_browser)
        return self.me()

    def me(self):
        return self.request("GET", "/api/v2/me")

    def capabilities(self):
        return self.request("GET", "/api/v2/capabilities")

    def assistant_conversation_create(self, title="新会话"):
        return self.request("POST", "/api/v2/agent/conversations", json={"title": title})

    def assistant_conversations(self, **params):
        return self.request("GET", "/api/v2/agent/conversations", params=params)

    def assistant_conversation(self, identifier, **params):
        return self.request(
            "GET", f"/api/v2/agent/conversations/{quote(identifier, safe='')}", params=params
        )

    def assistant_conversation_archive(self, identifier, version, archived):
        return self.request(
            "POST",
            "/api/v2/agent/conversations/archive",
            json={"identifier": identifier, "version": version, "archived": archived},
        )

    def assistant_conversation_rename(self, identifier, version, title):
        return self.request(
            "POST",
            "/api/v2/agent/conversations/rename",
            json={"identifier": identifier, "version": version, "title": title},
        )

    def assistant_capabilities(self):
        return self.request("GET", "/api/v2/agent/capabilities")

    def assistant_start(self, prompt, request_key, *, conversation_id=None, context_page=""):
        """启动服务器模型任务；request_key重试须不变，受理不等于完成。"""
        payload = {"prompt": prompt, "request_key": request_key}
        if conversation_id is not None:
            payload.update(conversation_id=conversation_id, context_page=context_page)
        elif context_page:
            payload["context_page"] = context_page
        return self.request("POST", "/api/v2/agent/runs", json=payload)

    def assistant_runs(self, **params):
        return self.request("GET", "/api/v2/agent/runs", params=params)

    def assistant_run(self, identifier):
        return self.request("GET", f"/api/v2/agent/runs/{quote(identifier, safe='')}")

    def assistant_decide(self, identifier, version, proposal_id, approve):
        """真人核对服务器预览后决定；不允许替换参数或提交模型历史。"""
        return self.request(
            "POST",
            "/api/v2/agent/runs/decision",
            json={
                "identifier": identifier,
                "version": version,
                "proposal_id": proposal_id,
                "approve": approve,
            },
        )

    def assistant_cancel(self, identifier, version):
        return self.request(
            "POST", "/api/v2/agent/runs/cancel", json={"identifier": identifier, "version": version}
        )

    def access_list(self, kind, **params):
        return self.request("GET", f"/api/v2/access/{quote(kind, safe='')}", params=params)

    def access_detail(self, kind, identifier, **params):
        return self.request(
            "GET",
            f"/api/v2/access/{quote(kind, safe='')}/{quote(identifier, safe='')}",
            params=params,
        )

    def access_links(self, kind, identifier, relation):
        return self.request(
            "GET",
            f"/api/v2/access/{quote(kind, safe='')}/{quote(identifier, safe='')}/{quote(relation, safe='')}",
        )

    def preview_access_change(self, change):
        """只读预览；返回的摘要只能确认同一实例、员工与当前授权状态。"""
        return self.request("POST", "/api/v2/access-changes/preview", json={"change": change})

    def apply_access_change(self, change, confirmation_digest):
        """调用者先展示预览并取得用户确认；不自动重试写入。"""
        return self.request(
            "POST",
            "/api/v2/access-changes/apply",
            json={"change": change, "confirmation_digest": confirmation_digest},
        )

    def account_capabilities(self):
        return self.request("GET", "/api/v2/accounts/capabilities")

    def search_accounts(self, q):
        return self.request("GET", "/api/v2/accounts/search", params={"q": q})

    def employee_account(self, identifier):
        return self.request("GET", f"/api/v2/accounts/employees/{quote(identifier, safe='')}")

    def preview_account(self, request):
        return self.request("POST", "/api/v2/account-operations/preview", json={"request": request})

    def submit_account(self, request, confirmation_digest, idempotency_key):
        """人工确认后提交；保留原幂等键。秘密环节仅在返回的受控页面处理。"""
        return self.request(
            "POST",
            "/api/v2/account-operations",
            json={
                "request": request,
                "confirmation_digest": confirmation_digest,
                "idempotency_key": idempotency_key,
            },
        )

    def account_operation(self, identifier):
        return self.request("GET", f"/api/v2/account-operations/{quote(identifier, safe='')}")

    def account_history(self, **params):
        return self.request("GET", "/api/v2/account-operations", params=params)

    def preview_account_resume(self, identifier, mode=None):
        return self.request(
            "POST",
            f"/api/v2/account-operations/{quote(identifier, safe='')}/resume-preview",
            json={"identifier": identifier, "mode": mode},
        )

    def resume_account(self, identifier, version, confirmation_digest, mode=None):
        return self.request(
            "POST",
            f"/api/v2/account-operations/{quote(identifier, safe='')}/resume",
            json={
                "identifier": identifier,
                "version": version,
                "confirmation_digest": confirmation_digest,
                "mode": mode,
            },
        )

    def preview_project(self, name: str, *, idempotency_key: str | None = None):
        """返回含稳定创建标识的预览；确认时复用返回的change，不重新生成请求。"""
        return self.preview_data_change(
            {
                "operation": "project.create",
                "payload": {
                    "name": name,
                    "idempotency_key": str(uuid4()) if idempotency_key is None else idempotency_key,
                },
            }
        )

    def preview_data_change(self, change):
        return self.request("POST", "/api/v2/data-changes/preview", json={"change": change})

    def apply_data_change(self, change, confirmation_digest):
        return self.request(
            "POST",
            "/api/v2/data-changes/apply",
            json={"change": change, "confirmation_digest": confirmation_digest},
        )

    def pending_identities(self):
        return self.request("GET", "/api/v2/pending-identities")

    def aggregate(self, identifier, revision, query, *, scope="own"):
        """只读受控聚合；不重试，不接受原始SQL，返回覆盖和列类型。"""
        return self.request(
            "POST",
            "/api/v2/data/aggregate",
            json={
                "identifier": identifier,
                "revision": revision,
                "scope": scope,
                "query": query,
            },
        )

    def export_confirmed(self, project_id, max_records=1000):
        return self.request(
            "GET",
            f"/api/v2/projects/{quote(project_id, safe='')}/confirmed-export",
            params={"max_records": max_records},
        )

    def projects(self, **params):
        return self.request("GET", "/api/v2/projects", params=params)

    def collect_projects(
        self, *, max_pages=20, max_items=1000, max_bytes=5 * 1024 * 1024, **filters
    ):
        """有界读取项目列表；须检查complete，不能当作业务数据快照。"""
        return collect_pages(
            self.projects, filters, max_pages=max_pages, max_items=max_items, max_bytes=max_bytes
        )

    def collect_records(
        self, *, max_pages=20, max_items=1000, max_bytes=5 * 1024 * 1024, **filters
    ):
        """有界读取当前权限可见的记录；每页仍经原有鉴权，不增加scope参数。"""
        return collect_pages(
            self.records, filters, max_pages=max_pages, max_items=max_items, max_bytes=max_bytes
        )

    def schema(self, project_id: str, revision: int):
        # 缓存按不可变修订号；是否更新由项目列表中的 schema_revision 判断。
        key = (project_id, revision)
        if key not in self._schemas:
            self._schemas[key] = self.request(
                "GET", f"/api/v2/projects/{project_id}/schema", params={"revision": revision}
            )
        return json.loads(json.dumps(self._schemas[key]))

    def records(self, **params):
        return self.request("GET", "/api/v2/records", params=params)

    def submit(self, *, project_id, schema_revision, values, idempotency_key):
        return self.request(
            "POST",
            "/api/v2/records",
            json={
                "project_id": project_id,
                "schema_revision": schema_revision,
                "values": values,
                "idempotency_key": idempotency_key,
            },
        )

    def record(self, record_id):
        return self.request("GET", f"/api/v2/records/{record_id}")

    def confirm(self, record_id, version):
        return self.request(
            "POST", f"/api/v2/records/{record_id}/confirm", json={"version": version}
        )

    def correct(self, record_id, *, version, values, reason):
        return self.request(
            "POST",
            f"/api/v2/records/{record_id}/corrections",
            json={"version": version, "values": values, "reason": reason},
        )

    def confirmed(self, record_id):
        return self.request("GET", f"/api/v2/records/{record_id}/confirmed")

    def history(self, record_id, **params):
        return self.request("GET", f"/api/v2/records/{record_id}/history", params=params)

    def close(self):
        self.oauth.close()
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
