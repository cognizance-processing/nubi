"""DuckDB connector — deterministic local engine and conformance fixture.

``DuckDBConnector`` wraps an in-memory (or on-disk) DuckDB database.  It is
the primary fixture engine for the conformance suite (Wave M1-C) and the
fallback executor used by the query endpoint when no external data-store is
configured.

Design notes
------------
- Pure Python; no network I/O; deterministic given the same seed data.
- Native Arrow output via ``duckdb.DuckDBPyConnection.arrow()`` — zero-copy
  where DuckDB supports it.
- ``register(tables)`` seeds named tables from a dict of ``{name: pa.Table}``
  so tests can inject arbitrary fixture data without touching the filesystem.
- S3/httpfs: call ``setup_s3_httpfs(conn, cfg)`` before executing queries that
  reference ``s3://`` paths.  The helper installs and loads the httpfs
  extension, then registers a DuckDB S3 SECRET from ``cfg`` credentials or
  the standard ``AWS_*`` / ``S3_*`` environment variables.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    import duckdb as _duckdb_t
    import pyarrow as pa

from app.connectors.base import Connector
from app.connectors.plan import PhysicalPlan
from app.errors import AppError


# ---------------------------------------------------------------------------
# S3 / httpfs helper
# ---------------------------------------------------------------------------


def setup_s3_httpfs(
    conn: "_duckdb_t.DuckDBPyConnection",
    cfg: "dict | None" = None,
) -> None:
    """Install/load the httpfs extension and register an S3 SECRET on *conn*.

    This is a no-op when neither *cfg* nor environment variables supply S3
    credentials — the secret is only created when at least ``key_id`` is
    resolvable, which avoids clobbering any ambient credential chain already
    present in DuckDB's default provider.

    Parameters
    ----------
    conn:
        An open DuckDB connection.
    cfg:
        Optional connector configuration dict.  The following keys are
        consumed if present:

        ``s3_key_id`` / ``aws_access_key_id``
            AWS/MinIO access key ID.
        ``s3_secret`` / ``aws_secret_access_key``
            AWS/MinIO secret access key.
        ``s3_endpoint`` / ``endpoint_url``
            Custom endpoint URL for MinIO or other S3-compatible services
            (e.g. ``"http://localhost:9000"``).  The scheme is stripped when
            building the DuckDB ``ENDPOINT`` option so that DuckDB receives
            only ``host:port``.
        ``s3_region`` / ``aws_region``
            AWS region (defaults to ``"us-east-1"``).
        ``s3_url_style``
            ``"path"`` (default for MinIO) or ``"vhost"``.

        When a key is absent from *cfg* the corresponding ``AWS_*`` /
        ``S3_ENDPOINT_URL`` / ``S3_URL_STYLE`` environment variable is
        consulted.

    Side-effects
    ------------
    Executes ``INSTALL httpfs``, ``LOAD httpfs``, and optionally
    ``CREATE OR REPLACE SECRET nubi_s3 (TYPE S3, ...)`` on *conn*.
    All statements are idempotent.
    """
    cfg = cfg or {}

    conn.execute("INSTALL httpfs")
    conn.execute("LOAD httpfs")

    # ---- credential resolution (cfg → env) --------------------------------
    key_id = (
        cfg.get("s3_key_id")
        or cfg.get("aws_access_key_id")
        or os.getenv("AWS_ACCESS_KEY_ID")
        or os.getenv("S3_ACCESS_KEY", "")
    )
    secret = (
        cfg.get("s3_secret")
        or cfg.get("aws_secret_access_key")
        or os.getenv("AWS_SECRET_ACCESS_KEY")
        or os.getenv("S3_SECRET_KEY", "")
    )

    # Raw endpoint value may include a scheme (http:// / https://).  DuckDB's
    # ENDPOINT option wants only the host[:port] portion.
    endpoint_raw = (
        cfg.get("s3_endpoint")
        or cfg.get("endpoint_url")
        or os.getenv("S3_ENDPOINT_URL")
        or os.getenv("AWS_ENDPOINT_URL")
        or ""
    )
    # Strip scheme so DuckDB only sees "host:port", and infer USE_SSL from the
    # scheme (MinIO/local is plain http → USE_SSL false; AWS S3 is https → true).
    endpoint = endpoint_raw
    scheme_ssl: bool | None = None
    if endpoint_raw.startswith("https://"):
        endpoint = endpoint_raw[len("https://"):]
        scheme_ssl = True
    elif endpoint_raw.startswith("http://"):
        endpoint = endpoint_raw[len("http://"):]
        scheme_ssl = False
    # Remove any trailing slash.
    endpoint = endpoint.rstrip("/")

    region = (
        cfg.get("s3_region")
        or cfg.get("aws_region")
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or os.getenv("S3_REGION")
        or "us-east-1"
    )
    url_style = (
        cfg.get("s3_url_style")
        or os.getenv("S3_URL_STYLE")
        or ("path" if endpoint else "vhost")
    )

    # Only register a secret when we have at least a key_id — otherwise rely
    # on the default DuckDB credential chain.
    if not key_id:
        return

    parts: list[str] = [
        "TYPE S3",
        f"KEY_ID '{key_id}'",
        f"SECRET '{secret}'",
        f"REGION '{region}'",
        f"URL_STYLE '{url_style}'",
    ]
    if endpoint:
        parts.append(f"ENDPOINT '{endpoint}'")
        # USE_SSL: explicit cfg/env override, else inferred from the endpoint
        # scheme (http→false for MinIO, https→true). Without this DuckDB defaults
        # to SSL and fails against a plain-http MinIO endpoint with an
        # "SSL connect error" on every PUT/GET.
        ssl_override = cfg.get("s3_use_ssl")
        if ssl_override is None:
            ssl_override = os.getenv("S3_USE_SSL")
        if ssl_override is not None:
            use_ssl = str(ssl_override).strip().lower() in ("1", "true", "yes", "on")
        elif scheme_ssl is not None:
            use_ssl = scheme_ssl
        else:
            use_ssl = True
        parts.append(f"USE_SSL {'true' if use_ssl else 'false'}")

    secret_sql = "CREATE OR REPLACE SECRET nubi_s3 (\n    " + ",\n    ".join(parts) + "\n)"
    conn.execute(secret_sql)


class DuckDBConnector(Connector):
    """Connector backed by an in-process DuckDB database.

    Parameters
    ----------
    connection:
        An existing ``duckdb.DuckDBPyConnection`` to use.  Defaults to a
        fresh in-memory database when ``None``.

    Usage
    -----
    ::

        conn = DuckDBConnector()
        conn.register({"demo": pa.table({"id": [1, 2], "value": [10.0, 20.0]})})
        plan = planner.plan("SELECT * FROM demo")
        table = conn.execute(plan)
    """

    def __init__(self, connection: "_duckdb_t.DuckDBPyConnection | None" = None) -> None:
        try:
            import duckdb
        except ImportError as exc:
            raise AppError(
                "driver_unavailable",
                "DuckDB is not installed.  Add 'duckdb>=1.0' to requirements.txt.",
                status=500,
            ) from exc

        if connection is not None:
            self._conn = connection
        else:
            self._conn = duckdb.connect(database=":memory:")

        self.validate_capabilities()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, bool]:
        """Return DuckDB connector capability flags.

        DuckDB supports native Arrow output, full predicate and projection
        push-down (they are encoded in the SQL the planner generates), and
        predicate-level RLS (injected into the WHERE clause by the planner).
        It does not support partition routing, column masking, or CDC.
        """
        return {
            "native_arrow": True,
            "predicate_pushdown": True,
            "projection_pushdown": True,
            "partition_pushdown": False,
            "predicate_rls": True,
            "column_masking": False,
            "streaming_cdc": False,
        }

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, plan: PhysicalPlan) -> "pa.Table":
        """Execute *plan* and return the full result as a PyArrow Table.

        Parameters
        ----------
        plan:
            A fully-baked ``PhysicalPlan``.  ``plan.sql`` is run verbatim via
            DuckDB; ``plan.params`` are passed as positional parameters.

        Returns
        -------
        pyarrow.Table
            The full query result.

        Raises
        ------
        AppError
            ``code="query_error"`` (500) if DuckDB raises any exception.
        """
        try:
            rel = self._conn.execute(plan.sql, plan.params)
            # duckdb >=1.0 returns a RecordBatchReader from .arrow(); call
            # .read_all() to materialise it as a pyarrow.Table.
            result = rel.arrow()
            if hasattr(result, "read_all"):
                return result.read_all()
            return result  # already a pa.Table in older builds
        except Exception as exc:
            raise AppError(
                "query_error",
                f"DuckDB query failed: {exc}",
                status=500,
            ) from exc

    def execute_stream(self, plan: PhysicalPlan) -> Iterator["pa.RecordBatch"]:
        """Execute *plan* and yield result data as a stream of RecordBatches.

        Parameters
        ----------
        plan:
            A fully-baked ``PhysicalPlan``.

        Yields
        ------
        pyarrow.RecordBatch
            One or more batches forming the full result.  For M1 the entire
            result is fetched eagerly and yielded as a single batch stream;
            true chunking will be added in M2.

        Raises
        ------
        AppError
            ``code="query_error"`` (500) if DuckDB raises any exception.
        """
        table = self.execute(plan)
        yield from table.to_batches()

    # ------------------------------------------------------------------
    # Seeding helper
    # ------------------------------------------------------------------

    def register(self, tables: dict[str, "pa.Table"]) -> None:
        """Register Arrow tables as named DuckDB views.

        This is the primary way to inject fixture data for tests and the
        demo dataset.  Each entry in *tables* becomes a named relation that
        can be queried by ``SELECT * FROM <name>``.

        Parameters
        ----------
        tables:
            A mapping of ``{table_name: pyarrow.Table}``.  Existing views
            with the same name are replaced.

        Example
        -------
        ::

            import pyarrow as pa
            conn = DuckDBConnector()
            conn.register({
                "orders": pa.table({
                    "id": pa.array([1, 2, 3], type=pa.int32()),
                    "total": pa.array([9.99, 19.99, 4.99], type=pa.float64()),
                })
            })
        """
        for name, table in tables.items():
            # DuckDB can register a PyArrow table directly as a named relation.
            self._conn.register(name, table)
