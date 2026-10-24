"""查询边界、注入反例与跨协议参数；期望来自技术合同。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.application.commands import validate_command
from app.common.errors import DomainError
from app.entrypoints.mcp.validation import ValidatedMCPServer
from app.modules.query.compiler import compile_query
from app.modules.query.schemas import AggregateQuery
from mcp.server.mcpserver.exceptions import ToolError
from office_client.client import read_only_request, recovery_action
from pydantic import ValidationError
from sqlalchemy import column, select, table
from sqlalchemy.dialects.postgresql import JSONB, dialect

BASE = {"measures": [{"alias": "n", "op": "count"}]}
FIELDS = [{"id": "x", "type": "decimal", "required": False}]


def test_predicate_preserves_literal_whitespace():
    spec = AggregateQuery.model_validate(
        {**BASE, "filters": [{"predicates": [{"field": "t", "op": "eq", "values": [" a "]}]}]}
    )
    assert spec.filters[0].predicates[0].values == [" a "]


def test_typed_condition_error_locates_the_actual_query_value():
    with pytest.raises(DomainError) as caught:
        compile_spec(
            {**BASE, "filters": [{"predicates": [{"field": "x", "op": "eq", "values": [True]}]}]}
        )
    assert caught.value.details == [
        {
            "path": ["query", "filters", "0", "predicates", "0", "values", "0"],
            "code": "invalid_field",
        }
    ]


@pytest.mark.parametrize(
    "query",
    [
        {**BASE, "sql": "secret"},
        {**BASE, "max_groups": True},
        {**BASE, "max_groups": 1001},
        {**BASE, "group_by": [{"field": "x;drop table employee"}]},
        {**BASE, "filters": [{"predicates": [{"field": "x", "op": "eq", "values": []}]}]},
        {**BASE, "filters": [{"predicates": [{"field": "x", "op": "in", "values": []}]}]},
        {**BASE, "filters": [{"predicates": [{"field": "x", "op": "eq", "values": [1.1]}]}]},
        {**BASE, "group_by": [{"field": "n"}]},
        {**BASE, "order_by": [{"key": "private"}]},
        {"measures": [{"alias": "x", "op": "sum"}]},
    ],
)
def test_query_rejects_undefined_or_unbounded_input(query):
    with pytest.raises(ValidationError):
        AggregateQuery.model_validate(query)


def compile_spec(query, fields=FIELDS):
    source = select(table("fixture", column("values", JSONB), column("schema_revision"))).cte(
        "authorized"
    )
    return compile_query(source, fields, 1, AggregateQuery.model_validate(query))


@pytest.mark.parametrize(
    "query",
    [
        {"measures": [{"alias": "n", "op": "sum", "field": "unknown"}]},
        {**BASE, "group_by": [{"field": "x", "bucket": "month"}]},
        {**BASE, "filters": [{"predicates": [{"field": "x", "op": "contains", "values": ["a"]}]}]},
        {**BASE, "filters": [{"predicates": [{"field": "x", "op": "eq", "values": [True]}]}]},
    ],
)
def test_compiler_rejects_incompatible_fields_and_types(query):
    with pytest.raises(DomainError) as caught:
        compile_spec(query)
    assert caught.value.status == 422


def test_values_and_field_names_are_bound_parameters():
    attack = "%' OR 1=1 --"
    statement, _ = compile_spec(
        {
            **BASE,
            "filters": [{"predicates": [{"field": "t", "op": "contains", "values": [attack]}]}],
        },
        [{"id": "t", "type": "text", "required": False}],
    )
    compiled = statement.compile(dialect=dialect())
    assert attack not in str(compiled)
    assert "LIKE" in str(compiled) and "ESCAPE" in str(compiled)


def test_command_and_mcp_share_strict_query_validation():
    invalid = {"identifier": "p", "revision": "1", "query": BASE}
    with pytest.raises(ValidationError):
        validate_command("data.aggregate", invalid)

    async def run():
        with pytest.raises(ToolError) as caught:
            await ValidatedMCPServer("isolated").call_tool("aggregate_data", invalid)
        assert "422001" in str(caught.value)

    asyncio.run(run())


def test_aggregate_post_is_read_only_for_recovery():
    assert read_only_request("POST", "/api/v2/data/aggregate")
    assert recovery_action("POST", "/api/v2/data/aggregate", 503) == "retry_read"


def test_cli_reads_query_file_without_write_confirmation(tmp_path):
    from unittest.mock import Mock

    from office_client.cli import build_parser
    from office_client.cli_commands import execute

    path = tmp_path / "q.json"
    path.write_text(json.dumps(BASE))
    args = build_parser().parse_args(["aggregate", "p", "--revision", "1", "--file", str(path)])
    client = Mock()
    client.aggregate.return_value = {"complete": True}
    assert execute(client, args) == (True, {"complete": True})
    client.aggregate.assert_called_once_with("p", 1, BASE, scope="own")


def test_http_query_model_and_error_envelope(monkeypatch, tmp_path):
    from app.infrastructure.config import Settings
    from app.main import create_app

    settings = Settings(
        database_user="office_app",
        database_password_file=tmp_path / "unused",
        issuer="https://identity.invalid",
        audience="https://service.invalid/mcp",
        public_url="https://service.invalid",
    )
    monkeypatch.setattr("app.main.async_sessions", lambda _: SimpleNamespace())
    app = create_app(settings)
    app.state.application.verifier.verify_async = AsyncMock(return_value=object())
    app.state.application.call = AsyncMock()

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/api/v2/data/aggregate",
                headers={"Authorization": "Bearer synthetic"},
                json={"identifier": "p", "revision": True, "query": BASE},
            )
            assert response.status_code == 422 and response.json()["code"] == 422001
            assert response.json()["meta"]["trace_id"]
            app.state.application.call.assert_not_called()

    asyncio.run(run())
