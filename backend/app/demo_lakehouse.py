"""Editable demo lakehouse — the demo data as a REAL, OWNED, writable connector.

Today the demo bundle ships the 17 demo tables as DuckDB **views** over
``read_parquet(...)`` (``app/demo_bundle.py``).  Views are READ-ONLY: the data
browser only treats a table as writable when it is a native ``BASE TABLE`` in an
on-disk DuckDB file opened read-write (see ``routes/data_browser.py``).

This module materialises the same demo datasets as **native BASE TABLEs in an
on-disk ``.duckdb`` file** in the org's local lakehouse storage area, so the
user gets their OWN, EDITABLE copy of the demo data (the Supabase-style grid can
edit cells).  The file is a normal user-owned connector — not marked
``managed``/``system``/hidden.

Row identity (so cells are editable)
------------------------------------
``CREATE TABLE … AS SELECT …`` carries no constraints, and the parquet sources
have no primary keys, so the materialised tables would have NO row identity —
``_writable_meta_duckdb`` would still reject them.  We therefore add a synthetic
``_row_id BIGINT PRIMARY KEY`` to every table (a monotonic ``row_number()``).
The demo queries reference explicit columns (never ``SELECT *``), so the extra
column is inert for the dashboards while giving the grid a stable identity to
PATCH/DELETE against.

Local vs cloud (judgement call — FLAGGED)
-----------------------------------------
A native ``.duckdb`` file CANNOT be opened read-write over ``httpfs``/S3, so the
editable-demo path is **self-host / local-storage only**.  When central storage
is S3 (managed cloud), there is no editable on-disk file we can hand the connector
resolver, so the caller falls back to the existing read-only parquet-view demo
(``demo_bundle``).  ``editable_demo_supported()`` reports which path applies; it
is derived purely from the server storage config, never user input.
"""

from __future__ import annotations

import os
from typing import Any

from seed_data.generators import ALL_TABLES, DATASET_TABLES

# Synthetic primary-key column added to every materialised demo table so the
# data-browser write path treats it as an editable native table.
ROW_ID_COL = "_row_id"


# ---------------------------------------------------------------------------
# Storage resolution — where the editable .duckdb file lives
# ---------------------------------------------------------------------------


def _local_lake_root() -> str | None:
    """Return the absolute local lakehouse root, or ``None`` for the cloud path.

    The editable demo needs a LOCAL directory to host an on-disk ``.duckdb`` file
    opened read-write.  Resolution (server-derived, never user input):

      1. ``resolve_central_storage()`` with ``scheme == "file"`` — the managed
         lakehouse local root (``NUBI_MANAGED_LAKE_DIR`` / ``NUBI_LOCAL_LAKE_DIR``).
         This is the canonical self-host storage area.
      2. ``NUBI_DEMO_LAKE_DIR`` — an explicit override for the demo file root.
      3. ``None`` when central storage is S3 (managed cloud) and no local override
         is set → caller must fall back to the read-only parquet-view demo, since
         a native ``.duckdb`` cannot be opened read-write over httpfs.
    """
    # 1. Managed-lake local root (the self-host storage area).
    try:
        from app.lakehouse.managed import resolve_central_storage  # noqa: PLC0415

        central = resolve_central_storage()
        if central is not None and central.scheme == "file":
            return central.bucket  # absolute root dir for the file backend
    except Exception:  # noqa: BLE001 — degrade to the explicit override / None
        pass

    # 2. Explicit demo-lake override.
    override = os.getenv("NUBI_DEMO_LAKE_DIR")
    if override:
        return os.path.abspath(override)

    # 3. No local storage → cloud/read-only path applies.
    return None


def editable_demo_supported() -> bool:
    """True when the editable on-disk demo connector can be provisioned here.

    Equivalent to "a local lakehouse storage root is configured".  When False,
    seeding falls back to the existing read-only parquet-view demo (S3/cloud).
    """
    return _local_lake_root() is not None


def demo_duckdb_path(org_id: str, project_id: str | None) -> str | None:
    """Absolute path to the org/project's editable demo ``.duckdb`` file.

    Server-pinned and tenant-scoped: ``<lake-root>/orgs/<org>/demo/<scope>.duckdb``
    where ``<scope>`` is the project id (or ``org`` when project-less).  Returns
    ``None`` when no local lake root is configured (cloud path).
    """
    root = _local_lake_root()
    if root is None:
        return None
    scope = str(project_id) if project_id else "org"
    return os.path.join(root, "orgs", str(org_id), "demo", f"{scope}.duckdb")


# ---------------------------------------------------------------------------
# Materialisation — native BASE TABLEs with a synthetic PK
# ---------------------------------------------------------------------------


def _has_all_tables(path: str) -> bool:
    """True if *path* exists and already holds all 17 demo BASE TABLEs."""
    if not os.path.exists(path):
        return False
    try:
        import duckdb  # noqa: PLC0415

        con = duckdb.connect(database=path, read_only=True)
        try:
            have = {
                r[0]
                for r in con.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_type = 'BASE TABLE'"
                ).fetchall()
            }
        finally:
            con.close()
        return set(ALL_TABLES) <= have
    except Exception:  # noqa: BLE001 — unreadable/corrupt → rebuild
        return False


def materialize_demo_duckdb(
    org_id: str,
    project_id: str | None,
    force: bool = False,
) -> str | None:
    """Materialise the 17 demo datasets as native BASE TABLEs in an on-disk file.

    Each table gets a synthetic ``_row_id BIGINT PRIMARY KEY`` so it is editable
    via the data-browser write path.  Idempotent: skips entirely when the file
    already holds every demo table (unless ``force``).  Reuses
    ``seed_data.generators`` — no duplicated data generation.

    Returns the absolute ``.duckdb`` path, or ``None`` when no local lake root is
    configured (cloud path — caller falls back to the read-only view demo).
    """
    path = demo_duckdb_path(org_id, project_id)
    if path is None:
        return None
    if not force and _has_all_tables(path):
        return path

    from seed_data.generators import build_dataset  # noqa: PLC0415

    import duckdb  # noqa: PLC0415

    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = duckdb.connect(database=path, read_only=False)
    try:
        for dataset, tables in DATASET_TABLES.items():
            built = build_dataset(dataset)
            for table in tables:
                con.register("_demo_src", built[table])
                try:
                    # Build a native table with a synthetic monotonic PK so the
                    # grid has a stable row identity (the parquet sources carry
                    # no PK and CTAS carries no constraints).  row_number() over
                    # the source order gives a deterministic 1..N identity.
                    con.execute(f"DROP TABLE IF EXISTS {table}")
                    con.execute(
                        f"CREATE TABLE {table} AS "
                        f"SELECT CAST(row_number() OVER () AS BIGINT) AS {ROW_ID_COL}, * "
                        f"FROM _demo_src"
                    )
                    con.execute(
                        f"ALTER TABLE {table} ADD PRIMARY KEY ({ROW_ID_COL})"
                    )
                finally:
                    con.unregister("_demo_src")
        con.commit()
    finally:
        con.close()
    return path


# ---------------------------------------------------------------------------
# Datastore config factory
# ---------------------------------------------------------------------------


def editable_demo_datastore_config(db_path: str) -> dict[str, Any]:
    """``duckdb`` connector config for the editable on-disk demo file.

    A real ``database=<abs path>`` connector (NOT ``:memory:``, NOT ``view_sql``)
    so the resolver opens the file and the data browser reports the tables as
    native, writable BASE TABLEs.  Deliberately NOT marked
    ``managed``/``system``/hidden — the user OWNS this connector and sees it in
    the connectors list like any other.
    """
    return {
        "connector_type": "duckdb",
        "database": db_path,
        "description": (
            "Demo datasets (retail sales, SaaS metrics, web analytics, finance ops) "
            "as your own editable lakehouse tables."
        ),
        # No read_only flag: the data-browser write path reopens on-disk files
        # read-write for DML; /query reads open them read-only.
        "sample": True,
    }
