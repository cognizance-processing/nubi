"""Per-warehouse bulk loaders (design §4 phase 4).

When the loader layer (:mod:`app.flows.loaders`) chooses the ``bulk`` strategy —
i.e. the TARGET connector advertises a ``bulk_load_from`` scheme that intersects
the STAGING store's scheme — it dispatches here.  Each warehouse has a NATIVE
bulk-load primitive that reads the staged objects directly from cloud storage,
far faster than the universal ``stream`` fallback (worker reads Parquet and
streams batches over the wire):

==========  =====================  =============================================
Target      Loads from             Mechanism
==========  =====================  =============================================
BigQuery    ``gcs``                load job from ``gs://…`` (``LoadJobConfig``)
Snowflake   ``s3`` / ``gcs`` / az  ``COPY INTO`` from an external stage / URL
Redshift    ``s3``                 ``COPY … FROM 's3://…'``
ClickHouse  ``s3`` / ``gcs``       ``INSERT … SELECT … FROM s3(…)`` table function
==========  =====================  =============================================

Cross-cloud mismatch (e.g. staging on S3, target BigQuery which only loads from
GCS) is NOT handled here — :func:`app.flows.loaders.choose_strategy` already
falls back to ``stream`` when the schemes do not intersect (no multi-cloud
staging in v1).  The :data:`WAREHOUSE_BULK_LOAD_FROM` table below is what gates
that decision; keep it the single source of truth.

Design constraints honoured here
--------------------------------
* **Lazy driver imports.**  The warehouse drivers (google-cloud-bigquery,
  snowflake-connector-python, psycopg/redshift, clickhouse-connect) are imported
  INSIDE the executor functions, never at module import — identical to the query
  connectors.  The statement BUILDERS (``*_copy_statement`` / ``*_uris``) are
  pure string/list functions with NO driver dependency, so they are unit-testable
  with no creds and no client.
* **Central secrets.**  All bulk loads run on central workers with
  centrally-resolved secrets; the warehouse client is built from the target
  connector's resolved config (same path as the query connectors).  Nothing is
  shipped to an agent.
* **Client seam.**  Each executor takes an OPTIONAL pre-built ``client`` so a
  test can inject a mock and assert the constructed load job / COPY statement
  WITHOUT a live warehouse round-trip.  When ``client is None`` the executor
  builds the real client lazily (the live-creds path).

The staged objects' bytes are NOT re-read by the worker on the bulk path — the
warehouse pulls them straight from staging — so the loader passes us the staged
URIs (``staging.uri(rel_path)``) rather than the bytes.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable

from app.errors import AppError

if TYPE_CHECKING:
    from app.flows.loaders import LoadTarget
    from app.lakehouse.staging import StagingArea, StagingManifest


# ---------------------------------------------------------------------------
# Identifier validation — the ONLY user-controlled value that reaches the
# warehouse statement builders is the target TABLE name (``target.object``).
# It is interpolated into ``COPY INTO <table>`` / ``COPY <table>`` /
# ``INSERT INTO <table>``, so it MUST be a strict, dot-qualified SQL identifier
# (optionally double-quoted parts) — never raw user text. A value like
# ``orders; DROP TABLE secrets; --`` is rejected here, before it can reach any
# warehouse, rather than concatenated into the statement.
# ---------------------------------------------------------------------------
#
# Each dotted component is either:
#   * a bare identifier:  [A-Za-z_][A-Za-z0-9_$]*
#   * a double-quoted identifier: "..." with internal "" escaping and no other
#     double-quotes (so the closing quote can't be smuggled).
_BARE_IDENT = r"[A-Za-z_][A-Za-z0-9_$]*"
_QUOTED_IDENT = r'"(?:[^"]|"")+"'
_IDENT_PART = rf"(?:{_BARE_IDENT}|{_QUOTED_IDENT})"
_TABLE_RE = re.compile(rf"^{_IDENT_PART}(?:\.{_IDENT_PART}){{0,3}}$")


def validate_table_identifier(table: str) -> str:
    """Return *table* if it is a safe dot-qualified SQL identifier, else raise.

    The target table name is the single user-controlled token that lands in a
    bulk-load statement (``COPY INTO``/``COPY``/``INSERT INTO``). Allowing only
    ``db.schema.table`` style identifiers (bare or double-quoted parts, up to 4
    components) blocks statement injection through ``target.object`` without
    needing per-warehouse quoting.
    """
    name = (table or "").strip()
    if not name or len(name) > 512 or not _TABLE_RE.match(name):
        raise AppError(
            "invalid_identifier",
            f"Target object {table!r} is not a valid table identifier "
            "(expected db.schema.table — letters, digits, underscores, or "
            'double-quoted parts).',
            status=400,
        )
    return name


# ---------------------------------------------------------------------------
# Capability table — which staging schemes each warehouse can bulk-load from.
# This is the SINGLE SOURCE OF TRUTH for choose_strategy's cross-cloud gate.
# ---------------------------------------------------------------------------
#
# Keyed by the connector ``ctype`` (``connector_type`` / ``type`` in the
# datastore config).  A scheme NOT in the list ⇒ that staging posture falls back
# to ``stream`` for this warehouse (design's cross-cloud rule).
WAREHOUSE_BULK_LOAD_FROM: dict[str, list[str]] = {
    # BigQuery load jobs only read from Google Cloud Storage.
    "bigquery": ["gcs"],
    # Snowflake external stages support S3, GCS and Azure.
    "snowflake": ["s3", "gcs", "az"],
    # Redshift COPY reads from S3 (Redshift Spectrum / native COPY).
    "redshift": ["s3"],
    # ClickHouse s3() table function reads S3 (and GCS via the S3-compatible API).
    "clickhouse": ["s3", "gcs"],
}

# Connector ctypes that this module knows how to bulk-load into.
BULK_WAREHOUSES: frozenset[str] = frozenset(WAREHOUSE_BULK_LOAD_FROM)


# ---------------------------------------------------------------------------
# Stage-URI helpers
# ---------------------------------------------------------------------------


def staged_uris(staging: "StagingArea", manifest: "StagingManifest") -> list[str]:
    """Return the full staging URIs for every object in *manifest*.

    The warehouse reads these directly (load job / COPY / s3()), so the worker
    never re-downloads the bytes on the bulk path.
    """
    return [staging.uri(entry.path) for entry in manifest.files]


def _gcs_uri(uri: str) -> str:
    """Normalise a staging URI to BigQuery's ``gs://`` form.

    The staging layer may use the ``gcs://`` scheme alias; BigQuery load jobs
    expect ``gs://``.  ``s3://`` / ``file://`` are returned unchanged (the caller
    only reaches here when the scheme is gcs-compatible).
    """
    if uri.startswith("gcs://"):
        return "gs://" + uri[len("gcs://"):]
    return uri


# ---------------------------------------------------------------------------
# Statement / job builders (PURE — no driver, no client, no creds).
# These are the unit-testable core of each warehouse strategy.
# ---------------------------------------------------------------------------


def bigquery_source_uris(uris: list[str]) -> list[str]:
    """BigQuery load-job source URIs (``gs://…``) from staged URIs."""
    return [_gcs_uri(u) for u in uris]


def snowflake_copy_statement(
    table: str, stage_uri: str, *, file_format: str = "PARQUET"
) -> str:
    """Build a Snowflake ``COPY INTO`` statement from an external storage URL.

    Snowflake can ``COPY INTO <table> FROM '<url>'`` referencing an external
    location directly (with a ``FILE_FORMAT``).  We point it at the per-run
    staging prefix's URL.  ``MATCH_BY_COLUMN_NAME`` lets Parquet columns map to
    table columns by name rather than position (robust to column re-ordering).
    """
    table = validate_table_identifier(table)
    return (
        f"COPY INTO {table} FROM '{stage_uri}' "
        f"FILE_FORMAT = (TYPE = {file_format}) "
        "MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE "
        "PURGE = FALSE"
    )


def redshift_copy_statement(
    table: str,
    s3_uri: str,
    *,
    iam_role: str | None = None,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    region: str | None = None,
) -> str:
    """Build a Redshift ``COPY … FROM 's3://…' FORMAT AS PARQUET`` statement.

    Auth is either an IAM role (``IAM_ROLE '<arn>'``) or static key credentials
    (``ACCESS_KEY_ID``/``SECRET_ACCESS_KEY``).  IAM role is preferred when both
    are present.  Credentials are resolved CENTRALLY (from the target
    connector's secret) and embedded only in the in-memory statement sent to
    Redshift — never logged, never shipped to an agent.
    """
    table = validate_table_identifier(table)
    parts = [f"COPY {table}", f"FROM '{s3_uri}'"]
    if iam_role:
        parts.append(f"IAM_ROLE '{iam_role}'")
    elif access_key_id and secret_access_key:
        parts.append(
            f"ACCESS_KEY_ID '{access_key_id}' "
            f"SECRET_ACCESS_KEY '{secret_access_key}'"
        )
    parts.append("FORMAT AS PARQUET")
    if region:
        parts.append(f"REGION '{region}'")
    return " ".join(parts)


def clickhouse_insert_statement(
    table: str,
    s3_uri: str,
    *,
    access_key_id: str | None = None,
    secret_access_key: str | None = None,
    file_format: str = "Parquet",
) -> str:
    """Build a ClickHouse ``INSERT … SELECT * FROM s3(…)`` statement.

    Uses the ``s3()`` table function to read the staged Parquet directly.  When
    static credentials are supplied they are passed positionally
    (``s3(url, access_key, secret, format)``); otherwise the 2-arg form
    (``s3(url, format)``) relies on the server's configured access (IAM / env).
    """
    table = validate_table_identifier(table)
    if access_key_id and secret_access_key:
        src = (
            f"s3('{s3_uri}', '{access_key_id}', '{secret_access_key}', "
            f"'{file_format}')"
        )
    else:
        src = f"s3('{s3_uri}', '{file_format}')"
    return f"INSERT INTO {table} SELECT * FROM {src}"


# ---------------------------------------------------------------------------
# Per-warehouse executors (lazy driver import; optional client seam for tests).
# Each returns the number of rows loaded (best-effort; falls back to the
# manifest's row count when the driver does not report it).
# ---------------------------------------------------------------------------


def bigquery_bulk_load(
    cfg: dict[str, Any],
    table: str,
    uris: list[str],
    *,
    manifest_rows: int,
    client: Any | None = None,
) -> int:
    """Run a BigQuery load job from staged ``gs://`` URIs into *table*.

    Builds a real ``bigquery.Client`` from *cfg* (service account / ADC) when no
    *client* is injected.  The load job is configured for Parquet with
    ``WRITE_APPEND`` (the loader's ``mode`` maps overwrite→TRUNCATE upstream).
    """
    sources = bigquery_source_uris(uris)
    if client is None:  # pragma: no cover — live-creds path
        from app.connectors.bigquery import BigQueryConnector  # noqa: PLC0415

        client = BigQueryConnector(cfg)._build_client()

    from google.cloud import bigquery  # noqa: PLC0415  # pragma: no cover

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    job = client.load_table_from_uri(sources, table, job_config=job_config)
    result = job.result()  # block until the load completes
    return int(getattr(result, "output_rows", None) or manifest_rows)


def snowflake_bulk_load(
    cfg: dict[str, Any],
    table: str,
    stage_uri: str,
    *,
    manifest_rows: int,
    client: Any | None = None,
) -> int:
    """Run a Snowflake ``COPY INTO`` from the staged prefix into *table*."""
    sql = snowflake_copy_statement(table, stage_uri)
    if client is None:  # pragma: no cover — live-creds path
        from app.connectors.snowflake import SnowflakeConnector  # noqa: PLC0415

        client = SnowflakeConnector(cfg)._connect()

    cur = client.cursor()
    try:
        cur.execute(sql)
        rows = _rowcount_from_cursor(cur)
    finally:
        cur.close()
    return int(rows if rows is not None else manifest_rows)


def redshift_bulk_load(
    cfg: dict[str, Any],
    table: str,
    s3_uri: str,
    *,
    manifest_rows: int,
    client: Any | None = None,
) -> int:
    """Run a Redshift ``COPY`` from the staged S3 prefix into *table*.

    Redshift is wire-compatible with Postgres, so the connection is psycopg.
    Auth for the COPY comes from the connector config (``iam_role`` or static
    S3 keys), resolved centrally.
    """
    sql = redshift_copy_statement(
        table,
        s3_uri,
        iam_role=cfg.get("iam_role") or cfg.get("copy_iam_role"),
        access_key_id=cfg.get("aws_access_key_id") or cfg.get("access_key_id"),
        secret_access_key=cfg.get("aws_secret_access_key") or cfg.get("secret_access_key"),
        region=cfg.get("region") or cfg.get("region_name"),
    )
    if client is None:  # pragma: no cover — live-creds path
        import psycopg  # noqa: PLC0415

        client = psycopg.connect(_redshift_dsn(cfg))

    with client.cursor() as cur:
        cur.execute(sql)
        rows = _rowcount_from_cursor(cur)
    try:
        client.commit()
    except Exception:  # noqa: BLE001  — autocommit clients have no commit
        pass
    return int(rows if rows is not None else manifest_rows)


def clickhouse_bulk_load(
    cfg: dict[str, Any],
    table: str,
    s3_uri: str,
    *,
    manifest_rows: int,
    client: Any | None = None,
) -> int:
    """Run a ClickHouse ``INSERT … SELECT … FROM s3(…)`` into *table*."""
    sql = clickhouse_insert_statement(
        table,
        s3_uri,
        access_key_id=cfg.get("aws_access_key_id") or cfg.get("access_key_id"),
        secret_access_key=cfg.get("aws_secret_access_key") or cfg.get("secret_access_key"),
    )
    if client is None:  # pragma: no cover — live-creds path
        from app.connectors.clickhouse import ClickHouseConnector  # noqa: PLC0415

        client = ClickHouseConnector(cfg)._connect()

    client.command(sql)
    return int(manifest_rows)


# ---------------------------------------------------------------------------
# Dispatch + target resolution
# ---------------------------------------------------------------------------


def _stage_prefix_uri(staging: "StagingArea") -> str:
    """The URI of the per-run staging PREFIX (directory), with trailing slash.

    Snowflake / Redshift / ClickHouse can load every object under a prefix in
    one statement, so the prefix URI is the natural unit; BigQuery takes an
    explicit URI list instead (it supports wildcards but the list is exact).
    """
    return staging.uri("").rstrip("/") + "/"


def make_bulk_callable(
    ctype: str,
    cfg: dict[str, Any],
    staging: "StagingArea",
    manifest: "StagingManifest",
    *,
    client: Any | None = None,
) -> "Callable[[str], int]":
    """Return a ``bulk(object_name) -> rows`` callable for *ctype*.

    Dispatches to the right per-warehouse executor, closing over the resolved
    config + staged location.  ``client`` is the test/seam injection point —
    when ``None`` each executor builds the real warehouse client lazily.
    """
    rows = manifest.total_rows
    prefix_uri = _stage_prefix_uri(staging)
    uris = staged_uris(staging, manifest)

    if ctype == "bigquery":
        def _bulk(table: str) -> int:
            return bigquery_bulk_load(cfg, table, uris, manifest_rows=rows, client=client)
    elif ctype == "snowflake":
        def _bulk(table: str) -> int:
            return snowflake_bulk_load(cfg, table, prefix_uri, manifest_rows=rows, client=client)
    elif ctype == "redshift":
        def _bulk(table: str) -> int:
            return redshift_bulk_load(cfg, table, prefix_uri, manifest_rows=rows, client=client)
    elif ctype == "clickhouse":
        def _bulk(table: str) -> int:
            return clickhouse_bulk_load(cfg, table, prefix_uri, manifest_rows=rows, client=client)
    else:  # pragma: no cover — guarded by BULK_WAREHOUSES at the call site
        raise ValueError(f"No bulk loader for connector type {ctype!r}.")

    return _bulk


def resolve_bulk_target(
    object_name: str,
    ctype: str,
    cfg: dict[str, Any],
) -> "LoadTarget | None":
    """Build a bulk-capable :class:`LoadTarget` for *ctype*, or ``None``.

    Returns ``None`` when *ctype* is not a known bulk warehouse — the caller
    then falls back to the ``stream`` target.  The returned target advertises
    ``bulk_load_from`` from :data:`WAREHOUSE_BULK_LOAD_FROM` so
    :func:`app.flows.loaders.choose_strategy` can apply the cross-cloud gate; the
    ``bulk`` callable is bound later (once staging exists) via
    :func:`bind_bulk`.
    """
    if ctype not in BULK_WAREHOUSES:
        return None

    from app.connectors.base import file_capabilities  # noqa: PLC0415
    from app.flows.loaders import LoadTarget  # noqa: PLC0415

    target = LoadTarget(
        object_name=object_name,
        capabilities=file_capabilities(bulk_load_from=WAREHOUSE_BULK_LOAD_FROM[ctype]),
    )
    # Stash what bind_bulk needs once the staging area is known.
    target._bulk_ctype = ctype  # type: ignore[attr-defined]
    target._bulk_cfg = cfg  # type: ignore[attr-defined]
    return target


def bind_bulk(
    target: "LoadTarget",
    staging: "StagingArea",
    manifest: "StagingManifest",
    *,
    client: Any | None = None,
) -> None:
    """Finish wiring the ``bulk`` callable now that staging + manifest exist.

    No-op when *target* was not produced by :func:`resolve_bulk_target` (so the
    handler can call it unconditionally, mirroring ``file_ingest._bind_promote``).
    """
    ctype = getattr(target, "_bulk_ctype", None)
    cfg = getattr(target, "_bulk_cfg", None)
    if ctype is None or cfg is None:
        return
    target.bulk = make_bulk_callable(ctype, cfg, staging, manifest, client=client)


# ---------------------------------------------------------------------------
# Small internal helpers
# ---------------------------------------------------------------------------


def _rowcount_from_cursor(cur: Any) -> int | None:
    """Best-effort row count from a DB-API cursor after a COPY/INSERT."""
    rc = getattr(cur, "rowcount", None)
    if isinstance(rc, int) and rc >= 0:
        return rc
    return None


def _redshift_dsn(cfg: dict[str, Any]) -> str:  # pragma: no cover — live-creds path
    """Build a Redshift (Postgres-wire) DSN from connector config."""
    dsn = cfg.get("dsn")
    if dsn:
        return str(dsn)
    host = cfg.get("host", "localhost")
    port = cfg.get("port", 5439)
    dbname = cfg.get("dbname") or cfg.get("database") or "dev"
    user = cfg.get("user") or cfg.get("username") or "awsuser"
    password = cfg.get("password", "")
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
