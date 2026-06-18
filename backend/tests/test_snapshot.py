"""Tests for the frozen DuckDB snapshot module + the snapshot_refresh task kind.

Coverage
--------
1. create_snapshot
   a. Collects board widget data and writes a .duckdb sidecar artifact to a
      local-path base URI; the artifact contains a widget table + the embedded
      ``_nubi_snapshot_meta`` row, and the descriptor is persisted org-scoped on
      the board.
   b. The board-not-found case raises AppError("board_not_found").

2. snapshot_refresh task kind
   a. Registered in the flows task-kind registry.
   b. The handler re-runs the collector and rewrites the SAME artifact URI in
      place (file mtime/content changes; URI unchanged), bumping refreshed_at.

All storage is local-path; the demo DuckDB connector (no datastore) supplies
rows so there is no network access.
"""

from __future__ import annotations

import os

import duckdb
import pytest

from app.embedding.snapshot import (
    create_snapshot,
    refresh_snapshot,
    snapshot_refresh_handler,
)
from app.errors import AppError
from app.flows.registry import get_task_kind_registry, reset_for_tests
from app.queries.registry import get_query_registry
from app.queries.registry import reset_for_tests as reset_query_registry
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

_ORG = "org-snap-1"
_USER = "user-snap-1"


@pytest.fixture()
def repo() -> InMemoryRepo:
    """Fresh in-memory repo, registered as the active repo, with a demo query."""
    reset_query_registry()
    # A registered query with NO datastore_id runs against the built-in demo
    # DuckDB connector (seeds a `demo` table) — fully offline.
    get_query_registry().register(
        id="demo_snap",
        sql="SELECT id, name FROM demo ORDER BY id",
        name="Demo snapshot query",
    )
    r = InMemoryRepo()
    set_repo(r)
    yield r
    set_repo(None)


async def _make_board(repo: InMemoryRepo) -> str:
    """Create a board with one data widget bound to the demo query."""
    board = await repo.create(
        "boards",
        _ORG,
        _USER,
        "Snap board",
        {
            "spec": {
                "title": "Snap board",
                "widgets": [
                    {"id": "w1", "query_id": "demo_snap", "type": "table"},
                    {"id": "txt", "type": "text"},  # no query → carries no data
                ],
            }
        },
    )
    return str(board["id"])


@pytest.mark.asyncio
async def test_create_snapshot_writes_sidecar(repo: InMemoryRepo, tmp_path) -> None:
    base_uri = "file://" + str(tmp_path)
    board_id = await _make_board(repo)

    descriptor = await create_snapshot(
        board_id,
        _ORG,
        claims={"policies": {}},
        repo=repo,
        created_by=_USER,
        schedule="0 6 * * *",
        base_uri=base_uri,
    )

    # Descriptor shape.
    assert descriptor["board_id"] == board_id
    assert descriptor["schedule"] == "0 6 * * *"
    assert descriptor["policy_fingerprint"] == "none"  # empty policies sentinel
    artifact = descriptor["artifact"]
    assert artifact["format"] == "duckdb"
    assert artifact["uri"].startswith(base_uri)

    # The data widget produced a table; the text widget produced none.
    tables = {t.get("table"): t for t in artifact["tables"] if "table" in t}
    assert len(tables) == 1
    (table_name,) = tables.keys()
    assert tables[table_name]["widget_id"] == "w1"
    assert tables[table_name]["row_count"] >= 1

    # Sidecar file exists and is a real DuckDB database with the widget table
    # plus the embedded metadata row.
    local_path = artifact["uri"][len("file://"):]
    assert os.path.exists(local_path)
    conn = duckdb.connect(local_path, read_only=True)
    try:
        names = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert table_name in names
        assert "_nubi_snapshot_meta" in names
        meta = conn.execute(
            "SELECT board_id, snapshot_id, policy_fingerprint FROM _nubi_snapshot_meta"
        ).fetchone()
        assert meta[0] == board_id
        assert meta[1] == descriptor["id"]
        assert meta[2] == "none"
        rowcount = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        assert rowcount == tables[table_name]["row_count"]
    finally:
        conn.close()

    # Persisted org-scoped on the board row.
    board = await repo.get("boards", _ORG, board_id)
    persisted = board["config"]["snapshots"]
    assert len(persisted) == 1
    assert persisted[0]["id"] == descriptor["id"]


@pytest.mark.asyncio
async def test_create_snapshot_board_not_found(repo: InMemoryRepo, tmp_path) -> None:
    with pytest.raises(AppError) as exc:
        await create_snapshot(
            "missing-board",
            _ORG,
            claims={"policies": {}},
            repo=repo,
            created_by=_USER,
            base_uri="file://" + str(tmp_path),
        )
    assert exc.value.code == "board_not_found"


def test_snapshot_refresh_task_kind_registered() -> None:
    reset_for_tests()
    registry = get_task_kind_registry()
    assert "snapshot_refresh" in registry.all()


@pytest.mark.asyncio
async def test_snapshot_refresh_rewrites_artifact_in_place(
    repo: InMemoryRepo, tmp_path
) -> None:
    base_uri = "file://" + str(tmp_path)
    board_id = await _make_board(repo)

    created = await create_snapshot(
        board_id,
        _ORG,
        claims={"policies": {}},
        repo=repo,
        created_by=_USER,
        base_uri=base_uri,
    )
    snapshot_id = created["id"]
    uri = created["artifact"]["uri"]
    local_path = uri[len("file://"):]
    assert os.path.exists(local_path)
    first_mtime = os.path.getmtime(local_path)

    # Direct refresh: same URI, bumped refreshed_at.
    refreshed = await refresh_snapshot(
        board_id,
        _ORG,
        snapshot_id,
        claims={"policies": {}},
        repo=repo,
        base_uri=base_uri,
    )
    assert refreshed["id"] == snapshot_id
    assert refreshed["artifact"]["uri"] == uri  # rewritten IN PLACE
    assert os.path.exists(local_path)
    assert os.path.getmtime(local_path) >= first_mtime

    # Still exactly one persisted descriptor (upsert, not append).
    board = await repo.get("boards", _ORG, board_id)
    assert len(board["config"]["snapshots"]) == 1

    # The registered task-kind handler path rewrites the same artifact too.
    result = await snapshot_refresh_handler(
        config={
            "board_id": board_id,
            "snapshot_id": snapshot_id,
            "org_id": _ORG,
            "policies": {},
            "base_uri": base_uri,
        },
        ctx=None,
        claims={},
    )
    assert result["snapshot_id"] == snapshot_id
    assert result["artifact_uri"] == uri
    assert os.path.exists(local_path)
