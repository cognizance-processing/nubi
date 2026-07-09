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

GET  /boards/{id}/export.pdf
    Render the board to a high-fidelity PDF via the T2 → T3 pipeline:
      1. Collect the board's widget data (server-side, no RLS — editor view).
      2. Render per-widget SVGs with the Node.js echarts-SSR script.
      3. Compose a page SVG.
      4. Render the page SVG to PDF via cairosvg (preferred) or svglib+reportlab.
    Respects the T5 export config (page_size, header/footer, title_slide).
    Returns ``application/pdf``.  Requires cairosvg or svglib+reportlab; returns
    503 with a clear install message when neither is available.

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
    GET  /api/v1/boards/{id}/export.pdf
    POST /api/v1/boards/{id}/share
"""

from __future__ import annotations

import asyncio
import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from app.auth.deps import current_user
from app.config import get_settings
from app.dashboards.collect import (
    collect_board_data,
    run_query_rows,
    spec_from_board,
    widget_query_targets,
)
from app.errors import AppError
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

    spec = spec_from_board(board)

    # First-party access tokens carry no RLS policies (full-access editor view).
    widgets_out = await collect_board_data(
        board_id, org_id, claims={"policies": {}}, repo=repo, only_query_id=query_id
    )

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

    spec = spec_from_board(board)
    targets = widget_query_targets(spec, query_id)
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
            columns, rows = await run_query_rows(t["query_id"], org_id, repo, policies)
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
# GET /boards/{id}/export.pdf
# ---------------------------------------------------------------------------


@router.get("/{board_id}/export.pdf")
async def export_board_pdf(
    board_id: str,
    request: Request,
    page_size: str | None = None,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Render the board to a high-fidelity vector PDF.

    Uses the T2 (echarts-SSR) → T3 (cairosvg / svglib+reportlab) pipeline.
    The board's widget data is collected server-side with no RLS (editor view).
    Export config (page_size, header/footer, title_slide) is read from the
    board spec's T5 ``export`` block; ``?page_size=`` overrides it.

    Returns
    -------
    Response
        ``application/pdf`` with ``Content-Disposition: attachment``.

    Raises
    ------
    AppError("board_not_found", 404)
        When the board does not exist in this org.
    AppError("pdf_backend_missing", 503)
        When neither cairosvg nor svglib+reportlab are installed.
    AppError("node_not_found", 503)
        When Node.js is not available for SVG rendering.
    """
    from app.dashboards.spec import get_export_config, validate_spec  # noqa: PLC0415
    from app.dashboards.svg_render import render_board_svg  # noqa: PLC0415
    from app.embedding.render_pdf import render_board_pdf, svg_page_size_px  # noqa: PLC0415

    board = await _load_board(board_id, str(user["id"]), repo, request)
    spec_dict = spec_from_board(board)
    validated_spec, _ = validate_spec(spec_dict)
    if validated_spec is None:
        validated_spec, _ = validate_spec({"widgets": []})

    export_cfg = get_export_config(validated_spec)
    resolved_page_size = page_size or export_cfg.get("page_size") or "A4"
    export_cfg["page_size"] = resolved_page_size

    # Collect widget data (first-party = no RLS, full editor view).
    from app.routes._org import resolve_org_id as _resolve  # noqa: PLC0415
    org_id = await _resolve(str(user["id"]), repo, request)
    widget_data = await collect_board_data(
        board_id, org_id, claims={"policies": {}}, repo=repo
    )

    # Render page SVG via T2 (Node.js echarts-SSR).
    # Off-loaded to a thread so the blocking subprocess.run calls inside
    # render_board_svg do not stall the asyncio event loop.
    page_w_px, page_h_px = svg_page_size_px(resolved_page_size)
    page_svg = await asyncio.to_thread(
        render_board_svg,
        validated_spec,
        widget_data,
        page_width_px=page_w_px,
    )

    board_title = (
        spec_dict.get("title")
        or board.get("name")
        or f"Dashboard {board_id}"
    )

    pdf_bytes = render_board_pdf([page_svg], export_cfg, title=board_title)

    safe_name = "".join(c for c in str(board_id) if c.isalnum() or c in "-_") or "board"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'},
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
    spec = spec_from_board(board)
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
