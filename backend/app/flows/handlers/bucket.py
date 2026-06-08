"""``bucket_load`` task handler — writes data to object storage.

This handler serialises an upstream task's row-payload (or raw bytes/URI) into
the requested format and uploads it to an object-storage destination via the
:mod:`app.storage` abstraction layer.

Public API
----------
``handle(config, ctx, claims) -> dict``
    Write upstream data to cloud / local storage.  Returns a result dict
    describing the upload:

    .. code-block:: python

        {
            "uri":          "s3://my-bucket/path/out.parquet",
            "format":       "parquet",
            "row_count":    1234,
            "bytes_written": 56789,
        }

Config keys
-----------
uri : str
    Destination storage URI (e.g. ``s3://bucket/path/out.parquet``).
secret : str, optional
    Name of a secret in ``ctx.secrets`` whose value is a JSON-encoded
    credentials dict.  When absent, environment-variable / ADC credentials
    are used by the storage backend.
source : str
    Key of the upstream task whose result provides the data.
format : ``'csv'`` | ``'json'`` | ``'parquet'`` | ``'ndjson'``
    Serialisation format.  Defaults to ``'csv'``.
mode : ``'overwrite'`` | ``'append'``
    Write mode.  ``'append'`` downloads the existing object, deserialises it,
    merges rows, and re-uploads.  Defaults to ``'overwrite'``.

Upstream payload shapes
-----------------------
Row-shaped (produced by ``query`` / ``materialize`` handlers):
    ``{"rows": [{...}, ...], "columns": [...], ...}``

Raw-bytes:
    ``{"bytes": b"..."}`` — written verbatim regardless of *format*.

URI-shaped (delegate to storage-client download then re-upload):
    ``{"uri": "file:///tmp/data.parquet"}``

Lazy imports
------------
``pandas`` and ``pyarrow`` are imported lazily inside the function body and
are only required when *format* is ``'parquet'``.  The stdlib ``csv`` and
``json`` modules cover all other formats.
"""

from __future__ import annotations

import csv
import io
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.flows.executor import TaskContext


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _rows_to_csv(rows: list[dict[str, Any]]) -> bytes:
    """Serialise a list of row dicts to CSV bytes (UTF-8, with header)."""
    if not rows:
        return b""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _rows_to_json(rows: list[dict[str, Any]]) -> bytes:
    """Serialise a list of row dicts to a JSON array (UTF-8)."""
    return json.dumps(rows, default=str).encode("utf-8")


def _rows_to_ndjson(rows: list[dict[str, Any]]) -> bytes:
    """Serialise a list of row dicts to newline-delimited JSON (UTF-8)."""
    lines = [json.dumps(row, default=str) for row in rows]
    return "\n".join(lines).encode("utf-8")


def _rows_to_parquet(rows: list[dict[str, Any]]) -> bytes:
    """Serialise a list of row dicts to Parquet bytes via pandas + pyarrow.

    Raises
    ------
    RuntimeError
        If ``pandas`` or ``pyarrow`` is not installed.
    """
    try:
        import pandas as pd  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "The 'parquet' format requires pandas. "
            "Install it with: pip install pandas"
        ) from exc
    try:
        import pyarrow  # noqa: PLC0415, F401
    except ImportError as exc:
        raise RuntimeError(
            "The 'parquet' format requires pyarrow. "
            "Install it with: pip install pyarrow"
        ) from exc

    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_parquet(buf, index=False, engine="pyarrow")
    return buf.getvalue()


def _bytes_from_rows(rows: list[dict[str, Any]], fmt: str) -> bytes:
    """Convert *rows* to bytes in the requested *fmt*."""
    if fmt == "csv":
        return _rows_to_csv(rows)
    if fmt == "json":
        return _rows_to_json(rows)
    if fmt == "ndjson":
        return _rows_to_ndjson(rows)
    if fmt == "parquet":
        return _rows_to_parquet(rows)
    raise ValueError(
        f"Unsupported format {fmt!r}. "
        "Supported formats: csv, json, ndjson, parquet."
    )


# ---------------------------------------------------------------------------
# Append-mode merge helpers
# ---------------------------------------------------------------------------


def _existing_rows(client: Any, key: str, fmt: str) -> list[dict[str, Any]]:
    """Download and deserialise existing data at *key*.

    Returns an empty list if the object does not exist.  Only supports
    CSV, JSON, and NDJSON for round-trip append; parquet append requires
    pandas/pyarrow (imported lazily).
    """
    try:
        raw = client.download_bytes(key)
    except FileNotFoundError:
        return []

    if fmt == "csv":
        text = raw.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        return list(reader)

    if fmt == "json":
        return json.loads(raw.decode("utf-8"))

    if fmt == "ndjson":
        lines = raw.decode("utf-8").splitlines()
        return [json.loads(ln) for ln in lines if ln.strip()]

    if fmt == "parquet":
        try:
            import pandas as pd  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "The 'parquet' format requires pandas for append mode. "
                "Install it with: pip install pandas"
            ) from exc
        buf = io.BytesIO(raw)
        df = pd.read_parquet(buf, engine="pyarrow")
        return df.to_dict(orient="records")

    raise ValueError(f"Unsupported format {fmt!r} for append.")


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


def handle(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],  # noqa: ARG001  — reserved for future RLS
) -> dict[str, Any]:
    """Write upstream data to object storage.

    Parameters
    ----------
    config:
        Resolved task config dict with the following keys:

        ``uri`` (required)
            Destination storage URI.
        ``source`` (required)
            Upstream task key whose result provides the data.
        ``format`` (optional, default ``'csv'``)
            Serialisation format: ``'csv'``, ``'json'``, ``'ndjson'``, or
            ``'parquet'``.
        ``mode`` (optional, default ``'overwrite'``)
            Write mode: ``'overwrite'`` or ``'append'``.
        ``secret`` (optional)
            Secret name in ``ctx.secrets`` whose JSON-decoded value is used
            as the credentials dict for the storage backend.
    ctx:
        Task execution context; ``ctx.inputs`` supplies upstream results and
        ``ctx.secrets`` supplies resolved secret values.
    claims:
        Caller's auth claims (reserved for future row-level security use).

    Returns
    -------
    dict
        ``{"uri": str, "format": str, "row_count": int, "bytes_written": int}``

    Raises
    ------
    ValueError
        If required config keys are missing or the format/mode is invalid.
    RuntimeError
        If a required cloud SDK is not installed, with an actionable message.
    KeyError
        If the ``source`` task key is not found in ``ctx.inputs``.
    """
    from app.storage.base import get_storage_client, parse_uri  # noqa: PLC0415

    # ------------------------------------------------------------------
    # Validate / extract config.
    # ------------------------------------------------------------------
    dest_uri: str = config.get("uri", "").strip()
    if not dest_uri:
        raise ValueError("bucket_load task requires 'uri' in config.")

    source_key: str = config.get("source", "").strip()
    if not source_key:
        raise ValueError("bucket_load task requires 'source' in config.")

    fmt: str = config.get("format", "csv").lower().strip()
    mode: str = config.get("mode", "overwrite").lower().strip()
    secret_name: str | None = config.get("secret") or None

    if fmt not in {"csv", "json", "ndjson", "parquet"}:
        raise ValueError(
            f"Invalid format {fmt!r}. "
            "Supported formats: csv, json, ndjson, parquet."
        )
    if mode not in {"overwrite", "append"}:
        raise ValueError(
            f"Invalid mode {mode!r}. "
            "Supported modes: overwrite, append."
        )

    # ------------------------------------------------------------------
    # Resolve storage credentials.
    # ------------------------------------------------------------------
    creds: dict[str, Any] | None = None
    if secret_name is not None:
        raw_secret = ctx.secrets.get(secret_name)
        if raw_secret is None:
            raise ValueError(
                f"Secret {secret_name!r} not found in ctx.secrets. "
                "Ensure it is created and that the flow org has access."
            )
        try:
            creds = json.loads(raw_secret)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Secret {secret_name!r} is not valid JSON: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Pull upstream payload.
    # ------------------------------------------------------------------
    if source_key not in ctx.inputs:
        raise KeyError(
            f"Upstream source {source_key!r} not found in ctx.inputs. "
            f"Available keys: {sorted(ctx.inputs)}"
        )

    upstream: Any = ctx.inputs[source_key]

    # ------------------------------------------------------------------
    # Determine the destination key from the URI.
    # ------------------------------------------------------------------
    _scheme, _bucket, dest_key = parse_uri(dest_uri)
    client = get_storage_client(dest_uri, creds)

    # ------------------------------------------------------------------
    # Serialise data to bytes.
    # ------------------------------------------------------------------
    data: bytes
    rows: list[dict[str, Any]]
    row_count: int

    # Case 1: upstream is raw bytes (e.g. from a previous bucket_load).
    if isinstance(upstream, dict) and "bytes" in upstream:
        data = upstream["bytes"]
        row_count = 0

    # Case 2: upstream is a URI — download and re-upload verbatim.
    elif isinstance(upstream, dict) and "uri" in upstream and "rows" not in upstream:
        src_uri: str = upstream["uri"]
        from app.storage.base import get_storage_client as _gsc  # noqa: PLC0415

        src_client = _gsc(src_uri, creds)
        _src_scheme, _src_bucket, src_key = parse_uri(src_uri)
        data = src_client.download_bytes(src_key)
        row_count = 0

    # Case 3: row-shaped (from query / materialize handlers).
    elif isinstance(upstream, dict) and "rows" in upstream:
        rows = upstream["rows"]
        if mode == "append" and dest_key:
            existing = _existing_rows(client, dest_key, fmt)
            rows = existing + rows
        row_count = len(rows)
        data = _bytes_from_rows(rows, fmt)

    # Case 4: upstream is a plain list of dicts.
    elif isinstance(upstream, list):
        rows = upstream
        if mode == "append" and dest_key:
            existing = _existing_rows(client, dest_key, fmt)
            rows = existing + rows
        row_count = len(rows)
        data = _bytes_from_rows(rows, fmt)

    # Case 5: unknown shape — serialise as JSON fallback.
    else:
        data = json.dumps(upstream, default=str).encode("utf-8")
        row_count = 0

    # ------------------------------------------------------------------
    # Upload.
    # ------------------------------------------------------------------
    bytes_written = len(data)
    final_uri = client.upload_bytes(data, dest_key)

    return {
        "uri": final_uri,
        "format": fmt,
        "row_count": row_count,
        "bytes_written": bytes_written,
    }
