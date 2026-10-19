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
jwks_from_pasted_key(text) -> dict
    Convert a pasted PEM public key / JWK / JWKS into ``static_jwks_json``.
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

    # asyncpg may return JSONB columns as raw JSON strings on some
    # configurations.  Parse to dict here so callers always receive a dict.
    if isinstance(static_jwks, str):
        import json as _json  # noqa: PLC0415

        try:
            static_jwks = _json.loads(static_jwks)
        except (_json.JSONDecodeError, ValueError):
            static_jwks = None

    if static_jwks is not None:
        jwks = static_jwks
    elif jwks_url:
        from app.auth.jwks_cache import get_jwks  # noqa: PLC0415

        jwks = get_jwks(jwks_url)
    else:
        raise AppError("invalid_token", "Token is invalid or has expired.", 401)

    return _select_key_from_jwks(jwks, kid, alg)


def jwks_from_pasted_key(text: str) -> dict[str, Any]:
    """Turn a pasted public key into a JWKS dict for ``static_jwks_json``.

    Accepts a PEM-encoded RSA/EC public key (``-----BEGIN PUBLIC KEY-----``),
    a single JWK JSON object, or a full JWKS (``{"keys": [...]}``). The result
    is what :func:`resolve_signing_key` reads, so a pasted key verifies exactly
    like a fetched JWKS. Private keys are rejected — only public material may be
    stored.

    Raises
    ------
    AppError("invalid_public_key", 422)
        If *text* is not a usable public key.
    """
    import json as _json  # noqa: PLC0415

    from app.errors import AppError  # noqa: PLC0415

    def _bad(msg: str) -> AppError:
        return AppError("invalid_public_key", msg, 422)

    raw = (text or "").strip()
    if not raw:
        raise _bad("The pasted key is empty.")

    if raw.startswith("{"):
        try:
            data = _json.loads(raw)
        except ValueError as exc:
            raise _bad("The pasted key is not valid JSON.") from exc
        keys = data.get("keys") if isinstance(data, dict) and "keys" in data else [data]
        if not isinstance(keys, list) or not keys or not all(isinstance(k, dict) and k.get("kty") for k in keys):
            raise _bad("A JWK needs a 'kty'; a JWKS needs a non-empty 'keys' list.")
        # "d" (and RSA's p/q/dp/dq/qi) carry private-key material.
        if any(k.get("d") is not None for k in keys):
            raise _bad("That is a private key. Paste the PUBLIC key only.")
        return {"keys": keys}

    if "PRIVATE KEY" in raw:
        raise _bad("That is a private key. Paste the PUBLIC key only.")
    try:
        import jwt as _jwt  # noqa: PLC0415
        from cryptography.hazmat.primitives.asymmetric import ec, rsa  # noqa: PLC0415
        from cryptography.hazmat.primitives.serialization import load_pem_public_key  # noqa: PLC0415

        key = load_pem_public_key(raw.encode())
    except Exception as exc:  # noqa: BLE001 — any parse failure is a bad paste
        raise _bad("Could not read the PEM public key.") from exc

    if isinstance(key, rsa.RSAPublicKey):
        jwk = _jwt.algorithms.RSAAlgorithm.to_jwk(key, as_dict=True)
    elif isinstance(key, ec.EllipticCurvePublicKey):
        jwk = _jwt.algorithms.ECAlgorithm.to_jwk(key, as_dict=True)
    else:
        raise _bad("Only RSA and EC public keys are supported.")
    return {"keys": [{**jwk, "use": "sig"}]}
