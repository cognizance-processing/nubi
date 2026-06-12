"""Editable demo lakehouse — per-PROJECT parquet files in managed storage.

Product directive: the demo data (and the editable-data model generally) must be
**per-PROJECT isolated FILES in the managed lakehouse OBJECT STORAGE** (S3 in
cloud, the local-file storage backend in dev) — NOT a server-local ``.duckdb``
file.  Cells must still be EDITABLE — implemented via *rewrite-on-edit* in
``routes/data_browser.py`` (load the parquet, apply the parameterised mutation,
COPY the whole table back over the file).

What this module provisions
---------------------------
For an ``(org, project)`` it materialises the 17 demo tables as one **parquet
file per table** under a SERVER-PINNED, per-project prefix::

    <lake-root>/orgs/<org>/projects/<project>/demo/<dataset>/<table>.parquet

where ``<lake-root>`` is the managed-lakehouse central storage — an ``s3://``
bucket in the cloud, or the local-file backend's root dir in dev.  BOTH go
through the same per-project prefix (``managed.project_demo_prefix``) and the
same storage abstraction; dev is still FILES, just per-project under the lake
root — never an ad-hoc local ``.duckdb``.

Row identity (so cells are editable)
------------------------------------
Each table's parquet gets a synthetic ``_row_id`` column — a stable
``row_number()`` 1..N identity — written at provision time.  Rewrite-on-edit
targets / identifies rows by ``_row_id`` (bound params).  The demo queries
reference explicit columns (never ``SELECT *`` on a literal that breaks), so the
extra column is inert for the dashboards.

The connector
-------------
A real, user-owned ``duckdb`` connector named "Demo Lakehouse" with
``s3_views`` (S3 path) or ``view_sql`` (local-file path) of
``CREATE VIEW <t> AS SELECT * FROM read_parquet('<uri>')`` over the per-project
parquet URIs — exactly like a user-created DuckDB-over-parquet connector, with
no demo special-casing in the query pipeline.  It is NOT marked
``managed``/``system``/hidden — the user OWNS it.

Security
--------
The org/project prefix is server-pinned from trusted ids (never user input), so
a user can never point the connector / rewrite at another project's files.  The
parquet URIs stored in the connector config are likewise server-derived.
"""

from __future__ import annotations

import io
import os
from typing import Any

from seed_data.generators import DATASET_TABLES

# Synthetic row-identity column added to every demo table's parquet so the
# data-browser write path treats it as an editable (rewrite-on-edit) table.
ROW_ID_COL = "_row_id"


# ---------------------------------------------------------------------------
# Storage resolution — the managed lakehouse central storage (s3 OR local file)
# ---------------------------------------------------------------------------


def _resolve_storage():
    """Return ``(central, client)`` for the managed lakehouse, or ``(None, None)``.

    ``central`` is the resolved :class:`CentralStorage` (``scheme == "s3"`` or
    ``"file"``); ``client`` is the matching storage client.  ``(None, None)``
    when no central storage is configured — the caller then falls back to the
    legacy read-only parquet-view demo.
    """
    try:
        from app.lakehouse.managed import resolve_central_storage  # noqa: PLC0415

        central = resolve_central_storage()
    except Exception:  # noqa: BLE001
        central = None
    if central is None:
        return None, None

    if central.scheme == "file":
        from app.storage.local import LocalStorageClient  # noqa: PLC0415

        return central, LocalStorageClient(root=central.bucket)

    from app.storage.base import get_storage_client  # noqa: PLC0415

    return central, get_storage_client(central.base_uri(), central.creds or None)


def editable_demo_supported() -> bool:
    """True when the per-project parquet demo can be provisioned here.

    Equivalent to "a central lakehouse storage (S3 or local file) is
    configured".  When False, seeding falls back to the legacy read-only
    parquet-view demo.  Derived purely from server storage config, never user
    input.
    """
    central, _ = _resolve_storage()
    return central is not None


# ---------------------------------------------------------------------------
# URI resolution — per-project, server-pinned
# ---------------------------------------------------------------------------


def _read_parquet_uri(central, key: str) -> str:
    """The URI a DuckDB ``read_parquet(...)`` references for storage *key*.

    - S3: the canonical ``s3://<bucket>/<key>`` URI.
    - local file: the ABSOLUTE filesystem path (DuckDB reads/writes a bare
      path, not a ``file://`` URI), pinned under the lake root.
    """
    if central.scheme == "file":
        return os.path.join(central.bucket, key)
    return f"s3://{central.bucket}/{key}"


def demo_table_uris(org_id: str, project_id: str | None) -> dict[str, str] | None:
    """Return ``{table: read_parquet_uri}`` for the project's demo, or ``None``.

    URIs are server-pinned under ``orgs/<org>/projects/<project>/demo/`` — never
    user input.  ``None`` when no central storage is configured.
    """
    central, _ = _resolve_storage()
    if central is None:
        return None
    from app.lakehouse.managed import project_demo_prefix  # noqa: PLC0415

    prefix = project_demo_prefix(org_id, project_id)
    uris: dict[str, str] = {}
    for dataset, tables in DATASET_TABLES.items():
        for table in tables:
            key = f"{prefix}{dataset}/{table}.parquet"
            uris[table] = _read_parquet_uri(central, key)
    return uris


# ---------------------------------------------------------------------------
# Provisioning — per-project parquet files with a synthetic _row_id
# ---------------------------------------------------------------------------


def provision_demo_parquet(
    org_id: str,
    project_id: str | None,
    force: bool = False,
) -> dict[str, str] | None:
    """Provision the 17 demo tables as per-project parquet files; return URIs.

    Writes one parquet per table (each with a synthetic ``_row_id`` 1..N) under
    the project's server-pinned lakehouse prefix, via the storage abstraction
    (S3 in cloud, local-file backend in dev).  Idempotent: skips a table whose
    parquet already exists unless ``force``.  Reuses ``seed_data.generators`` —
    no duplicated data generation.

    Returns ``{table: read_parquet_uri}`` for ALL tables (existing + written),
    or ``None`` when no central storage is configured (caller falls back to the
    read-only view demo).
    """
    central, client = _resolve_storage()
    if central is None:
        return None

    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.compute as pc  # noqa: PLC0415
    import pyarrow.parquet as pq  # noqa: PLC0415

    from app.lakehouse.managed import project_demo_prefix  # noqa: PLC0415
    from seed_data.generators import build_dataset  # noqa: PLC0415

    prefix = project_demo_prefix(org_id, project_id)
    uris: dict[str, str] = {}
    for dataset, tables in DATASET_TABLES.items():
        built = None  # generated lazily, only when a table needs writing
        for table in tables:
            key = f"{prefix}{dataset}/{table}.parquet"
            uris[table] = _read_parquet_uri(central, key)
            if not force and client.exists(key):
                continue
            if built is None:
                built = build_dataset(dataset)
            src = built[table]
            # Prepend a stable 1..N _row_id identity column (rewrite-on-edit key).
            row_ids = pa.array(range(1, src.num_rows + 1), type=pa.int64())
            with_id = src.add_column(0, ROW_ID_COL, row_ids)
            buf = io.BytesIO()
            pq.write_table(with_id, buf)
            client.upload_bytes(buf.getvalue(), key)
    return uris


def _has_all_parquet(org_id: str, project_id: str | None) -> bool:
    """True when every demo table's parquet already exists for the project."""
    central, client = _resolve_storage()
    if central is None:
        return False
    from app.lakehouse.managed import project_demo_prefix  # noqa: PLC0415

    prefix = project_demo_prefix(org_id, project_id)
    for dataset, tables in DATASET_TABLES.items():
        for table in tables:
            if not client.exists(f"{prefix}{dataset}/{table}.parquet"):
                return False
    return True


# ---------------------------------------------------------------------------
# Datastore config factory
# ---------------------------------------------------------------------------


def editable_demo_datastore_config(uris: dict[str, str]) -> dict[str, Any]:
    """``duckdb`` connector config for the per-project parquet demo.

    Uses ``s3_views`` (the canonical multi-table-S3 shape the data browser /
    query pipeline already understands) so every table is a
    ``read_parquet('<uri>')`` view.  ``s3_views`` also works for local paths —
    ``_build_view_sql_from_s3_views`` just emits ``read_parquet('<path>')`` and
    DuckDB reads a bare path fine.

    Deliberately NOT marked ``managed``/``system``/hidden — the user OWNS this
    connector and sees it in the connectors list like any other.  ``sample`` is
    set by the caller (``_upsert``).
    """
    return {
        "connector_type": "duckdb",
        "database": ":memory:",
        "s3_views": dict(uris),
        "description": (
            "Demo datasets (retail sales, SaaS metrics, web analytics, finance ops) "
            "as your own editable per-project lakehouse files."
        ),
        # Marker so the rewrite-on-edit path can recognise this connector and so
        # the read path treats the parquet-backed views as writable.
        "editable_parquet": True,
        "sample": True,
    }
