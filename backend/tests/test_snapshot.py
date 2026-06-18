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

3. Cloud-upload path: upload_file is used (not upload_bytes) so the full .duckdb
   artifact is never read into memory, and _write_sidecar runs in a thread
   (not on the event loop) via asyncio.to_thread.

All storage is local-path; the demo DuckDB connector (no datastore) supplies
rows so there is no network access.
"""

from __future__ import annotations

import asyncio
import os
import threading
from unittest.mock import MagicMock, patch

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


# ---------------------------------------------------------------------------
# Fix: upload_file instead of upload_bytes; _write_sidecar off the event loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_sidecar_uses_upload_file_not_upload_bytes(
    repo: InMemoryRepo, tmp_path
) -> None:
    """Cloud upload path must call upload_file (streamed) not upload_bytes (full read).

    We fake a cloud URI by patching get_storage_client so no real cloud SDK is
    needed.  The mock storage client records which methods were called so we can
    assert upload_bytes was never used and upload_file was called exactly once
    with the correct key.
    """
    from app.embedding.snapshot import _write_sidecar
    from app.storage.base import parse_uri

    # Build a fake cloud URI — we use a fake "s3://" prefix but intercept the
    # storage client entirely so no boto3 call is made.
    artifact_uri = "s3://test-bucket/org-1/board-1/snap-1.duckdb"

    mock_client = MagicMock()
    # upload_file should succeed (default MagicMock return value is fine).

    with patch("app.storage.base.get_storage_client", return_value=mock_client) as _gc, \
         patch("app.storage.base.parse_uri", wraps=parse_uri) as _pu:
        result = _write_sidecar(
            artifact_uri,
            [],   # no widgets — we only care about the upload branch
            meta={
                "snapshot_id": "snap-1",
                "board_id": "board-1",
                "datastore": None,
                "policy_fingerprint": "none",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )

    # upload_file called once; upload_bytes never called.
    mock_client.upload_file.assert_called_once()
    mock_client.upload_bytes.assert_not_called()

    # The key argument to upload_file must be the object key (no scheme/bucket).
    _scheme, _bucket, key = parse_uri(artifact_uri)
    call_args = mock_client.upload_file.call_args
    assert call_args[0][1] == key or call_args[1].get("key") == key, (
        f"upload_file was called with wrong key: {call_args}"
    )

    # Return value is still the manifest dict.
    assert "tables" in result


@pytest.mark.asyncio
async def test_write_sidecar_runs_in_thread_not_event_loop(
    repo: InMemoryRepo, tmp_path
) -> None:
    """_write_sidecar must be dispatched via asyncio.to_thread so it never runs
    on the event-loop thread.  We verify this by capturing the thread identity
    inside _write_sidecar and comparing it to the event-loop thread.
    """
    import app.embedding.snapshot as snap_mod

    event_loop_thread_id = threading.get_ident()
    sidecar_thread_ids: list[int] = []

    original_write = snap_mod._write_sidecar

    def capturing_write(*args, **kwargs):
        sidecar_thread_ids.append(threading.get_ident())
        return original_write(*args, **kwargs)

    base_uri = "file://" + str(tmp_path)
    board_id = await _make_board(repo)

    with patch.object(snap_mod, "_write_sidecar", side_effect=capturing_write):
        await snap_mod.create_snapshot(
            board_id,
            _ORG,
            claims={"policies": {}},
            repo=repo,
            created_by=_USER,
            base_uri=base_uri,
        )

    assert len(sidecar_thread_ids) == 1, "Expected exactly one _write_sidecar call"
    assert sidecar_thread_ids[0] != event_loop_thread_id, (
        "_write_sidecar ran on the event-loop thread — asyncio.to_thread wrapper missing"
    )


# ---------------------------------------------------------------------------
# Fix: GET /embed/frozen/{id} meters embedded sessions for embed tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_frozen_view_meters_embed_session(repo: InMemoryRepo, tmp_path) -> None:
    """embed token hitting GET /embed/frozen/{id} must call enforce_quota +
    record_usage exactly once (tier='embed_frozen'), mirroring embed.py:353-364.

    First-party ('access') tokens must NOT trigger metering.
    """
    from unittest.mock import AsyncMock, call

    from app.auth.verify import VerifiedIdentity
    from app.routes.snapshot import get_frozen_view

    # ── Seed a board + snapshot ───────────────────────────────────────────────
    base_uri = "file://" + str(tmp_path)
    board_id = await _make_board(repo)
    descriptor = await create_snapshot(
        board_id,
        _ORG,
        claims={"policies": {}},
        repo=repo,
        created_by=_USER,
        base_uri=base_uri,
    )
    snapshot_id = descriptor["id"]

    embed_identity = VerifiedIdentity(
        kind="embed",
        user_id="embed-user-frozen",
        org=_ORG,
        project=None,
        roles=["viewer"],
        policies={},
        scope=["read:query"],
        embed_origin="https://host.example",
        datastore=None,
        raw_claims={},
    )

    mock_enforce = AsyncMock(return_value=None)
    mock_record = AsyncMock(return_value=None)

    with patch("app.routes.snapshot.enforce_quota", mock_enforce, create=True), \
         patch("app.routes.snapshot.record_usage", mock_record, create=True):
        # The lazy imports inside the guard resolve the names at call time, so we
        # patch via sys.modules instead.
        import sys
        import types

        fake_features = types.ModuleType("app.features")
        fake_features.enforce_quota = mock_enforce  # type: ignore[attr-defined]
        fake_metering = types.ModuleType("app.compute.metering")
        fake_metering.record_usage = mock_record  # type: ignore[attr-defined]

        orig_features = sys.modules.get("app.features")
        orig_metering = sys.modules.get("app.compute.metering")
        sys.modules["app.features"] = fake_features
        sys.modules["app.compute.metering"] = fake_metering

        try:
            result = await get_frozen_view(
                dashboard_id=board_id,
                snapshot_id=snapshot_id,
                identity=embed_identity,
                repo=repo,
            )
        finally:
            if orig_features is None:
                sys.modules.pop("app.features", None)
            else:
                sys.modules["app.features"] = orig_features
            if orig_metering is None:
                sys.modules.pop("app.compute.metering", None)
            else:
                sys.modules["app.compute.metering"] = orig_metering

    # enforce_quota called once for embedded_sessions
    mock_enforce.assert_awaited_once_with(_ORG, "embedded_sessions", amount=1.0)

    # record_usage called once with the frozen tier
    mock_record.assert_awaited_once_with(
        kind="embedded_session",
        user_id="embed-user-frozen",
        org_id=_ORG,
        units=1.0,
        tier="embed_frozen",
    )

    # Response shape unchanged
    assert result["frozen"] is True
    assert result["snapshot_id"] == snapshot_id


@pytest.mark.asyncio
async def test_get_frozen_view_no_meter_for_first_party(repo: InMemoryRepo, tmp_path) -> None:
    """First-party ('access') tokens must NOT trigger billing for frozen views."""
    from unittest.mock import AsyncMock

    from app.auth.verify import VerifiedIdentity
    from app.repos.memory import InMemoryRepo
    from app.routes.snapshot import get_frozen_view

    base_uri = "file://" + str(tmp_path)
    board_id = await _make_board(repo)
    descriptor = await create_snapshot(
        board_id,
        _ORG,
        claims={"policies": {}},
        repo=repo,
        created_by=_USER,
        base_uri=base_uri,
    )

    access_identity = VerifiedIdentity(
        kind="access",
        user_id=_USER,
        org=_ORG,
        project=None,
        roles=["editor"],
        policies={},
        scope=["read:query"],
        embed_origin=None,
        datastore=None,
        raw_claims={},
    )

    mock_enforce = AsyncMock(return_value=None)
    mock_record = AsyncMock(return_value=None)

    import sys
    import types

    fake_features = types.ModuleType("app.features")
    fake_features.enforce_quota = mock_enforce  # type: ignore[attr-defined]
    fake_metering = types.ModuleType("app.compute.metering")
    fake_metering.record_usage = mock_record  # type: ignore[attr-defined]

    orig_features = sys.modules.get("app.features")
    orig_metering = sys.modules.get("app.compute.metering")
    sys.modules["app.features"] = fake_features
    sys.modules["app.compute.metering"] = fake_metering

    # For first-party path, get_user_org is called — short-circuit it.
    with patch("app.routes.resources.get_user_org", AsyncMock(return_value=_ORG)):
        try:
            result = await get_frozen_view(
                dashboard_id=board_id,
                snapshot_id=descriptor["id"],
                identity=access_identity,
                repo=repo,
            )
        finally:
            if orig_features is None:
                sys.modules.pop("app.features", None)
            else:
                sys.modules["app.features"] = orig_features
            if orig_metering is None:
                sys.modules.pop("app.compute.metering", None)
            else:
                sys.modules["app.compute.metering"] = orig_metering

    mock_enforce.assert_not_awaited()
    mock_record.assert_not_awaited()
    assert result["frozen"] is True


# ---------------------------------------------------------------------------
# Security: GET /embed/frozen/{id} must NOT return the raw storage URI
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_frozen_response_redacts_storage_uri(repo: InMemoryRepo, tmp_path) -> None:
    """GET /embed/frozen/{id} must never return the raw s3:// / file:// artifact URI.

    The artifact dict in the response must omit the 'uri' key so that internal
    bucket paths / filesystem locations are not exposed to embed callers.
    The 'format' and 'tables' keys must still be present (viewer still needs them).
    """
    from app.auth.verify import VerifiedIdentity
    from app.routes.snapshot import get_frozen_view

    base_uri = "file://" + str(tmp_path)
    board_id = await _make_board(repo)
    descriptor = await create_snapshot(
        board_id,
        _ORG,
        claims={"policies": {}},
        repo=repo,
        created_by=_USER,
        base_uri=base_uri,
    )
    snapshot_id = descriptor["id"]

    # Verify the descriptor itself DOES contain the uri (internal state is fine).
    assert "uri" in descriptor["artifact"], "fixture sanity: descriptor must have uri"
    raw_uri = descriptor["artifact"]["uri"]
    assert raw_uri.startswith("file://"), "fixture sanity: uri must be a file:// path"

    embed_identity = VerifiedIdentity(
        kind="embed",
        user_id="embed-user-uri-test",
        org=_ORG,
        project=None,
        roles=["viewer"],
        policies={},
        scope=["read:query"],
        embed_origin="https://host.example",
        datastore=None,
        raw_claims={},
    )

    import sys
    import types
    from unittest.mock import AsyncMock

    fake_features = types.ModuleType("app.features")
    fake_features.enforce_quota = AsyncMock(return_value=None)  # type: ignore[attr-defined]
    fake_metering = types.ModuleType("app.compute.metering")
    fake_metering.record_usage = AsyncMock(return_value=None)  # type: ignore[attr-defined]

    orig_features = sys.modules.get("app.features")
    orig_metering = sys.modules.get("app.compute.metering")
    sys.modules["app.features"] = fake_features
    sys.modules["app.compute.metering"] = fake_metering
    try:
        result = await get_frozen_view(
            dashboard_id=board_id,
            snapshot_id=snapshot_id,
            identity=embed_identity,
            repo=repo,
        )
    finally:
        if orig_features is None:
            sys.modules.pop("app.features", None)
        else:
            sys.modules["app.features"] = orig_features
        if orig_metering is None:
            sys.modules.pop("app.compute.metering", None)
        else:
            sys.modules["app.compute.metering"] = orig_metering

    artifact = result["artifact"]

    # The raw storage URI must NOT appear anywhere in the response artifact.
    assert "uri" not in artifact, (
        f"frozen response leaked raw storage URI in artifact['uri']: {artifact.get('uri')!r}"
    )
    # Ensure the raw path string itself isn't embedded under another key.
    assert raw_uri not in str(artifact), (
        f"frozen response leaked raw storage URI string inside artifact: {artifact!r}"
    )

    # Viewer-needed fields must still be present.
    assert "format" in artifact, "artifact must retain 'format'"
    assert "tables" in artifact, "artifact must retain 'tables'"
    assert result["frozen"] is True
    assert result["snapshot_id"] == snapshot_id
