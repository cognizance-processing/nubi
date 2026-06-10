"""A4: Output-shape contract — a registered query declares its output columns +
portable types; the query route validates the executed result against them.

Coverage
--------
Unit (no HTTP):
  - ``OutputColumn`` dataclass defaults.
  - ``_portable_arrow_type`` maps Arrow types → text|number|bool|date|timestamp|json.
  - ``_validate_output_schema`` passes on a match, flags renamed/retyped columns,
    and skips entirely when output_schema is None.
  - ``_schema_from_config`` round-trips the persisted list-of-dicts form and
    coerces unknown types to "text"; None → None.

HTTP integration (DuckDBConnector demo path + registry, FakeDB injection):
  - A declared schema that MATCHES the result → 200, no X-Nubi-Schema header.
  - A declared schema with a RENAMED column → 200 + X-Nubi-Schema: MISMATCH
    (WARN mode, default).
  - A declared schema with a RETYPED column → 200 + X-Nubi-Schema: MISMATCH.
  - STRICT mode (env NUBI_OUTPUT_SCHEMA_STRICT) → 422 output_schema_mismatch.
  - STRICT mode (per-query flag) → 422 output_schema_mismatch.
  - A query WITHOUT output_schema is unaffected (no header, 200).
"""

from __future__ import annotations

import uuid
from io import BytesIO

import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Unit tests — registry dataclass + helpers
# ---------------------------------------------------------------------------


class TestOutputColumnDataclass:
    def test_defaults(self):
        from app.queries.registry import OutputColumn

        c = OutputColumn(name="id")
        assert c.name == "id"
        assert c.type == "text"

    def test_explicit_type(self):
        from app.queries.registry import OutputColumn

        c = OutputColumn(name="value", type="number")
        assert c.type == "number"


class TestPortableArrowType:
    def test_mappings(self):
        from app.routes.query import _portable_arrow_type

        assert _portable_arrow_type(pa.int32()) == "number"
        assert _portable_arrow_type(pa.int64()) == "number"
        assert _portable_arrow_type(pa.float64()) == "number"
        assert _portable_arrow_type(pa.decimal128(10, 2)) == "number"
        assert _portable_arrow_type(pa.string()) == "text"
        assert _portable_arrow_type(pa.large_string()) == "text"
        assert _portable_arrow_type(pa.bool_()) == "bool"
        assert _portable_arrow_type(pa.date32()) == "date"
        assert _portable_arrow_type(pa.date64()) == "date"
        assert _portable_arrow_type(pa.timestamp("us")) == "timestamp"
        # Non-portable types fall back to json.
        assert _portable_arrow_type(pa.list_(pa.int32())) == "json"
        assert _portable_arrow_type(pa.binary()) == "json"


class TestValidateOutputSchema:
    def _table(self):
        return pa.table(
            {
                "id": pa.array([1, 2], type=pa.int32()),
                "name": pa.array(["a", "b"], type=pa.string()),
            }
        )

    def test_none_schema_skips(self):
        from app.queries.registry import QueryRegistry
        from app.routes.query import _validate_output_schema

        rq = QueryRegistry().register(id="q", sql="SELECT 1", name="Q")
        ok, detail = _validate_output_schema(rq, self._table())
        assert ok is True
        assert detail is None

    def test_match_passes(self):
        from app.queries.registry import OutputColumn, QueryRegistry
        from app.routes.query import _validate_output_schema

        rq = QueryRegistry().register(
            id="q",
            sql="SELECT 1",
            name="Q",
            output_schema=[
                OutputColumn(name="id", type="number"),
                OutputColumn(name="name", type="text"),
            ],
        )
        ok, detail = _validate_output_schema(rq, self._table())
        assert ok is True, detail

    def test_renamed_column_flagged(self):
        from app.queries.registry import OutputColumn, QueryRegistry
        from app.routes.query import _validate_output_schema

        rq = QueryRegistry().register(
            id="q",
            sql="SELECT 1",
            name="Q",
            output_schema=[
                OutputColumn(name="id", type="number"),
                OutputColumn(name="label", type="text"),  # actual is "name"
            ],
        )
        ok, detail = _validate_output_schema(rq, self._table())
        assert ok is False
        assert "label" in detail

    def test_retyped_column_flagged(self):
        from app.queries.registry import OutputColumn, QueryRegistry
        from app.routes.query import _validate_output_schema

        rq = QueryRegistry().register(
            id="q",
            sql="SELECT 1",
            name="Q",
            output_schema=[
                OutputColumn(name="id", type="text"),  # actual is number
                OutputColumn(name="name", type="text"),
            ],
        )
        ok, detail = _validate_output_schema(rq, self._table())
        assert ok is False
        assert "id" in detail

    def test_column_count_mismatch_flagged(self):
        from app.queries.registry import OutputColumn, QueryRegistry
        from app.routes.query import _validate_output_schema

        rq = QueryRegistry().register(
            id="q",
            sql="SELECT 1",
            name="Q",
            output_schema=[OutputColumn(name="id", type="number")],
        )
        ok, detail = _validate_output_schema(rq, self._table())
        assert ok is False
        assert "count" in detail


class TestSchemaFromConfig:
    def test_none_returns_none(self):
        from app.queries.registry import _schema_from_config

        assert _schema_from_config(None) is None

    def test_roundtrip(self):
        from app.queries.registry import _schema_from_config

        out = _schema_from_config(
            [{"name": "id", "type": "number"}, {"name": "label", "type": "text"}]
        )
        assert out is not None
        assert [(c.name, c.type) for c in out] == [
            ("id", "number"),
            ("label", "text"),
        ]

    def test_unknown_type_coerced_to_text(self):
        from app.queries.registry import _schema_from_config

        out = _schema_from_config([{"name": "x", "type": "weird_type"}])
        assert out[0].type == "text"


# ---------------------------------------------------------------------------
# Persist / load round-trip survival (load_persisted_queries shape)
# ---------------------------------------------------------------------------


class TestPersistLoadRoundTrip:
    def test_register_carries_output_schema(self):
        from app.queries.registry import OutputColumn, QueryRegistry

        reg = QueryRegistry()
        rq = reg.register(
            id="q",
            sql="SELECT 1",
            name="Q",
            output_schema=[OutputColumn(name="id", type="number")],
            strict_output_schema=True,
        )
        assert rq.output_schema == (OutputColumn(name="id", type="number"),)
        assert rq.strict_output_schema is True

    def test_no_schema_defaults_to_none(self):
        from app.queries.registry import QueryRegistry

        rq = QueryRegistry().register(id="q", sql="SELECT 1", name="Q")
        assert rq.output_schema is None
        assert rq.strict_output_schema is False


# ---------------------------------------------------------------------------
# HTTP integration tests
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str) -> dict[str, str]:
    from app.auth.jwt import mint_access_token

    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _parse_arrow(content: bytes):
    return pa_ipc.open_stream(BytesIO(content)).read_all()


@pytest_asyncio.fixture
async def schema_client(app, fake_db):
    """HTTPX client with a seeded user for the output-schema tests."""
    user_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "schema_tester@example.com",
        "name": "Schema Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id


@pytest.mark.asyncio
async def test_matching_schema_passes_no_header(schema_client):
    """Declared schema matching the demo table → 200, no X-Nubi-Schema header."""
    from app.queries.registry import OutputColumn, get_query_registry

    client, user_id = schema_client
    get_query_registry().register(
        id="test_schema_match",
        sql="SELECT id, name, value, active FROM demo",
        name="Test — matching schema",
        output_schema=[
            OutputColumn(name="id", type="number"),
            OutputColumn(name="name", type="text"),
            OutputColumn(name="value", type="number"),
            OutputColumn(name="active", type="bool"),
        ],
    )

    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "test_schema_match"},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("X-Nubi-Cache") == "MISS"
    assert resp.headers.get("X-Nubi-Schema") is None
    table = _parse_arrow(resp.content)
    assert table.num_rows == 5


@pytest.mark.asyncio
async def test_renamed_column_warns(schema_client):
    """A renamed declared column → 200 + X-Nubi-Schema: MISMATCH (WARN mode)."""
    from app.queries.registry import OutputColumn, get_query_registry

    client, user_id = schema_client
    get_query_registry().register(
        id="test_schema_renamed",
        sql="SELECT id, name FROM demo",
        name="Test — renamed column",
        output_schema=[
            OutputColumn(name="id", type="number"),
            OutputColumn(name="label", type="text"),  # actual column is "name"
        ],
    )

    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "test_schema_renamed"},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("X-Nubi-Schema") == "MISMATCH"


@pytest.mark.asyncio
async def test_retyped_column_warns(schema_client):
    """A retyped declared column → 200 + X-Nubi-Schema: MISMATCH (WARN mode)."""
    from app.queries.registry import OutputColumn, get_query_registry

    client, user_id = schema_client
    get_query_registry().register(
        id="test_schema_retyped",
        sql="SELECT id, name FROM demo",
        name="Test — retyped column",
        output_schema=[
            OutputColumn(name="id", type="text"),  # actual is number
            OutputColumn(name="name", type="text"),
        ],
    )

    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "test_schema_retyped"},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("X-Nubi-Schema") == "MISMATCH"


@pytest.mark.asyncio
async def test_strict_mode_env_raises_422(schema_client, monkeypatch):
    """STRICT mode via NUBI_OUTPUT_SCHEMA_STRICT → 422 output_schema_mismatch."""
    from app.queries.registry import OutputColumn, get_query_registry

    monkeypatch.setenv("NUBI_OUTPUT_SCHEMA_STRICT", "1")

    client, user_id = schema_client
    get_query_registry().register(
        id="test_schema_strict_env",
        sql="SELECT id, name FROM demo",
        name="Test — strict env",
        output_schema=[
            OutputColumn(name="id", type="number"),
            OutputColumn(name="wrong", type="text"),
        ],
    )

    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "test_schema_strict_env"},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "output_schema_mismatch"


@pytest.mark.asyncio
async def test_strict_mode_per_query_flag_raises_422(schema_client):
    """STRICT mode via per-query strict_output_schema flag → 422."""
    from app.queries.registry import OutputColumn, get_query_registry

    client, user_id = schema_client
    get_query_registry().register(
        id="test_schema_strict_flag",
        sql="SELECT id, name FROM demo",
        name="Test — strict flag",
        output_schema=[
            OutputColumn(name="id", type="number"),
            OutputColumn(name="wrong", type="text"),
        ],
        strict_output_schema=True,
    )

    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "test_schema_strict_flag"},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "output_schema_mismatch"


@pytest.mark.asyncio
async def test_query_without_schema_unaffected(schema_client):
    """A query with no output_schema is unaffected: 200, no mismatch header."""
    from app.queries.registry import get_query_registry

    client, user_id = schema_client
    get_query_registry().register(
        id="test_schema_none",
        sql="SELECT id, name FROM demo",
        name="Test — no schema",
    )

    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "test_schema_none"},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("X-Nubi-Schema") is None
