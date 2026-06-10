"""HTTP/JSON connector — fetches a JSON API endpoint and normalises it to Arrow.

This is the primary connector for REST API sources that return JSON arrays (or
nested objects containing an array).  Because the remote source cannot push down
predicates or projections, all security and shaping steps are applied post-fetch
in Python.

Security contract
-----------------
``HttpJsonConnector`` declares ``predicate_rls=True`` and
``predicate_pushdown=False``.  Per the SDK contract this means
``apply_rls_postfetch`` **must** be called before returning data — and it is,
inside ``execute()``.  ``apply_rls_postfetch`` is fail-closed: if the remote
JSON body does not contain the column named in the RLS policy, it raises
``AppError("rls_column_missing", 403)`` rather than returning unfiltered data.

This is the server-side enforcement layer for API sources.  The browser MUST
never be trusted to filter rows.

Configuration
-------------
Pass a ``config`` dict with the following keys:

``url`` (required)
    Full HTTP URL to fetch with a GET request.
``record_path`` (optional)
    A dot-separated path to navigate into the parsed JSON body before
    interpreting the records, e.g. ``"data.items"`` traverses
    ``body["data"]["items"]``.  If omitted the top-level body must itself be a
    list of records.
``headers`` (optional)
    A ``{name: value}`` dict of additional request headers (e.g. Authorization).

Example
-------
::

    conn = HttpJsonConnector({
        "url": "https://api.example.com/tenants/records",
        "record_path": "data.results",
        "headers": {"Authorization": "Bearer <token>"},
    })
    plan = PhysicalPlan(
        sql="SELECT id, tenant_id FROM records",
        rls_claims={"policies": {"tenant_id": "acme"}},
        ...
    )
    table = conn.execute(plan)   # only acme rows, only requested columns
"""

from __future__ import annotations

from typing import Any, Iterator

import pyarrow as pa

from app.connectors.base import Connector
from app.connectors.plan import PhysicalPlan
from app.connectors.ssrf import guard_url
from app.connectors.sdk import (
    apply_limit_postfetch,
    apply_projection_postfetch,
    apply_rls_postfetch,
)
from app.errors import AppError


class HttpJsonConnector(Connector):
    """Connector for REST/JSON API sources.

    Fetches a JSON endpoint via HTTP GET, normalises the response to a
    PyArrow Table, then applies post-fetch RLS, projection, and limit guards.

    The connector does **not** push down any operations to the remote source.
    All filtering and shaping happens in Python after the raw JSON is fetched.

    Parameters
    ----------
    config:
        A dict with the following keys:

        ``url`` (str, required)
            The HTTP endpoint to fetch.
        ``record_path`` (str, optional)
            Dot-separated path into the JSON body to reach the list of records.
            E.g. ``"data.items"`` for ``{"data": {"items": [...]}}``.
        ``headers`` (dict, optional)
            Additional HTTP request headers.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._url: str = config["url"]
        self._record_path: str | None = config.get("record_path")
        self._headers: dict[str, str] = config.get("headers") or {}
        self.validate_capabilities()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, bool]:
        """Return capability flags for an HTTP/JSON source.

        This source cannot push down any operations to the remote endpoint.
        RLS is declared as supported (``predicate_rls=True``) because this
        connector applies ``apply_rls_postfetch`` server-side after fetching.
        """
        return {
            "native_arrow": False,
            "predicate_pushdown": False,
            "projection_pushdown": False,
            "partition_pushdown": False,
            "predicate_rls": True,
            "column_masking": False,
            "streaming_cdc": False,
        }

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, plan: PhysicalPlan) -> pa.Table:
        """Fetch the JSON endpoint, normalise to Arrow, and apply post-fetch guards.

        Steps
        -----
        1. Fetch ``config['url']`` via HTTP GET (using ``httpx``; lazy import).
        2. Navigate ``record_path`` (if configured) to reach the list of records.
        3. Normalise the list of dicts to a ``pyarrow.Table`` (union of all keys;
           missing keys become nulls).
        4. Apply ``apply_rls_postfetch`` (fail-closed — 403 if policy column absent).
        5. Apply ``apply_projection_postfetch`` if ``plan.projection`` is set.
        6. Apply ``apply_limit_postfetch`` (best-effort from plan).

        Parameters
        ----------
        plan:
            The physical query plan.  ``plan.rls_claims['policies']`` drives RLS;
            ``plan.projection`` drives column selection.

        Returns
        -------
        pyarrow.Table
            Post-fetch-guarded result: RLS-filtered, projected, and capped.

        Raises
        ------
        app.errors.AppError
            ``code="source_fetch_error"`` (502) on any network or HTTP error.
        app.errors.AppError
            ``code="rls_column_missing"`` (403) if a policy column is absent
            from the JSON records (fail-closed).
        """
        # Step 1: fetch JSON via httpx (lazy import — httpx is not always available
        # in every test environment and importing at module level would break pure
        # unit-test imports that monkeypatch httpx).
        import httpx  # noqa: PLC0415

        # SSRF guard: reject the fetch if the target host resolves to an
        # internal/loopback/link-local/metadata address before any outbound
        # request is made.  Raises AppError("ssrf_blocked", 400) on a block.
        guard_url(self._url)

        try:
            response = httpx.get(self._url, headers=self._headers)
            response.raise_for_status()
            body = response.json()
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            raise AppError(
                "source_fetch_error",
                f"Failed to fetch JSON source at {self._url!r}: {exc}",
                status=502,
            ) from exc

        # Step 2: navigate record_path.
        records = _navigate_record_path(body, self._record_path, self._url)

        # Step 3: normalise list-of-dicts to Arrow Table.
        table = _records_to_arrow(records)

        # Step 4: RLS post-fetch guard (fail-closed).
        policies: dict[str, Any] = plan.rls_claims.get("policies", {})
        table = apply_rls_postfetch(table, policies)

        # Step 5: projection post-fetch guard.
        table = apply_projection_postfetch(table, plan.projection)

        # Step 6: limit post-fetch guard (best-effort).
        limit = _extract_limit(plan)
        table = apply_limit_postfetch(table, limit)

        return table

    def execute_stream(self, plan: PhysicalPlan) -> Iterator[pa.RecordBatch]:
        """Yield the result of ``execute()`` as a stream of RecordBatches.

        Delegates all work to ``execute()`` so that post-fetch guards are applied
        exactly once, then yields the resulting table's batches.

        Yields
        ------
        pyarrow.RecordBatch
            One or more batches from the post-fetch-guarded result.
        """
        table = self.execute(plan)
        yield from table.to_batches()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _navigate_record_path(
    body: Any,
    record_path: str | None,
    url: str,
) -> list[dict[str, Any]]:
    """Navigate a dot-separated *record_path* in *body* to find the records list.

    Parameters
    ----------
    body:
        The parsed JSON response body.
    record_path:
        A dot-separated string such as ``"data.items"``.  Each segment is used
        as a dict key in sequence.  ``None`` means the body itself is the list.
    url:
        The source URL (used only in error messages).

    Returns
    -------
    list[dict]
        The list of record dicts to be normalised to Arrow.

    Raises
    ------
    app.errors.AppError
        ``code="source_fetch_error"`` (502) if the path does not resolve to a
        list, or if a segment key is missing from the body.
    """
    if record_path is None:
        node = body
    else:
        node = body
        for segment in record_path.split("."):
            if not isinstance(node, dict) or segment not in node:
                raise AppError(
                    "source_fetch_error",
                    f"record_path '{record_path}' could not be navigated in "
                    f"the JSON response from {url!r}: missing key '{segment}'.",
                    status=502,
                )
            node = node[segment]

    if not isinstance(node, list):
        raise AppError(
            "source_fetch_error",
            f"Expected a list of records at "
            f"{'record_path=' + repr(record_path) if record_path else 'top level'} "
            f"from {url!r}, but got {type(node).__name__}.",
            status=502,
        )

    return node  # type: ignore[return-value]


def _records_to_arrow(records: list[dict[str, Any]]) -> pa.Table:
    """Normalise a list of record dicts to a PyArrow Table.

    Column set is the **union** of all keys across all records.  Records that
    lack a key have a ``null`` in that column.

    Parameters
    ----------
    records:
        A list of dicts representing rows.  May be empty.

    Returns
    -------
    pyarrow.Table
        An Arrow table with one column per key found across all records,
        inferred types, and nulls where keys are absent.
    """
    if not records:
        # Return an empty table with no columns.  The schema will be refined
        # once real data arrives; an empty list means no schema can be inferred.
        return pa.table({})

    # Collect the union of all keys, preserving insertion order (Python 3.7+).
    all_keys: list[str] = []
    seen: set[str] = set()
    for record in records:
        for key in record:
            if key not in seen:
                all_keys.append(key)
                seen.add(key)

    # Build per-column Python lists (None for missing keys).
    columns: dict[str, list[Any]] = {key: [] for key in all_keys}
    for record in records:
        for key in all_keys:
            columns[key].append(record.get(key))  # None if absent

    # Let PyArrow infer types.  Columns with mixed or all-None values will be
    # typed as null or the broadest compatible type.
    return pa.table(columns)


def _extract_limit(plan: PhysicalPlan) -> int | None:
    """Extract a best-effort LIMIT value from *plan*.

    Mirrors the same logic used by ``FunctionConnector`` / ``sdk._extract_limit``.
    """
    import re  # noqa: PLC0415

    sql_upper = plan.sql.upper().rstrip()
    if plan.params and (
        "LIMIT ?" in sql_upper
        or bool(re.search(r"LIMIT\s+\$\d+\s*$", sql_upper))
    ):
        last_param = plan.params[-1]
        if isinstance(last_param, int) and last_param >= 0:
            return last_param
    return None
