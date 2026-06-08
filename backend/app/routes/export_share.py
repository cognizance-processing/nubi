"""Dashboard EXPORT + SHARE router (auth + org-scoped).

Endpoints
---------
GET  /boards/{id}/export.csv
    Resolve the board (org-scoped), collect the widget ``query_id``s from its
    ``config.spec``, run each registered query server-side and stream the
    combined result as CSV.  Best-effort: a widget whose query fails (or whose
    datastore cannot be resolved in this environment) is skipped with a
    ``# error`` comment rather than failing the whole export.  Pass
    ``?query_id=<id>`` to export a single widget's data.

GET  /boards/{id}/export.json
    Same resolution as the CSV export but returns ``{widget_id, query_id,
    columns, rows}`` per widget as JSON — handy when the client wants to drive
    its own CSV / Excel writer (e.g. SheetJS) without re-running queries.

POST /boards/{id}/share
    Return everything the host needs to embed the board: the embed URL, a
    copy-paste ``<nubi-dashboard>`` snippet, the RLS / auth model summary, and
    the embed-token requirements.

Embed-token minting model — IMPORTANT
-------------------------------------
Nubi embed tokens are **host-signed** (RS256 / ES256) and verified against the
host's JWKS via the issuer registry (see ``app/auth/verify.py`` and
``docs/embedding.md``).  Nubi does **not** mint embed JWTs — the host backend
signs them with its own private key so that the ``policies`` (RLS) claims are
authored and controlled by the host, never the browser.

Therefore ``POST /boards/{id}/share`` does **not** return a signed embed token.
It returns the embed descriptor + a documented snippet + the exact claim shape
the host must sign (the ``mint`` block), including the max token lifetime.  This
is the real, production model — not a stub.  The single piece that is a
documented placeholder is ``mint.token`` (always ``None``) because only the host
can produce it.

Row-level security is enforced **server-side in the connector** at query time
(predicate injection from the verified token's ``policies`` claim); the browser
is untrusted and never sees rows it is not authorised for.

Router wiring (for main.py owner)
---------------------------------
    from app.routes.export_share import router as export_share_router
    api_router.include_router(export_share_router)   # already prefixed "/boards"

``main.py`` mounts ``api_router`` under ``/api/v1`` so the final paths are::

    GET  /api/v1/boards/{id}/export.csv
    GET  /api/v1/boards/{id}/export.json
    POST /api/v1/boards/{id}/share
"""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from app.auth.deps import current_user
from app.config import get_settings
from app.errors import AppError
from app.queries.registry import ensure_persisted_query, get_query_registry
from app.repos.provider import Repo, get_repo
from app.routes._org import resolve_org_id

router = APIRouter(prefix="/boards", tags=["export-share"])

# Max lifetime Nubi accepts for a host-signed embed JWT (see docs/embedding.md).
_EMBED_TOKEN_MAX_TTL_MIN = 15


# ---------------------------------------------------------------------------
# Board / widget helpers
# ---------------------------------------------------------------------------


async def _load_board(board_id: str, user_id: str, repo: Repo, request: Request) -> dict[str, Any]:
    """Load an org-scoped board row or raise 404.

    Cross-org rows return 404 (not 403) — same no-leak policy as the resources
    CRUD route: ``repo.get`` is already org-scoped, so a row in another org is
    simply not found.
    """
    org_id = await resolve_org_id(user_id, repo, request)
    board = await repo.get("boards", org_id, board_id)
    if board is None:
        raise AppError("board_not_found", f"Board {board_id!r} not found.", 404)
    return board


def _spec_from_board(board: dict[str, Any]) -> dict[str, Any]:
    """Return the dashboard spec dict from a board row (``config.spec``).

    Returns an empty spec (no widgets) when the board has no structured spec.
    """
    config = board.get("config") or {}
    spec = config.get("spec")
    if isinstance(spec, dict):
        return spec
    return {"widgets": []}


def _widget_query_targets(spec: dict[str, Any], only_query_id: str | None) -> list[dict[str, str]]:
    """Collect ``{widget_id, query_id}`` data targets from a spec.

    Only widgets with a non-empty ``query_id`` are returned (text / pure-filter
    widgets carry no data).  When *only_query_id* is given, the list is filtered
    to that query.  Duplicate (widget_id, query_id) pairs are de-duplicated.
    """
    widgets = spec.get("widgets")
    if not isinstance(widgets, list):
        return []

    targets: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for w in widgets:
        if not isinstance(w, dict):
            continue
        qid = w.get("query_id")
        if not qid:
            continue
        if only_query_id and qid != only_query_id:
            continue
        wid = str(w.get("id") or qid)
        key = (wid, str(qid))
        if key in seen:
            continue
        seen.add(key)
        targets.append({"widget_id": wid, "query_id": str(qid)})
    return targets


# ---------------------------------------------------------------------------
# Server-side query execution (best-effort)
# ---------------------------------------------------------------------------


async def _run_query_rows(
    query_id: str,
    org_id: str,
    repo: Repo,
    policies: dict[str, Any],
) -> tuple[list[str], list[list[Any]]]:
    """Run a registered query and return ``(columns, rows)``.

    Reuses the same primitives as ``POST /query``:
      * the query registry resolves ``query_id`` → canonical SQL (the browser
        never supplies SQL here — only the registered id is honoured);
      * the planner injects RLS predicates from *policies* (AST-level, never
        string-concatenated);
      * a datastore-bound query executes against its datastore via the
        connector registry, otherwise the built-in demo DuckDB connector runs.

    Raises ``AppError`` with a descriptive code on any failure so the caller can
    decide whether to skip the widget or surface the error.
    """
    registry = get_query_registry()
    registered = registry.get(query_id) or await ensure_persisted_query(query_id)
    if registered is None:
        raise AppError("query_not_registered", f"No registered query for id={query_id!r}.", 404)

    # Resolve declared named params to their defaults (export has no live
    # filter state), turning {{name}} placeholders into positional $N binds.
    sql = registered.sql
    params: list[Any] = []
    if registered.params:
        from app.connectors.planner import resolve_named_params

        resolved = {p.name: (p.default if p.default is not None else None) for p in registered.params}
        sql, params = resolve_named_params(sql, resolved)

    from app.connectors import plan as planner_plan

    physical_plan = planner_plan(sql=sql, claims={"policies": policies}, params=params)

    connector = await _resolve_connector(registered, org_id, repo, physical_plan)

    arrow_table = connector.execute(physical_plan)
    columns = list(arrow_table.schema.names)
    rows: list[list[Any]] = []
    for record in arrow_table.to_pylist():
        rows.append([record.get(c) for c in columns])
    return columns, rows


async def _resolve_connector(registered: Any, org_id: str, repo: Repo, physical_plan: Any) -> Any:
    """Pick the connector for a registered query (datastore-bound or demo).

    Pragmatic subset of the ``POST /query`` connector path: it covers the demo
    connector and a directly-configured duckdb/postgres/http_json datastore.
    Secret injection and network bridges are intentionally out of scope here —
    if a datastore needs them, the connector construction will raise and the
    caller skips that widget (best-effort export).
    """
    datastore_id = getattr(registered, "datastore_id", None)
    if not datastore_id:
        from app.routes.query import _get_demo_connector

        return _get_demo_connector()

    ds = await repo.get("datastores", org_id, datastore_id)
    if ds is None:
        raise AppError("datastore_not_found", f"Datastore {datastore_id!r} not found.", 404)

    from app.connectors.registry import get_connector_registry

    cfg: dict[str, Any] = dict(ds.get("config") or {})
    ctype = cfg.get("type")
    factory = get_connector_registry().get(ctype)

    if ctype == "duckdb":
        db_path = cfg.get("database") or cfg.get("path")
        if db_path and db_path != ":memory:":
            import duckdb

            conn = duckdb.connect(database=db_path, read_only=True)
            return factory(conn)
        return factory()
    if ctype == "postgres":
        dsn = cfg.get("dsn")
        if dsn is None:
            host = cfg.get("host", "localhost")
            port = cfg.get("port", 5432)
            dbname = cfg.get("dbname") or cfg.get("database") or "postgres"
            user = cfg.get("user") or cfg.get("username") or "postgres"
            password = cfg.get("password", "")
            dsn = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
        return factory(dsn)

    connector = factory(cfg)

    # Defence-in-depth: never run a policy-bearing query on a source that
    # cannot enforce RLS server-side.
    policies = (getattr(physical_plan, "rls_claims", None) or {}).get("policies") or {}
    if policies and connector.capabilities().get("predicate_rls") is False:
        raise AppError(
            "source_unsupported_rls",
            "Source does not support Row-Level Security (predicate_rls=False).",
            501,
        )
    return connector


# ---------------------------------------------------------------------------
# GET /boards/{id}/export.json
# ---------------------------------------------------------------------------


@router.get("/{board_id}/export.json")
async def export_board_json(
    board_id: str,
    request: Request,
    query_id: str | None = None,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return per-widget ``{widget_id, query_id, columns, rows}`` for a board.

    The client can feed this straight into a CSV / Excel writer.  Per-widget
    errors are reported inline (``error`` key) instead of failing the request.
    """
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    board = await repo.get("boards", org_id, board_id)
    if board is None:
        raise AppError("board_not_found", f"Board {board_id!r} not found.", 404)

    spec = _spec_from_board(board)
    targets = _widget_query_targets(spec, query_id)

    # First-party access tokens carry no RLS policies (full-access editor view).
    policies: dict[str, Any] = {}

    widgets_out: list[dict[str, Any]] = []
    for t in targets:
        entry: dict[str, Any] = {"widget_id": t["widget_id"], "query_id": t["query_id"]}
        try:
            columns, rows = await _run_query_rows(t["query_id"], org_id, repo, policies)
            entry["columns"] = columns
            entry["rows"] = rows
        except AppError as exc:
            entry["error"] = exc.code
        except Exception as exc:  # noqa: BLE001 — best-effort export
            entry["error"] = f"export_failed: {exc.__class__.__name__}"
        widgets_out.append(entry)

    return {
        "board_id": board_id,
        "title": spec.get("title") or board.get("name") or board_id,
        "widgets": widgets_out,
    }


# ---------------------------------------------------------------------------
# GET /boards/{id}/export.csv
# ---------------------------------------------------------------------------


@router.get("/{board_id}/export.csv")
async def export_board_csv(
    board_id: str,
    request: Request,
    query_id: str | None = None,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Stream the board's widget data as CSV.

    One CSV section per data widget, separated by a ``# widget: <id>`` comment
    line.  Widgets whose query cannot be executed in this environment are
    emitted as a ``# error`` comment so the export never hard-fails.

    Pass ``?query_id=<id>`` to export only that widget's rows.
    """
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    board = await repo.get("boards", org_id, board_id)
    if board is None:
        raise AppError("board_not_found", f"Board {board_id!r} not found.", 404)

    spec = _spec_from_board(board)
    targets = _widget_query_targets(spec, query_id)
    policies: dict[str, Any] = {}

    buf = io.StringIO()
    writer = csv.writer(buf)

    if not targets:
        writer.writerow(["# no data widgets on this board"])

    for idx, t in enumerate(targets):
        if idx > 0:
            buf.write("\n")
        writer.writerow([f"# widget: {t['widget_id']} (query: {t['query_id']})"])
        try:
            columns, rows = await _run_query_rows(t["query_id"], org_id, repo, policies)
            writer.writerow(columns)
            for row in rows:
                writer.writerow(["" if v is None else v for v in row])
        except AppError as exc:
            writer.writerow([f"# error: {exc.code} — {exc.message}"])
        except Exception as exc:  # noqa: BLE001 — best-effort export
            writer.writerow([f"# error: export_failed — {exc.__class__.__name__}"])

    safe_name = "".join(c for c in str(board_id) if c.isalnum() or c in "-_") or "board"
    filename = f"{safe_name}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# POST /boards/{id}/share
# ---------------------------------------------------------------------------


@router.post("/{board_id}/share")
async def share_board(
    board_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return the embed descriptor + RLS/auth model + embed-token requirements.

    See the module docstring: Nubi does not mint embed JWTs — the host signs
    them with its own key.  This endpoint returns the embed URL, a ready-to-paste
    ``<nubi-dashboard>`` snippet, the exact claim shape the host must sign, and
    the RLS policy summary those claims carry.
    """
    board = await _load_board(board_id, str(user["id"]), repo, request)
    spec = _spec_from_board(board)
    settings = get_settings()

    title = spec.get("title") or board.get("name") or f"Dashboard {board_id}"

    # Origin of the request gives a sensible default for the embed URL + the
    # embed_origin claim suggestion the host should pin the token to.
    origin = request.headers.get("origin") or ""
    embed_path = f"/d/{board_id}"
    embed_url = f"{origin}{embed_path}" if origin else embed_path

    # The host fetches the read-only descriptor via this endpoint at runtime.
    config_endpoint = f"/api/v1/embed/config/{board_id}"

    snippet = (
        '<script src="https://cdn.example.com/dist-embed/nubi-dashboard.js"></script>\n'
        f'<nubi-dashboard\n'
        f'  dashboard-id="{board_id}"\n'
        f'  get-token="getEmbedToken"\n'
        f'  backend="https://api.example.com"\n'
        f'  style="display:block; height:600px;">\n'
        f'</nubi-dashboard>'
    )

    # The exact claim shape the host's backend must RS256/ES256-sign.  This is
    # the source of truth surfaced to the share UI so the integrator can copy it.
    mint = {
        "token": None,  # host-minted only — Nubi never signs embed JWTs
        "algorithm": "RS256 | ES256",
        "max_ttl_minutes": _EMBED_TOKEN_MAX_TTL_MIN,
        "first_party_access_ttl_minutes": settings.JWT_ACCESS_TTL_MIN,
        "how_to_mint": (
            "Sign these claims with your private key (RS256/ES256). Expose the "
            "matching public key at your JWKS endpoint and register the issuer "
            "in app/auth/issuers.py. The frontend getToken()/getEmbedToken() "
            "function returns this signed JWT to the <nubi-dashboard> element."
        ),
        "required_claims": {
            "iss": "https://your-app.example.com",
            "sub": "user-or-service-id",
            "aud": "nubi:your-project-id",
            "org": "your-org-slug",
            "scope": ["read:dashboard:*"],
            "policies": {"tenant_id": "<viewer-tenant>"},
            "embed_origin": origin or "https://your-host-page.example.com",
            "exp": "<= iat + 15m",
        },
    }

    # Human-readable RLS / auth model the share panel renders verbatim.
    rls = {
        "model": "row-level-security",
        "enforced": "server-side, in the connector (predicate injection)",
        "trust_boundary": (
            "The browser is UNTRUSTED. RLS predicates are injected from the "
            "verified token's `policies` claim into the SQL AST server-side "
            "before the query reaches the warehouse — never string-concatenated, "
            "never enforced in the browser."
        ),
        "policies_carried": "policies claim on the embed JWT (e.g. {tenant_id})",
        "cache_isolation": (
            "The content-addressed cache key includes `policies`, so two viewers "
            "with different RLS contexts never share a cache slot."
        ),
        "token_locked_params": (
            "Embed tokens can lock query params (per-viewer data boundaries) "
            "that filter widgets / URL params cannot override."
        ),
        "embed_origin_pinning": (
            "The `embed_origin` claim must match the request Origin header — "
            "ties a token to a single host page."
        ),
        "embed_constraints": [
            "No arbitrary SQL — embed tokens must reference a registered query id.",
            "No compute or AI routes (embed tokens lack those scopes).",
            "Must carry at least read:* or read:dashboard:* scope.",
        ],
    }

    return {
        "board_id": board_id,
        "title": title,
        "embed_url": embed_url,
        "config_endpoint": config_endpoint,
        "snippet": snippet,
        "mint": mint,
        "rls": rls,
    }
