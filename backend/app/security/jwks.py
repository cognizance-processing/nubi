"""JWKS fetch + key resolution for the managed-issuers verification path.

This module is the higher-level counterpart to ``app.auth.jwks_cache``.
It resolves the signing key for a token from a ``JwtIssuerRow`` (as returned
by :mod:`app.security.issuers_store`), supporting:

* ``static_jwks_json``  — pre-built JWKS dict stored in the DB row; no network
  fetch required.
* ``jwks_url``          — remote HTTPS endpoint; fetched and TTL-cached via
  :func:`app.auth.jwks_cache.get_jwks`.

The ``kid`` and ``alg`` from the token's JOSE header are used to select the
correct key from the JWKS.

Public API
----------
resolve_signing_key(issuer_row, kid, alg) -> Any
    Return a public key object (or PEM string) suitable for ``jwt.decode``.
"""

from __future__ import annotations

from typing import Any


def resolve_signing_key(
    issuer_row: dict[str, Any],
    kid: str | None,
    alg: str,
) -> Any:
    """Return the public key for verifying a token from *issuer_row*.

    Key resolution priority
    -----------------------
    1. ``static_jwks_json`` in the DB row — no network call.
    2. ``jwks_url`` — fetched and TTL-cached via
       :func:`app.auth.jwks_cache.get_jwks`.

    Parameters
    ----------
    issuer_row:
        A ``JwtIssuerRow`` dict from the issuers store.
    kid:
        The ``kid`` from the token's JOSE header (may be ``None``).
    alg:
        The ``alg`` from the token's JOSE header (e.g. ``"RS256"``).

    Returns
    -------
    Any
        A public key object accepted by ``PyJWT``'s ``jwt.decode`` call.

    Raises
    ------
    AppError("invalid_token", 401)
        If neither ``static_jwks_json`` nor ``jwks_url`` is configured, or if
        the JWKS cannot be fetched / parsed, or if no usable key is found.
    """
    from app.auth.verify import _select_key_from_jwks  # noqa: PLC0415
    from app.errors import AppError  # noqa: PLC0415

    static_jwks: dict[str, Any] | None = issuer_row.get("static_jwks_json")
    jwks_url: str | None = issuer_row.get("jwks_url")

    if static_jwks is not None:
        jwks = static_jwks
    elif jwks_url:
        from app.auth.jwks_cache import get_jwks  # noqa: PLC0415

        jwks = get_jwks(jwks_url)
    else:
        raise AppError("invalid_token", "Token is invalid or has expired.", 401)

    return _select_key_from_jwks(jwks, kid, alg)
