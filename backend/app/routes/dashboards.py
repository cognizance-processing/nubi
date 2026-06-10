"""Dashboard spec validation endpoint for external AI agents.

Endpoint
--------
POST /dashboards/validate
    Accepts ``{spec: <dashboard spec dict>}``, runs the canonical
    ``validate_spec`` parser, and returns *structured, repair-oriented* issues
    (see ``app.dashboards.errors``) split into ``errors`` and ``warnings``.

    This endpoint is **read-only** — it validates and reports; it NEVER saves
    anything.  Requires a valid first-party Bearer token (``current_user``),
    matching the sibling AI/grounding routes in ``app.routes.ai``.

Response shape
--------------
::

    {
        "valid": true | false,            # true iff there are no error-severity issues
        "errors":   [StructuredIssue...], # severity == "error"
        "warnings": [StructuredIssue...]  # severity == "warning"
    }

Each StructuredIssue is::

    {
        "path": "widgets[2].encoding.x",  # JSON path to the offending value
        "code": "missing_encoding_x",     # stable machine code
        "message": "Widget 'w3' (chart): encoding must include 'x' column.",
        "severity": "error",              # "error" | "warning"
        "suggestion": "Set encoding.x to one of the bound query's columns ...",
        "valid_options": ["region", "revenue", "month"]  # or null
    }

A mid-tier agent can read a single error and fix it in one round-trip: ``path``
tells it WHERE and ``valid_options`` tells it the legal VALUES (e.g. the bound
query's real columns for a bad chart encoding).
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends
from pydantic import BaseModel

from app.auth.deps import current_user
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
