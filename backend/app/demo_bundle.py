"""Shared demo-content loader — the single source of truth for demo dashboards.

The demo workspace (queries + dashboards) is defined declaratively as JSON files
under ``backend/seed_data/demo/`` so the content lives in versioned files rather
than hardcoded Python:

  - ``queries.json`` : ``{logical_key: {name, sql, params}}``
  - ``boards.json``  : ``[{seed_id, name, starter, spec}, ...]`` where every widget
    references a query by the ``"@<logical_key>"`` placeholder (resolved to the
    real query UUID at seed time).

Two consumers share this loader:
  - ``seed.py --demo`` (superuser): materialises ALL boards (comprehensive demo).
  - ``app/sample.py`` (per project): materialises only the ``starter`` boards as a
    small, editable, removable onboarding bundle.

Both reuse the bundled read-only DuckDB star schema (``seed_data/sample.duckdb``,
built by ``seed_data_duckdb.build_duckdb_file``) as the single datasource.

When S3 is configured (``NUBI_BUCKET_URI`` / ``S3_ENDPOINT_URL`` + ``S3_ACCESS_KEY``
env vars), the demo data is exported per-project to
``s3://<bucket>/projects/<project_id>/demo/<table>.parquet`` and the datastore
config uses DuckDB views over those S3 Parquet files.  This flows through the
normal connector → /query → /data-browser pipeline.  When S3 is not configured,
the datastore falls back to the bundled read-only local ``.duckdb`` file so offline
development still works.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_DEMO_DIR = Path(__file__).resolve().parent.parent / "seed_data" / "demo"

# The 6 tables in the bundled star schema (exact names; order is import order).
DEMO_TABLES: tuple[str, ...] = (
    "dim_regions",
    "dim_products",
    "dim_customers",
    "sales",
    "budget",
    "targets",
)


# ---------------------------------------------------------------------------
# Demo fixture loaders
# ---------------------------------------------------------------------------


def load_queries() -> dict[str, dict[str, Any]]:
    """Return ``{logical_key: {name, sql, params}}`` from queries.json."""
    with open(_DEMO_DIR / "queries.json") as f:
        return json.load(f)


def load_boards(starter_only: bool = False) -> list[dict[str, Any]]:
    """Return board fixtures ``[{seed_id, name, starter, spec}, ...]``."""
    with open(_DEMO_DIR / "boards.json") as f:
        boards = json.load(f)
    return [b for b in boards if b.get("starter")] if starter_only else boards


def referenced_query_keys(boards: list[dict[str, Any]]) -> list[str]:
    """Logical query keys referenced by the given boards (via ``@key`` placeholders)."""
    keys: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if node.startswith("@"):
                keys.add(node[1:])
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for b in boards:
        walk(b["spec"])
    return sorted(keys)


def resolve_placeholders(spec: Any, idmap: dict[str, str]) -> Any:
    """Deep-copy *spec*, replacing every ``"@key"`` string with ``idmap["@key"]``.

    Unknown placeholders resolve to ``""`` (an empty query_id renders as a
    "needs configuration" widget rather than crashing).
    """
    if isinstance(spec, str):
        return idmap.get(spec, "") if spec.startswith("@") else spec
    if isinstance(spec, dict):
        return {k: resolve_placeholders(v, idmap) for k, v in spec.items()}
    if isinstance(spec, list):
        return [resolve_placeholders(v, idmap) for v in spec]
    return spec


def sample_db_path() -> str:
    """Absolute path to the bundled demo DuckDB file, building it if missing."""
    from seed_data_duckdb import SAMPLE_DB_PATH, build_duckdb_file  # noqa: PLC0415

    path = os.path.abspath(SAMPLE_DB_PATH)
    if not os.path.exists(path):
        build_duckdb_file(path)
    return path


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------


def _s3_is_configured() -> bool:
    """Return ``True`` when env vars supply enough S3/MinIO config to write parquet.

    Checks for an access key via the ``S3_ACCESS_KEY`` / ``AWS_ACCESS_KEY_ID``
    env-var families — necessary for any authenticated write to MinIO or AWS S3.
    ``NUBI_BUCKET_URI`` alone (without creds) is not sufficient when the bucket
    requires authentication.
    """
    access_key = (
        os.getenv("S3_ACCESS_KEY")
        or os.getenv("AWS_ACCESS_KEY_ID")
        or ""
    )
    return bool(access_key)


def _s3_bucket() -> str:
    """Return the S3 bucket name to use for demo parquet storage.

    Resolution order:
    1. ``NUBI_BUCKET_URI`` (e.g. ``s3://nubi``) — parse the bucket segment.
    2. ``NUBI_BUCKET_NAME`` — explicit bucket name.
    3. ``"nubi"`` — hard-coded default (matches local MinIO convention).
    """
    bucket_uri = os.getenv("NUBI_BUCKET_URI", "")
    if bucket_uri.startswith("s3://"):
        # e.g. "s3://nubi" → "nubi"; "s3://nubi/some/prefix" → "nubi"
        after_scheme = bucket_uri[len("s3://"):]
        return after_scheme.split("/")[0] or "nubi"

    return os.getenv("NUBI_BUCKET_NAME", "nubi")


def _s3_endpoint() -> str:
    """Return the S3 endpoint URL (empty string for AWS S3)."""
    return (
        os.getenv("S3_ENDPOINT_URL")
        or os.getenv("AWS_ENDPOINT_URL")
        or ""
    )


def _s3_creds_for_httpfs() -> dict[str, str]:
    """Build a DuckDB-httpfs credentials dict from env vars.

    Returns a dict with keys accepted by ``setup_s3_httpfs``:
    ``s3_key_id``, ``s3_secret``, ``s3_endpoint``, ``s3_region``,
    ``s3_url_style``.  Missing values are omitted so that
    ``setup_s3_httpfs`` can fall back to its own env-var lookup.
    """
    key_id = os.getenv("S3_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID") or ""
    secret = os.getenv("S3_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY") or ""
    endpoint = _s3_endpoint()
    region = (
        os.getenv("S3_REGION")
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    url_style = os.getenv("S3_URL_STYLE") or ("path" if endpoint else "vhost")

    result: dict[str, str] = {}
    if key_id:
        result["s3_key_id"] = key_id
    if secret:
        result["s3_secret"] = secret
    if endpoint:
        result["s3_endpoint"] = endpoint
    result["s3_region"] = region
    result["s3_url_style"] = url_style
    return result


# ---------------------------------------------------------------------------
# S3 export
# ---------------------------------------------------------------------------


def _parquet_s3_uri(project_id: str, table: str, bucket: str) -> str:
    """Return the canonical S3 URI for a demo table parquet file."""
    return f"s3://{bucket}/projects/{project_id}/demo/{table}.parquet"


def export_demo_to_s3(
    project_id: str,
    bucket: str | None = None,
    force: bool = False,
) -> dict[str, str]:
    """Export the bundled demo star schema to per-project S3 parquet files.

    Uses the ``PROVEN MECHANISM``: DuckDB ``COPY (SELECT * FROM <table>) TO
    's3://...'`` with httpfs + S3 SECRET configured by ``setup_s3_httpfs``.

    Idempotent — skips tables whose parquet file already exists in S3 unless
    ``force=True`` is given.  The skip check is implemented as a try/read; if the
    parquet is readable the table is considered present.

    Parameters
    ----------
    project_id:
        The project UUID whose per-project demo files are being written.
    bucket:
        S3 bucket name (default: auto-detected from env via ``_s3_bucket()``).
    force:
        When ``True``, re-exports all tables even if already present in S3.

    Returns
    -------
    dict[str, str]
        ``{table: s3_uri}`` for every table written (skipped tables are absent).

    Raises
    ------
    RuntimeError
        If the sample DuckDB file cannot be built or any DuckDB operation fails.
    """
    import duckdb  # noqa: PLC0415
    from app.connectors.duckdb_conn import setup_s3_httpfs  # noqa: PLC0415

    bucket = bucket or _s3_bucket()
    db_path = sample_db_path()

    # Open the source DuckDB file read-only.
    src_conn = duckdb.connect(database=db_path, read_only=True)

    # Open a separate in-memory connection for the S3 write (httpfs writes need
    # a writable connection; the read-only file conn cannot attach httpfs).
    write_conn = duckdb.connect(database=":memory:")
    setup_s3_httpfs(write_conn)

    written: dict[str, str] = {}
    try:
        for table in DEMO_TABLES:
            uri = _parquet_s3_uri(project_id, table, bucket)

            if not force:
                # Idempotency check — try to read the first row.
                try:
                    write_conn.execute(
                        f"SELECT * FROM read_parquet('{uri}') LIMIT 1"
                    ).fetchone()
                    continue  # already exists; skip
                except Exception:
                    pass  # not present or unreadable — proceed with write

            # Read all rows from the source (small tables — fits in memory).
            arrow_tbl = src_conn.execute(f"SELECT * FROM {table}").arrow()
            if hasattr(arrow_tbl, "read_all"):
                arrow_tbl = arrow_tbl.read_all()

            # Register the Arrow table on the write connection and COPY to S3.
            write_conn.register("_demo_src", arrow_tbl)
            try:
                write_conn.execute(
                    f"COPY (SELECT * FROM _demo_src) TO '{uri}' (FORMAT parquet)"
                )
                written[table] = uri
            finally:
                try:
                    write_conn.unregister("_demo_src")
                except Exception:
                    pass
    finally:
        src_conn.close()
        write_conn.close()

    return written


# ---------------------------------------------------------------------------
# Datastore config factories
# ---------------------------------------------------------------------------


def s3_datastore_config(
    project_id: str,
    bucket: str | None = None,
) -> dict[str, Any]:
    """Return a ``duckdb`` datastore config that reads the 6 demo tables from S3.

    The config uses DuckDB ``CREATE VIEW`` statements over ``read_parquet(s3://...)``
    so the normal connector → /query → /data-browser pipeline picks up all six
    tables.  The ``data_browser._build_duckdb_connector`` helper detects
    ``s3://`` in ``view_sql`` and calls ``setup_s3_httpfs`` automatically before
    executing the view SQL; ``query.py`` executes the same ``view_sql`` on an
    in-memory connection and also auto-configures httpfs when ``s3://`` refs are
    present via ``_cfg_references_s3`` in the data-browser path.

    The S3 credentials are NOT stored in the config dict — they are resolved
    from environment variables at query time by ``setup_s3_httpfs``, which reads
    ``S3_ACCESS_KEY`` / ``AWS_ACCESS_KEY_ID`` / ``S3_ENDPOINT_URL`` etc.  This
    keeps the datastore row free of plaintext secrets.

    Parameters
    ----------
    project_id:
        The project UUID whose per-project demo parquet files are being exposed.
    bucket:
        S3 bucket name (default: auto-detected via ``_s3_bucket()``).

    Returns
    -------
    dict[str, Any]
        A connector config dict with:
        - ``connector_type``: ``"duckdb"``
        - ``database``: ``":memory:"``
        - ``view_sql``: multi-statement SQL creating one view per demo table
        - ``description``: human-readable label
        - ``demo_project_id``: stored for reference / future cache-bust
        - ``demo_s3_bucket``: stored for introspection
    """
    bucket = bucket or _s3_bucket()

    view_statements: list[str] = []
    for table in DEMO_TABLES:
        uri = _parquet_s3_uri(project_id, table, bucket)
        view_statements.append(
            f"CREATE OR REPLACE VIEW {table} AS SELECT * FROM read_parquet('{uri}')"
        )

    view_sql = ";\n".join(view_statements)

    return {
        "connector_type": "duckdb",
        "database": ":memory:",
        "view_sql": view_sql,
        "description": (
            "Per-project demo dataset — FMCG sales star schema backed by S3 parquet files."
        ),
        "demo_project_id": project_id,
        "demo_s3_bucket": bucket,
        "sample": True,
        # Internal: backs the demo dashboards/queries by id. The connectors list
        # surfaces the branded virtual "Demo data" connector instead of this raw
        # row, so flag it system so list_connectors hides it (no duplicate card).
        "system": True,
    }


def datastore_config(db_path: str) -> dict[str, Any]:
    """Connector config for the read-only DuckDB demo datasource (local-file path).

    This is the offline / fallback variant used when S3 is not configured.
    ``app/sample.py`` calls ``s3_datastore_config()`` when S3 is available and
    falls back to this function otherwise.
    """
    return {
        "connector_type": "duckdb",
        "database": db_path,
        "read_only": True,
        "description": "Bundled demo dataset (read-only FMCG sales star schema).",
        # Internal: see s3_datastore_config — hidden from the connectors list so
        # the branded virtual "Demo data" connector is the only demo card.
        "system": True,
    }
