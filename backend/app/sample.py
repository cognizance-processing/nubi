"""Onboarding *sample bundle* seeder — a removable starter workspace.

Every new org/project gets a real, explorable bundle so the user lands on a
populated workspace instead of an empty one.  The bundle is created from the
SAME declarative demo fixtures the superuser demo uses (``seed_data/demo/*.json``
via ``app/demo_bundle.py``) — the FULL set: four demo datasets (retail sales,
SaaS metrics, web analytics, finance ops — 17 tables), all registered queries,
and all 10 dashboards — pointing at a single REAL ``duckdb`` datastore that
behaves exactly like a user-created connector (parquet + ``read_parquet`` views,
no demo special-casing in the query pipeline).

The parquet files are written once to the shared local directory
``backend/seed_data/parquet/<dataset>/<table>.parquet`` and the datastore views
read those — a READ-ONLY view demo (Nubi does not host a writable managed
lakehouse; this is local/offline data served straight from disk).

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
    export_demo_parquet_local,
    load_boards,
    load_flows,
    load_queries,
    local_parquet_datastore_config,
    referenced_query_keys,
    resolve_placeholders,
)
from app.repos.provider import Repo, get_repo

# ── Stable sample identifiers (stored in config.sample_id) ────────────────────
SAMPLE_DS = "sample:datastore:duckdb"

# Resource tables the bundle touches (order matters for remove: boards →
# queries → datastores, so nothing dangling is left if interrupted).
_SAMPLE_TABLES = ("boards", "queries", "datastores")


# ── Idempotency helpers ─────────────────────────────────────────────────────────

async def _find_sample(
    repo: Repo, table: str, org_id: str, sample_id: str, project_id: str | None
) -> dict[str, Any] | None:
    """Return the existing bundle row for *sample_id* in the TARGET project, or ``None``.

    Idempotency is scoped per (org, project): every project owns its own copy of
    the demo bundle, so seeding a NEW project creates a fresh bundle there —
    including its own per-project demo-lakehouse connector — instead of silently
    reusing rows that live in another project.  ``project_id=None`` keeps the
    legacy org-wide match (project-less callers / test doubles).
    """
    for row in await repo.list(table, org_id, project_id):
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
    existing = await _find_sample(repo, table, org_id, sample_id, project_id)
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

    The demo parquet files are written to the local ``seed_data/parquet/``
    directory and the datastore's ``view_sql`` reads those via ``read_parquet``
    — a real ``duckdb`` connector, no demo special-casing in the query pipeline.

    Creates a "Sample" DuckDB datastore, every query the demo boards reference,
    and ALL demo dashboards from the shared fixtures — all tagged ``sample=true``.
    Designed to never break signup: returns ``{"skipped": reason}`` if the demo
    dataset can't be built.
    """
    repo = repo or get_repo()

    # ── 1. Build / resolve the datastore config ────────────────────────────────
    ds_config: dict[str, Any]
    name = "Sample"

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

    # ── 3. All demo queries (board-referenced + metric-backed) ────────────────
    boards = load_boards()
    queries = load_queries()
    needed = referenced_query_keys(boards)

    # Also seed ALL metric-backed queries so they are available via /metrics
    # even if no board references them directly.
    all_needed = set(needed) | {k for k, v in queries.items() if v.get("metric")}

    idmap: dict[str, str] = {}
    for key in sorted(all_needed):
        q = queries.get(key)
        if q is None:
            continue
        # Build the query config: include the metric block when present so
        # load_metrics_from_queries can pick it up after startup.
        q_config: dict[str, Any] = {
            "sql": q["sql"],
            "datastore_id": datastore_id,
            "params": q.get("params", []),
        }
        if q.get("metric"):
            q_config["metric"] = q["metric"]
        row, q_created = await _upsert(
            repo, "queries", org_id, created_by, q["name"],
            q_config,
            f"sample:query:{key}", project_id,
        )
        idmap[f"@{key}"] = str(row["id"])
        if q_created:
            created.append("queries")

    # Keep a stable @sample_datastore placeholder for flows that reference it.
    idmap["@sample_datastore"] = datastore_id

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

    # ── 5. Flows ───────────────────────────────────────────────────────────────
    flow_ids: list[str] = []
    try:
        from app.flows.store import get_flow_store  # noqa: PLC0415

        flow_store = get_flow_store()
        flows_fixture = load_flows()

        # Idempotency: check for existing flows by seed_id stored in their spec.
        existing_flows = await flow_store.list_flows(str(org_id), str(project_id))
        existing_seed_ids: set[str] = {
            f.get("spec", {}).get("_seed_id", "")
            for f in existing_flows
        }

        for fl in flows_fixture:
            seed_id = fl["seed_id"]
            if seed_id in existing_seed_ids:
                continue
            # Resolve @placeholders inside the flow spec (e.g. datastore_id).
            spec = resolve_placeholders(fl["spec"], idmap)
            # Tag the spec with the seed_id for idempotency on future runs.
            spec["_seed_id"] = seed_id
            new_flow = await flow_store.create_flow(
                org_id=str(org_id),
                created_by=str(created_by),
                name=fl["name"],
                spec=spec,
                enabled=fl.get("enabled", True),
                schedule=fl.get("schedule"),
                project_id=str(project_id) if project_id is not None else None,
            )
            flow_ids.append(str(new_flow["id"]))
            created.append("flows")
    except Exception:  # noqa: BLE001 — never fail seeding over flows
        pass

    # ── 6. Watch — monitor retail NSV metric (threshold alert demo) ───────────
    watch_ids: list[str] = []
    try:
        import json as _json
        from app.db import execute as _execute, fetchrow as _fetchrow  # noqa: PLC0415
        from app.repos.projects import get_default_project  # noqa: PLC0415

        watch_slug = "sample_watch_retail_nsv"
        watch_name = "Retail NSV Alert (demo)"
        metric_slug = "retail_nsv"

        existing_watch = await _fetchrow(
            "SELECT id FROM watches WHERE org_id = $1::uuid AND slug = $2",
            org_id, watch_slug,
        )
        if existing_watch is None:
            import uuid as _uuid  # noqa: PLC0415

            watch_id = str(_uuid.uuid4())
            watch_config = {
                "threshold": {"op": ">", "value": 0},
                "time_grain": "month",
                "enabled": True,
                "description": "Demo watch: fires when monthly retail NSV is above 0 (always active — illustrates the watch feature).",
                "sample": True,
            }
            _watch_proj = str(project_id) if project_id is not None else None
            if _watch_proj is None:
                try:
                    _proj = await get_default_project(str(org_id))
                    _watch_proj = str(_proj["id"]) if _proj else None
                except Exception:  # noqa: BLE001
                    pass
            if _watch_proj:
                await _execute(
                    """
                    INSERT INTO watches
                        (id, org_id, project_id, created_by, slug, name, metric_id, config)
                    VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7, $8::jsonb)
                    ON CONFLICT (org_id, slug) DO NOTHING
                    """,
                    watch_id, org_id, _watch_proj, created_by,
                    watch_slug, watch_name, metric_slug,
                    _json.dumps(watch_config),
                )
                watch_ids.append(watch_id)
                created.append("watches")
    except Exception:  # noqa: BLE001 — best-effort; never fail seeding over watches
        pass

    # ── 7. Job — schedule a demo report job ───────────────────────────────────
    job_ids: list[str] = []
    try:
        from app.jobs.store import get_job_store  # noqa: PLC0415

        job_store = get_job_store()
        existing_jobs = await job_store.list_jobs(str(org_id))
        sample_job_names = {j["name"] for j in existing_jobs if j.get("name", "").startswith("Demo")}
        if "Demo — Retail Monthly Report" not in sample_job_names:
            new_job = await job_store.create_job(
                org_id=str(org_id),
                created_by=str(created_by),
                name="Demo — Retail Monthly Report",
                kind="report",
                target=board_ids[0] if board_ids else "",
                schedule="0 8 1 * *",
                enabled=True,
                project_id=str(project_id) if project_id is not None else None,
            )
            job_ids.append(str(new_job["id"]))
            created.append("jobs")
    except Exception:  # noqa: BLE001 — best-effort; never fail seeding over jobs
        pass

    return {
        "datastore_id": datastore_id,
        "board_ids": board_ids,
        "flow_ids": flow_ids,
        "watch_ids": watch_ids,
        "job_ids": job_ids,
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
