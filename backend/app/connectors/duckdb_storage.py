"""DuckDB storage connector — object-storage-aware wrapper around DuckDBConnector.

Extends :class:`~app.connectors.duckdb_conn.DuckDBConnector` with:

* **Local file paths** (``config.database`` / ``config.path``): identical to the
  existing behaviour in ``query.py`` — ``duckdb.connect(path, read_only=True)``.
  No httpfs needed; works fully offline.

* **s3:// URIs** (MinIO / AWS S3): installs and loads the ``httpfs`` DuckDB
  extension, registers a ``CREATE SECRET (TYPE s3, ...)`` using the credentials
  supplied in *config* (or from environment variables as a fallback), and
  attaches the remote database / reads remote Parquet files via DuckDB's native
  httpfs support.

* **write_result(sql, dest_uri)**: executes *sql* (a SELECT) and writes the
  result to *dest_uri* as a Parquet file using DuckDB's
  ``COPY (<sql>) TO '<dest>' (FORMAT parquet)``.  Works for both local
  ``file://`` paths and ``s3://`` URIs (the latter requires httpfs to already
  be configured, which ``from_config`` handles automatically).

Scheme detection
----------------
The scheme is inferred from ``config["database"]`` (or ``config["path"]``):

* Starts with ``s3://``, ``s3a://``, ``gs://``, ``az://`` → cloud path; httpfs.
* Starts with ``/``, ``./``, ``file://``, or any other non-URI string → local.
* ``:memory:`` / absent / ``None`` → in-memory DuckDB (fixture / demo path).

RLS
---
All RLS predicate injection is performed by the planner **before** this
connector is called.  ``execute()`` / ``execute_stream()`` are inherited from
:class:`DuckDBConnector` and run ``plan.sql`` verbatim — they MUST NOT touch RLS
logic.

Credentials dict shape (s3 / MinIO)
------------------------------------
.. code-block:: python

    {
        # Required for s3:// paths (or use env vars below):
        "aws_access_key_id":     "minioadmin",
        "aws_secret_access_key": "minioadmin",
        # Optional — defaults apply when absent:
        "aws_region":            "us-east-1",
        "s3_endpoint":           "http://localhost:9000",  # MinIO / S3-compat
        "s3_url_style":          "path",   # "path" for MinIO, "vhost" for AWS
        # Convenience aliases (also accepted):
        "endpoint_url":          "http://localhost:9000",
        "region_name":           "us-east-1",
    }

Environment variable fallbacks (used when the corresponding config key is absent):

* ``AWS_ACCESS_KEY_ID`` / ``AWS_ACCESS_KEY``
* ``AWS_SECRET_ACCESS_KEY`` / ``AWS_SECRET_KEY``
* ``AWS_DEFAULT_REGION`` / ``AWS_REGION``
* ``S3_ENDPOINT_URL``

Usage
-----
::

    cfg = {
        "connector_type": "duckdb",
        "database": "s3://my-bucket/data/warehouse.duckdb",
        "aws_access_key_id": "minioadmin",
        "aws_secret_access_key": "minioadmin",
        "s3_endpoint": "http://localhost:9000",
    }
    connector = DuckDBStorageConnector.from_config(cfg)
    plan = planner.plan("SELECT * FROM my_table LIMIT 10")
    table = connector.execute(plan)

    # Write a query result back to object storage:
    uri = connector.write_result("SELECT id, amount FROM orders", "s3://bucket/out.parquet")
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import TYPE_CHECKING, BinaryIO, Iterator

if TYPE_CHECKING:
    import duckdb as _duckdb_t
    import pyarrow as pa

from app.connectors.base import Connector, FileConnectorMixin, FileStat, file_capabilities
from app.connectors.duckdb_conn import DuckDBConnector
from app.connectors.plan import PhysicalPlan
from app.connectors.storage_files import StorageFileSupport
from app.errors import AppError

# Schemes that require httpfs + secret registration before any query.
_S3_SCHEMES = frozenset({"s3", "s3a"})
_CLOUD_SCHEMES = frozenset({"s3", "s3a", "gs", "az"})


def _detect_scheme(database: str) -> str | None:
    """Return the URI scheme for *database*, or ``None`` for local/in-memory.

    Parameters
    ----------
    database:
        The ``database`` / ``path`` value from the connector config.

    Returns
    -------
    str | None
        Lower-cased URI scheme (``"s3"``, ``"s3a"``, ``"gs"``, ``"az"``) when
        the path is a cloud URI; ``None`` for local filesystem paths and
        ``:memory:``.
    """
    if not database or database.strip() == ":memory:":
        return None
    lower = database.strip().lower()
    if "://" in lower:
        scheme, _ = lower.split("://", 1)
        return scheme
    return None  # bare path / relative path — local


def _get_creds(config: dict) -> dict[str, str]:
    """Extract S3 credentials from *config* with env-var fallbacks.

    Precedence: config key > environment variable.

    Returns
    -------
    dict[str, str]
        A normalised credentials dict with keys:
        ``key_id``, ``secret``, ``region``, ``endpoint``, ``url_style``.
        Missing or empty values are represented as ``""``.
    """
    def _get(config_keys: list[str], env_keys: list[str], default: str = "") -> str:
        for k in config_keys:
            v = config.get(k)
            if v:
                return str(v)
        for k in env_keys:
            v = os.environ.get(k)
            if v:
                return str(v)
        return default

    key_id = _get(
        ["aws_access_key_id", "access_key_id", "key_id"],
        ["AWS_ACCESS_KEY_ID", "AWS_ACCESS_KEY"],
    )
    secret = _get(
        ["aws_secret_access_key", "secret_access_key", "secret"],
        ["AWS_SECRET_ACCESS_KEY", "AWS_SECRET_KEY"],
    )
    region = _get(
        ["aws_region", "region_name", "region"],
        ["AWS_DEFAULT_REGION", "AWS_REGION"],
        default="us-east-1",
    )
    endpoint = _get(
        ["s3_endpoint", "endpoint_url", "endpoint"],
        ["S3_ENDPOINT_URL"],
    )
    url_style = _get(
        ["s3_url_style", "url_style"],
        [],
        default="path" if endpoint else "vhost",
    )
    scope = _get(["s3_scope", "scope"], [])
    return {
        "key_id": key_id,
        "secret": secret,
        "region": region,
        "endpoint": endpoint,
        "url_style": url_style,
        "scope": scope,
    }


def _install_httpfs(conn: "_duckdb_t.DuckDBPyConnection") -> None:
    """Install and load the DuckDB httpfs extension.

    DuckDB >=0.9 ships httpfs in the default extensions bundle for most
    platforms; the ``INSTALL`` step is a no-op if already present.

    Parameters
    ----------
    conn:
        An open DuckDB connection.

    Raises
    ------
    AppError
        ``code="httpfs_unavailable"`` (500) if httpfs cannot be loaded.
    """
    try:
        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")
    except Exception as exc:
        raise AppError(
            "httpfs_unavailable",
            f"DuckDB httpfs extension could not be loaded: {exc}",
            status=500,
        ) from exc


def _register_s3_secret(
    conn: "_duckdb_t.DuckDBPyConnection",
    creds: dict[str, str],
    secret_name: str = "nubi_s3",
) -> None:
    """Register a DuckDB S3 secret on *conn* using *creds*.

    Uses DuckDB's ``CREATE OR REPLACE SECRET`` (≥0.10.0) so re-running on
    the same connection is idempotent.

    Parameters
    ----------
    conn:
        An open DuckDB connection with httpfs already loaded.
    creds:
        Normalised credentials dict from :func:`_get_creds`.
    secret_name:
        Name for the DuckDB secret (default ``"nubi_s3"``).

    Notes
    -----
    The ``ENDPOINT`` clause strips the protocol prefix (``http://`` /
    ``https://``) because DuckDB expects a bare host:port string in the
    secret definition; the ``USE_SSL`` flag controls whether TLS is used.
    The ``URL_STYLE 'path'`` is required for MinIO and other S3-compatible
    stores that do not support virtual-hosted-style bucket addressing.
    When ``creds["scope"]`` is non-empty, the secret is bound to that path
    prefix via DuckDB's ``SCOPE`` clause — queries against paths outside the
    scope have no credentials, giving per-tenant isolation at the engine
    layer independent of RLS.
    """
    endpoint_raw = creds["endpoint"]
    use_ssl = "true"
    endpoint_bare = ""
    if endpoint_raw:
        if endpoint_raw.startswith("http://"):
            use_ssl = "false"
            endpoint_bare = endpoint_raw[len("http://"):]
        elif endpoint_raw.startswith("https://"):
            use_ssl = "true"
            endpoint_bare = endpoint_raw[len("https://"):]
        else:
            endpoint_bare = endpoint_raw

    parts: list[str] = [
        f"    TYPE s3",
        f"    KEY_ID '{creds['key_id']}'",
        f"    SECRET '{creds['secret']}'",
        f"    REGION '{creds['region']}'",
        f"    USE_SSL {use_ssl}",
        f"    URL_STYLE '{creds['url_style']}'",
    ]
    if endpoint_bare:
        parts.append(f"    ENDPOINT '{endpoint_bare}'")
    if creds.get("scope"):
        parts.append(f"    SCOPE '{creds['scope']}'")

    sql = (
        f"CREATE OR REPLACE SECRET {secret_name} (\n"
        + ",\n".join(parts)
        + "\n)"
    )
    conn.execute(sql)


def _storage_file_uri(config: dict) -> str | None:
    """Derive the object-storage URI the FILE interface operates over.

    Precedence: an explicit ``storage_uri`` / ``files_uri`` (where ingest files
    live, distinct from the ``database`` DuckDB file), else the bucket root of
    the ``database`` URI for cloud paths, else a local directory.

    Returns ``None`` when no file root can be determined (in-memory connectors).
    """
    explicit = config.get("storage_uri") or config.get("files_uri")
    if explicit:
        return str(explicit)

    db_path = config.get("database") or config.get("path") or ""
    if not db_path or db_path.strip() == ":memory:":
        return None
    db_path = db_path.strip()

    if "://" in db_path:
        # Cloud URI — operate over the bucket root (scheme://bucket).
        scheme, rest = db_path.split("://", 1)
        bucket = rest.split("/", 1)[0]
        return f"{scheme}://{bucket}"

    # Local DuckDB file → file interface over its containing directory.
    if db_path.startswith("file://"):
        db_path = db_path[len("file://"):]
    parent = os.path.dirname(os.path.abspath(db_path))
    return f"file://{parent}"


class DuckDBStorageConnector(Connector, FileConnectorMixin):
    """DuckDB connector with dual local-file / S3 object-storage support.

    Do not instantiate directly — use :meth:`from_config` to build an
    instance from a datastore config dict, or :meth:`for_local_path` /
    :meth:`for_memory` for specific use-cases.

    The connector wraps an inner :class:`~app.connectors.duckdb_conn.DuckDBConnector`
    for all execution; this class adds the httpfs + secret bootstrap layer on
    top and exposes :meth:`write_result` for Parquet write-back.

    Parameters
    ----------
    inner:
        A fully-configured :class:`DuckDBConnector` instance.
    is_cloud:
        ``True`` when the underlying database is a cloud URI (s3://…); used
        to gate write-back path decisions.
    """

    def __init__(
        self,
        inner: DuckDBConnector,
        *,
        is_cloud: bool = False,
        config: dict | None = None,
    ) -> None:
        self._inner = inner
        self._is_cloud = is_cloud
        # Retained so the FILE interface can build a storage client lazily.
        self._config: dict = dict(config or {})
        self._file_support: StorageFileSupport | None = None
        # No validate_capabilities() here — delegated to the inner connector.

    # ------------------------------------------------------------------
    # Factory constructors
    # ------------------------------------------------------------------

    @classmethod
    def for_memory(cls) -> "DuckDBStorageConnector":
        """Return a connector backed by a fresh in-memory DuckDB database.

        Suitable for fixtures, demo datasets, and conformance tests.
        """
        return cls(DuckDBConnector(), is_cloud=False)

    @classmethod
    def for_local_path(cls, path: str, *, read_only: bool = True) -> "DuckDBStorageConnector":
        """Return a connector backed by the DuckDB file at *path*.

        Parameters
        ----------
        path:
            Absolute path to the ``.duckdb`` / ``.db`` file.
        read_only:
            Open the file in read-only mode (default ``True``).  Set to
            ``False`` only when the connector needs write access (e.g.
            building a local lakehouse during a test).
        """
        try:
            import duckdb  # noqa: PLC0415
        except ImportError as exc:
            raise AppError(
                "driver_unavailable",
                "DuckDB is not installed.  Add 'duckdb>=1.0' to requirements.txt.",
                status=500,
            ) from exc
        conn = duckdb.connect(database=path, read_only=read_only)
        if read_only:
            # A read-only file source has no need to touch the local FS /
            # network at query time; freeze the settings for the connection.
            from app.connectors.duckdb_conn import harden_connection  # noqa: PLC0415

            harden_connection(conn, disable_external_access=True)
        return cls(DuckDBConnector(conn), is_cloud=False)

    @classmethod
    def for_s3(
        cls,
        database: str,
        creds: dict[str, str],
    ) -> "DuckDBStorageConnector":
        """Return a connector configured for *database* at an S3/MinIO URI.

        Installs httpfs, registers a DuckDB S3 secret, and opens a new
        in-memory DuckDB connection suitable for querying ``read_parquet()``
        and attaching remote databases over httpfs.

        Parameters
        ----------
        database:
            The ``s3://`` URI.  May be a ``.duckdb`` database URI or a
            Parquet file / prefix — the connector does not auto-ATTACH; callers
            issue explicit SQL (``SELECT * FROM read_parquet('s3://...')``,
            ``ATTACH '...'``, etc.).
        creds:
            Normalised credentials dict from :func:`_get_creds`.
        """
        try:
            import duckdb  # noqa: PLC0415
        except ImportError as exc:
            raise AppError(
                "driver_unavailable",
                "DuckDB is not installed.  Add 'duckdb>=1.0' to requirements.txt.",
                status=500,
            ) from exc

        # Use an in-memory DuckDB connection for cloud reads — the remote file
        # is accessed via httpfs; we do NOT open it as the local DB file.
        conn = duckdb.connect(database=":memory:")
        _install_httpfs(conn)
        _register_s3_secret(conn, creds)
        # Cloud connections read object storage only — tenant SQL must never
        # reach the host filesystem.  Hardened AFTER httpfs + secret setup
        # because lock_configuration freezes settings for the connection.
        from app.connectors.duckdb_conn import harden_connection  # noqa: PLC0415

        harden_connection(conn, block_local_fs=True)

        inner = DuckDBConnector(conn)
        inst = cls(inner, is_cloud=True)
        inst._database_uri = database
        return inst

    @classmethod
    def from_config(cls, config: dict) -> "DuckDBStorageConnector":
        """Build a connector from a datastore config dict.

        Scheme detection is automatic (see module docstring).

        Parameters
        ----------
        config:
            A datastore ``config`` dict as stored in the repo.  Relevant keys:
            ``database`` / ``path`` (the DB URI or file path), plus any
            credential keys (see module docstring).

        Returns
        -------
        DuckDBStorageConnector
            A fully bootstrapped connector ready to execute plans.
        """
        db_path: str = config.get("database") or config.get("path") or ":memory:"
        scheme = _detect_scheme(db_path)

        if scheme in _S3_SCHEMES:
            creds = _get_creds(config)
            inst = cls.for_s3(db_path, creds)
        elif scheme in _CLOUD_SCHEMES:
            # GCS / Azure — not fully implemented yet; fall back to httpfs
            # with the S3-compat credentials structure and let DuckDB handle it.
            # Callers are expected to use specialised connectors for gs:// / az://.
            creds = _get_creds(config)
            inst = cls.for_s3(db_path, creds)
        elif db_path and db_path.strip() not in (":memory:", ""):
            # Local file.
            path = db_path.strip()
            if path.startswith("file://"):
                path = path[len("file://"):]
            inst = cls.for_local_path(path)
        else:
            inst = cls.for_memory()

        # Retain the config so the (additive) file interface can build a storage
        # client lazily; query behaviour is unchanged.
        inst._config = dict(config)
        return inst

    # ------------------------------------------------------------------
    # Connector interface (delegated to inner DuckDBConnector)
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, bool]:
        """Return DuckDB connector capability flags + the ingestion extension.

        The 7 query flags delegate to the inner :class:`DuckDBConnector`.  The
        ingestion extension marks this connector as BOTH file-capable
        (``file_interface``) AND a viable object-storage target: it can be the
        ``promote`` destination for a matching staging scheme (``bulk_load_from``)
        and the worker can stream batches into it (``stream_load``).  A
        purely in-memory connector (no resolvable file root) advertises no file
        interface so it is never mistaken for an ingest source/target.
        """
        caps = dict(self._inner.capabilities())
        file_uri = _storage_file_uri(self._config)
        has_files = file_uri is not None
        scheme = (file_uri.split("://", 1)[0] if has_files else "")
        # Map a storage scheme to the loader's staging-scheme vocabulary.
        bulk_from: list[str] = []
        if scheme in ("s3", "s3a"):
            bulk_from = ["s3"]
        elif scheme == "gs":
            bulk_from = ["gcs"]
        elif scheme == "az":
            bulk_from = ["az"]
        caps.update(
            file_capabilities(
                file_interface=has_files,
                bulk_load_from=bulk_from,
                stream_load=has_files,
            )
        )
        return caps

    def execute(self, plan: PhysicalPlan) -> "pa.Table":
        """Execute *plan* and return the full result as a PyArrow Table.

        Delegates verbatim to the inner :class:`DuckDBConnector`.  RLS
        predicates have already been injected by the planner into
        ``plan.sql``; this method does NOT touch them.

        Parameters
        ----------
        plan:
            A fully-baked ``PhysicalPlan`` produced by the planner.

        Returns
        -------
        pyarrow.Table
        """
        return self._inner.execute(plan)

    def execute_stream(self, plan: PhysicalPlan) -> Iterator["pa.RecordBatch"]:
        """Execute *plan* and yield the result as RecordBatches.

        Delegates verbatim to the inner :class:`DuckDBConnector`.

        Parameters
        ----------
        plan:
            A fully-baked ``PhysicalPlan``.

        Yields
        ------
        pyarrow.RecordBatch
        """
        yield from self._inner.execute_stream(plan)

    def register(self, tables: dict[str, "pa.Table"]) -> None:
        """Register Arrow tables as named DuckDB views (fixture/demo helper).

        Parameters
        ----------
        tables:
            A mapping of ``{table_name: pyarrow.Table}``.
        """
        self._inner.register(tables)

    # ------------------------------------------------------------------
    # Write-back: COPY ... TO 's3://...' (FORMAT parquet)
    # ------------------------------------------------------------------

    def write_result(self, sql: str, dest_uri: str) -> str:
        """Execute *sql* and write the result to *dest_uri* as Parquet.

        Uses DuckDB's ``COPY (<sql>) TO '<dest_uri>' (FORMAT parquet)``.

        For ``s3://`` destinations the connector must have been created via
        :meth:`for_s3` or :meth:`from_config` with an S3 URI so that httpfs
        + the S3 secret are already configured on the connection.  For local
        ``file://`` or bare-path destinations, DuckDB writes to the
        filesystem directly without requiring httpfs.

        Parameters
        ----------
        sql:
            A SELECT SQL string.  Must be a SELECT (the planner's COPY wrapper
            does not validate this; DuckDB will raise if it is not).
        dest_uri:
            Destination URI.  Accepts:

            - ``s3://bucket/key.parquet`` (cloud, requires httpfs)
            - ``/abs/path/to/output.parquet`` (local filesystem)
            - ``file:///abs/path/to/output.parquet`` (local filesystem, file:// prefix)

        Returns
        -------
        str
            *dest_uri* (echoed back for convenience).

        Raises
        ------
        AppError
            ``code="write_result_error"`` (500) if DuckDB raises during the
            COPY operation.
        """
        # Normalise file:// URIs to bare paths — DuckDB does not accept them
        # in the COPY TO statement.
        effective_dest = dest_uri
        if effective_dest.startswith("file://"):
            effective_dest = effective_dest[len("file://"):]

        # Ensure parent directory exists for local paths.
        if not effective_dest.startswith("s3://") and not effective_dest.startswith("s3a://"):
            import os as _os  # noqa: PLC0415
            parent = _os.path.dirname(effective_dest)
            if parent:
                _os.makedirs(parent, exist_ok=True)

        copy_sql = f"COPY ({sql}) TO '{effective_dest}' (FORMAT parquet)"
        try:
            self._inner._conn.execute(copy_sql)
        except Exception as exc:
            raise AppError(
                "write_result_error",
                f"DuckDB COPY TO '{dest_uri}' failed: {exc}",
                status=500,
            ) from exc
        return dest_uri

    def read_parquet(self, uri: str) -> "pa.Table":
        """Read a Parquet file at *uri* and return as a PyArrow Table.

        Convenience wrapper around ``SELECT * FROM read_parquet('<uri>')``.
        For ``s3://`` URIs the connector must have been configured for S3.

        Parameters
        ----------
        uri:
            Path / URI to the Parquet file.  Accepts local paths and
            ``s3://`` URIs (httpfs must be loaded).

        Returns
        -------
        pyarrow.Table

        Raises
        ------
        AppError
            ``code="query_error"`` (500) if DuckDB raises.
        """
        effective_uri = uri
        if effective_uri.startswith("file://"):
            effective_uri = effective_uri[len("file://"):]

        sql = f"SELECT * FROM read_parquet('{effective_uri}')"
        try:
            rel = self._inner._conn.execute(sql)
            result = rel.arrow()
            if hasattr(result, "read_all"):
                return result.read_all()
            return result
        except Exception as exc:
            raise AppError(
                "query_error",
                f"DuckDB read_parquet('{uri}') failed: {exc}",
                status=500,
            ) from exc

    # ------------------------------------------------------------------
    # File interface (FileConnectorMixin) — reuses app.storage clients
    # ------------------------------------------------------------------

    def _files(self) -> StorageFileSupport:
        """Lazily build the ``StorageFileSupport`` over the resolved file root.

        Reuses the existing ``app.storage`` client for the connector's scheme
        (S3/GCS/Azure/local) — no new storage client is written here.  Raises
        ``AppError("file_interface_unavailable", 400)`` for in-memory connectors
        that have no file root.
        """
        if self._file_support is not None:
            return self._file_support

        file_uri = _storage_file_uri(self._config)
        if file_uri is None:
            raise AppError(
                "file_interface_unavailable",
                "This duckdb_storage connector has no object-storage root "
                "(in-memory or path-less); the file interface is unavailable. "
                "Set 'storage_uri' or a cloud/local 'database' path.",
                status=400,
            )
        # Object-storage creds reuse the connector's S3-style config (and the
        # secret-store-merged aws_secret_access_key), mirroring the httpfs path.
        creds = _storage_creds_for(self._config)
        client = _build_storage_client(file_uri, creds)
        # The base prefix is any key portion under the bucket the caller scoped
        # the file interface to (config['files_prefix']); empty by default.
        prefix = str(self._config.get("files_prefix") or "").strip("/")
        self._file_support = StorageFileSupport(client, base_prefix=prefix)
        return self._file_support

    def list_files(self, pattern: str, since: "datetime | None" = None) -> list[FileStat]:
        """List ingest files matching *pattern* (newer than *since*)."""
        return self._files().list_files(pattern, since)

    def open(self, path: str) -> BinaryIO:
        """Open an object at *path* for streaming read."""
        return self._files().open(path)

    def move(self, src: str, dst: str) -> None:
        """Move *src* to *dst* in object storage (post_action archive)."""
        self._files().move(src, dst)

    def delete(self, path: str) -> None:
        """Delete the object at *path* (post_action delete)."""
        self._files().delete(path)


def _build_storage_client(file_uri: str, creds: dict):
    """Return a ``StorageClient`` for *file_uri*, reusing the app.storage backends.

    For ``file://`` URIs the ENTIRE path after the scheme is the root directory
    (so the connector's keys are relative to that directory) — this bypasses
    ``parse_uri``'s "first two path components are the bucket" convention, which
    is wrong for a deep local ingest root.  Cloud schemes delegate to
    ``get_storage_client`` (bucket = first path segment, as usual).
    """
    if file_uri.startswith("file://"):
        from app.storage.local import LocalStorageClient  # noqa: PLC0415

        return LocalStorageClient(root=file_uri[len("file://"):])

    from app.storage.base import get_storage_client  # noqa: PLC0415

    return get_storage_client(file_uri, creds=creds)


def _storage_creds_for(config: dict) -> dict:
    """Build an ``app.storage`` creds dict from a duckdb_storage config.

    Maps the connector's S3-style config keys onto the
    :func:`app.storage.base.get_storage_client` credential shape so the file
    interface authenticates the same way the httpfs query path does.
    """
    creds = _get_creds(config)
    out: dict[str, str] = {}
    if creds.get("key_id"):
        out["aws_access_key_id"] = creds["key_id"]
    if creds.get("secret"):
        out["aws_secret_access_key"] = creds["secret"]
    if creds.get("region"):
        out["region_name"] = creds["region"]
    if creds.get("endpoint"):
        out["endpoint_url"] = creds["endpoint"]
    return out
