"""Onboarding *sample bundle* seeder — a removable starter workspace.

Every new org/project gets a real, explorable bundle so the user lands on a
populated workspace instead of an empty one.  The bundle is created from the
SAME declarative demo fixtures the superuser demo uses (``seed_data/demo/*.json``
via ``app/demo_bundle.py``) — the FULL set: four demo datasets (retail sales,
SaaS metrics, web analytics, finance ops — 17 tables), all registered queries,
and all 10 dashboards — pointing at a single REAL ``duckdb`` datastore that
behaves exactly like a user-created connector (parquet + ``read_parquet`` views,
no demo special-casing in the query pipeline):

- **S3 configured** (``S3_ACCESS_KEY`` / ``AWS_ACCESS_KEY_ID`` env vars present):
  every dataset is exported per-project to
  ``s3://<bucket>/projects/<project_id>/demo/<dataset>/<table>.parquet`` and the
  datastore config exposes all 17 tables as DuckDB views over those S3 parquet
  files.  Each new project gets its own isolated file set (idempotent re-seeds
  are safe — files are written only once per project).

- **No S3** (offline dev / CI without MinIO): the same parquet files are written
  once to the deterministic local directory
  ``backend/seed_data/parquet/<dataset>/<table>.parquet`` and the datastore views
  read those local files — local mode ALSO goes through parquet.

Every row created here is tagged ``config.sample = true`` (plus a stable
``config.sample_id`` for idempotency) so the whole bundle can be bulk-removed —
and later restored — by the remove/restore endpoints in ``app/routes/projects.py``.

Public API
----------
``seed_sample_bundle(org_id, project_id, created_by)``
    Idempotently create the starter bundle.  Safe to call on every signup / to
    re-run for "restore".  Never raises on the happy path — returns
    ``{"skipped": reason}`` if the demo dataset can't be built.
``checkpoint_and_promote_bundle(org_id, project_id, created_by)``
    Checkpoint every demo query/board/flow (v1) and pin it in the project's
    dev AND prod environments so the demo works end-to-end under strict
    protected-env visibility.  Best-effort — returns ``{"skipped": reason}``
    instead of raising.
``remove_sample_bundle(org_id, project_id=None)``
    Delete every ``sample = true`` resource in the org (optionally scoped to a
    project).  Returns the per-resource delete counts.
"""

from __future__ import annotations

from typing import Any

from app.demo_bundle import (
    _s3_is_configured,
    export_demo_parquet_local,
    export_demo_to_s3,
    load_boards,
    load_queries,
    local_parquet_datastore_config,
    referenced_query_keys,
    resolve_placeholders,
    s3_datastore_config,
)
from app.demo_lakehouse import (
    editable_demo_datastore_config,
    editable_demo_supported,
    materialize_demo_duckdb,
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
    exports all four demo datasets to per-project S3 parquet files BEFORE creating
    the datastore row, so the connector is live-backed by real object storage from
    day one.  When S3 is absent, the same parquet files are written to the local
    ``seed_data/parquet/`` directory and the views read those — both modes flow
    through the identical parquet + ``read_parquet`` connector shape.

    Creates a "Sample" DuckDB datastore, every query the demo boards reference,
    and ALL demo dashboards from the shared fixtures — all tagged ``sample=true``.
    Designed to never break signup: returns ``{"skipped": reason}`` if the demo
    dataset can't be built.
    """
    repo = repo or get_repo()

    # ── 1. Build / resolve the datastore config ────────────────────────────────
    #
    # Three paths, decided purely from the server storage config (never user
    # input):
    #
    #   A. EDITABLE on-disk DuckDB (self-host / local lake root configured) —
    #      the demo data becomes the user's OWN, native, WRITABLE tables (the
    #      Supabase-style grid can edit cells).  Preferred whenever a local
    #      lakehouse root exists.
    #   B. S3 read-only views (managed cloud) — a native .duckdb file cannot be
    #      opened read-write over httpfs, so we fall back to the existing
    #      read-only parquet-view demo.  (FLAGGED: cloud editable-demo is a
    #      follow-up.)
    #   C. Local read-only views (no S3, no local lake root) — the existing
    #      offline parquet-view demo.
    ds_config: dict[str, Any]
    name = "Sample"

    if editable_demo_supported():
        # A. Editable on-disk DuckDB connector — the user's owned, writable copy.
        try:
            db_path = materialize_demo_duckdb(org_id, project_id)
            if db_path is None:  # storage vanished between check and use
                raise RuntimeError("no local lake root resolved")
            ds_config = editable_demo_datastore_config(db_path)
            name = "Demo Lakehouse"
        except Exception as exc:  # noqa: BLE001 — fall back to the view-based demo
            try:
                export_demo_parquet_local()
                ds_config = local_parquet_datastore_config()
            except Exception as exc2:  # noqa: BLE001
                return {
                    "skipped": (
                        f"editable demo failed: {exc}; local view fallback also "
                        f"failed: {exc2}"
                    )
                }
    elif project_id is not None and _s3_is_configured():
        # B. S3 read-only path: export per-project parquet, point the datastore at it.
        try:
            export_demo_to_s3(project_id)
            ds_config = s3_datastore_config(project_id)
        except Exception as exc:  # noqa: BLE001 — fall back gracefully
            # S3 export failed; fall back to local parquet files.
            try:
                export_demo_parquet_local()
                ds_config = local_parquet_datastore_config()
            except Exception as exc2:  # noqa: BLE001
                return {"skipped": f"demo export failed: {exc}; local fallback also failed: {exc2}"}
    else:
        # C. Local read-only view path (no S3 configured, no local lake root).
        try:
            export_demo_parquet_local()
            ds_config = local_parquet_datastore_config()
        except Exception as exc:  # noqa: BLE001 — never fail signup over the sample bundle
            return {"skipped": f"demo parquet unavailable: {exc}"}

    created: list[str] = []

    # ── 2. Sample datastore ────────────────────────────────────────────────────
    ds, ds_created = await _upsert(
        repo, "datastores", org_id, created_by, name,
        ds_config, SAMPLE_DS, project_id,
    )
    if ds_created:
        created.append("datastores")
    datastore_id = str(ds["id"])

    # ── 3. All demo boards + the queries they reference ────────────────────────
    boards = load_boards()
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

    # ── 4. Dashboards — resolve @placeholders to real query UUIDs ─────────────
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


async def checkpoint_and_promote_bundle(
    org_id: str,
    project_id: str,
    created_by: str,
    repo: Repo | None = None,
) -> dict[str, Any]:
    """Checkpoint the demo bundle (v1) and pin it in BOTH dev and prod.

    A fresh demo project must work end-to-end under strict protected-env
    visibility: every demo query/board/flow gets a v1 ``resource_versions``
    snapshot and ``resource_environments`` pointers in the project's ``dev``
    AND ``prod`` environments, exactly as if the user had checkpointed and
    promoted each resource by hand.

    Best-effort by design — returns ``{"skipped": reason}`` instead of
    raising, so demo seeding can never break signup or ``seed --demo``.

    Returns ``{"checkpointed": {"query": n, "board": n, "flow": n}}`` on
    success.
    """
    repo = repo or get_repo()
    try:
        from app.environments.store import get_env_store  # noqa: PLC0415

        env_store = get_env_store()
        envs = await env_store.ensure_project_envs(str(project_id))
        targets = [e for e in envs if e.get("key") in ("dev", "prod")]
        if not targets:
            return {"skipped": "project has no dev/prod environments"}
    except Exception as exc:  # noqa: BLE001 — env store unavailable
        return {"skipped": f"env store unavailable: {exc}"}

    counts = {"query": 0, "board": 0, "flow": 0}

    async def _pin(kind: str, resource_id: str, config: dict[str, Any]) -> None:
        version = await env_store.create_version(
            org_id=str(org_id),
            project_id=str(project_id),
            kind=kind,
            resource_id=str(resource_id),
            config=config,
            created_by=str(created_by),
            message="Demo seed",
        )
        for env in targets:
            await env_store.set_pointer(
                kind, str(resource_id), env["id"], version["id"],
                promoted_by=str(created_by),
            )
        counts[kind] += 1

    try:
        # Queries + boards: the bundle rows are tagged config.sample = true.
        for kind, table in (("query", "queries"), ("board", "boards")):
            for row in await repo.list(table, org_id, project_id):
                cfg = row.get("config") or {}
                if cfg.get("sample") is not True:
                    continue
                await _pin(kind, str(row["id"]), cfg)

        # Flows: snapshot the spec of every flow in the demo project.
        from app.flows.store import get_flow_store  # noqa: PLC0415

        flow_store = get_flow_store()
        for flow in await flow_store.list_flows(str(org_id)):
            if str(flow.get("project_id") or "") != str(project_id):
                continue
            await _pin("flow", str(flow["id"]), flow.get("spec") or {})
    except Exception as exc:  # noqa: BLE001 — never fail seeding on promote
        return {"skipped": f"checkpoint/promote failed: {exc}", "checkpointed": counts}

    return {"checkpointed": counts}


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
