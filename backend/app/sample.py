"""Onboarding *sample bundle* seeder — a removable starter workspace.

Every new org/project gets a small, real, explorable bundle so the user lands on
a populated workspace instead of an empty one.  The bundle is created from the
SAME declarative demo fixtures the superuser demo uses (``seed_data/demo/*.json``
via ``app/demo_bundle.py``) — but only the ``starter`` subset of dashboards — and
points at a REAL ``duckdb`` datastore:

- **S3 configured** (``S3_ACCESS_KEY`` / ``AWS_ACCESS_KEY_ID`` env vars present):
  the demo star schema is exported per-project to
  ``s3://<bucket>/projects/<project_id>/demo/<table>.parquet`` and the datastore
  config exposes all 6 tables as DuckDB views over those S3 parquet files.  Each
  new project gets its own isolated file set (idempotent re-seeds are safe — files
  are written only once per project).

- **No S3** (offline dev / CI without MinIO): falls back to the single shared,
  read-only DuckDB file (``seed_data/sample.duckdb``).  Behaviour is identical to
  the pre-S3 implementation.

Every row created here is tagged ``config.sample = true`` (plus a stable
``config.sample_id`` for idempotency) so the whole bundle can be bulk-removed —
and later restored — by the remove/restore endpoints in ``app/routes/projects.py``.

Public API
----------
``seed_sample_bundle(org_id, project_id, created_by)``
    Idempotently create the starter bundle.  Safe to call on every signup / to
    re-run for "restore".  Never raises on the happy path — returns
    ``{"skipped": reason}`` if the demo dataset can't be built.
``remove_sample_bundle(org_id, project_id=None)``
    Delete every ``sample = true`` resource in the org (optionally scoped to a
    project).  Returns the per-resource delete counts.
"""

from __future__ import annotations

from typing import Any

from app.demo_bundle import (
    _s3_is_configured,
    datastore_config,
    export_demo_to_s3,
    load_boards,
    load_queries,
    referenced_query_keys,
    resolve_placeholders,
    s3_datastore_config,
    sample_db_path,
)
from app.repos.provider import Repo, get_repo

# ── Stable sample identifiers (stored in config.sample_id) ────────────────────
SAMPLE_DS = "sample:datastore:duckdb"

# Resource tables the bundle touches (order matters for remove: boards →
# queries → datastores, so nothing dangling is left if interrupted).
_SAMPLE_TABLES = ("boards", "queries", "datastores")


# ── Idempotency helpers ─────────────────────────────────────────────────────────

async def _find_sample(
    repo: Repo, table: str, org_id: str, sample_id: str
) -> dict[str, Any] | None:
    """Return the existing bundle row for *sample_id* in *org_id*, or ``None``."""
    for row in await repo.list(table, org_id):
        cfg = row.get("config") or {}
        if cfg.get("sample") is True and cfg.get("sample_id") == sample_id:
            return row
    return None


async def _upsert(
    repo: Repo,
    table: str,
    org_id: str,
    created_by: str,
    name: str,
    config: dict[str, Any],
    sample_id: str,
    project_id: str | None,
) -> tuple[dict[str, Any], bool]:
    """Create the row (tagged sample) if absent; return ``(row, created)``."""
    existing = await _find_sample(repo, table, org_id, sample_id)
    if existing is not None:
        return existing, False
    full_config = {**config, "sample": True, "sample_id": sample_id}
    row = await repo.create(
        table,
        org_id=org_id,
        created_by=created_by,
        name=name,
        config=full_config,
        project_id=project_id,
    )
    return row, True


# ── Public API ──────────────────────────────────────────────────────────────────

async def seed_sample_bundle(
    org_id: str,
    project_id: str | None,
    created_by: str,
    repo: Repo | None = None,
) -> dict[str, Any]:
    """Idempotently seed the removable starter bundle into *org_id* / *project_id*.

    When S3 is configured (``S3_ACCESS_KEY`` / ``AWS_ACCESS_KEY_ID`` env vars),
    exports the demo star schema to per-project S3 parquet files BEFORE creating
    the datastore row, so the connector is live-backed by real object storage from
    day one.  Falls back to the bundled read-only local file when S3 is absent.

    Creates a "Sample" DuckDB datastore, the queries the starter boards need, and
    the ``starter`` dashboard(s) from the shared demo fixtures — all tagged
    ``sample=true``.  Designed to never break signup: returns
    ``{"skipped": reason}`` if the bundled dataset can't be built.
    """
    repo = repo or get_repo()

    # ── 1. Build / resolve the datastore config ────────────────────────────────
    ds_config: dict[str, Any]

    if project_id is not None and _s3_is_configured():
        # S3 path: export per-project parquet files, then point the datastore at them.
        try:
            export_demo_to_s3(project_id)
            ds_config = s3_datastore_config(project_id)
        except Exception as exc:  # noqa: BLE001 — fall back gracefully
            # S3 export failed; fall back to the bundled local file.
            try:
                db_path = sample_db_path()
                ds_config = datastore_config(db_path)
            except Exception as exc2:  # noqa: BLE001
                return {"skipped": f"sample db unavailable: {exc}; fallback also failed: {exc2}"}
    else:
        # Local-file fallback (no S3 configured or no project_id).
        try:
            db_path = sample_db_path()
            ds_config = datastore_config(db_path)
        except Exception as exc:  # noqa: BLE001 — never fail signup over the sample bundle
            return {"skipped": f"sample db unavailable: {exc}"}

    created: list[str] = []

    # ── 2. Sample datastore ────────────────────────────────────────────────────
    ds, ds_created = await _upsert(
        repo, "datastores", org_id, created_by, "Sample",
        ds_config, SAMPLE_DS, project_id,
    )
    if ds_created:
        created.append("datastores")
    datastore_id = str(ds["id"])

    # ── 3. Starter boards + the queries they reference ─────────────────────────
    boards = load_boards(starter_only=True)
    queries = load_queries()
    needed = referenced_query_keys(boards)

    idmap: dict[str, str] = {}
    for key in needed:
        q = queries.get(key)
        if q is None:
            continue
        row, q_created = await _upsert(
            repo, "queries", org_id, created_by, q["name"],
            {"sql": q["sql"], "datastore_id": datastore_id, "params": q["params"]},
            f"sample:query:{key}", project_id,
        )
        idmap[f"@{key}"] = str(row["id"])
        if q_created:
            created.append("queries")

    # ── 4. Starter dashboard(s) — resolve @placeholders to real query UUIDs ───
    board_ids: list[str] = []
    for b in boards:
        spec = resolve_placeholders(b["spec"], idmap)
        board, b_created = await _upsert(
            repo, "boards", org_id, created_by, b["name"],
            {"spec": spec}, f"sample:{b['seed_id']}", project_id,
        )
        board_ids.append(str(board["id"]))
        if b_created:
            created.append("boards")

    return {
        "datastore_id": datastore_id,
        "board_ids": board_ids,
        "created": created,
    }


async def remove_sample_bundle(
    org_id: str,
    project_id: str | None = None,
    repo: Repo | None = None,
) -> dict[str, int]:
    """Delete every ``sample=true`` resource in *org_id* (optionally a project).

    Returns ``{table: deleted_count, ...}``.  Idempotent — removing an already
    empty bundle returns all-zero counts.
    """
    repo = repo or get_repo()
    counts: dict[str, int] = {}
    for table in _SAMPLE_TABLES:
        deleted = 0
        rows = await repo.list(table, org_id, project_id)
        for row in rows:
            cfg = row.get("config") or {}
            if cfg.get("sample") is True:
                if await repo.delete(table, org_id, str(row["id"])):
                    deleted += 1
        counts[table] = deleted
    return counts
