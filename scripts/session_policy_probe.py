"""只供双库恢复临时实例调用；短周期验证空闲、最长寿命及刷新轮换。"""

import time

from office_client.auth import ClientError, OAuth
from office_client.product import PRODUCT
from recovery_oidc import RestoredIdentityTransport, restored_login
from session_policy import ROOT, admin_headers, apply_policy, policy, read_policy


class MemoryStore:
    def __init__(self):
        self.value = None

    def load(self):
        return self.value

    def save(self, value):
        self.value = value

    def clear(self):
        self.value = None


def verify_session_policy(http, settings, target_port, browser_executable):
    # 此入口必须由拒绝原始服务端口的受控传输调用，避免测试短策略进入源实例。
    transport = http._transport
    if not isinstance(transport, RestoredIdentityTransport) or transport.target_port != target_port:
        raise RuntimeError("策略探针只允许隔离恢复传输")
    print("SESSION_POLICY_BEGIN", flush=True)
    headers = admin_headers(http)
    print("SESSION_POLICY_ADMIN_READY", flush=True)
    original = read_policy(http, headers)
    print("SESSION_POLICY_SNAPSHOT_READY", flush=True)
    desired = policy()
    apply_policy(http, headers, desired)
    print("SESSION_POLICY_APPLIED", flush=True)
    if read_policy(http, headers) != desired:
        raise RuntimeError("七天／三十天及Web分级策略未准确生效")
    # 按相同机制压缩测试时间；不声称等待了真实七天／三十天。
    short = {
        "realm": {**desired["realm"], "ssoSessionIdleTimeout": 12, "ssoSessionMaxLifespan": 45},
        "clients": {
            PRODUCT["client_id"]: {
                "client.session.idle.timeout": "12",
                "client.session.max.lifespan": "35",
            },
            PRODUCT["web_client_id"]: {
                "client.session.idle.timeout": "8",
                "client.session.max.lifespan": "30",
            },
        },
    }
    apply_policy(http, headers, short)
    print("SESSION_POLICY_SHORT_READY", flush=True)
    password = (ROOT / "runtime/secrets/operator_password").read_text().strip()

    def login(client_id):
        store = MemoryStore()
        config = settings.model_copy(
            update={
                "client_id": client_id,
                "redirect_uri": settings.public_url + "/auth/callback"
                if client_id == PRODUCT["web_client_id"]
                else settings.redirect_uri,
            }
        )
        oauth = OAuth(settings.public_url, store)
        oauth.http.close()
        oauth.http = http  # 生命周期由外层恢复脚本管理，不在此关闭共享连接。
        oauth._config = {"issuer": settings.issuer, "client_id": client_id}
        claims, _ = restored_login(
            http,
            config,
            "operator",
            password,
            target_port=target_port,
            browser_executable=browser_executable,
            token_sink=oauth._save,
        )
        return oauth, store, claims

    _idle, idle_store, idle_claims = login(PRODUCT["client_id"])
    idle_started = time.monotonic()
    active, active_store, active_claims = login(PRODUCT["client_id"])
    active_started = time.monotonic()
    assert idle_claims["sid"] != active_claims["sid"]
    web, web_store, _ = login(PRODUCT["web_client_id"])
    print("SESSION_POLICY_LOGINS_READY", flush=True)
    old_refresh = active_store.load()["refresh_token"]

    def refresh(oauth, store):
        store.value["expires_at"] = 1
        return oauth.access_token()

    refresh(active, active_store)
    refresh(web, web_store)
    web_active_at = time.monotonic()
    # 不把重放攻击施加到后续最长寿命测试的会话，使用独立会话。
    replay, replay_store, _ = login(PRODUCT["client_id"])
    previous = replay_store.load()["refresh_token"]
    refresh(replay, replay_store)
    response = http.post(
        settings.issuer + "/protocol/openid-connect/token",
        data={
            "grant_type": "refresh_token",
            "client_id": PRODUCT["client_id"],
            "refresh_token": previous,
        },
    )
    if response.status_code != 400 or response.json().get("error") != "invalid_grant":
        raise RuntimeError("旧刷新凭证未被轮换规则拒绝")
    if old_refresh == active_store.load()["refresh_token"]:
        raise RuntimeError("成功刷新没有更新凭证")
    rounds = 0
    idle_checked = web_checked = False
    last_active = time.monotonic()
    started = time.monotonic()

    def expired_before_maximum(store, name, born, lower, upper):
        age = time.monotonic() - born
        if not lower <= age < upper:
            raise RuntimeError("闲置测试超出与最长寿命区分的时间窗口")
        response = http.post(
            settings.issuer + "/protocol/openid-connect/token",
            data={
                "grant_type": "refresh_token",
                "client_id": name,
                "refresh_token": store.load()["refresh_token"],
            },
        )
        if response.status_code != 400 or response.json().get("error") != "invalid_grant":
            raise RuntimeError("闲置授权没有在最长寿命之前失效")

    while time.monotonic() - started < 50:
        time.sleep(3)
        rounds += 1
        if not idle_checked and time.monotonic() - idle_started >= 16:
            expired_before_maximum(idle_store, PRODUCT["client_id"], idle_started, 16, 30)
            idle_checked = True
        if not web_checked and time.monotonic() - web_active_at >= 12:
            expired_before_maximum(web_store, PRODUCT["web_client_id"], web_active_at, 12, 23)
            web_checked = True
        active_store.value["expires_at"] = 1
        try:
            active.access_token()
            last_active = time.monotonic()
        except ClientError:
            # 最近刷新不到6秒，排除12秒空闲失效；35秒客户端最长寿命前不能接受拒绝。
            if (
                time.monotonic() - active_started < 33
                or time.monotonic() - last_active >= 6
                or active_store.load().get("refresh_outcome") != "invalid"
            ):
                raise RuntimeError("最长寿命测试出现非预期失败") from None
            break
    else:
        raise RuntimeError("持续活动绕过了最长会话期限")
    if not idle_checked or not web_checked:
        raise RuntimeError("没有独立证明CLI与Web空闲失效")
    apply_policy(http, admin_headers(http), original)
    return {
        "configured_policy": True,
        "refresh_rotation": True,
        "cli_refresh": True,
        "web_refresh": True,
        "independent_sessions": True,
        "idle_expiry": True,
        "maximum_lifetime": True,
        "bounded_rounds": rounds,
    }
