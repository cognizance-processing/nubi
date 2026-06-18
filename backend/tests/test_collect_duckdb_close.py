"""Test that on-disk DuckDB connections opened by run_query_rows are closed.

Regression test for the file-descriptor leak: _resolve_connector opened a
duckdb.connect(read_only=True) for on-disk DuckDB datastores but the connection
was never explicitly closed, relying on CPython's GC to eventually release it.

This test verifies:
1. DuckDBConnector.close() closes the underlying connection.
2. Connector.close() (base) is a no-op and does not raise.
3. run_query_rows calls connector.close() after execute() completes.
4. run_query_rows calls connector.close() even when execute() raises.
5. The demo connector (singleton, owned=False) is NOT closed by run_query_rows.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")

import pathlib
import unittest.mock as mock

import duckdb
import pyarrow as pa
import pytest

from app.connectors.duckdb_conn import DuckDBConnector
from app.dashboards.collect import _resolve_connector, run_query_rows
from app.queries.registry import get_query_registry
from app.queries.registry import reset_for_tests as reset_query_registry
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

_ORG = "org-close-test"


# ---------------------------------------------------------------------------
# 1. DuckDBConnector.close() closes the connection
# ---------------------------------------------------------------------------


def test_duckdb_connector_close_releases_connection(tmp_path: pathlib.Path) -> None:
    """close() on an on-disk DuckDBConnector closes the DuckDB file connection."""
    db_file = tmp_path / "test.duckdb"
    raw_conn = duckdb.connect(str(db_file))
    raw_conn.execute("CREATE TABLE t (x INT)")
    raw_conn.close()

    raw_conn2 = duckdb.connect(str(db_file), read_only=True)
    conn = DuckDBConnector(connection=raw_conn2)

    # Connection is alive before close.
    assert conn._conn is raw_conn2

    conn.close()

    # After close(), the underlying connection is closed; further use raises.
    with pytest.raises(Exception):
        raw_conn2.execute("SELECT 1")


# ---------------------------------------------------------------------------
# 2. Connector base close() is a no-op
# ---------------------------------------------------------------------------


def test_base_connector_close_is_noop() -> None:
    """Connector.close() default implementation does not raise."""
    conn = DuckDBConnector()  # in-memory; close() should be silent
    conn.close()  # must not raise
    conn.close()  # idempotent


# ---------------------------------------------------------------------------
# 3 & 4. run_query_rows closes the connector (success and error paths)
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo_with_duckdb_datastore(tmp_path: pathlib.Path) -> InMemoryRepo:
    """Repo wired with a duckdb datastore and a matching registered query."""
    reset_query_registry()
    db_file = tmp_path / "board.duckdb"

    # Seed the on-disk DuckDB file with a table.
    seed_conn = duckdb.connect(str(db_file))
    seed_conn.execute("CREATE TABLE items AS SELECT 1 AS id, 42.0 AS value")
    seed_conn.close()

    r = InMemoryRepo()
    set_repo(r)

    import asyncio

    async def _setup() -> None:
        await r.create(
            "datastores",
            org_id=_ORG,
            created_by="test",
            name="test-duckdb",
            config={"type": "duckdb", "database": str(db_file)},
            id="ds-1",
        )

    asyncio.run(_setup())

    get_query_registry().register(
        id="q-items",
        sql="SELECT id, value FROM items",
        name="Items query",
        datastore_id="ds-1",
    )

    yield r

    set_repo(None)
    reset_query_registry()


@pytest.mark.asyncio
async def test_run_query_rows_closes_duckdb_connector_on_success(
    repo_with_duckdb_datastore: InMemoryRepo,
) -> None:
    """run_query_rows closes the owned DuckDB connector after a successful query."""
    close_calls: list[bool] = []

    original_close = DuckDBConnector.close

    def _spy_close(self: DuckDBConnector) -> None:
        close_calls.append(True)
        original_close(self)

    with mock.patch.object(DuckDBConnector, "close", _spy_close):
        cols, rows = await run_query_rows("q-items", _ORG, repo_with_duckdb_datastore, {})

    assert cols == ["id", "value"]
    assert rows == [[1, 42.0]]
    assert len(close_calls) == 1, "close() must be called exactly once after execute()"


@pytest.mark.asyncio
async def test_run_query_rows_closes_duckdb_connector_on_error(
    repo_with_duckdb_datastore: InMemoryRepo,
) -> None:
    """run_query_rows closes the owned DuckDB connector even when execute() raises."""
    close_calls: list[bool] = []

    original_close = DuckDBConnector.close

    def _spy_close(self: DuckDBConnector) -> None:
        close_calls.append(True)
        original_close(self)

    from app.errors import AppError

    def _boom(self: DuckDBConnector, plan: object) -> None:
        raise AppError("query_error", "deliberate test failure", 500)

    with mock.patch.object(DuckDBConnector, "close", _spy_close), mock.patch.object(
        DuckDBConnector, "execute", _boom
    ):
        with pytest.raises(AppError, match="deliberate test failure"):
            await run_query_rows("q-items", _ORG, repo_with_duckdb_datastore, {})

    assert len(close_calls) == 1, "close() must be called even when execute() raises"


# ---------------------------------------------------------------------------
# 5. Demo connector (singleton) is NOT closed
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo_demo() -> InMemoryRepo:
    reset_query_registry()
    get_query_registry().register(
        id="demo-q",
        sql="SELECT id, name FROM demo ORDER BY id",
        name="Demo query",
    )
    r = InMemoryRepo()
    set_repo(r)
    yield r
    set_repo(None)
    reset_query_registry()


@pytest.mark.asyncio
async def test_run_query_rows_does_not_close_demo_singleton(
    repo_demo: InMemoryRepo,
) -> None:
    """run_query_rows must NOT close the module-level demo DuckDB connector."""
    close_calls: list[bool] = []

    original_close = DuckDBConnector.close

    def _spy_close(self: DuckDBConnector) -> None:
        close_calls.append(True)
        original_close(self)

    with mock.patch.object(DuckDBConnector, "close", _spy_close):
        cols, rows = await run_query_rows("demo-q", _ORG, repo_demo, {})

    assert cols  # query ran successfully
    assert len(close_calls) == 0, "demo singleton close() must NOT be called"

    # Subsequent call must still work (connection not invalidated).
    cols2, rows2 = await run_query_rows("demo-q", _ORG, repo_demo, {})
    assert cols2 == cols


# ---------------------------------------------------------------------------
# 6. On-disk DuckDB connector is hardened (harden_connection called)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_connector_hardens_ondisk_duckdb(
    tmp_path: pathlib.Path,
) -> None:
    """_resolve_connector must call harden_connection on on-disk DuckDB connections.

    Regression test for: collect.py DuckDB branch skipped harden_connection,
    allowing ad-hoc read_csv/read_parquet/httpfs calls in snapshot/export paths.
    After the fix the hardened connection must reject local-file reads injected
    in query text (enable_external_access=false + lock_configuration=true).
    """
    from app.connectors.duckdb_conn import DuckDBConnector
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    reset_query_registry()

    org = "org-harden-test"
    db_file = tmp_path / "harden.duckdb"

    # Seed an on-disk DuckDB file with a benign table.
    seed = duckdb.connect(str(db_file))
    seed.execute("CREATE TABLE t (x INT); INSERT INTO t VALUES (1)")
    seed.close()

    repo = InMemoryRepo()
    set_repo(repo)

    await repo.create(
        "datastores",
        org_id=org,
        created_by="test",
        name="harden-duckdb",
        config={"type": "duckdb", "database": str(db_file)},
        id="ds-harden",
    )

    harden_calls: list[bool] = []
    import app.connectors.duckdb_conn as _dmod

    original_harden = _dmod.harden_connection

    def _spy_harden(conn: object, **kwargs: object) -> None:
        harden_calls.append(True)
        original_harden(conn, **kwargs)  # type: ignore[arg-type]

    with mock.patch.object(_dmod, "harden_connection", _spy_harden):
        connector, owned = await _resolve_connector(
            mock.MagicMock(datastore_id="ds-harden"),
            org,
            repo,
            None,  # physical_plan not used in the duckdb branch
        )

    try:
        assert owned is True
        assert len(harden_calls) == 1, "harden_connection must be called for on-disk DuckDB"

        # Verify the connection is actually hardened: injecting a local-file
        # read_csv should raise now that enable_external_access=false is set.
        assert isinstance(connector, DuckDBConnector)
        with pytest.raises(Exception):
            connector._conn.execute("SELECT * FROM read_csv('/etc/passwd')")
    finally:
        connector.close()
        set_repo(None)
        reset_query_registry()
