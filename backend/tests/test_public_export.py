"""Tests for the UNSAFE public/CDN static export (Mode 3b).

Coverage
--------
1. Gating (UNSAFE — OFF by default)
   a. With the deployment switch ALLOW_UNSAFE_PUBLIC_EXPORTS off, the export is
      refused with AppError("public_exports_disabled", 403) and NOTHING is
      written.
   b. With the deployment switch on but the org feature gate off, the export is
      likewise refused 403.

2. Success path (both interlocks on)
   a. A fresh snapshot is captured (Mode 3a reuse) and a self-contained static
      HTML is written that embeds/links the snapshot sidecar artifact + the loud
      UNSAFE notice.
   b. An audit entry is written org-scoped on the board row.
   c. Reusing an existing snapshot_id links that exact sidecar.

All storage is local-path; the demo DuckDB connector (no datastore) supplies
rows so there is no network access.
"""

from __future__ import annotations

import os

import pytest

from app.embedding.public_export import PUBLIC_EXPORTS_FEATURE, export_public
from app.errors import AppError
from app.features import register_feature
from app.features import reset_for_tests as reset_features
from app.queries.registry import get_query_registry
from app.queries.registry import reset_for_tests as reset_query_registry
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

_ORG = "org-pubexp-1"
_USER = "user-pubexp-1"


@pytest.fixture()
def repo() -> InMemoryRepo:
    """Fresh in-memory repo + demo query, with feature gates reset around it."""
    reset_query_registry()
    reset_features()
    get_query_registry().register(
        id="demo_pub",
        sql="SELECT id, name FROM demo ORDER BY id",
        name="Demo public-export query",
    )
    r = InMemoryRepo()
    set_repo(r)
    yield r
    set_repo(None)
    reset_features()


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Clear the cached Settings so monkeypatched env vars take effect."""
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _make_board(repo: InMemoryRepo) -> str:
    board = await repo.create(
        "boards",
        _ORG,
        _USER,
        "Pub board",
        {
            "spec": {
                "title": "Pub board",
                "widgets": [
                    {"id": "w1", "query_id": "demo_pub", "type": "table"},
                    {"id": "txt", "type": "text"},
                ],
            }
        },
    )
    return str(board["id"])


def _enable_deployment(monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("ALLOW_UNSAFE_PUBLIC_EXPORTS", "true")
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_export_refused_when_deployment_switch_off(
    repo: InMemoryRepo, tmp_path, monkeypatch
) -> None:
    # Deployment switch off (default). Org feature gate would be on by default in
    # OSS, but the kill switch must still refuse.
    monkeypatch.delenv("ALLOW_UNSAFE_PUBLIC_EXPORTS", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()
    board_id = await _make_board(repo)

    with pytest.raises(AppError) as exc:
        await export_public(
            board_id,
            _ORG,
            claims={"policies": {}},
            repo=repo,
            exported_by=_USER,
            base_uri="file://" + str(tmp_path),
        )
    assert exc.value.code == "public_exports_disabled"
    assert exc.value.status == 403

    # Nothing written, no audit entry.
    assert not os.listdir(tmp_path) or all(
        not f.endswith(".html") for root, _, files in os.walk(tmp_path) for f in files
    )
    board = await repo.get("boards", _ORG, board_id)
    assert "public_export_audit" not in (board["config"] or {})


@pytest.mark.asyncio
async def test_export_refused_when_org_feature_gate_off(
    repo: InMemoryRepo, tmp_path, monkeypatch
) -> None:
    _enable_deployment(monkeypatch)
    # Org feature gate explicitly off.
    register_feature(PUBLIC_EXPORTS_FEATURE, lambda: False)
    board_id = await _make_board(repo)

    with pytest.raises(AppError) as exc:
        await export_public(
            board_id,
            _ORG,
            claims={"policies": {}},
            repo=repo,
            exported_by=_USER,
            base_uri="file://" + str(tmp_path),
        )
    assert exc.value.code == "public_exports_disabled"
    assert exc.value.status == 403

    board = await repo.get("boards", _ORG, board_id)
    assert "public_export_audit" not in (board["config"] or {})


@pytest.mark.asyncio
async def test_export_succeeds_writes_html_audit_and_links_snapshot(
    repo: InMemoryRepo, tmp_path, monkeypatch
) -> None:
    _enable_deployment(monkeypatch)
    register_feature(PUBLIC_EXPORTS_FEATURE, lambda: True)
    base_uri = "file://" + str(tmp_path)
    board_id = await _make_board(repo)

    descriptor = await export_public(
        board_id,
        _ORG,
        claims={"policies": {}},
        repo=repo,
        exported_by=_USER,
        public_base_url="https://cdn.example.com/exports",
        base_uri=base_uri,
    )

    assert descriptor["unsafe"] is True
    assert descriptor["board_id"] == board_id
    assert descriptor["snapshot_id"]

    # HTML artifact exists on disk.
    html_path = descriptor["html_uri"][len("file://"):]
    assert os.path.exists(html_path)
    with open(html_path, encoding="utf-8") as fh:
        document = fh.read()

    # Loud UNSAFE notice present.
    assert "UNSAFE PUBLIC EXPORT" in document
    assert "nubi-unsafe-banner" in document
    # DuckDB-WASM loaded from a CDN (no live backend).
    assert "duckdb-wasm" in document
    # The snapshot sidecar's PUBLIC URL is embedded/linked.
    assert descriptor["snapshot_public_url"] in document
    assert descriptor["snapshot_public_url"].startswith("https://cdn.example.com/exports/")
    assert descriptor["snapshot_public_url"].endswith(".duckdb")

    # The referenced snapshot sidecar was actually written (Mode 3a reuse).
    snap = next(
        s
        for s in (await repo.get("boards", _ORG, board_id))["config"]["snapshots"]
        if s["id"] == descriptor["snapshot_id"]
    )
    snap_local = snap["artifact"]["uri"][len("file://"):]
    assert os.path.exists(snap_local)

    # Audit entry written org-scoped on the board row.
    board = await repo.get("boards", _ORG, board_id)
    audit = board["config"]["public_export_audit"]
    assert len(audit) == 1
    assert audit[0]["exported_by"] == _USER
    assert audit[0]["board_id"] == board_id
    assert audit[0]["snapshot_id"] == descriptor["snapshot_id"]
    assert audit[0]["unsafe"] is True


@pytest.mark.asyncio
async def test_export_reuses_existing_snapshot(
    repo: InMemoryRepo, tmp_path, monkeypatch
) -> None:
    _enable_deployment(monkeypatch)
    register_feature(PUBLIC_EXPORTS_FEATURE, lambda: True)
    base_uri = "file://" + str(tmp_path)
    board_id = await _make_board(repo)

    from app.embedding.snapshot import create_snapshot

    snap = await create_snapshot(
        board_id,
        _ORG,
        claims={"policies": {}},
        repo=repo,
        created_by=_USER,
        base_uri=base_uri,
    )

    descriptor = await export_public(
        board_id,
        _ORG,
        claims={"policies": {}},
        repo=repo,
        exported_by=_USER,
        snapshot_id=snap["id"],
        base_uri=base_uri,
    )

    assert descriptor["snapshot_id"] == snap["id"]
    # Linked to the SAME frozen sidecar; no second snapshot created.
    board = await repo.get("boards", _ORG, board_id)
    assert len(board["config"]["snapshots"]) == 1
    assert snap["artifact"]["uri"] in descriptor["snapshot_public_url"] or (
        descriptor["snapshot_public_url"] == snap["artifact"]["uri"]
    )

    # Unknown snapshot id → 404.
    with pytest.raises(AppError) as exc:
        await export_public(
            board_id,
            _ORG,
            claims={"policies": {}},
            repo=repo,
            exported_by=_USER,
            snapshot_id="does-not-exist",
            base_uri=base_uri,
        )
    assert exc.value.code == "snapshot_not_found"
