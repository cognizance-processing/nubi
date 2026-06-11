"""Loader layer — load staged data into *any* connector target (design §4).

Ingestion is ``source connector → staging → target connector``.  Once a
producer has landed verified Parquet in the per-run staging prefix
(:mod:`app.lakehouse.staging`), the loader picks a strategy from the TARGET
connector's ``capabilities()`` and moves the staged data into the final target:

============================================  ==========  ==================================
Target class                                  Strategy    Mechanism
============================================  ==========  ==================================
Object storage (incl. managed lakehouse)      ``promote`` server-side copy staging → final
Warehouse with a compatible bulk loader       ``bulk``    (phase 4 — SEAM only here)
Everything else (Postgres, MySQL, …)          ``stream``  worker reads Parquet, streams batches
============================================  ==========  ==================================

Strategy selection (``choose_strategy``):
  1. ``capabilities()["file_interface"]`` (object storage / managed lake) →
     ``promote``.
  2. ``capabilities()["bulk_load_from"]`` intersects the STAGING scheme → ``bulk``
     — but ``bulk`` is **phase 4**.  The seam is here (``_BULK_LOAD_ENABLED``
     flag, default ``False``); until then we fall through to ``stream`` rather
     than building per-warehouse load jobs.  This matches the design's
     "cross-cloud mismatch falls back to stream" rule for free.
  3. ``capabilities()["stream_load"]`` → ``stream`` — the UNIVERSAL fallback.

All loads run on CENTRAL workers with centrally-resolved secrets, regardless of
where the bytes were sourced (design §4).

The loader takes a :class:`LoadTarget` descriptor rather than a live connector
so it is unit-testable with a fake target and so the connector-resolution path
(``_resolve_flow_connector``) stays in one place (the handler).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterator

if TYPE_CHECKING:
    from app.lakehouse.staging import StagingArea, StagingManifest


# Phase-4 seam: per-warehouse bulk loads (BigQuery load jobs, Snowflake COPY
# INTO, Redshift COPY, ClickHouse s3()) are NOT built in phase 1.  Flipping this
# to True (plus implementing ``_bulk_load``) is the only change needed to light
# them up; until then ``choose_strategy`` never returns ``"bulk"``.
_BULK_LOAD_ENABLED = False

# Staging schemes the loader can produce from.  The bulk path (phase 4) requires
# the target's ``bulk_load_from`` to intersect this set; otherwise we stream.
_STAGING_SCHEMES = ("s3", "gcs", "az", "file")


# ---------------------------------------------------------------------------
# Target descriptor
# ---------------------------------------------------------------------------


@dataclass
class LoadTarget:
    """A resolved load target: capabilities + the bits each strategy needs.

    Attributes
    ----------
    object_name:
        The logical target object (``target.object`` from the task config, e.g.
        ``"raw.orders"``).  For ``promote`` it becomes the final key/path under
        the target's storage base; for ``stream`` it is the destination table.
    capabilities:
        The target connector's ``capabilities()`` dict (must carry the §4
        extension keys ``file_interface`` / ``bulk_load_from`` / ``stream_load``).
    promote:
        ``promote(rel_path, object_name) -> str`` — server-side copy of a staged
        object into the final destination, returning the final URI.  Required
        when the target is object-storage class.
    stream:
        ``stream(record_batches, object_name) -> int`` — read staged batches and
        load them into the target (Postgres ``COPY FROM STDIN`` / batched
        INSERT), returning the row count.  The UNIVERSAL fallback.
    """

    object_name: str
    capabilities: dict[str, Any] = field(default_factory=dict)
    promote: "Callable[[str, str], str] | None" = None
    stream: "Callable[[Iterator[Any], str], int] | None" = None


# ---------------------------------------------------------------------------
# Strategy selection
# ---------------------------------------------------------------------------


def choose_strategy(capabilities: dict[str, Any], staging_scheme: str) -> str:
    """Return ``"promote"`` | ``"bulk"`` | ``"stream"`` for *capabilities*.

    See the module docstring for the decision table.  ``staging_scheme`` is the
    scheme of the staging store (``s3`` / ``gcs`` / ``az`` / ``file``); it gates
    the (phase-4) bulk path's cross-cloud-mismatch fallback to ``stream``.
    """
    caps = capabilities or {}
    if caps.get("file_interface"):
        return "promote"
    if _BULK_LOAD_ENABLED:
        bulk_from = caps.get("bulk_load_from") or []
        if staging_scheme in bulk_from:
            return "bulk"
    # Universal fallback.  ``stream_load`` is advisory; we stream regardless so a
    # target that forgot to advertise the flag is still loadable.
    return "stream"


def staging_scheme(staging: "StagingArea") -> str:
    """Return the scheme of *staging*'s store (``s3`` / ``file`` / …)."""
    base = staging.base_uri
    return base.split("://", 1)[0].lower() if "://" in base else "file"


# ---------------------------------------------------------------------------
# Parquet batch reader (for the stream strategy)
# ---------------------------------------------------------------------------


def read_parquet_batches(
    data: bytes, batch_rows: int = 10_000
) -> "Iterator[Any]":
    """Yield ``pyarrow.RecordBatch`` chunks from Parquet *bytes*.

    Memory note: a staged object's bytes are already in memory at this point
    (the producer wrote them per-file), but we read them out of Parquet in
    bounded ``batch_rows`` record-batches so the stream loader never holds the
    full decoded table — it loads batch-by-batch into the target.
    """
    import pyarrow.parquet as pq  # noqa: PLC0415

    reader = pq.ParquetFile(io.BytesIO(data))
    yield from reader.iter_batches(batch_size=batch_rows)


# ---------------------------------------------------------------------------
# Load orchestration
# ---------------------------------------------------------------------------


def load_staged(
    staging: "StagingArea",
    manifest: "StagingManifest",
    target: LoadTarget,
    *,
    verify: bool = True,
    batch_rows: int = 10_000,
) -> dict[str, Any]:
    """Verify the manifest, choose a strategy, and load into *target*.

    Steps
    -----
    1. **Verify** the manifest (size + sha256) against the staged bytes BEFORE
       any promote/load — the trust gate (design §5).  ``verify=False`` is for
       tests that pre-verify.
    2. **Choose** the strategy from ``target.capabilities``.
    3. **Promote** (object-storage / managed-lake target) — server-side copy
       each staged object to its final destination; or **stream** (everything
       else) — read each staged Parquet object as bounded record-batches and
       load them into the target.

    Returns
    -------
    dict
        ``{strategy, rows_loaded, files_loaded, final_uris}``.
    """
    if verify:
        staging.verify(manifest)

    scheme = staging_scheme(staging)
    strategy = choose_strategy(target.capabilities, scheme)

    if strategy == "promote":
        return _promote(staging, manifest, target)
    if strategy == "bulk":  # pragma: no cover — phase 4 seam, never selected yet
        return _bulk_load(staging, manifest, target)
    return _stream(staging, manifest, target, batch_rows=batch_rows)


def _promote(
    staging: "StagingArea", manifest: "StagingManifest", target: LoadTarget
) -> dict[str, Any]:
    """Server-side copy each staged object into the target's final location."""
    if target.promote is None:
        raise RuntimeError(
            "promote strategy selected but target has no promote() callable "
            "(object-storage targets must supply one)."
        )
    final_uris: list[str] = []
    for entry in manifest.files:
        final_uris.append(target.promote(entry.path, target.object_name))
    return {
        "strategy": "promote",
        "rows_loaded": manifest.total_rows,
        "files_loaded": len(manifest.files),
        "final_uris": final_uris,
    }


def _stream(
    staging: "StagingArea",
    manifest: "StagingManifest",
    target: LoadTarget,
    *,
    batch_rows: int,
) -> dict[str, Any]:
    """Read staged Parquet and stream record-batches into the target.

    The worker reads each staged object's bytes, decodes them into bounded
    record-batches, and hands the batch iterator to ``target.stream`` (the
    Postgres path uses ``COPY FROM STDIN`` / batched INSERT under the hood —
    implemented by the handler's connector adapter, kept out of the loader so
    this layer is connector-agnostic and unit-testable).
    """
    if target.stream is None:
        raise RuntimeError(
            "stream strategy selected but target has no stream() callable "
            "(non-object-storage targets must supply one)."
        )

    def _all_batches() -> "Iterator[Any]":
        for entry in manifest.files:
            data = staging.read_bytes(entry.path)
            yield from read_parquet_batches(data, batch_rows=batch_rows)

    rows = target.stream(_all_batches(), target.object_name)
    return {
        "strategy": "stream",
        "rows_loaded": int(rows),
        "files_loaded": len(manifest.files),
        "final_uris": [],
    }


def _bulk_load(  # pragma: no cover — phase 4
    staging: "StagingArea", manifest: "StagingManifest", target: LoadTarget
) -> dict[str, Any]:
    """Per-warehouse bulk load (BigQuery load job, Snowflake COPY INTO, …).

    PHASE 4 SEAM — not implemented in phase 1.  ``choose_strategy`` never
    returns ``"bulk"`` while ``_BULK_LOAD_ENABLED`` is ``False``.
    """
    raise NotImplementedError(
        "bulk_load is a phase-4 feature; set loaders._BULK_LOAD_ENABLED and "
        "implement per-warehouse load jobs to enable it."
    )
