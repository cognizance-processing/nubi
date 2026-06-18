"""Demo parquet serving — GET /api/v1/demo-parquet/*

Exposes the static demo-dataset parquet files to the browser so DuckDB-WASM
can call ``read_parquet('<url>')`` directly, enabling the D2 free-wedge:
demo dashboard VIEW queries are computed entirely in the browser — zero server
compute, zero /query calls.

Endpoints
---------
GET /api/v1/demo-parquet/_manifest
    Returns the table→URL map for all 17 demo tables.
    No auth required — the demo data is public / read-only.
    Response: { "tables": { "<table>": "<url>", ... } }

GET /api/v1/demo-parquet/_query-map
    Returns { "queries": { "<uuid>": "<sql>" } } for all persisted queries
    whose backing datastore is the seeded demo DuckDB connector
    (identified by config.sample=true + config.system=true or
    config.editable_parquet=true).
    Requires a valid auth token (same as GET /query/registry) so we can
    scope the result to the caller's org + project.

GET /api/v1/demo-parquet/{dataset}/{table}.parquet
    Serves the raw parquet file bytes.
    No auth required — demo data is read-only and public.
    Path is validated against DATASET_TABLES to prevent path traversal.
    Content-Type: application/octet-stream
    CORS: inherited from the global CORSMiddleware (allow_origins=settings.cors_origins).

Security
--------
- Path-traversal protection: ``dataset`` and ``table`` are validated against
  ``DATASET_TABLES`` (the closed allowlist of known datasets + tables).
- Files are served from the deterministic local parquet dir
  (``backend/seed_data/parquet/``); the path is assembled from the validated
  dataset+table keys, never from raw user input.
- No auth on the parquet-file route — demo data is deliberately public;
  it mirrors the virtual "__demo__" connector which any browser can query.

DuckDB-WASM browser usage
--------------------------
    // 1. Fetch the manifest once
    const manifest = await fetch('/api/v1/demo-parquet/_manifest').then(r => r.json())
    // manifest.tables: { sales: '/api/v1/demo-parquet/retail_sales/sales.parquet', ... }

    // 2. In DuckDB-WASM, register views:
    for (const [table, url] of Object.entries(manifest.tables)) {
        await conn.query(`CREATE OR REPLACE VIEW ${table} AS
                          SELECT * FROM read_parquet('${origin + url}')`)
    }
    // 3. Run SQL locally
    const result = await conn.query(sql)
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.auth.deps import verified_identity
from app.auth.scopes import has_scope
from app.auth.verify import VerifiedIdentity
from app.routes import api_router

logger = logging.getLogger("nubi.demo_parquet")

router = APIRouter(tags=["demo-parquet"])

# ---------------------------------------------------------------------------
# Import DATASET_TABLES from the canonical location (no heavy imports)
# ---------------------------------------------------------------------------

try:
    from seed_data.generators import DATASET_TABLES as _DATASET_TABLES
except Exception:  # noqa: BLE001 — graceful degradation if generators missing
    _DATASET_TABLES = {}


def _parquet_dir() -> Path:
    """Return the local parquet directory (backend/seed_data/parquet/)."""
    from app.demo_bundle import LOCAL_PARQUET_DIR  # noqa: PLC0415
    return Path(LOCAL_PARQUET_DIR)


def _ensure_parquet() -> None:
    """Idempotently generate parquet files if they don't exist yet."""
    try:
        from app.demo_bundle import export_demo_parquet_local  # noqa: PLC0415
        export_demo_parquet_local()
    except Exception as exc:  # noqa: BLE001
        logger.warning("demo_parquet: could not generate parquet files: %s", exc)


# ---------------------------------------------------------------------------
# GET /demo-parquet/_manifest
# ---------------------------------------------------------------------------


@router.get("/demo-parquet/_manifest")
async def demo_parquet_manifest(request: Request) -> dict:
    """Return the table→URL map for all 17 demo tables.

    No auth required.  The URL path shape is
    ``/api/v1/demo-parquet/{dataset}/{table}.parquet``.

    The frontend uses this to build ``read_parquet('<origin><url>')`` view
    statements in DuckDB-WASM so demo widget queries run entirely in the browser.
    """
    origin = str(request.base_url).rstrip("/")
    tables: dict[str, str] = {}
    for dataset, table_names in _DATASET_TABLES.items():
        for table in table_names:
            tables[table] = f"/api/v1/demo-parquet/{dataset}/{table}.parquet"
    return {"tables": tables, "origin": origin}


# ---------------------------------------------------------------------------
# GET /demo-parquet/_query-map
# ---------------------------------------------------------------------------


@router.get("/demo-parquet/_query-map")
async def demo_query_map(
    request: Request,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Return {queries: {<uuid>: <sql>}} for all demo-datastore queries.

    Auth: requires a valid token with at least one read:* scope (same gate as
    GET /query/registry).  The result is scoped to the caller's org + project.

    A query is "demo" when its ``datastore_id`` refers to a connector whose
    config has ``sample=true`` AND (``system=true`` OR ``editable_parquet=true``).
    Those are the seeded local-parquet DuckDB connectors created by
    ``app/sample.py`` and ``app/demo_lakehouse.py``.
    """
    from app.errors import AppError as _AppError  # noqa: PLC0415
    from app.repos.provider import get_repo  # noqa: PLC0415

    # ── Scope gate ─────────────────────────────────────────────────────────────
    _scopes = identity.scope
    _has_read = has_scope(_scopes, "read:query") or any(
        s.startswith("read:") for s in _scopes
    )
    if not _has_read:
        raise _AppError(
            "insufficient_scope",
            "Token does not carry the required scope: read:query",
            403,
        )

    repo = get_repo()

    # ── Resolve org + project ──────────────────────────────────────────────────
    org_id: str | None = None
    project_id: str | None = None

    try:
        if identity.kind == "embed" and identity.org:
            org_id = identity.org
        else:
            from app.routes._org import (  # noqa: PLC0415
                resolve_org_id as _resolve_org_id,
                resolve_project_filter as _resolve_project_filter,
            )
            org_id = await _resolve_org_id(identity.user_id, repo, request)
            project_id = await _resolve_project_filter(org_id, request)
    except Exception:  # noqa: BLE001 — graceful: return empty on resolution failure
        return {"queries": {}}

    if not org_id:
        return {"queries": {}}

    # ── Find demo datastore IDs ────────────────────────────────────────────────
    # A connector is "demo" when its config has sample=True AND
    # (system=True OR editable_parquet=True) AND one of the server-pinned
    # provenance markers is present: sample_id starts with "sample:" (set only
    # by app/sample.py) OR demo_local_dir is set (set only by app/demo_bundle.py).
    # The provenance check is a defence-in-depth guard: _RESERVED_CONFIG_KEYS in
    # update_connector already prevents users from forging sample/system flags, but
    # requiring a marker that only server provisioning code writes makes the combined
    # predicate unforgeable without a direct DB write.
    demo_ds_ids: set[str] = set()
    try:
        for row in await repo.list("datastores", org_id, project_id):
            cfg = row.get("config") or {}
            sample_id: str = cfg.get("sample_id") or ""
            has_provenance = sample_id.startswith("sample:") or bool(cfg.get("demo_local_dir"))
            if (
                cfg.get("sample") is True
                and isinstance(cfg.get("connector_type"), str)
                and cfg["connector_type"] == "duckdb"
                and (cfg.get("system") is True or cfg.get("editable_parquet") is True)
                and has_provenance
            ):
                demo_ds_ids.add(str(row["id"]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("demo_query_map: datastore lookup failed: %s", exc)
        return {"queries": {}}

    if not demo_ds_ids:
        return {"queries": {}}

    # ── Collect queries bound to those datastores ──────────────────────────────
    result: dict[str, str] = {}
    try:
        for row in await repo.list("queries", org_id, project_id):
            cfg = row.get("config") or {}
            ds_id = str(cfg.get("datastore_id") or "")
            sql = str(cfg.get("sql") or "").strip()
            if ds_id in demo_ds_ids and sql:
                result[str(row["id"])] = sql
    except Exception as exc:  # noqa: BLE001
        logger.warning("demo_query_map: query lookup failed: %s", exc)

    return {"queries": result}


# ---------------------------------------------------------------------------
# GET /demo-parquet/{dataset}/{table}.parquet
# ---------------------------------------------------------------------------


@router.get("/demo-parquet/{dataset}/{table_name}")
async def serve_demo_parquet(dataset: str, table_name: str) -> FileResponse:
    """Serve a demo parquet file to the browser.

    No auth required — demo data is public/read-only.

    Path validation: ``dataset`` must be a key of DATASET_TABLES and
    ``table_name`` must be ``<table>.parquet`` where ``<table>`` is in that
    dataset's tuple.  Any other combination → 404.  The file path is
    assembled from those validated keys, not from raw user input — no
    path-traversal is possible.
    """
    # Validate and strip the .parquet suffix
    if not table_name.endswith(".parquet"):
        raise HTTPException(status_code=404, detail="Not found")
    table = table_name[: -len(".parquet")]

    # Path-traversal guard: both keys must be in the closed allowlist
    if dataset not in _DATASET_TABLES:
        raise HTTPException(status_code=404, detail="Not found")
    if table not in _DATASET_TABLES[dataset]:
        raise HTTPException(status_code=404, detail="Not found")

    # Ensure parquet files exist (idempotent; skips generation if all present)
    _ensure_parquet()

    parquet_path = _parquet_dir() / dataset / f"{table}.parquet"
    if not parquet_path.exists():
        raise HTTPException(
            status_code=503, detail="Demo parquet unavailable; retry in a moment."
        )

    return FileResponse(
        path=str(parquet_path),
        media_type="application/octet-stream",
        filename=f"{table}.parquet",
        headers={
            # Tell the browser this is safe to share cross-origin so DuckDB-WASM
            # fetch() calls from any origin can read the bytes.
            "Access-Control-Allow-Origin": "*",
            # Cache for 1 hour — deterministic content, so long cache is fine.
            "Cache-Control": "public, max-age=3600, immutable",
        },
    )


# ---------------------------------------------------------------------------
# Register on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
