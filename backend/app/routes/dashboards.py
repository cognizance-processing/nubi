"""Dashboard spec validation + DataProvider data endpoints.

Endpoints
---------
POST /dashboards/validate
    Accepts ``{spec: <dashboard spec dict>}``, runs the canonical
    ``validate_spec`` parser, and returns *structured, repair-oriented* issues
    (see ``app.dashboards.errors``) split into ``errors`` and ``warnings``.

    This endpoint is **read-only** — it validates and reports; it NEVER saves
    anything.  Requires a valid first-party Bearer token (``current_user``),
    matching the sibling AI/grounding routes in ``app.routes.ai``.

POST /boards/{board_id}/providers/{provider_id}/data
    Resolve a DataProvider declared in a board's DashboardSpec and return the
    named result-sets as a multi-table Arrow IPC stream (one IPC stream per
    declared ``results[*].name``).

    * Org-scoped: the board is resolved in the caller's org only.
    * RLS: ``claims["policies"]`` from the **verified token** only — never from
      the request body.
    * Accepts both first-party Bearer tokens (``current_user``) and host-signed
      embed JWTs (``verified_identity``).
    * Cached by ``(provider_id, params, rls_hash)`` using the Wave-2 base-scan
      cache (TTL 5 minutes).

GET /widgets/{widget_id}/usage
    "Used by N" for a widget-LIBRARY row (``backend/app/routes/resources.py``,
    resource ``widgets``): scans every board in the caller's org for spec
    widgets whose ``ref`` equals *widget_id* and returns the boards + widget
    instance ids referencing it. Org-scoped via ``resolve_org_id`` — never
    leaks another org's boards. See ``app.dashboards.widget_refs`` for the
    reference model this counts.

GET /queries/{query_id}/usage
    Same shape as the widget-usage endpoint, but for a registered query id:
    every spec widget (including ``metric``/query bindings nested under a
    ``stepper``'s ``props.steps[].widget``) whose ``query_id`` equals
    *query_id*. Reuses ``app.dashboards.collect.widget_query_targets`` — the
    same helper board export/thumbnail code already uses to find a query's
    data targets on one board — across every board in the org.

Response shape for GET /widgets/{id}/usage and GET /queries/{id}/usage
-------------------------------------------------------------------------
::

    {
        "widget_id" | "query_id": "<id>",
        "count": 3,
        "boards": [
            {"board_id": "...", "board_name": "...", "widget_ids": ["w1", "w4"]},
            ...
        ]
    }

``count`` is the total number of referencing widget instances (not boards);
``boards`` omits any board with zero matches.

Response shape for /boards/{id}/providers/{pid}/data
------------------------------------------------------
``Content-Type: application/vnd.apache.arrow.stream``

A binary multi-table IPC frame::

    4-byte big-endian table count N
    then N frames, each:
        4-byte big-endian name length
        UTF-8 name bytes
        4-byte big-endian IPC stream length
        Arrow IPC stream bytes

POST /dashboards/validate response shape
-----------------------------------------
::

    {
        "valid": true | false,
        "errors":   [StructuredIssue...],
        "warnings": [StructuredIssue...]
    }

Each StructuredIssue is::

    {
        "path": "widgets[2].encoding.x",
        "code": "missing_encoding_x",
        "message": "Widget 'w3' (chart): encoding must include 'x' column.",
        "severity": "error",
        "suggestion": "Set encoding.x to one of the bound query's columns ...",
        "valid_options": ["region", "revenue", "month"]
    }
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, model_validator

from app.auth.deps import current_user, verified_identity
from app.auth.verify import VerifiedIdentity
from app.repos.provider import Repo, get_repo
from app.routes import api_router


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ValidateRequest(BaseModel):
    """Request body for POST /dashboards/validate."""

    spec: dict[str, Any]


class ValidateResponse(BaseModel):
    """Response body for POST /dashboards/validate."""

    valid: bool
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@api_router.post(
    "/dashboards/validate", response_model=ValidateResponse, tags=["dashboards"]
)
async def validate_dashboard(
    body: ValidateRequest,
    _user: dict[str, Any] = Depends(current_user),
) -> ValidateResponse:
    """Validate a dashboard spec and return structured, repair-oriented issues.

    Pipeline
    --------
    1. Run ``validate_spec`` — Pydantic parse + the canonical semantic checks
       (chart encodings, filter/text requirements, var refs, tab refs, query-id
       registry lookups).  Returns plain-string issues.
    2. Convert those strings to ``StructuredIssue`` objects via
       ``to_structured_issues`` — recovering a JSON ``path`` + machine ``code``
       and enriching with ``valid_options`` (e.g. the bound query's real output
       columns for a bad chart encoding, or the known query ids for an unknown
       ``query_id``).
    3. Split by severity; ``valid`` is true iff there are zero error-severity
       issues.

    This endpoint NEVER persists the spec — it is purely a validation oracle for
    external AI agents authoring dashboards.

    Parameters
    ----------
    body:
        ``{spec: <dashboard spec dict>}``.
    _user:
        Injected by ``current_user``; ensures the endpoint requires auth.

    Returns
    -------
    ValidateResponse
        ``{valid, errors, warnings}``.

    Raises
    ------
    AppError("unauthorized", 401)
        If no valid Bearer token is provided.
    """
    # Lazy imports keep the route module import-time side-effect free (mirrors
    # the pattern used by app.routes.ai for spec helpers).
    from app.dashboards.errors import to_structured_issues  # noqa: PLC0415
    from app.dashboards.spec import validate_spec  # noqa: PLC0415

    _spec, raw_issues = validate_spec(body.spec)

    structured = to_structured_issues(body.spec, raw_issues)

    errors = [i.to_dict() for i in structured if i.severity == "error"]
    warnings = [i.to_dict() for i in structured if i.severity == "warning"]

    return ValidateResponse(
        valid=not errors,
        errors=errors,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# POST /boards/{board_id}/providers/{provider_id}/data
# ---------------------------------------------------------------------------


# Bounds for ProviderDataRequest.params to prevent CPU-amplification DoS via
# the json.dumps + sha256 hash performed on every cache-miss in resolve_provider_data.
_MAX_PARAMS_KEYS = 50
_MAX_PARAMS_BYTES = 8192


class ProviderDataRequest(BaseModel):
    """Request body for POST /boards/{id}/providers/{pid}/data."""

    params: dict[str, Any] = {}

    @model_validator(mode="after")
    def _check_params_size(self) -> "ProviderDataRequest":
        """Cap params key count and serialized byte size.

        This validator enforces the bounds when ProviderDataRequest is
        instantiated directly (e.g. in unit tests or service code).  The HTTP
        route re-enforces via ``AppError`` to guarantee a 400 — not a 422 —
        reaches the client when the limit is exceeded.
        """
        from app.errors import AppError  # noqa: PLC0415

        if len(self.params) > _MAX_PARAMS_KEYS:
            raise AppError(
                "params_too_large",
                f"params must not exceed {_MAX_PARAMS_KEYS} keys "
                f"(got {len(self.params)}).",
                400,
            )
        serialized = json.dumps(self.params, separators=(",", ":"), sort_keys=True)
        if len(serialized.encode()) > _MAX_PARAMS_BYTES:
            raise AppError(
                "params_too_large",
                f"params serialized size must not exceed {_MAX_PARAMS_BYTES} bytes "
                f"(got {len(serialized.encode())}).",
                400,
            )
        return self


@api_router.post(
    "/boards/{board_id}/providers/{provider_id}/data",
    tags=["dashboards"],
    response_class=Response,
    responses={
        200: {"content": {"application/vnd.apache.arrow.stream": {}}},
        404: {"description": "Board or provider not found"},
        403: {"description": "Forbidden"},
    },
)
async def get_provider_data(
    board_id: str,
    provider_id: str,
    body: ProviderDataRequest,
    request: Request,
    identity: VerifiedIdentity = Depends(verified_identity),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Resolve a DataProvider and return a multi-table Arrow IPC stream.

    Resolves the DataProvider named *provider_id* declared in board *board_id*,
    executes it (flow or inline), and returns the named result-sets as a
    concatenated multi-table Arrow IPC stream.

    Parameters
    ----------
    board_id:
        The board id (must exist in the caller's org).
    provider_id:
        The ``DataProvider.id`` declared in the board's spec.
    body:
        ``{params: {...}}`` — request-time parameter overrides merged with the
        provider's declared param defaults.  RLS is NEVER taken from the body.
    request:
        The incoming FastAPI request (used for embed origin verification via
        ``verified_identity`` and for org resolution).
    identity:
        Verified token identity (injected by ``verified_identity``).
        Accepts both first-party HS256 access tokens and host-signed embed JWTs.
    repo:
        Active repository implementation.

    Returns
    -------
    Response
        ``Content-Type: application/vnd.apache.arrow.stream`` — binary
        multi-table IPC frame (one Arrow IPC stream per declared result).

    Raises
    ------
    AppError("unauthorized", 401)
        If the token is missing or invalid.
    AppError("insufficient_scope", 403)
        If the token does not carry a qualifying read scope.
    AppError("board_not_found", 404)
        If no board with *board_id* exists in the caller's org.
    AppError("provider_not_found", 404)
        If *provider_id* is not declared in the board's spec.
    AppError("provider_mode_unsupported", 501)
        If the materialized execution mode is requested (later wave).

    Security notes
    --------------
    * RLS predicates come ONLY from ``identity.policies`` (the verified token).
    * The board and any datastore are resolved org-scoped — never cross-org.
    * Embed tokens with ``embed_origin`` are validated against the request
      ``Origin`` header by ``verified_identity`` before this handler runs.
    * Viewers (embed tokens) are NOT counted as metered compute (the per-result
      cache means compute is only charged once, not once per viewer).
    """
    from app.auth.scopes import has_scope  # noqa: PLC0415
    from app.dashboards.board_data import (  # noqa: PLC0415
        resolve_provider_data,
        tables_to_multi_ipc_stream,
    )
    from app.routes._org import resolve_org_id  # noqa: PLC0415

    # ── Scope gate (mirrors embed.py) ─────────────────────────────────────────
    _scopes = identity.scope
    _has_read = has_scope(_scopes, "read:query") or any(
        s.startswith("read:") for s in _scopes
    )
    if not _has_read:
        from app.errors import AppError  # noqa: PLC0415

        raise AppError(
            "insufficient_scope",
            "Token does not carry the required scope: read:query",
            403,
        )

    # ── Resolve org_id ────────────────────────────────────────────────────────
    _is_embed = identity.kind == "embed"
    if _is_embed and identity.org:
        org_id = identity.org
    else:
        # resolve_org_id, not get_user_org: the latter ignores X-Org-Id and
        # pins the user's FIRST org, so a board in any other org resolves
        # against the wrong tenant's data.
        org_id = await resolve_org_id(identity.user_id, repo, request)

    # ── Build claims (RLS from verified token only) ───────────────────────────
    claims: dict[str, Any] = {
        "policies": dict(identity.policies or {}),
        "sub": identity.user_id or "",
        "org_id": org_id,
    }

    # ── Determine whether metering should be skipped ──────────────────────────
    # INVARIANT: viewers are NEVER metered — this applies to BOTH embed tokens
    # AND first-party users whose org role is 'viewer'.  Only first-party
    # writer/admin/member callers trigger quota enforcement on a cache miss.
    _skip_metering = _is_embed  # embed tokens always skip metering
    if not _is_embed and identity.user_id:
        # First-party caller: resolve the org role and skip metering for viewers.
        from app.auth.roles import get_org_role  # noqa: PLC0415

        _caller_role = await get_org_role(identity.user_id, org_id, repo)
        if _caller_role == "viewer":
            _skip_metering = True

    # ── Resolve provider data ─────────────────────────────────────────────────
    # Pass skip_metering so resolve_provider_data can skip quota enforcement for
    # embed tokens and first-party viewer-role users (viewers are never metered).
    tables = await resolve_provider_data(
        board_id=board_id,
        provider_id=provider_id,
        params=dict(body.params),
        org_id=org_id,
        claims=claims,
        repo=repo,
        is_embed=_is_embed,
        skip_metering=_skip_metering,
    )

    # ── Serialise to multi-table IPC stream ───────────────────────────────────
    # Offload to a thread pool to avoid blocking the async event loop on
    # CPU-bound Arrow IPC serialisation (can be tens–hundreds of ms near cap).
    ipc_bytes = await asyncio.to_thread(tables_to_multi_ipc_stream, tables)

    return Response(
        content=ipc_bytes,
        media_type="application/vnd.apache.arrow.stream",
        headers={
            "X-Provider-Id": provider_id,
            "X-Board-Id": board_id,
            "X-Result-Count": str(len(tables)),
        },
    )


# ---------------------------------------------------------------------------
# GET /widgets/{widget_id}/usage, GET /queries/{query_id}/usage
# ---------------------------------------------------------------------------


async def _usage_by_board(
    org_id: str,
    repo: Repo,
    match: Any,
) -> tuple[int, list[dict[str, Any]]]:
    """Shared board-scan for the two usage endpoints below.

    Lists every board in *org_id*, parses its stored spec (best-effort — a
    board with no structured spec contributes an empty widgets list, never an
    error), and calls ``match(spec_dict) -> list[{"widget_id": ...}]`` per
    board. Boards with zero matches are omitted from the result so the
    response only lists boards actually using the thing being looked up.

    Returns
    -------
    tuple[int, list[dict]]
        ``(total_count, boards)`` — ``total_count`` sums widget instances
        across all boards; each ``boards`` entry is
        ``{board_id, board_name, widget_ids}``.
    """
    from app.dashboards.collect import spec_from_board  # noqa: PLC0415

    boards_rows = await repo.list("boards", org_id)
    total = 0
    usage: list[dict[str, Any]] = []
    for board in boards_rows:
        spec_dict = spec_from_board(board)
        targets = match(spec_dict)
        if not targets:
            continue
        widget_ids = [t["widget_id"] for t in targets]
        total += len(widget_ids)
        usage.append(
            {
                "board_id": str(board.get("id")),
                "board_name": board.get("name"),
                "widget_ids": widget_ids,
            }
        )
    return total, usage


@api_router.get("/widgets/{widget_id}/usage", tags=["dashboards"])
async def get_widget_usage(
    widget_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return every board/widget instance referencing library widget *widget_id*.

    Scans the caller's org (never another org's boards — resolved via
    ``resolve_org_id``, which honours ``X-Org-Id``) for spec widgets whose
    ``ref`` equals *widget_id*, using
    ``app.dashboards.widget_refs.widget_ref_usage`` per board. Does NOT check
    that *widget_id* itself still exists in the ``widgets`` library — a
    dangling ref is exactly the case this endpoint helps a caller find before
    deleting (or after deleting) a library entry.

    Parameters
    ----------
    widget_id:
        The ``widgets`` library row id to look up.
    request:
        Used by ``resolve_org_id`` to honour ``X-Org-Id``.
    user:
        Injected by ``current_user``; ensures the endpoint requires auth.
    repo:
        Active repository implementation.

    Returns
    -------
    dict
        ``{widget_id, count, boards: [{board_id, board_name, widget_ids}]}``.
    """
    from app.dashboards.widget_refs import widget_ref_usage  # noqa: PLC0415
    from app.routes._org import resolve_org_id  # noqa: PLC0415

    org_id = await resolve_org_id(str(user["id"]), repo, request)
    count, boards = await _usage_by_board(
        org_id, repo, lambda spec_dict: widget_ref_usage(spec_dict, widget_id)
    )
    return {"widget_id": widget_id, "count": count, "boards": boards}


@api_router.get("/queries/{query_id}/usage", tags=["dashboards"])
async def get_query_usage(
    query_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return every board/widget instance binding to query *query_id*.

    Scans the caller's org (org-scoped via ``resolve_org_id``) for spec
    widgets whose ``query_id`` equals *query_id* — reusing
    ``app.dashboards.collect.widget_query_targets``, the same helper the
    board-export / thumbnail pipeline already uses to find a query's data
    targets on one board, across every board in the org.

    Parameters
    ----------
    query_id:
        The registered query id to look up.
    request:
        Used by ``resolve_org_id`` to honour ``X-Org-Id``.
    user:
        Injected by ``current_user``; ensures the endpoint requires auth.
    repo:
        Active repository implementation.

    Returns
    -------
    dict
        ``{query_id, count, boards: [{board_id, board_name, widget_ids}]}``.
    """
    from app.dashboards.collect import widget_query_targets  # noqa: PLC0415
    from app.routes._org import resolve_org_id  # noqa: PLC0415

    org_id = await resolve_org_id(str(user["id"]), repo, request)
    count, boards = await _usage_by_board(
        org_id, repo, lambda spec_dict: widget_query_targets(spec_dict, query_id)
    )
    return {"query_id": query_id, "count": count, "boards": boards}
