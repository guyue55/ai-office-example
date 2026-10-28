"""显式创建全新PG18、执行正式迁移；不访问既有库或身份服务。"""

import asyncio
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.application.service import Application
from app.common.errors import DomainError
from app.infrastructure.config import Settings
from app.infrastructure.database import async_sessions, sessions
from app.modules.access.authentication import Principal
from app.modules.access.service import bootstrap

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.live


def docker(*args, stdin=None):
    # 不回显命令输出，防止失败时秘密或SQL参数进入报告。
    proc = subprocess.run(
        ["docker", *args], input=stdin, text=True, capture_output=True, timeout=60, check=False
    )
    if proc.returncode:
        raise RuntimeError("独立查询验收容器操作失败：" + args[0])
    return proc.stdout.strip()


@pytest.fixture(scope="module")
def isolated_query_db(tmp_path_factory, request):
    if os.environ.get("OFFICE_QUERY_PG_TESTS") != "1":
        pytest.skip("需OFFICE_QUERY_PG_TESTS=1，创建本轮临时PG18，不使用既有实例")
    target_revision = getattr(request, "param", "head")
    assert target_revision in {"head", "0013"}, "隔离夹具只支持已知迁移目标"
    folder = tmp_path_factory.mktemp("query-pg")
    for name in ("postgres_password", "owner_password", "app_password", "identity_password"):
        path = folder / name
        path.write_text(secrets.token_urlsafe(32))
        path.chmod(0o600)
    name = "office-query-test-" + uuid4().hex[:12]
    try:
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--tmpfs",
            "/var/lib/postgresql:rw",
            "-e",
            "POSTGRES_PASSWORD_FILE=/run/secrets/postgres_password",
            "-v",
            f"{folder}:/run/secrets:ro",
            "-p",
            "127.0.0.1::5432",
            "pgvector/pgvector:0.8.7-pg18-trixie@sha256:9d9c930220cb9bf2f956d10a8f909cf9973d9672ca0278aef2c1e6facccad2e0",
        )
        for _ in range(40):
            try:
                docker("exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres")
                break
            except RuntimeError:
                time.sleep(0.5)
        else:
            raise RuntimeError("临时PG18未就绪")
        # 扩展只在隔离事务内验收，回滚后业务库与身份库仍保持未启用。
        vector_probe = docker(
            "exec",
            "-i",
            name,
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            stdin="BEGIN; CREATE EXTENSION vector; "
            "SELECT '[1,0,0]'::vector <-> '[0,1,0]'::vector; ROLLBACK; "
            "SELECT count(*) FROM pg_extension WHERE extname='vector';",
        )
        assert "1.4142135623730951" in vector_probe
        assert vector_probe.splitlines()[-1] == "0"
        docker(
            "exec",
            "-i",
            "-e",
            "POSTGRES_USER=postgres",
            name,
            "bash",
            "-s",
            stdin=(ROOT / "deploy/database/init.sh").read_text(),
        )
        port = int(docker("port", name, "5432/tcp").rsplit(":", 1)[1])
        common = {
            "database_host": "127.0.0.1",
            "database_port": port,
            "database_name": "office",
            "issuer": "https://fixture.invalid/realm",
            "audience": "https://fixture.invalid/mcp",
            "public_url": "https://fixture.invalid",
        }
        owner = Settings(
            **common, database_user="office_owner", database_password_file=folder / "owner_password"
        )
        owner_config = folder / "owner.json"
        owner_config.write_text(owner.model_dump_json())
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "office-data-service/alembic.ini",
                "upgrade",
                target_revision,
            ],
            check=False,
            cwd=ROOT,
            env={**os.environ, "OFFICE_CONFIG": str(owner_config)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, "独立PG迁移失败，未回显实例配置"
        factory = sessions(owner)
        try:
            with factory.begin() as db:
                bootstrap(db, owner.issuer, "operator", "构造管理员")
        finally:
            factory.kw["bind"].dispose()
        settings = Settings(
            **common, database_user="office_app", database_password_file=folder / "app_password"
        )
        (folder / "container-name").write_text(name)
        yield settings
    finally:
        try:
            docker("rm", "-f", name)
        finally:
            for filename in (
                "postgres_password",
                "owner_password",
                "app_password",
                "identity_password",
                "owner.json",
            ):
                (folder / filename).unlink(missing_ok=True)


def principal(settings, subject):
    return Principal(
        settings.issuer,
        subject,
        {
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "sid": subject + "-session",
            "azp": "fixture",
        },
    )


async def dataset(settings):
    factory = async_sessions(settings)
    app = Application(settings, factory, None)
    admin, user = principal(settings, "operator"), principal(settings, uuid4().hex)

    async def call(who, operation, payload):
        return await app.call(who, operation, payload, uuid4().hex)

    # 使用正式管理/数据操作创建测试身份、结构与数据，真实权限由Access执行。
    employee = await call(
        admin,
        "access.create",
        {
            "kind": "employees",
            "body": {"issuer": settings.issuer, "subject": user.subject, "name": "构造员工"},
        },
    )
    del employee
    project = await call(admin, "project.create", {"payload": {"name": "抽象数据集"}})
    fields = [
        {
            "id": key,
            "label": key,
            "type": kind,
            "required": False,
            "description": "构造技术字段，不代表业务指标",
        }
        for key, kind in (("v", "decimal"), ("d", "date"), ("tag", "text"), ("i", "integer"))
    ]
    await call(
        admin,
        "schema.publish",
        {"identifier": project["id"], "payload": {"expected_revision": 0, "fields": fields}},
    )
    records = []
    for who, values in [
        (user, {"v": "0.10", "d": "2025-12-31", "tag": "a", "i": 1}),
        (user, {"v": "0.20", "d": "2026-01-01", "tag": "a", "i": 2}),
        (user, {"d": "2026-01-31", "tag": "b"}),
        (admin, {"v": "100.00", "d": "2026-01-01", "tag": "z"}),
    ]:
        record = await call(
            who,
            "record.create",
            {
                "payload": {
                    "project_id": project["id"],
                    "schema_revision": 1,
                    "values": values,
                    "idempotency_key": uuid4().hex,
                }
            },
        )
        await call(admin, "record.confirm", {"identifier": record["id"], "payload": {"version": 1}})
        records.append(record)
    return factory, app, call, admin, user, project, records, fields


def test_pg_exact_aggregates_nulls_date_buckets_and_permissions(isolated_query_db):
    async def run():
        factory, _app, call, admin, user, project, records, _ = await dataset(isolated_query_db)
        try:
            request = {
                "identifier": project["id"],
                "revision": 1,
                "query": {
                    "measures": [
                        {"alias": "n", "op": "count"},
                        {"alias": "s", "op": "sum", "field": "v"},
                        {"alias": "a", "op": "avg", "field": "v"},
                        {"alias": "non_null", "op": "count", "field": "v"},
                    ]
                },
            }
            result = await call(user, "data.aggregate", request)
            assert result["rows"] == [{"n": 3, "s": "0.30", "a": "0.15000000", "non_null": 2}]
            assert result["eligible_records"] == result["matched_records"] == 3
            assert result["complete"] and result["excluded_schema_records"] == 0
            with pytest.raises(DomainError) as caught:
                await call(user, "data.aggregate", {**request, "scope": "all"})
            assert caught.value.status == 403
            all_result = await call(admin, "data.aggregate", {**request, "scope": "all"})
            assert all_result["rows"][0]["s"] == "100.30"
            grouped = {
                **request,
                "query": {
                    "group_by": [{"field": "d", "bucket": "year"}],
                    "measures": [{"alias": "n", "op": "count"}],
                },
            }
            result = await call(user, "data.aggregate", grouped)
            assert result["rows"] == [{"d": "2025-01-01", "n": 1}, {"d": "2026-01-01", "n": 2}]
            with pytest.raises(DomainError) as caught:
                await call(
                    user,
                    "data.aggregate",
                    {**grouped, "query": {**grouped["query"], "max_groups": 1}},
                )
            assert caught.value.code == "query_budget_exceeded"
            # 最新确认仅一次；未确认更正不污染正式值。
            await call(
                user,
                "record.correct",
                {
                    "identifier": records[0]["id"],
                    "payload": {"version": 2, "values": {"v": "9.00"}, "reason": "构造修订"},
                },
            )
            assert (await call(user, "data.aggregate", request))["rows"][0]["s"] == "0.30"
            await call(
                admin, "record.confirm", {"identifier": records[0]["id"], "payload": {"version": 3}}
            )
            assert (await call(user, "data.aggregate", request))["rows"][0]["s"] == "9.20"
        finally:
            await factory.kw["bind"].dispose()

    asyncio.run(run())


def test_pg_schema_coverage_empty_filters_and_transport(isolated_query_db, monkeypatch):
    async def run():
        settings = isolated_query_db
        factory, app, call, admin, user, project, _records, fields = await dataset(settings)
        try:
            request = {
                "identifier": project["id"],
                "revision": 1,
                "query": {
                    "filters": [
                        {
                            "mode": "or",
                            "predicates": [
                                {"field": "v", "op": "gte", "values": ["0.15"]},
                                {"field": "v", "op": "is_null"},
                            ],
                        }
                    ],
                    "measures": [{"alias": "n", "op": "count"}],
                },
            }
            assert (await call(user, "data.aggregate", request))["rows"] == [{"n": 2}]
            empty = {
                **request,
                "query": {
                    "filters": [{"predicates": [{"field": "i", "op": "gt", "values": [999]}]}],
                    "measures": [
                        {"alias": "n", "op": "count"},
                        {"alias": "s", "op": "sum", "field": "v"},
                    ],
                },
            }
            result = await call(user, "data.aggregate", empty)
            assert result["rows"] == [{"n": 0, "s": None}] and result["matched_records"] == 0
            empty["query"]["group_by"] = [{"field": "tag"}]
            assert (await call(user, "data.aggregate", empty))["rows"] == []
            await call(
                admin,
                "schema.publish",
                {
                    "identifier": project["id"],
                    "payload": {
                        "expected_revision": 1,
                        "fields": fields
                        + [
                            {
                                "id": "new_scalar",
                                "type": "integer",
                                "label": "新技术字段",
                                "description": "不改接口",
                            }
                        ],
                    },
                },
            )
            new = await call(
                user,
                "record.create",
                {
                    "payload": {
                        "project_id": project["id"],
                        "schema_revision": 2,
                        "values": {"new_scalar": 7},
                        "idempotency_key": uuid4().hex,
                    }
                },
            )
            await call(
                admin, "record.confirm", {"identifier": new["id"], "payload": {"version": 1}}
            )
            result = await call(user, "data.aggregate", request)
            assert result["eligible_records"] == 4 and result["excluded_schema_records"] == 1
            fresh = await call(
                user,
                "data.aggregate",
                {
                    "identifier": project["id"],
                    "revision": 2,
                    "query": {"measures": [{"alias": "total", "op": "sum", "field": "new_scalar"}]},
                },
            )
            assert fresh["rows"] == [{"total": "7"}]
            assert fresh["excluded_schema_records"] == 3
            # 全HTTP包装真实应用/数据库；仅身份产品验证器注入测试身份，不接既有Keycloak。
            from unittest.mock import AsyncMock

            from app.entrypoints.mcp.server import build_mcp
            from app.main import create_app

            web = create_app(settings)
            web.state.application.verifier.verify_async = AsyncMock(return_value=user)
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=web), base_url="http://testserver"
                ) as client:
                    response = await client.post(
                        "/api/v2/data/aggregate",
                        headers={"Authorization": "Bearer fixture"},
                        json=request,
                    )
                    assert response.status_code == 200 and response.json()["code"] == 0
                    assert response.json()["data"]["rows"] == result["rows"]
                    assert response.json()["meta"]["trace_id"]
                    # SDK严格解码真实HTTP产生的包装，使用内存测试凭证，不碰钥匙串。
                    from office_client import OfficeClient

                    sdk = OfficeClient("http://127.0.0.1:1", store=SimpleNamespace())
                    sdk.oauth.access_token = lambda: "fixture"
                    sdk.http.close()
                    encoded = response.content
                    sdk.http = httpx.Client(
                        base_url="http://127.0.0.1:1",
                        transport=httpx.MockTransport(
                            lambda _: httpx.Response(
                                200, content=encoded, headers={"content-type": "application/json"}
                            )
                        ),
                    )
                    try:
                        assert (
                            sdk.aggregate(project["id"], 1, request["query"])["rows"]
                            == result["rows"]
                        )
                    finally:
                        sdk.close()
            finally:
                await web.state.application.sessions.kw["bind"].dispose()
            app.verifier = SimpleNamespace(verify_async=AsyncMock(return_value=user))
            monkeypatch.setattr(
                "app.entrypoints.mcp.server.get_access_token",
                lambda: SimpleNamespace(token="fixture"),
            )
            server, _ = build_mcp(app)
            tool_result = await server.call_tool("aggregate_data", request)
            # 官方MCP工具同样返回相同业务数据；失败会在SDK抛ToolError。
            assert "excluded_schema_records" in str(tool_result)
        finally:
            await factory.kw["bind"].dispose()

    asyncio.run(run())


def test_pg_multidimensional_operators_revoke_and_cancel(isolated_query_db, monkeypatch):
    async def run():
        factory, _app, call, admin, user, project, _records, _ = await dataset(isolated_query_db)
        try:
            base = {"identifier": project["id"], "revision": 1}

            async def query(spec):
                return await call(user, "data.aggregate", {**base, "query": spec})

            result = await query(
                {
                    "group_by": [{"field": "d", "bucket": "month"}, {"field": "tag"}],
                    "measures": [
                        {"alias": "hi", "op": "max", "field": "v"},
                        {"alias": "lo", "op": "min", "field": "i"},
                    ],
                    "order_by": [{"key": "d", "direction": "desc"}],
                }
            )
            assert result["rows"] == [
                {"d": "2026-01-01", "tag": "a", "hi": "0.20", "lo": 2},
                {"d": "2026-01-01", "tag": "b", "hi": None, "lo": None},
                {"d": "2025-12-01", "tag": "a", "hi": "0.10", "lo": 1},
            ]
            for op, values, count in [
                ("eq", [1], 1),
                ("ne", [1], 1),
                ("gt", [1], 1),
                ("gte", [1], 2),
                ("lt", [2], 1),
                ("lte", [2], 2),
                ("in", [1, 2], 2),
                ("not_in", [1], 1),
                ("is_null", [], 1),
                ("not_null", [], 2),
            ]:
                result = await query(
                    {
                        "filters": [{"predicates": [{"field": "i", "op": op, "values": values}]}],
                        "measures": [{"alias": "n", "op": "count"}],
                    }
                )
                assert result["rows"] == [{"n": count}], op
            # 半开日期条件、公历季度；不将录入时刻冒充业务日期。
            result = await query(
                {
                    "filters": [
                        {
                            "predicates": [
                                {"field": "d", "op": "gte", "values": ["2026-01-01"]},
                                {"field": "d", "op": "lt", "values": ["2026-02-01"]},
                            ]
                        }
                    ],
                    "group_by": [{"field": "d", "bucket": "quarter"}],
                    "measures": [{"alias": "n", "op": "count"}],
                }
            )
            assert result["rows"] == [{"d": "2026-01-01", "n": 2}]
            result = await query(
                {
                    "filters": [
                        {
                            "predicates": [
                                {"field": "tag", "op": "contains", "values": ["%' OR 1=1 --"]}
                            ]
                        }
                    ],
                    "measures": [{"alias": "n", "op": "count"}],
                }
            )
            assert result["rows"] == [{"n": 0}]
            spaced = await call(
                user,
                "record.create",
                {
                    "payload": {
                        "project_id": project["id"],
                        "schema_revision": 1,
                        "values": {"tag": " a "},
                        "idempotency_key": uuid4().hex,
                    }
                },
            )
            await call(
                admin, "record.confirm", {"identifier": spaced["id"], "payload": {"version": 1}}
            )
            literal = {
                "filters": [{"predicates": [{"field": "tag", "op": "eq", "values": [" a "]}]}],
                "measures": [{"alias": "n", "op": "count"}],
            }
            assert (await query(literal))["rows"] == [{"n": 1}]
            with pytest.raises(DomainError) as caught:
                await call(user, "record.archive", {"identifier": spaced["id"], "version": 2})
            assert caught.value.code == "record_frozen"
            # 内容预算也整次失败，不仅限制分组数量。
            import app.modules.data.query as adapter

            old_budget = adapter.PAGE_BYTES
            monkeypatch.setattr(adapter, "PAGE_BYTES", 1)
            with pytest.raises(DomainError) as caught:
                await query({"measures": [{"alias": "n", "op": "count"}]})
            assert caught.value.code == "query_budget_exceeded"
            monkeypatch.setattr(adapter, "PAGE_BYTES", old_budget)
            # 同一身份对象撤权后立即拒绝，恢复后可查；不改权限模型。
            identity = await call(user, "me", {})
            links = {"kind": "employees", "identifier": identity["id"], "relation": "roles"}
            original = await call(admin, "access.links.get", links)
            removed = await call(
                admin,
                "access.links.replace",
                {**links, "body": {"version": original["version"], "ids": []}},
            )
            with pytest.raises(DomainError) as caught:
                await query({"measures": [{"alias": "n", "op": "count"}]})
            assert caught.value.status == 403
            await call(
                admin,
                "access.links.replace",
                {**links, "body": {"version": removed["version"], "ids": original["ids"]}},
            )
            # 实际PG慢语句取消后连接必须释放；不是以sleep模拟应用返回。
            from sqlalchemy import func, select, text

            original_compile = adapter.compile_query
            monkeypatch.setattr(
                adapter, "compile_query", lambda *args: (select(func.pg_sleep(10)), [])
            )
            task = asyncio.create_task(query({"measures": [{"alias": "n", "op": "count"}]}))
            # 等PG实际报告慢语句在执行，再取消，避免只取消了尚未进入数据库的任务。
            for _ in range(50):
                async with factory() as monitor:
                    active = await monitor.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid() "
                            "AND usename = current_user AND state = 'active' "
                            "AND query LIKE '%pg_sleep%'"
                        )
                    )
                if active:
                    break
                await asyncio.sleep(0.02)
            else:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                pytest.fail("未观察到独立PG真实慢查询，不将提前取消当作数据库取消证据")
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert factory.kw["bind"].pool.checkedout() == 0
            monkeypatch.setattr(adapter, "compile_query", original_compile)
            assert (await query({"measures": [{"alias": "n", "op": "count"}]}))[
                "matched_records"
            ] == 4
        finally:
            await factory.kw["bind"].dispose()

    asyncio.run(run())
