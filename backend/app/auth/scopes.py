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
"""

from __future__ import annotations

from app.errors import AppError


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
