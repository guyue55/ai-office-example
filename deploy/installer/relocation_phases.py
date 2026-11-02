"""显式HTTP演示实例迁址的容器内阶段；不打印凭证或身份响应。"""

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from app.infrastructure.config import Settings
from app.infrastructure.database import sessions
from app.infrastructure.models import (
    AccessRevision,
    AgentRun,
    Audit,
    ClientAuthorization,
    Employee,
    EmployeeRole,
    IdentityLink,
    IdentityOperation,
    PendingIdentity,
    Project,
    Record,
    Role,
)
from product import PRODUCT
from sqlalchemy import func, select, update

ROOT = Path("/state")


def identity(http):
    base = "http://identity:8080/identity"
    until = time.monotonic() + 180
    while True:
        try:
            response = http.get(base + "/realms/master/.well-known/openid-configuration")
            response.raise_for_status()
            break
        except httpx.HTTPError:
            if time.monotonic() >= until:
                raise RuntimeError("身份服务未就绪") from None
            time.sleep(1)
    response = http.post(
        base + "/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": "setup-admin",
            "password": (ROOT / "identity/secrets/admin_password").read_text().strip(),
        },
    )
    response.raise_for_status()
    http.headers["Authorization"] = "Bearer " + response.json()["access_token"]
    return base + "/admin/realms/office"


def request(http, method, url, **kwargs):
    response = http.request(method, url, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else None


def identity_state(http, realm, old, new, *, apply=False):
    old_audience, new_audience = old + "/mcp", new + "/mcp"
    realm_info = request(http, "GET", realm)
    admin = json.loads((ROOT / "maintenance/identity.json").read_text())
    # 确认仍为原realm和管理员主体，不能认领同名的新身份系统。
    request(http, "GET", realm + "/users/" + admin["subject"])
    count = request(http, "GET", realm + "/users/count")
    clients = request(http, "GET", realm + "/clients")
    by_name = {c["clientId"]: c for c in clients}
    required = (PRODUCT["client_id"], PRODUCT["web_client_id"], old_audience)
    if any(name not in by_name for name in required) or new_audience in by_name:
        raise RuntimeError("身份客户端缺失或新受众已存在，停止迁址")
    mappings = {}
    for name in required[:2]:
        client = by_name[name]
        values = request(
            http, "GET", realm + "/clients/" + client["id"] + "/protocol-mappers/models"
        )
        mapper = next((v for v in values if v["name"] == "office-audience"), None)
        if (
            mapper is None
            or mapper.get("config", {}).get("included.custom.audience") != old_audience
        ):
            raise RuntimeError("原客户端受众不匹配")
        mappings[name] = mapper
    attrs = realm_info.get("attributes", {})
    if attrs.get("frontendUrl") and attrs["frontendUrl"] != old + "/identity":
        raise RuntimeError("realm存在不同的显式前端地址")
    if old + "/auth/callback" not in by_name[PRODUCT["web_client_id"]].get("redirectUris", []):
        raise RuntimeError("Web客户端原回调不匹配")
    result = {
        "realm": realm_info["id"],
        "users": count,
        "clients": {n: by_name[n]["id"] for n in required},
    }
    if not apply:
        return result
    resource = by_name[old_audience]
    request(
        http,
        "PUT",
        realm + "/clients/" + resource["id"],
        json={**resource, "clientId": new_audience},
    )
    for name in required[:2]:
        client = by_name[name]
        path = realm + "/clients/" + client["id"]
        if name == PRODUCT["web_client_id"]:
            client["redirectUris"] = [
                new + "/auth/callback" if uri == old + "/auth/callback" else uri
                for uri in client["redirectUris"]
            ]
            attrs = client.get("attributes", {})
            logout = attrs.get("post.logout.redirect.uris", "")
            if logout:
                attrs["post.logout.redirect.uris"] = logout.replace(old + "/", new + "/")
            client["attributes"] = attrs
            request(http, "PUT", path, json=client)
        mapper = mappings[name]
        request(
            http,
            "PUT",
            path + "/protocol-mappers/models/" + mapper["id"],
            json={
                **mapper,
                "config": {**mapper["config"], "included.custom.audience": new_audience},
            },
        )
    attrs = realm_info.get("attributes", {})
    if attrs.get("frontendUrl"):
        if attrs["frontendUrl"] != old + "/identity":
            raise RuntimeError("realm存在不同的显式前端地址")
        request(
            http, "PUT", realm, json={"attributes": {**attrs, "frontendUrl": new + "/identity"}}
        )
    request(http, "POST", realm + "/logout-all")
    if request(http, "GET", realm + "/users/count") != count:
        raise RuntimeError("身份用户数量变化")
    return result


def database_state(db, old, new, run_id, *, apply=False):
    old_issuer, new_issuer = old + "/identity/realms/office", new + "/identity/realms/office"
    for model in (IdentityLink, PendingIdentity):
        if db.scalar(select(func.count()).select_from(model).where(model.issuer == new_issuer)):
            raise RuntimeError("新issuer已有身份映射，禁止合并")
    if not db.scalar(
        select(func.count()).select_from(IdentityLink).where(IdentityLink.issuer == old_issuer)
    ):
        raise RuntimeError("原issuer没有身份映射")
    if db.scalar(
        select(func.count())
        .select_from(IdentityOperation)
        .where(IdentityOperation.status != "succeeded")
    ):
        raise RuntimeError("存在未完成账号操作，请处理后再迁址")
    if db.scalar(
        select(func.count())
        .select_from(AgentRun)
        .where(AgentRun.status.in_(["queued", "running", "awaiting_confirmation"]))
    ):
        raise RuntimeError("存在活动Agent任务，请完成或停止后再迁址")
    models = (Employee, Role, EmployeeRole, Project, Record, IdentityLink)
    counts = {m.__tablename__: db.scalar(select(func.count()).select_from(m)) for m in models}
    links = list(
        db.execute(
            select(IdentityLink.id, IdentityLink.employee_id, IdentityLink.subject)
            .where(IdentityLink.issuer == old_issuer)
            .order_by(IdentityLink.id)
        )
    )
    digest = hashlib.sha256(json.dumps([list(row) for row in links]).encode()).hexdigest()
    if apply:
        for model in (IdentityLink, PendingIdentity):
            db.execute(update(model).where(model.issuer == old_issuer).values(issuer=new_issuer))
        now = datetime.now(UTC)
        db.execute(
            update(ClientAuthorization)
            .where(ClientAuthorization.issuer == old_issuer, ClientAuthorization.state == "active")
            .values(
                state="revoked",
                ended_at=now,
                cleanup="done",
                version=ClientAuthorization.version + 1,
            )
        )
        db.execute(
            update(AccessRevision)
            .where(AccessRevision.id == 1)
            .values(revision=AccessRevision.revision + 1)
        )
        db.add(
            Audit(
                actor_id="system-maintenance",
                action="instance.relocated",
                resource_id=run_id,
                trace_id=run_id,
                detail={
                    "reason": "显式迁址；旧会话结束，用户与角色不变",
                    "before": old,
                    "after": new,
                },
            )
        )
        db.flush()
        if counts != {
            m.__tablename__: db.scalar(select(func.count()).select_from(m)) for m in models
        }:
            raise RuntimeError("业务对象数量变化，回滚数据库事务")
        moved = list(
            db.execute(
                select(IdentityLink.id, IdentityLink.employee_id, IdentityLink.subject)
                .where(IdentityLink.issuer == new_issuer)
                .order_by(IdentityLink.id)
            )
        )
        if links != moved:
            raise RuntimeError("身份主体或业务人员映射变化")
    return {"counts": counts, "identity_mapping_sha256": digest}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase", choices=["preflight", "apply"])
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--run-id", required=True)
    args = p.parse_args()
    cfg = Settings.load(ROOT / "maintenance/owner.json")
    factory = sessions(cfg)
    try:
        with factory.begin() as db:
            result = database_state(db, args.old, args.new, args.run_id)
        with httpx.Client(timeout=30, trust_env=False) as http:
            realm = identity(http)
            result["identity"] = identity_state(
                http, realm, args.old, args.new, apply=args.phase == "apply"
            )
        if args.phase == "apply":
            with factory.begin() as db:
                database_state(db, args.old, args.new, args.run_id, apply=True)
        print(json.dumps(result))
    finally:
        factory.kw["bind"].dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 -- 维护入口统一脱敏。
        # 不输出HTTP异常内容，避免管理员令牌／上游响应进入维护日志。
        print(
            "迁址阶段失败："
            + (str(error) if isinstance(error, RuntimeError) else type(error).__name__)
        )
        raise SystemExit(1) from None
