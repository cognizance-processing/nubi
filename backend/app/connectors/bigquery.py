"""BigQuery connector — native Arrow output via google-cloud-bigquery.

``BigQueryConnector`` executes the planner's ``PhysicalPlan`` against Google
BigQuery and returns the result as a ``pyarrow.Table`` via
``RowIterator.to_arrow()`` (BigQuery returns Arrow natively over the Storage /
REST path, so there is no row-by-row Python conversion step).

Placeholder translation
-----------------------
The Nubi planner always emits SQL in the ``postgres`` dialect, so ``plan.sql``
contains ``$1``, ``$2`` … positional placeholders and ``plan.params`` is the
matching positional value list (see ``app/connectors/planner.py``).  BigQuery's
Standard SQL does not understand ``$N``; it uses ``?`` for positional parameters
(``ScalarQueryParameter`` with no name).  This connector rewrites each ``$N``
into a ``?`` and re-orders ``plan.params`` so a repeated ``$1`` binds the same
value at each occurrence, then passes the values as positional
``ScalarQueryParameter`` objects.  **Binding is always parameterised — values are
NEVER string-concatenated into the SQL.**

Authentication
--------------
- If ``config['service_account_json']`` is present (a JSON string or already-parsed
  dict), service-account credentials are built from it via
  ``Credentials.from_service_account_info``.  query.py injects this from the
  encrypted secret store for ``ctype == "bigquery"``.
- Otherwise the client falls back to Application Default Credentials (ADC) —
  ``GOOGLE_APPLICATION_CREDENTIALS`` / workload identity / gcloud auth.
- ``config['project']`` (or ``project_id``) sets the billing/default project; if
  absent the client infers it from the credentials / ADC environment.

Lazy import
-----------
``google-cloud-bigquery`` is heavy and optional, so it is imported **inside** the
methods that need it.  The module imports cleanly without the driver installed;
calling ``execute`` / ``execute_stream`` without it raises
``AppError("driver_unavailable", 500)`` with an install hint.

Capabilities
------------
``native_arrow`` is ``True`` (``RowIterator.to_arrow()`` yields Arrow natively).
``predicate_pushdown`` / ``projection_pushdown`` / ``predicate_rls`` are ``True``
because the planner bakes those directly into the SQL sent to BigQuery.
``partition_pushdown`` / ``column_masking`` / ``streaming_cdc`` are ``False``.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    import pyarrow as pa

from app.connectors.base import Connector
from app.connectors.plan import PhysicalPlan, QueryEstimate
from app.errors import AppError

SOURCE_TYPE = "bigquery"

# Matches $1, $2, ... positional placeholders emitted by the planner's postgres
# dialect.  Captures the 1-based index.
_PG_PLACEHOLDER_RE = re.compile(r"\$(\d+)")


def _import_bigquery() -> Any:
    """Import google-cloud-bigquery lazily; return the module or raise ``AppError``."""
    try:
        from google.cloud import bigquery  # noqa: PLC0415

        return bigquery
    except ImportError as exc:  # pragma: no cover - import guard
        raise AppError(
            "driver_unavailable",
            (
                "google-cloud-bigquery is not installed (needed for the BigQuery "
                "connector). Install it with: pip install google-cloud-bigquery"
            ),
            status=500,
        ) from exc


def _import_service_account_credentials() -> Any:
    """Import the service-account credentials class lazily, or raise ``AppError``."""
    try:
        from google.oauth2 import service_account  # noqa: PLC0415

        return service_account.Credentials
    except ImportError as exc:  # pragma: no cover - import guard
        raise AppError(
            "driver_unavailable",
            (
                "google-auth is not installed (needed to build BigQuery service "
                "account credentials). Install it with: pip install google-auth"
            ),
            status=500,
        ) from exc


def _translate_placeholders(sql: str, params: list[Any]) -> tuple[str, list[Any]]:
    """Rewrite postgres ``$N`` placeholders to BigQuery positional ``?`` markers.

    BigQuery Standard SQL uses ``?`` for positional query parameters and expects
    exactly one ``ScalarQueryParameter`` per ``?`` occurrence, in order.  This
    rebuilds the param list so that a repeated ``$1`` binds the same value at each
    occurrence.

    Returns
    -------
    tuple[str, list]
        ``(rewritten_sql, ordered_params)`` — ``ordered_params`` aligns
        positionally with the ``?`` markers in ``rewritten_sql``.
    """
    if not params:
        return sql, []

    ordered: list[Any] = []

    def _sub(match: "re.Match[str]") -> str:
        idx = int(match.group(1)) - 1  # $N is 1-based
        if idx < 0 or idx >= len(params):
            raise AppError(
                "query_error",
                f"Placeholder ${idx + 1} has no corresponding value in plan.params "
                f"(len={len(params)}).",
                status=500,
            )
        ordered.append(params[idx])
        return "?"

    rewritten = _PG_PLACEHOLDER_RE.sub(_sub, sql)
    return rewritten, ordered


def _bq_param_type(value: Any) -> str:
    """Map a Python value to a BigQuery scalar parameter type string.

    BigQuery requires an explicit type for each query parameter.  We infer a
    sensible scalar type from the Python value; unknown types fall back to
    ``STRING`` (BigQuery will coerce/compare as text).
    """
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int):
        return "INT64"
    if isinstance(value, float):
        return "FLOAT64"
    # bytes → BYTES; everything else (str, Decimal, date/datetime as ISO) → STRING.
    if isinstance(value, (bytes, bytearray)):
        return "BYTES"
    return "STRING"


class BigQueryConnector(Connector):
    """Connector backed by Google BigQuery via google-cloud-bigquery.

    Parameters
    ----------
    config:
        A dict with optional keys:

        ``service_account_json``
            A JSON string or dict of service-account credentials.  When present,
            service-account auth is used; otherwise ADC is used.
        ``project`` / ``project_id``
            The default / billing project.  Optional; inferred from credentials
            when absent.
        ``location``
            Optional BigQuery dataset/job location (e.g. ``"US"``, ``"EU"``).

    Notes
    -----
    ``google-cloud-bigquery`` is imported lazily so the module loads without it.
    A client is built per ``execute`` call (cheap; the heavy lifting is the
    network job).  Pooling/caching of the client is deferred.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = dict(config or {})
        self.validate_capabilities()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, bool]:
        """Return BigQuery connector capability flags.

        ``native_arrow`` is ``True`` (``RowIterator.to_arrow()``).  Push-down +
        RLS are ``True`` because the planner bakes projection / predicate / RLS
        directly into the SQL sent to BigQuery.  Partition routing, column
        masking, and CDC are out of scope.
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
    # Internal: build a BigQuery client
    # ------------------------------------------------------------------

    def _build_client(self) -> Any:
        """Construct a ``bigquery.Client`` from config (service account or ADC)."""
        bigquery = _import_bigquery()

        project = self._config.get("project") or self._config.get("project_id")
        location = self._config.get("location")
        sa = self._config.get("service_account_json")

        if sa:
            credentials_cls = _import_service_account_credentials()
            if isinstance(sa, str):
                try:
                    info = json.loads(sa)
                except json.JSONDecodeError as exc:
                    raise AppError(
                        "config_error",
                        f"service_account_json is not valid JSON: {exc}",
                        status=500,
                    ) from exc
            elif isinstance(sa, dict):
                info = sa
            else:
                raise AppError(
                    "config_error",
                    "service_account_json must be a JSON string or dict.",
                    status=500,
                )
            credentials = credentials_cls.from_service_account_info(info)
            # Fall back to the credentials' project when none was configured.
            project = project or info.get("project_id")
            return bigquery.Client(
                project=project, credentials=credentials, location=location
            )

        # No explicit service account → Application Default Credentials.
        return bigquery.Client(project=project, location=location)

    def _build_query_params(self, params: list[Any]) -> list[Any]:
        """Build positional ``ScalarQueryParameter`` objects for BigQuery.

        Each value becomes an *un-named* ``ScalarQueryParameter`` (positional ``?``
        binding), with a BigQuery type inferred from the Python value.
        """
        bigquery = _import_bigquery()
        return [
            bigquery.ScalarQueryParameter(None, _bq_param_type(v), v) for v in params
        ]

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, plan: PhysicalPlan) -> "pa.Table":
        """Execute *plan* and return the full result as a PyArrow Table.

        ``$N`` placeholders are translated to BigQuery positional ``?`` markers
        and bound as ``ScalarQueryParameter`` objects (never string-concat).  The
        result is materialised via ``RowIterator.to_arrow()``.

        Raises
        ------
        AppError
            ``code="driver_unavailable"`` (500) if google-cloud-bigquery is
            absent.  ``code="query_error"`` (500) if the query fails.
        """
        bigquery = _import_bigquery()
        sql, ordered = _translate_placeholders(plan.sql, plan.params)
        try:
            client = self._build_client()
            job_config = bigquery.QueryJobConfig(
                query_parameters=self._build_query_params(ordered)
            )
            row_iter = client.query(sql, job_config=job_config).result()
            return row_iter.to_arrow()
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "query_error",
                f"BigQuery query failed: {exc}",
                status=500,
            ) from exc

    def estimate(self, plan: PhysicalPlan) -> "QueryEstimate | None":
        """Exact bytes-scanned via a BigQuery dry-run job (free, synchronous).

        BigQuery bills per byte scanned, so a dry run is the highest-value
        pre-run guard: it returns ``total_bytes_processed`` without executing
        the query or incurring cost. Runs the RLS-rewritten ``plan.sql`` so the
        estimate respects the caller's scope. Any failure returns ``None``.
        """
        bigquery = _import_bigquery()
        sql, ordered = _translate_placeholders(plan.sql, plan.params)
        try:
            client = self._build_client()
            job_config = bigquery.QueryJobConfig(
                query_parameters=self._build_query_params(ordered),
                dry_run=True,
                use_query_cache=False,
            )
            job = client.query(sql, job_config=job_config)
            return QueryEstimate(
                est_bytes_scanned=job.total_bytes_processed,
                mechanism="bigquery_dry_run",
                exact=True,
            )
        except Exception:  # noqa: BLE001 — advisory; never raise
            return None

    def execute_stream(self, plan: PhysicalPlan) -> Iterator["pa.RecordBatch"]:
        """Execute *plan* and yield the result as a stream of RecordBatches.

        Materialises via ``execute()`` then yields the table's batches.  True
        Storage-API streaming is deferred (mirrors the other connectors).

        Raises
        ------
        AppError
            ``code="driver_unavailable"`` (500) / ``code="query_error"`` (500).
        """
        table = self.execute(plan)
        yield from table.to_batches()
