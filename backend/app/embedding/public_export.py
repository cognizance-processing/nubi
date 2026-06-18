"""UNSAFE public/CDN static export of a board (Mode 3b).

.. danger::

    **THIS PRODUCES A NO-AUTH, NO-EXPIRY PUBLIC ARTIFACT.**

    A public export writes a self-contained static HTML file that renders a
    board's spec and loads the **Mode 3a snapshot sidecar** (a ``.duckdb`` file)
    from a *public* URL, querying it client-side with DuckDB-WASM.  The data is
    **frozen with the exporter's RLS view at export time** and is then exposed to
    **ANYONE who has the URL** — there is no authentication, no expiry, and no
    per-viewer security.  Treat the bytes you export as fully public.

This module is the worker behind ``POST /boards/{id}/export/public``.  It does
**not** re-implement data collection — it reuses
:func:`app.embedding.snapshot.create_snapshot` (or an existing snapshot) so the
public HTML simply points at the same frozen sidecar artifact.

Gating (UNSAFE — OFF by default)
--------------------------------
A public export only runs when BOTH interlocks are satisfied:

1. The deployment-wide kill switch
   :data:`app.config.Settings.ALLOW_UNSAFE_PUBLIC_EXPORTS` is ``True`` (it
   defaults to ``False``, so the whole capability is off until an operator opts
   in for the deployment), AND
2. The caller's org holds the ``public_exports`` feature gate
   (:func:`app.features.feature_enabled`) — a per-org opt-in.

If either is off the export is refused with
``AppError("public_exports_disabled", …, 403)`` and **nothing is written and no
artifact is produced**.

Audit
-----
Every successful export writes an audit entry recording **who** (user + org),
**when**, **which board**, the snapshot/artifact it froze, and the captured
policy fingerprint.  Entries are persisted **org-scoped on the board row** under
``board.config["public_export_audit"]`` (the same durable, RLS-respecting
persistence point Mode 3a uses for snapshot descriptors — no new resource table)
and are *also* emitted to the structured logger at WARNING level so an UNSAFE
export is loud in the operational logs.

Public API
----------
export_public(board_id, org_id, claims, repo, *, exported_by,
              snapshot_id=None, public_base_url=None, base_uri=None) -> dict
    Gate-check, (re)use a snapshot, render the static HTML, write the audit
    entry, and return the export descriptor (HTML URI + snapshot reference).
"""

from __future__ import annotations

import html
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.embedding.snapshot import (
    _materialize_base_uri,
    create_snapshot,
    get_snapshot,
)
from app.errors import AppError
from app.features import feature_enabled
from app.repos.provider import Repo

logger = logging.getLogger(__name__)

# Per-org feature gate name (see app.features).  Non-commercial by default, so
# in an OSS build feature_enabled() returns True for it UNLESS an operator/EE
# registers a stricter checker — which is why the deployment-wide
# ALLOW_UNSAFE_PUBLIC_EXPORTS kill switch (defaulting OFF) is the primary guard.
PUBLIC_EXPORTS_FEATURE = "public_exports"

# CDN-pinned DuckDB-WASM bundle the generated page loads (no build step needed).
_DUCKDB_WASM_CDN = "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.28.0/+esm"


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def _assert_public_exports_allowed(org_id: str) -> None:
    """Raise unless BOTH the deployment switch and the org feature gate are on.

    UNSAFE capability — see the module docstring.  The deployment-wide
    :data:`ALLOW_UNSAFE_PUBLIC_EXPORTS` defaults to ``False`` so public exports
    are off until an operator explicitly opts the deployment in; the per-org
    ``public_exports`` feature gate additionally scopes it to opted-in orgs.

    Raises
    ------
    AppError("public_exports_disabled", 403)
        When either interlock is off.  Nothing is written.
    """
    if not get_settings().ALLOW_UNSAFE_PUBLIC_EXPORTS:
        raise AppError(
            "public_exports_disabled",
            "UNSAFE public/CDN exports are disabled for this deployment "
            "(ALLOW_UNSAFE_PUBLIC_EXPORTS is off).",
            403,
        )
    if not feature_enabled(PUBLIC_EXPORTS_FEATURE):
        raise AppError(
            "public_exports_disabled",
            f"UNSAFE public/CDN exports are not enabled for org {org_id!r}.",
            403,
        )


# ---------------------------------------------------------------------------
# Artifact URIs / writer
# ---------------------------------------------------------------------------


def public_export_uri(
    board_id: str,
    org_id: str,
    export_id: str,
    *,
    base_uri: str | None = None,
) -> str:
    """Compute the deterministic static-HTML artifact URI for an export.

    Mirrors :func:`app.embedding.snapshot.snapshot_artifact_uri` so HTML lands
    alongside the snapshot sidecars, org-namespaced so artifacts never collide::

        <base>/snapshots/<org_id>/<board_id>/public-<export_id>.html
    """
    base = base_uri.rstrip("/") if base_uri else _materialize_base_uri()
    return f"{base}/{org_id}/{board_id}/public-{export_id}.html"


def _public_url_for(artifact_uri: str, public_base_url: str | None) -> str:
    """Best-effort map a storage artifact URI to the public URL viewers hit.

    When *public_base_url* (the CDN/bucket public origin) is given, the snapshot
    sidecar's basename is appended to it; otherwise the raw storage URI is used
    (fine for local dev where ``file://`` is served directly).  This is only the
    URL embedded in the generated HTML — it does not move bytes.
    """
    if not public_base_url:
        return artifact_uri
    return public_base_url.rstrip("/") + "/" + os.path.basename(artifact_uri)


def _write_html(html_uri: str, document: str) -> None:
    """Write the generated HTML to *html_uri* (local in place, else upload).

    Reuses the exact local-vs-cloud split of
    :func:`app.embedding.snapshot._write_sidecar`: ``file://``/local paths are
    written directly; ``s3://`` (and other cloud) destinations go through the
    ``app.storage`` client.
    """
    scheme = html_uri.split("://", 1)[0] if "://" in html_uri else ""
    is_local = scheme in ("", "file")
    payload = document.encode("utf-8")

    if is_local:
        local_path = html_uri[len("file://"):] if scheme == "file" else html_uri
        parent = os.path.dirname(local_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(local_path, "wb") as fh:
            fh.write(payload)
        return

    from app.storage.base import get_storage_client, parse_uri  # noqa: PLC0415

    _scheme, _bucket, key = parse_uri(html_uri)
    client = get_storage_client(html_uri)
    client.upload_bytes(payload, key)


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

# Loud, machine-greppable UNSAFE marker baked into every generated artifact.
_UNSAFE_NOTICE = (
    "UNSAFE PUBLIC EXPORT — this page and its data are PUBLIC. The data is "
    "frozen with the exporter's access (RLS) view and is exposed to ANYONE with "
    "this URL: no authentication, no expiry, no per-viewer security."
)


def _render_html(
    *,
    board_title: str,
    spec: dict[str, Any],
    snapshot_public_url: str,
    snapshot_descriptor: dict[str, Any],
    exported_at: str,
) -> str:
    """Render the self-contained static HTML for a public export.

    The page embeds the board *spec* and the **public URL of the Mode 3a
    snapshot sidecar**, then loads DuckDB-WASM from a CDN to read the frozen
    ``widget_<id>`` tables entirely in the browser — no live backend.  A loud
    UNSAFE banner is rendered at the top of the body and repeated as an HTML
    comment / ``<meta>`` tag so the warning survives view-source.
    """
    safe_title = html.escape(board_title or "Dashboard")
    spec_json = json.dumps(spec or {"widgets": []}, default=str)
    manifest = snapshot_descriptor.get("artifact", {}).get("tables", [])
    manifest_json = json.dumps(manifest, default=str)
    snap_url = html.escape(snapshot_public_url, quote=True)
    fingerprint = html.escape(str(snapshot_descriptor.get("policy_fingerprint") or "none"))

    return f"""<!doctype html>
<!-- {_UNSAFE_NOTICE} -->
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="nubi:unsafe-public-export" content="{html.escape(_UNSAFE_NOTICE, quote=True)}" />
<meta name="nubi:policy-fingerprint" content="{fingerprint}" />
<title>{safe_title} — frozen public snapshot</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 0; color: #111; }}
  .nubi-unsafe-banner {{
    background: #b00020; color: #fff; padding: 12px 16px; font-weight: 700;
    font-size: 14px; line-height: 1.4;
  }}
  main {{ padding: 16px; }}
  .nubi-meta {{ color: #666; font-size: 12px; margin-bottom: 16px; }}
  table {{ border-collapse: collapse; margin: 8px 0 24px; }}
  th, td {{ border: 1px solid #ddd; padding: 4px 8px; font-size: 13px; }}
  th {{ background: #f5f5f5; text-align: left; }}
  h2 {{ font-size: 16px; margin: 16px 0 4px; }}
</style>
</head>
<body>
  <div class="nubi-unsafe-banner" role="alert">
    ⚠ {html.escape(_UNSAFE_NOTICE)}
  </div>
  <main>
    <h1>{safe_title}</h1>
    <div class="nubi-meta">
      Frozen public snapshot · exported {html.escape(exported_at)} ·
      policy view <code>{fingerprint}</code>
    </div>
    <div id="nubi-root">Loading frozen data…</div>
  </main>
  <script type="module">
    // UNSAFE PUBLIC EXPORT — frozen data, no auth. See the banner above.
    const SNAPSHOT_URL = {json.dumps(snapshot_public_url)};
    const SPEC = {spec_json};
    const MANIFEST = {manifest_json};

    import * as duckdb from {json.dumps(_DUCKDB_WASM_CDN)};

    async function main() {{
      const root = document.getElementById("nubi-root");
      try {{
        const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
        const worker = await duckdb.createWorker(bundle.mainWorker);
        const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(), worker);
        await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
        // Attach the frozen sidecar straight from its public URL.
        await db.registerFileURL("snapshot.duckdb", SNAPSHOT_URL,
                                 duckdb.DuckDBDataProtocol.HTTP, false);
        const conn = await db.connect();
        await conn.query("ATTACH 'snapshot.duckdb' AS snap (READ_ONLY)");

        root.innerHTML = "";
        for (const entry of MANIFEST) {{
          if (!entry.table) continue;  // error / non-data widget
          const res = await conn.query(
            'SELECT * FROM snap."' + entry.table + '"');
          const rows = res.toArray().map((r) => r.toJSON());
          const h = document.createElement("h2");
          h.textContent = entry.widget_id || entry.table;
          root.appendChild(h);
          root.appendChild(renderTable(entry.columns || [], rows));
        }}
        await conn.close();
      }} catch (err) {{
        root.textContent = "Failed to load frozen snapshot: " + err;
      }}
    }}

    function renderTable(columns, rows) {{
      const table = document.createElement("table");
      const thead = document.createElement("thead");
      const htr = document.createElement("tr");
      for (const c of columns) {{
        const th = document.createElement("th");
        th.textContent = c;
        htr.appendChild(th);
      }}
      thead.appendChild(htr);
      table.appendChild(thead);
      const tbody = document.createElement("tbody");
      for (const row of rows) {{
        const tr = document.createElement("tr");
        for (const c of columns) {{
          const td = document.createElement("td");
          const v = row[c];
          td.textContent = v === null || v === undefined ? "" : String(v);
          tr.appendChild(td);
        }}
        tbody.appendChild(tr);
      }}
      table.appendChild(tbody);
      return table;
    }}

    main();
  </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


async def _write_audit_entry(
    board: dict[str, Any],
    org_id: str,
    repo: Repo,
    entry: dict[str, Any],
) -> None:
    """Append *entry* to ``board.config["public_export_audit"]`` (org-scoped).

    Durable, RLS-respecting audit trail — persisted on the board row via the
    same ``repo.update("boards", org_id, …)`` path Mode 3a uses, so org-scoping
    is unchanged.  Also emitted to the structured logger at WARNING level so the
    UNSAFE export is loud in operational logs.
    """
    config = dict(board.get("config") or {})
    log = config.get("public_export_audit")
    log = list(log) if isinstance(log, list) else []
    log.append(entry)
    config["public_export_audit"] = log
    await repo.update("boards", org_id, str(board["id"]), {"config": config})

    logger.warning(
        "UNSAFE public export: org=%s board=%s by=%s snapshot=%s "
        "policy_fingerprint=%s html=%s",
        org_id,
        entry.get("board_id"),
        entry.get("exported_by"),
        entry.get("snapshot_id"),
        entry.get("policy_fingerprint"),
        entry.get("html_uri"),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def export_public(
    board_id: str,
    org_id: str,
    claims: dict[str, Any],
    repo: Repo,
    *,
    exported_by: str,
    snapshot_id: str | None = None,
    public_base_url: str | None = None,
    base_uri: str | None = None,
) -> dict[str, Any]:
    """Produce an UNSAFE no-auth public static export of a board.

    .. danger::

        The produced HTML and the snapshot sidecar it references are PUBLIC.
        The data is frozen with **the exporter's RLS view** (``claims['policies']``,
        captured by Mode 3a — never a request body) and exposed to ANYONE with
        the URL: no auth, no expiry, no per-viewer security.

    Reuses Mode 3a: when *snapshot_id* is given that existing snapshot's frozen
    sidecar is referenced as-is; otherwise a fresh snapshot is created via
    :func:`app.embedding.snapshot.create_snapshot` (the ONLY data-collection
    path).  This function never re-collects data itself.

    Parameters
    ----------
    board_id, org_id:
        The board to export, resolved org-scoped (never cross-org).
    claims:
        Verified token claims.  Only ``claims["policies"]`` is consulted — the
        captured RLS view for a freshly created snapshot.
    repo:
        Active repository.
    exported_by:
        User id recorded in the audit entry.
    snapshot_id:
        Optional existing Mode 3a snapshot to reference; when omitted a new one
        is created.
    public_base_url:
        Public origin (CDN / bucket) the sidecar is served from; embedded in the
        HTML so the browser fetches the frozen artifact.  When omitted the raw
        storage URI is used (local dev).
    base_uri:
        Storage base override (tests).

    Returns
    -------
    dict
        ``{export_id, board_id, snapshot_id, html_uri, snapshot_public_url,
        policy_fingerprint, exported_at, unsafe: True}``.

    Raises
    ------
    AppError("public_exports_disabled", 403)
        When the deployment switch or the org feature gate is off (UNSAFE
        capability is off by default).  Nothing is written.
    AppError("board_not_found", 404)
        When the board does not exist in *org_id*.
    AppError("snapshot_not_found", 404)
        When *snapshot_id* is given but no such snapshot exists on the board.
    """
    # ── Gate FIRST: refuse before any work / write when disabled. ─────────────
    _assert_public_exports_allowed(org_id)

    board = await repo.get("boards", org_id, board_id)
    if board is None:
        raise AppError("board_not_found", f"Board {board_id!r} not found.", 404)

    # ── Reuse Mode 3a: existing snapshot, or create a fresh one. ──────────────
    if snapshot_id:
        snapshot = await get_snapshot(board_id, org_id, snapshot_id, repo)
        if snapshot is None:
            raise AppError("snapshot_not_found", f"Snapshot {snapshot_id!r} not found.", 404)
    else:
        snapshot = await create_snapshot(
            board_id,
            org_id,
            claims,
            repo,
            created_by=exported_by,
            base_uri=base_uri,
        )
        # create_snapshot persisted on board.config; re-read so the audit append
        # below does not clobber the freshly-written snapshots list.
        board = await repo.get("boards", org_id, board_id) or board

    snapshot_artifact_uri = snapshot["artifact"]["uri"]
    snapshot_public_url = _public_url_for(snapshot_artifact_uri, public_base_url)

    export_id = str(uuid.uuid4())
    exported_at = _now_iso()
    html_uri = public_export_uri(board_id, org_id, export_id, base_uri=base_uri)

    document = _render_html(
        board_title=str((board.get("config") or {}).get("spec", {}).get("title") or board.get("name") or ""),
        spec=snapshot.get("spec") or {},
        snapshot_public_url=snapshot_public_url,
        snapshot_descriptor=snapshot,
        exported_at=exported_at,
    )
    _write_html(html_uri, document)

    descriptor: dict[str, Any] = {
        "export_id": export_id,
        "board_id": board_id,
        "snapshot_id": snapshot["id"],
        "html_uri": html_uri,
        "snapshot_public_url": snapshot_public_url,
        "policy_fingerprint": snapshot.get("policy_fingerprint"),
        "exported_at": exported_at,
        "exported_by": exported_by,
        "unsafe": True,
    }

    await _write_audit_entry(board, org_id, repo, descriptor)
    return descriptor
