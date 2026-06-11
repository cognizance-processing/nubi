"""Scope helpers for Nubi's auth-as-code layer.

Scopes are coarse-grained capability strings in the form ``action:resource`` or
``action:resource:id``.  Wildcards are supported at the trailing segment level,
so ``"read:*"`` matches any scope that begins with ``"read:"`` and
``"read:dashboard:*"`` matches any scope that begins with ``"read:dashboard:"``.

Public API
----------
parse_scopes(claims) -> list[str]
    Extract the ``scope`` field from a decoded claims dict.

has_scope(scopes, required) -> bool
    Return ``True`` if *required* is covered by one of the entries in *scopes*.

require_scope(claims, required)
    Raise ``AppError("insufficient_scope", 403)`` if the claims lack *required*.

caller_claims (dependency) -> dict
    Decode the bearer token and return its raw claims (scopes + agent marker).

is_agent_caller(claims) / author_kind(claims)
    AI-authorship attribution derived from the token identity.

require_env_write(claims, resource, env, *, protected)
    Enforce env-scoped write tokens (agent sandbox); raise 403 when the token's
    write scopes do not authorise a write/promotion to *env*.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.jwt import decode_access_token
from app.errors import AppError

# Reuse the same bearer extractor shape as app.auth.deps so these dependencies
# can be added to a route alongside ``current_user`` without conflicting.
_bearer = HTTPBearer(auto_error=False)


def parse_scopes(claims: dict) -> list[str]:
    """Extract scopes from a decoded JWT claims dict.

    Accepts both a space-delimited string (``"scope"`` key, standard OAuth2
    format) and a list of strings (``"scope"`` or ``"scopes"`` key).

    Parameters
    ----------
    claims:
        Decoded JWT payload as returned by PyJWT.

    Returns
    -------
    list[str]
        Possibly-empty list of individual scope strings.
    """
    raw = claims.get("scope") or claims.get("scopes")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(s) for s in raw if s]
    # space-delimited string (RFC 6749)
    return [s for s in str(raw).split() if s]


def has_scope(scopes: list[str], required: str) -> bool:
    """Return ``True`` if *required* is satisfied by any entry in *scopes*.

    Wildcard rules (matched at trailing segment only):
    - ``"read:*"``            covers ``"read:anything"`` and ``"read:a:b"``
    - ``"read:dashboard:*"``  covers ``"read:dashboard:abc"`` but NOT
                              ``"read:other:abc"``
    - ``"*"``                 covers every scope (super-admin sentinel)

    Exact matches are also accepted.

    Parameters
    ----------
    scopes:
        List of scope strings from the token (as returned by :func:`parse_scopes`).
    required:
        The single scope that must be present or covered.

    Returns
    -------
    bool
    """
    for granted in scopes:
        if granted == required:
            return True
        if granted == "*":
            return True
        if granted.endswith(":*"):
            # "read:*" -> prefix is "read:"
            prefix = granted[:-1]  # everything before the trailing "*"
            if required.startswith(prefix):
                return True
    return False


def require_scope(claims: dict, required: str) -> None:
    """Assert that the claims carry the *required* scope.

    Parameters
    ----------
    claims:
        Decoded JWT payload.
    required:
        Scope string that must be present.

    Raises
    ------
    AppError("insufficient_scope", 403)
        If the scope is absent or not covered by any wildcard in the token.
    """
    scopes = parse_scopes(claims)
    if not has_scope(scopes, required):
        raise AppError(
            "insufficient_scope",
            f"Token does not carry the required scope: {required}",
            403,
        )


# ---------------------------------------------------------------------------
# Governance-as-agent-sandbox helpers
# ---------------------------------------------------------------------------
#
# Agent-scoped write tokens carry ``action:resource:env`` scopes (e.g.
# ``"write:board:dev"``) which restrict an automated caller to a single
# environment, plus an authorship marker so writes can be attributed to an AI
# agent vs a human.  Human / full-access first-party callers carry NO ``scope``
# claim at all — for them these helpers are no-ops so existing behavior and
# tests are preserved.


def caller_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    """Dependency: decode the bearer token and return its raw JWT claims.

    Unlike :func:`app.auth.deps.current_user` (which returns the DB user row),
    this surfaces the token's claims — including ``scope`` and the agent
    authorship marker — so routes can apply scope-based governance.

    Returns an empty dict when no credentials are present so it never raises on
    its own; the route's ``current_user`` dependency is the one that enforces
    authentication (401).
    """
    if credentials is None:
        return {}
    try:
        return decode_access_token(credentials.credentials)
    except AppError:
        # Authentication failures are surfaced by current_user; stay quiet here.
        return {}


def is_agent_caller(claims: dict[str, Any]) -> bool:
    """Return True when the token identifies the caller as an AI agent.

    Recognised markers (any one is sufficient):
    - ``actor`` / ``author_kind`` claim equal to ``"agent"``
    - a truthy ``agent`` claim
    - a scope of ``"actor:agent"`` (capability-style marker)
    """
    actor = str(claims.get("actor") or claims.get("author_kind") or "").lower()
    if actor == "agent":
        return True
    if claims.get("agent") is True:
        return True
    return "actor:agent" in parse_scopes(claims)


def author_kind(claims: dict[str, Any]) -> str:
    """Return ``"agent"`` or ``"human"`` for the caller (attribution stamp)."""
    return "agent" if is_agent_caller(claims) else "human"


def require_env_write(
    claims: dict[str, Any],
    resource: str,
    env: str,
    *,
    protected: bool = False,
) -> None:
    """Enforce env-scoped write tokens for a write/upsert targeting *env*.

    Rule
    ----
    A token that carries ANY ``write:`` scope is treated as an agent-scoped /
    restricted token: it may only write to *env* when it holds
    ``write:<resource>:<env>`` OR a broader wildcard (``write:<resource>:*``,
    ``write:*``, ``*``).  A token with NO ``write:`` scope is a full-access
    first-party caller (human dashboard session) and is allowed through —
    role-based guards (``require_writer``) already gate those.

    Protected environments
    -----------------------
    Promotion into a *protected* env (e.g. ``prod``) requires a broader-than-env
    scope: an exact ``write:<resource>:<env>`` token (a dev-scoped agent token)
    can never reach a protected env — only a resource-wide or global wildcard
    may.

    Raises
    ------
    AppError("insufficient_scope", 403)
        When the token's write scopes do not authorise this (resource, env).
    """
    scopes = parse_scopes(claims)
    write_scopes = [s for s in scopes if s == "*" or s.startswith("write:")]
    if not write_scopes:
        # No write scope on the token → full-access first-party caller.
        return

    # Wildcards that are broader than a single env always pass (and are the
    # ONLY way to reach a protected env).
    if has_scope(scopes, f"write:{resource}:*") or has_scope(scopes, "write:*"):
        return

    if protected:
        # Reaching a protected env requires more than an env-scoped token.
        raise AppError(
            "insufficient_scope",
            f"This token cannot write to the protected {env!r} environment; "
            f"promotion requires a broader write scope.",
            403,
        )

    if has_scope(scopes, f"write:{resource}:{env}"):
        return

    raise AppError(
        "insufficient_scope",
        f"This token is not scoped to write {resource!r} in the {env!r} "
        f"environment.",
        403,
    )
