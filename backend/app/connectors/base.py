"""Abstract base class for Nubi connectors.

Each connector wraps one data source and declares its capabilities via the
``capabilities()`` method.  The planner queries capabilities to decide which
push-downs are safe; the executor calls ``execute()`` or ``execute_stream()``
to materialise the result.

Design contract (ROADMAP §3.1, §4.1)
--------------------------------------
- ``capabilities()`` returns the 7-flag dict from §4.1.
- ``execute(plan)`` returns a ``pyarrow.Table`` (batch / materialise path).
- ``execute_stream(plan)`` returns an ``Iterator[pyarrow.RecordBatch]``
  (streaming path for large result sets).
- Both methods receive a fully-baked ``PhysicalPlan``; they MUST NOT rewrite
  SQL or touch RLS logic — that is the planner's job.
- Connectors are stateless w.r.t. individual queries; connection pools may live
  in instance state.

Adding a new connector
-----------------------
1. Subclass ``Connector``.
2. Implement ``capabilities()``, ``execute()``, and ``execute_stream()``.
3. Register it in the connector registry (Wave M1-B).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, BinaryIO, Iterator

if TYPE_CHECKING:
    import pyarrow as pa

from app.connectors.plan import PhysicalPlan, QueryEstimate

# Ingestion capability-extension keys (design §2 + §4).  These are ADDITIVE to
# the strict 7-flag query contract and are exempt from the bool-only validation
# in ``validate_capabilities`` (``bulk_load_from`` is a list).  The loader agent
# reads them off ``capabilities()`` to choose a target strategy.
#
#   file_interface : bool        — connector exposes ``FileConnectorMixin``
#   bulk_load_from : list[str]   — staging schemes the target can bulk-load from
#                                  (subset of ["s3", "gcs", "az"]); [] = none
#   stream_load    : bool        — worker can stream staged batches into target
_CAPABILITY_EXTENSION_KEYS: frozenset[str] = frozenset(
    {"file_interface", "bulk_load_from", "stream_load"}
)


def file_capabilities(
    *,
    file_interface: bool = False,
    bulk_load_from: "list[str] | None" = None,
    stream_load: bool = False,
) -> dict[str, Any]:
    """Return the ingestion capability-extension fragment with safe defaults.

    Connectors merge the result into their ``capabilities()`` dict so the
    loader/ingestion layer can read ``file_interface`` / ``bulk_load_from`` /
    ``stream_load`` uniformly.  Defaults are intentionally conservative
    (no file interface, no bulk load, no streaming) so a connector that does
    not opt in is never mistaken for a usable ingestion target.
    """
    return {
        "file_interface": bool(file_interface),
        "bulk_load_from": list(bulk_load_from or []),
        "stream_load": bool(stream_load),
    }


class Connector(ABC):
    """Abstract base for all Nubi data-source connectors.

    Concrete subclasses must implement the three abstract methods below.
    All other methods have sensible defaults and need not be overridden.
    """

    # ------------------------------------------------------------------
    # Capability descriptor
    # ------------------------------------------------------------------

    @abstractmethod
    def capabilities(self) -> dict[str, bool]:
        """Return the capability flags for this connector.

        Returns
        -------
        dict[str, bool]
            A dict with exactly the following keys (all booleans):

            ``native_arrow``
                The connector can return data as Arrow IPC natively (e.g. via
                ADBC) without a Python-level row-by-row conversion.
            ``predicate_pushdown``
                The connector can push WHERE predicates down to the source,
                reducing the amount of data transferred.
            ``projection_pushdown``
                The connector can push column selection down to the source so
                that only requested columns are fetched.
            ``partition_pushdown``
                The connector can route queries to specific partitions/shards
                based on partition-key predicates.
            ``predicate_rls``
                The connector supports AST-level predicate injection for
                Row-Level Security (enforced inside the connector, never
                browser-side).
            ``column_masking``
                The connector can mask/redact column values before they leave
                the connector boundary (e.g. nullify PII columns for
                unauthorised callers).
            ``streaming_cdc``
                The connector can stream Change-Data-Capture events for
                real-time / live-dashboard use cases.

        All seven keys MUST be present.  The planner will raise a ``KeyError``
        if any are missing.

        Example
        -------
        ::

            def capabilities(self) -> dict[str, bool]:
                return {
                    "native_arrow": True,
                    "predicate_pushdown": True,
                    "projection_pushdown": True,
                    "partition_pushdown": False,
                    "predicate_rls": True,
                    "column_masking": False,
                    "streaming_cdc": False,
                }
        """

    # ------------------------------------------------------------------
    # Execution interface
    # ------------------------------------------------------------------

    @abstractmethod
    def execute(self, plan: PhysicalPlan) -> "pa.Table":
        """Execute *plan* and return the full result as a PyArrow Table.

        This is the batch/materialise path.  Use ``execute_stream()`` for
        large result sets that should not be loaded into memory at once.

        Parameters
        ----------
        plan:
            A fully-baked ``PhysicalPlan`` produced by the planner.  The SQL
            in ``plan.sql`` is ready to run verbatim; do NOT rewrite it here.

        Returns
        -------
        pyarrow.Table
            The query result.  Column names and types are determined by the
            source; the planner's ``projection`` field is already encoded in
            ``plan.sql``.

        Raises
        ------
        app.errors.AppError
            With an appropriate ``code`` and HTTP status if the query fails.
        """

    @abstractmethod
    def execute_stream(self, plan: PhysicalPlan) -> Iterator["pa.RecordBatch"]:
        """Execute *plan* and yield result data as a stream of RecordBatches.

        Use this for large result sets.  The caller is responsible for reading
        all batches and releasing resources (e.g. closing the ADBC cursor).

        Parameters
        ----------
        plan:
            A fully-baked ``PhysicalPlan`` produced by the planner.

        Yields
        ------
        pyarrow.RecordBatch
            One or more record batches making up the full result set.
            Batch size is implementation-defined.

        Raises
        ------
        app.errors.AppError
            With an appropriate ``code`` and HTTP status if the query fails.
        """

    # ------------------------------------------------------------------
    # Optional pre-run estimate
    # ------------------------------------------------------------------

    def estimate(self, plan: PhysicalPlan) -> "QueryEstimate | None":
        """Return a best-effort pre-run cost/scan estimate, or ``None``.

        This is a NON-abstract, default-``None`` capability signal: a connector
        that cannot dry-run/EXPLAIN simply inherits this and reports "estimate
        unsupported". Overriding connectors MUST estimate the already-baked
        ``plan.sql`` (which is RLS-rewritten) — never the caller's raw SQL — so
        the estimate can never reveal rows outside the caller's scope. Any
        engine error must be swallowed and reported as ``None`` rather than
        raised: an estimate is advisory and must never block a run.

        Note: this is deliberately NOT an 8th key in the strict ``capabilities()``
        dict (``validate_capabilities`` asserts exactly 7 keys); the default-None
        method is the backward-compatible "unsupported" signal.
        """
        return None

    # ------------------------------------------------------------------
    # Optional helper
    # ------------------------------------------------------------------

    def validate_capabilities(self) -> None:
        """Assert that ``capabilities()`` returns all required keys.

        Called at connector construction time so misconfigured connectors fail
        fast rather than surfacing a ``KeyError`` during a live query.

        Raises
        ------
        ValueError
            If any required capability key is missing or the value is not bool.
        """
        _REQUIRED: frozenset[str] = frozenset(
            {
                "native_arrow",
                "predicate_pushdown",
                "projection_pushdown",
                "partition_pushdown",
                "predicate_rls",
                "column_masking",
                "streaming_cdc",
            }
        )
        caps: dict[str, Any] = self.capabilities()
        missing = _REQUIRED - caps.keys()
        if missing:
            raise ValueError(
                f"{type(self).__name__}.capabilities() is missing keys: {sorted(missing)}"
            )
        # The ingestion file/loader extension (``file_interface``,
        # ``stream_load`` — bool; ``bulk_load_from`` — list[str]) is additive and
        # NOT part of the strict 7-flag query contract; exempt those keys from
        # the bool check so file-capable connectors stay backward-compatible.
        non_bool = {
            k: v
            for k, v in caps.items()
            if k not in _CAPABILITY_EXTENSION_KEYS and not isinstance(v, bool)
        }
        if non_bool:
            raise ValueError(
                f"{type(self).__name__}.capabilities() has non-bool values: {non_bool}"
            )


# ---------------------------------------------------------------------------
# File interface (ingestion §2) — additive, parallel to the query interface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileStat:
    """A single file/object listing entry returned by ``list_files``.

    Fields
    ------
    path:
        The connector-relative path/key of the file (e.g.
        ``"outbound/orders-2024.csv"``).  This is the identifier passed back to
        :meth:`FileConnectorMixin.open` / ``move`` / ``delete``.  Together with
        *mtime* it feeds the ingestion watermark: the ``filename`` incremental
        strategy orders files lexicographically by *path*; the ``mtime``
        strategy ingests files whose *mtime* is newer than the stored mark.
    size:
        Size in bytes.  ``0`` when the backend cannot report a size cheaply.
    mtime:
        Last-modified timestamp (timezone-aware UTC where the backend supplies
        one), or ``None`` when unknown.  Feeds the ``mtime`` watermark strategy.
    etag:
        Optional opaque content tag (S3/GCS/Azure ETag, or a local
        ``size:mtime`` surrogate).  Used for change detection / dedupe; absent
        on backends that do not expose one.

    Notes
    -----
    The shape mirrors the ingestion design contract
    (``FileStat = {path, size, mtime, etag?}``).  Producers MUST set *path* and
    *size*; *mtime* and *etag* are best-effort.
    """

    path: str
    size: int
    mtime: datetime | None = None
    etag: str | None = None


class FileConnectorMixin:
    """File-interface contract for connectors that expose objects/files.

    A connector that mixes this in advertises ``file_interface: True`` from
    ``capabilities()`` and is usable as an ingestion *source* (and, for
    object-storage targets, a *promote* destination).  It is orthogonal to the
    SQL query interface: a connector may implement the query interface only,
    the file interface only (``sftp`` / ``ftp``), or both (``duckdb_storage``).

    Only :meth:`list_files` and :meth:`open` are required.  :meth:`move` and
    :meth:`delete` are optional (used by ``post_action`` archive/cleanup); the
    defaults raise ``NotImplementedError`` so a handler can feature-detect with
    ``hasattr`` is unnecessary — callers should instead check
    ``capabilities()["file_interface"]`` and handle the optional ops by trying
    them.

    All methods are synchronous; the caller is expected to run them inside a
    thread executor when invoked from async code (consistent with the
    ``StorageClient`` abstraction).
    """

    def list_files(self, pattern: str, since: datetime | None = None) -> list["FileStat"]:
        """List files matching *pattern*, optionally only those newer than *since*.

        Parameters
        ----------
        pattern:
            A glob-style pattern relative to the connector's root
            (e.g. ``"outbound/*.csv"``).  ``"*"`` / ``""`` lists everything.
        since:
            When given, only files with ``mtime > since`` are returned (the
            ``mtime`` watermark strategy).  Files with an unknown ``mtime``
            (``None``) are always included so they are not silently skipped.

        Returns
        -------
        list[FileStat]
            Matching entries, sorted by ``path`` for stable lexicographic
            (``filename`` watermark) ordering.
        """
        raise NotImplementedError

    def open(self, path: str) -> BinaryIO:
        """Open *path* for streaming binary read.

        The caller owns the returned handle and must close it (use as a
        context manager where possible).
        """
        raise NotImplementedError

    def move(self, src: str, dst: str) -> None:  # optional — archive-after-ingest
        """Move/rename *src* to *dst* (``post_action: move:<dir>``).

        Optional.  Backends that cannot rename in place raise
        ``NotImplementedError``.
        """
        raise NotImplementedError

    def delete(self, path: str) -> None:  # optional
        """Delete *path* (``post_action: delete``).

        Optional.  Backends that do not allow deletion raise
        ``NotImplementedError``.
        """
        raise NotImplementedError
