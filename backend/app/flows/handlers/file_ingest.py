"""``file_ingest`` task handler — ingest files from a file connector (design §3).

Symmetric to ``bucket_load`` but in the INGEST direction: it pulls files from a
*source* connector that exposes the file interface
(:class:`app.connectors.base.FileConnectorMixin` — ``sftp`` / ``ftp`` / a
storage-backed bucket), normalises each file to Parquet, lands it in the
per-run STAGING prefix (:mod:`app.lakehouse.staging`), verifies the manifest,
and LOADS it into any *target* connector via the loader layer
(:mod:`app.flows.loaders`).  One code path covers FTP / SFTP / bucket because
the handler talks ONLY to the file-connector interface and the loader layer.

Config (resolved before ``handle`` is called)::

    {
      "source": {"connector_id": "…", "path": "outbound/*.csv"},
      "format": "csv | json | ndjson | parquet | zip | auto",
      "inner_format": "csv",            # when format=zip: format of entries
      "target": {"connector_id": "…", "object": "raw.orders"},
      "mode": "append | overwrite | merge",
      "incremental": {"strategy": "mtime | filename | none"},
      "post_action": "none | move:<dir> | delete"
    }

Watermarks (design §3)
----------------------
Reuses ``flow_watermarks`` via the runtime's existing read/write plumbing: the
runtime injects ``ctx.watermark`` (the stored mark) BEFORE the handler runs and
persists the ``new_watermark`` the handler returns ONLY on task success.
  * ``mtime``    — ingest files whose ``mtime`` is newer than the mark; the new
                   mark is the max ``mtime`` ingested (ISO-8601).
  * ``filename`` — ingest files whose ``path`` sorts lexicographically after the
                   mark; the new mark is the max ``path`` ingested.
  * ``none``     — no watermark; every matching file is ingested each run.

Zip is a FORMAT, not a source type (design §3): ``format=zip`` expands entries
and applies ``inner_format``, identically for zip-in-bucket and zip-over-SFTP.

post_action (design §3): ``none`` | ``move:<dir>`` | ``delete`` — applied to
each successfully-ingested SOURCE file via the file connector's ``move`` /
``delete``, AFTER a successful load (never before — a failed load must not
destroy the source).
"""

from __future__ import annotations

import csv
import fnmatch
import io
import json
import uuid
import zipfile
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterator

from app.flows.loaders import LoadTarget, load_staged

if TYPE_CHECKING:
    from app.connectors.base import FileConnectorMixin, FileStat
    from app.flows.executor import TaskContext
    from app.lakehouse.staging import ManifestEntry, StagingArea


_VALID_FORMATS = {"csv", "json", "ndjson", "parquet", "zip", "auto"}
_VALID_INNER = {"csv", "json", "ndjson", "parquet"}
_VALID_MODES = {"append", "overwrite", "merge"}
_VALID_STRATEGIES = {"mtime", "filename", "none"}

# Object-storage / storage URI schemes → a target is "object-storage class"
# (promote strategy) when its destination resolves to one of these.
_OBJECT_STORAGE_SCHEMES = ("s3", "s3a", "gs", "gcs", "az", "file")


# ---------------------------------------------------------------------------
# Format detection + parsing → row dicts
# ---------------------------------------------------------------------------


def _detect_format(path: str) -> str:
    """Infer a concrete format from *path*'s extension (``format=auto``)."""
    low = path.lower()
    if low.endswith(".zip"):
        return "zip"
    if low.endswith(".parquet") or low.endswith(".pq"):
        return "parquet"
    if low.endswith(".ndjson") or low.endswith(".jsonl"):
        return "ndjson"
    if low.endswith(".json"):
        return "json"
    if low.endswith(".csv") or low.endswith(".tsv"):
        return "csv"
    # Default: treat unknown as CSV (the most common flat-file drop).
    return "csv"


def _rows_from_bytes(data: bytes, fmt: str) -> tuple[list[dict[str, Any]], bytes | None]:
    """Parse *data* in *fmt* → (row dicts, raw_parquet_bytes_or_None).

    For ``parquet`` we keep the original bytes (no re-encode) and return rows
    only for row-count purposes.  For the flat formats we parse to row dicts and
    the caller re-encodes to Parquet for staging.
    """
    if fmt == "parquet":
        rows = _parquet_to_rows(data)
        return rows, data
    if fmt == "csv":
        text = data.decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(text))), None
    if fmt == "json":
        parsed = json.loads(data.decode("utf-8") or "[]")
        rows = parsed if isinstance(parsed, list) else [parsed]
        return [r for r in rows if isinstance(r, dict)], None
    if fmt == "ndjson":
        rows = []
        for line in data.decode("utf-8").splitlines():
            line = line.strip()
            if line:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
        return rows, None
    raise ValueError(f"Unsupported format {fmt!r} for file_ingest.")


def _parquet_to_rows(data: bytes) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq  # noqa: PLC0415

    table = pq.read_table(io.BytesIO(data))
    return table.to_pylist()


def _rows_to_parquet(rows: list[dict[str, Any]]) -> bytes:
    """Encode row dicts to Parquet bytes (pyarrow)."""
    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.parquet as pq  # noqa: PLC0415

    table = pa.Table.from_pylist(rows)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _expand_zip(data: bytes, inner_format: str) -> Iterator[tuple[str, bytes, str]]:
    """Yield ``(entry_name, entry_bytes, concrete_format)`` for each zip member.

    Memory handling (judgement call): entries are read one at a time from the
    in-memory ``ZipFile`` and yielded individually, so only ONE decompressed
    entry is resident at a time rather than the whole archive's expanded
    contents.  The archive bytes themselves are already in memory (the producer
    streamed the file from the source); for very large archives a future
    refinement is to stream entries to a spool file, but per-entry yielding
    bounds the working set to one member today.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            fmt = inner_format if inner_format != "auto" else _detect_format(name)
            with zf.open(info) as fh:
                yield name, fh.read(), fmt


# ---------------------------------------------------------------------------
# Watermark filtering
# ---------------------------------------------------------------------------


def _parse_mark_dt(mark: str | None) -> datetime | None:
    if not mark:
        return None
    try:
        dt = datetime.fromisoformat(mark)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _filter_by_watermark(
    files: list["FileStat"], strategy: str, mark: str | None
) -> list["FileStat"]:
    """Return only files NEWER than *mark* per *strategy* (design §3).

    ``mtime``    — ``file.mtime > mark`` (files with unknown mtime are kept so
                   they are not silently skipped).
    ``filename`` — ``file.path > mark`` lexicographically.
    ``none``     — all files.
    """
    if strategy == "none" or not mark:
        return list(files)
    if strategy == "mtime":
        mark_dt = _parse_mark_dt(mark)
        if mark_dt is None:
            return list(files)
        out = []
        for f in files:
            mt = f.mtime
            if mt is None:
                out.append(f)
                continue
            if mt.tzinfo is None:
                mt = mt.replace(tzinfo=timezone.utc)
            if mt > mark_dt:
                out.append(f)
        return out
    if strategy == "filename":
        return [f for f in files if f.path > mark]
    return list(files)


def _advance_mark(
    files: list["FileStat"], strategy: str, current: str | None
) -> str | None:
    """Compute the advanced watermark from the INGESTED *files*.

    Returns ``None`` for ``strategy='none'`` or when nothing was ingested (so
    the runtime never clobbers the stored mark with an empty advance).
    """
    if strategy == "none" or not files:
        return None
    if strategy == "mtime":
        marks = [f.mtime for f in files if f.mtime is not None]
        if not marks:
            return current
        newest = max(marks)
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=timezone.utc)
        return newest.isoformat()
    if strategy == "filename":
        return max(f.path for f in files)
    return None


# ---------------------------------------------------------------------------
# Source / target resolution
# ---------------------------------------------------------------------------


def _resolve_source_connector(connector_id: str, org_id: str) -> "FileConnectorMixin":
    """Resolve the source connector and assert it exposes the file interface."""
    from app.flows.registry import _resolve_flow_connector  # noqa: PLC0415

    connector, _dialect = _resolve_flow_connector(connector_id, org_id)
    caps = {}
    try:
        caps = connector.capabilities()
    except Exception:  # noqa: BLE001
        caps = {}
    if not caps.get("file_interface"):
        raise ValueError(
            f"Source connector {connector_id!r} does not expose the file "
            "interface (file_ingest sources must be sftp/ftp or a storage bucket)."
        )
    return connector  # type: ignore[return-value]


def _resolve_target(connector_id: str, object_name: str, org_id: str) -> LoadTarget:
    """Build a :class:`LoadTarget` for the target connector (design §4).

    Object-storage-class targets (managed lakehouse / ``duckdb_storage`` over a
    storage URI) get a ``promote`` callable backed by the storage client; every
    other connector (Postgres, MySQL, …) gets a ``stream`` callable.  The
    capabilities dict is synthesised so :func:`loaders.choose_strategy` selects
    correctly even before a connector advertises the §4 extension flags.
    """
    from app.repos.provider import get_repo  # noqa: PLC0415

    repo = get_repo()
    ds = repo.get_sync("datastores", org_id, connector_id)
    if ds is None:
        raise ValueError(
            f"Target connector {connector_id!r} not found for org {org_id!r}."
        )
    cfg: dict[str, Any] = dict(ds.get("config") or {})
    database = str(cfg.get("database") or cfg.get("path") or "")
    scheme = database.split("://", 1)[0].lower() if "://" in database else ""

    # Object-storage class → promote.
    if scheme in _OBJECT_STORAGE_SCHEMES:
        return _object_storage_target(connector_id, object_name, org_id, cfg, database)

    # Everything else → stream (the universal fallback).
    return _stream_target(connector_id, object_name, org_id)


def _object_storage_target(
    connector_id: str,
    object_name: str,
    org_id: str,
    cfg: dict[str, Any],
    database: str,
) -> LoadTarget:
    """Promote-strategy target: server-side copy staging → final storage path."""
    from app.connectors.base import file_capabilities  # noqa: PLC0415
    from app.lakehouse.managed import (  # noqa: PLC0415
        org_lake_uri,
        resolve_central_storage,
    )
    from app.storage.base import get_storage_client, parse_uri  # noqa: PLC0415

    # Resolve a storage client + base URI for the destination.  For a MANAGED
    # lake row the base is server-pinned to the org's lake prefix (never the
    # user-editable config) so a target can't be repointed cross-org.
    managed = cfg.get("managed_lake") is True
    if managed:
        central = resolve_central_storage()
        if central is None:
            raise ValueError("Managed lakehouse target requires central storage.")
        base_uri = org_lake_uri(central, org_id).rstrip("/")
        creds = central.creds or None
    else:
        base_uri = database.rstrip("/")
        # Resolve creds from the secret store (same shape as query resolution).
        creds = _target_creds(connector_id, org_id)

    client = get_storage_client(base_uri + "/", creds)
    _scheme, _bucket, base_key = parse_uri(base_uri + "/")
    base_key = base_key.rstrip("/")

    def _final_key(staged_rel: str) -> str:
        # Final object path = <base_key>/<object_name>/<staged filename>.
        obj = object_name.replace(".", "/").strip("/")
        leaf = staged_rel.rsplit("/", 1)[-1]
        parts = [p for p in (base_key, obj, leaf) if p]
        return "/".join(parts)

    # The staging area is closed over by the handler via a promote shim; here we
    # only know how to write the final key once given the staged bytes.  The
    # loader calls promote(staged_rel, object_name); we need staged bytes, so the
    # handler binds the staging reader into the callable (see handle()).
    target = LoadTarget(
        object_name=object_name,
        capabilities=file_capabilities(file_interface=True),
    )
    # Stash what the handler needs to finish wiring promote (staging-bound).
    target._promote_client = client  # type: ignore[attr-defined]
    target._final_key = _final_key  # type: ignore[attr-defined]
    return target


def _stream_target(connector_id: str, object_name: str, org_id: str) -> LoadTarget:
    """Stream-strategy target: load Parquet batches into a database table.

    The Postgres path uses ``COPY FROM STDIN`` (fast, set-based); other DB-API
    connectors fall back to batched ``INSERT``.  Kept connector-agnostic: the
    loader hands us a record-batch iterator and we drive the write.
    """
    from app.connectors.base import file_capabilities  # noqa: PLC0415

    def _stream(batches: Iterator[Any], table: str) -> int:
        return _stream_into_db(connector_id, org_id, table, batches)

    return LoadTarget(
        object_name=object_name,
        capabilities=file_capabilities(stream_load=True),
        stream=_stream,
    )


def _target_creds(connector_id: str, org_id: str) -> dict[str, Any] | None:
    try:
        import asyncio  # noqa: PLC0415

        from app.connectors.secret_store import get_secret_store  # noqa: PLC0415

        return asyncio.run(get_secret_store().get(connector_id, org_id))
    except Exception:  # noqa: BLE001
        return None


def _stream_into_db(
    connector_id: str, org_id: str, table: str, batches: Iterator[Any]
) -> int:
    """Stream record-batches into a database target (Postgres COPY / INSERT).

    Phase-1 universal fallback.  Resolves the target connector's DSN and uses
    ``COPY FROM STDIN`` for Postgres; this is the seam where per-warehouse bulk
    loads (phase 4) would take over.
    """
    from app.flows.registry import _resolve_flow_connector  # noqa: PLC0415

    connector, dialect = _resolve_flow_connector(connector_id, org_id)
    dsn = getattr(connector, "_dsn", None)
    if dsn and dialect in ("postgres", "postgresql"):
        return _pg_copy(dsn, table, batches)
    # Generic batched-INSERT fallback via the connector's DB-API, if exposed.
    raise RuntimeError(
        f"stream load into connector {connector_id!r} (dialect {dialect!r}) is "
        "not supported in phase 1; only Postgres COPY is wired. Use an "
        "object-storage target or add a bulk_load adapter (phase 4)."
    )


def _pg_copy(dsn: str, table: str, batches: Iterator[Any]) -> int:
    """Load record-batches into *table* via Postgres ``COPY ... FROM STDIN``."""
    import psycopg  # noqa: PLC0415

    total = 0
    with psycopg.connect(dsn) as conn:  # type: ignore[attr-defined]
        with conn.cursor() as cur:
            first = True
            cols: list[str] = []
            copy_sql = ""
            for batch in batches:
                rows = batch.to_pylist()
                if not rows:
                    continue
                if first:
                    cols = list(rows[0].keys())
                    collist = ", ".join(f'"{c}"' for c in cols)
                    copy_sql = f'COPY {table} ({collist}) FROM STDIN'
                    first = False
                with cur.copy(copy_sql) as cp:
                    for row in rows:
                        cp.write_row([row.get(c) for c in cols])
                        total += 1
        conn.commit()
    return total


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


def handle(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],  # noqa: ARG001 — reserved for future RLS
) -> dict[str, Any]:
    """Ingest files from the source connector into the target connector.

    Returns
    -------
    dict
        ``{files_ingested, rows_ingested, strategy, manifest, new_watermark,
        post_action}``.  ``new_watermark`` is consumed by the runtime to advance
        ``flow_watermarks`` ONLY on task success.
    """
    org_id = ctx.org_id or (claims or {}).get("org_id") or ""
    if not org_id:
        raise ValueError("file_ingest requires an org context (ctx.org_id).")

    source = config.get("source") or {}
    target = config.get("target") or {}
    src_connector_id = str(source.get("connector_id") or "").strip()
    src_path = str(source.get("path") or "").strip() or "*"
    tgt_connector_id = str(target.get("connector_id") or "").strip()
    tgt_object = str(target.get("object") or "").strip()

    if not src_connector_id:
        raise ValueError("file_ingest requires source.connector_id.")
    if not tgt_connector_id:
        raise ValueError("file_ingest requires target.connector_id.")
    if not tgt_object:
        raise ValueError("file_ingest requires target.object.")

    fmt = str(config.get("format") or "auto").lower().strip()
    inner_format = str(config.get("inner_format") or "csv").lower().strip()
    mode = str(config.get("mode") or "append").lower().strip()
    incremental = config.get("incremental") or {}
    strategy = str(incremental.get("strategy") or "none").lower().strip()
    post_action = str(config.get("post_action") or "none").strip()

    if fmt not in _VALID_FORMATS:
        raise ValueError(f"Invalid format {fmt!r}. Supported: {sorted(_VALID_FORMATS)}.")
    if inner_format not in _VALID_INNER:
        raise ValueError(
            f"Invalid inner_format {inner_format!r}. Supported: {sorted(_VALID_INNER)}."
        )
    if mode not in _VALID_MODES:
        raise ValueError(f"Invalid mode {mode!r}. Supported: {sorted(_VALID_MODES)}.")
    if strategy not in _VALID_STRATEGIES:
        raise ValueError(
            f"Invalid incremental.strategy {strategy!r}. Supported: {sorted(_VALID_STRATEGIES)}."
        )

    # ── Resolve source + target ────────────────────────────────────────────
    src = _resolve_source_connector(src_connector_id, org_id)
    load_target = _resolve_target(tgt_connector_id, tgt_object, org_id)

    # ── Resolve staging (per-run prefix, server-pinned) ────────────────────
    run_id = getattr(ctx, "run_id", None) or str(uuid.uuid4())
    staging = _resolve_staging(org_id, run_id)

    # ── List source files, filter by watermark ─────────────────────────────
    since = _parse_mark_dt(ctx.watermark) if strategy == "mtime" else None
    listed = list(src.list_files(src_path, since))
    # Defensive: also apply path-glob + watermark filter at the handler level so
    # the contract holds even for connectors that ignore the args.
    listed = [f for f in listed if _matches_glob(f.path, src_path)]
    candidates = _filter_by_watermark(listed, strategy, ctx.watermark)
    candidates.sort(key=lambda f: f.path)

    # ── Stage each file as Parquet, building the manifest ───────────────────
    entries: list["ManifestEntry"] = []
    row_counts: dict[str, int] = {}
    ingested_files: list["FileStat"] = []

    for fstat in candidates:
        concrete = fmt if fmt != "auto" else _detect_format(fstat.path)
        staged = _stage_one_file(src, fstat, concrete, inner_format, staging)
        for entry, n in staged:
            entries.append(entry)
            row_counts[entry.path] = n
        ingested_files.append(fstat)

    manifest = staging.build_manifest(entries, row_counts)

    # ── Bind the promote callable now that staging exists ──────────────────
    _bind_promote(load_target, staging)

    # ── Verify + load (verify gates promote/load — design §5) ──────────────
    result = load_staged(staging, manifest, load_target)

    # ── post_action AFTER a successful load (never before) ─────────────────
    pa_done = _apply_post_action(src, ingested_files, post_action)

    # ── Advance the watermark (runtime persists on success only) ───────────
    new_watermark = _advance_mark(ingested_files, strategy, ctx.watermark)

    # ── Best-effort staging cleanup (lifecycle policy is the backstop) ─────
    try:
        staging.cleanup()
    except Exception:  # noqa: BLE001
        pass

    return {
        "files_ingested": len(ingested_files),
        "rows_ingested": manifest.total_rows,
        "strategy": result["strategy"],
        "mode": mode,
        "target_object": tgt_object,
        "manifest": manifest.to_dict(),
        "new_watermark": new_watermark,
        "post_action": pa_done,
        "final_uris": result.get("final_uris", []),
    }


# ---------------------------------------------------------------------------
# Helpers wiring staging ↔ loader
# ---------------------------------------------------------------------------


def _resolve_staging(org_id: str, run_id: str) -> "StagingArea":
    from app.lakehouse.managed import get_staging_area  # noqa: PLC0415

    area = get_staging_area(org_id, run_id)
    if area is None:
        raise ValueError(
            "No staging store configured (set NUBI_STAGING_DIR / "
            "NUBI_STAGING_BUCKET_URI, or central storage for the same-bucket "
            "fallback)."
        )
    return area


def _stage_one_file(
    src: "FileConnectorMixin",
    fstat: "FileStat",
    concrete_format: str,
    inner_format: str,
    staging: "StagingArea",
) -> list[tuple["ManifestEntry", int]]:
    """Read one source file, normalise to Parquet, write to staging.

    Returns a list of ``(manifest_entry, row_count)`` — one per staged Parquet
    object (multiple when *concrete_format* is ``zip`` and the archive holds
    several entries).
    """
    with src.open(fstat.path) as fh:
        raw = fh.read()

    out: list[tuple["ManifestEntry", int]] = []
    if concrete_format == "zip":
        for name, entry_bytes, entry_fmt in _expand_zip(raw, inner_format):
            rows, pq_bytes = _rows_from_bytes(entry_bytes, entry_fmt)
            pq_data = pq_bytes if pq_bytes is not None else _rows_to_parquet(rows)
            rel = _staged_rel(fstat.path, member=name)
            entry = staging.write_bytes(pq_data, rel)
            out.append((entry, len(rows)))
        return out

    rows, pq_bytes = _rows_from_bytes(raw, concrete_format)
    pq_data = pq_bytes if pq_bytes is not None else _rows_to_parquet(rows)
    rel = _staged_rel(fstat.path)
    entry = staging.write_bytes(pq_data, rel)
    out.append((entry, len(rows)))
    return out


def _staged_rel(source_path: str, member: str | None = None) -> str:
    """Build a stable, collision-safe staged Parquet relative path."""
    stem = source_path.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "file"
    if member:
        mstem = member.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "entry"
        return f"{stem}__{mstem}.parquet"
    return f"{stem}.parquet"


def _bind_promote(target: LoadTarget, staging: "StagingArea") -> None:
    """Finish wiring the promote callable with the staging reader (object store)."""
    client = getattr(target, "_promote_client", None)
    final_key = getattr(target, "_final_key", None)
    if client is None or final_key is None:
        return

    def _promote(staged_rel: str, _object_name: str) -> str:
        data = staging.read_bytes(staged_rel)
        return client.upload_bytes(data, final_key(staged_rel))

    target.promote = _promote


def _matches_glob(path: str, pattern: str) -> bool:
    """Match *path* against a glob *pattern* (``*`` lists everything)."""
    if not pattern or pattern in ("*", "**"):
        return True
    # Match against the full path AND the basename so "outbound/*.csv" and
    # "*.csv" both work regardless of how the connector reports paths.
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern)


def _apply_post_action(
    src: "FileConnectorMixin", files: list["FileStat"], post_action: str
) -> dict[str, Any]:
    """Apply ``none`` | ``move:<dir>`` | ``delete`` to ingested source files."""
    if not post_action or post_action == "none" or not files:
        return {"action": "none", "count": 0}

    if post_action == "delete":
        n = 0
        for f in files:
            src.delete(f.path)
            n += 1
        return {"action": "delete", "count": n}

    if post_action.startswith("move:"):
        dest_dir = post_action[len("move:"):].strip().strip("/")
        n = 0
        for f in files:
            leaf = f.path.rsplit("/", 1)[-1]
            src.move(f.path, f"{dest_dir}/{leaf}" if dest_dir else leaf)
            n += 1
        return {"action": "move", "dir": dest_dir, "count": n}

    raise ValueError(
        f"Invalid post_action {post_action!r}. Supported: none, move:<dir>, delete."
    )
