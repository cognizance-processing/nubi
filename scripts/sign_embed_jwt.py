"""sign_embed_jwt.py — Host-side embed JWT signer for Nubi.

Mints a short-lived RS256 JWT that the Nubi backend will accept as a valid
embed token.  The token carries per-tenant RLS claims (``policies``), an
origin pin (``embed_origin``), and a limited scope (``read:*``).

Usage
-----
Quick start (generate a fresh keypair + register the issuer, then sign):

    python scripts/sign_embed_jwt.py --tenant acme-corp --org demo-org

This command:
1. Generates a 2048-bit RSA keypair (stored in /tmp/nubi-embed-*.pem).
2. Prints the JWKS JSON you must register with the Nubi backend.
3. Mints a signed JWT and prints it to stdout.

Full options
------------
    python scripts/sign_embed_jwt.py \\
        --tenant globex-inc \\
        --org my-org \\
        --sub service-account-1 \\
        --iss https://myapp.example.com \\
        --aud nubi \\
        --origin https://myapp.example.com \\
        --ttl 900 \\
        --private-key /path/to/private.pem \\
        --public-key  /path/to/public.pem

Environment variables (override CLI defaults)
---------------------------------------------
NUBI_EMBED_ISS          Issuer URI (default: https://embed-host.local)
NUBI_EMBED_AUD          Audience (default: nubi)
NUBI_EMBED_ORIGIN       embed_origin claim (default: http://localhost:8080)
NUBI_EMBED_ORG          org claim (default: demo-org)
NUBI_EMBED_PRIVATE_KEY  Path to PEM private key
NUBI_EMBED_PUBLIC_KEY   Path to PEM public key

Output format
-------------
The script writes a single-line JSON to stdout containing:
    {
        "token":       "<signed JWT>",
        "jwks":        { "keys": [...] },   // register this with Nubi
        "claims":      { ... }              // the decoded payload (for inspection)
    }

Use --raw to print just the JWT string (no JSON wrapper), useful for piping:

    TOKEN=$(python scripts/sign_embed_jwt.py --tenant acme --raw)
    curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/query ...

Backend registration
--------------------
Before tokens from this signer are accepted by the Nubi backend you must
register the issuer + JWKS.  For local dev/self-host, call the registry in
a startup hook or seed script:

    from app.auth.issuers import get_issuer_registry
    registry = get_issuer_registry()
    registry.register(
        "https://embed-host.local",
        jwks_uri="https://embed-host.local/.well-known/jwks.json",
        aud="nubi",
        allowed_origins=["http://localhost:8080"],
        static_jwks=<paste the jwks dict printed by this script>,
    )

For a production deployment, expose a JWKS endpoint at the URI matching
`iss` + "/.well-known/jwks.json" so the backend can fetch it dynamically.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Lazy-import heavy crypto deps with a clear error message
# ---------------------------------------------------------------------------

def _require(pkg: str, pip_name: str | None = None) -> Any:
    """Import *pkg* or exit with an install hint."""
    import importlib  # noqa: PLC0415
    try:
        return importlib.import_module(pkg)
    except ImportError:
        install = pip_name or pkg
        print(
            f"[sign_embed_jwt] Missing dependency: {pkg!r}\n"
            f"  Install with:  pip install {install}",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Key generation helpers
# ---------------------------------------------------------------------------

def generate_rsa_keypair() -> tuple[Any, Any]:
    """Generate a new 2048-bit RSA keypair.

    Returns
    -------
    (private_key, public_key)
        Cryptography library key objects.
    """
    _crypto_rsa = _require("cryptography.hazmat.primitives.asymmetric.rsa", "cryptography")
    from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: PLC0415
    from cryptography.hazmat.backends import default_backend  # noqa: PLC0415

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    return private_key, private_key.public_key()


def load_private_key(path: str) -> Any:
    """Load a PEM RSA private key from *path*."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key  # noqa: PLC0415
    from cryptography.hazmat.backends import default_backend  # noqa: PLC0415

    with open(path, "rb") as fh:
        return load_pem_private_key(fh.read(), password=None, backend=default_backend())


def private_key_to_pem(private_key: Any) -> str:
    """Serialize *private_key* to PEM string."""
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415

    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def public_key_to_pem(public_key: Any) -> str:
    """Serialize *public_key* to PEM string."""
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415

    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def public_key_to_jwk(public_key: Any, kid: str = "embed-key-1") -> dict[str, Any]:
    """Convert *public_key* to a JWK dict with a ``kid`` and ``use=sig``."""
    from jwt.algorithms import RSAAlgorithm  # noqa: PLC0415

    jwk: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk["kid"] = kid
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return jwk


# ---------------------------------------------------------------------------
# JWT minting
# ---------------------------------------------------------------------------

def mint_embed_jwt(
    *,
    private_key: Any,
    kid: str,
    iss: str,
    aud: str,
    sub: str,
    org: str,
    tenant_id: str,
    embed_origin: str,
    scope: list[str],
    ttl_seconds: int,
    extra_policies: dict[str, str] | None = None,
) -> str:
    """Mint a signed RS256 embed JWT.

    Parameters
    ----------
    private_key:
        RSA private key object (from cryptography library).
    kid:
        Key identifier placed in the JOSE header (must match the JWKS entry).
    iss:
        Issuer URI — must be registered in the Nubi backend issuer registry.
    aud:
        Audience string — must match the ``aud`` in the issuer config.
    sub:
        Subject identifier (end-user or service account).
    org:
        Nubi org slug — resolves which org's data is queried.
    tenant_id:
        Per-tenant RLS value.  Injected as ``policies.tenant_id``.
    embed_origin:
        The exact ``Origin`` header the embedded iframe/page will send.
        The backend enforces this claim — requests from other origins are
        rejected with 403 ``origin_mismatch``.
    scope:
        OAuth2-style scope list.  Must include at least ``"read:*"`` or
        ``"read:query"`` for dashboard access.
    ttl_seconds:
        Lifetime of the token in seconds (recommended: 900 = 15 min).
    extra_policies:
        Additional RLS column→value pairs merged into ``policies``.

    Returns
    -------
    str
        Signed JWT string.
    """
    import jwt as pyjwt  # noqa: PLC0415

    now = datetime.now(tz=timezone.utc)
    policies: dict[str, str] = {"tenant_id": tenant_id}
    if extra_policies:
        policies.update(extra_policies)

    payload: dict[str, Any] = {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "org": org,
        "roles": ["viewer"],
        "scope": scope,
        "policies": policies,
        "embed_origin": embed_origin,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }

    token: str = pyjwt.encode(
        payload,
        private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )
    return token


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sign_embed_jwt.py",
        description="Mint a Nubi host-signed embed JWT (RS256) with per-tenant RLS claims.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--tenant",
        default="acme-corp",
        metavar="TENANT_ID",
        help="Value for policies.tenant_id RLS claim (default: acme-corp).",
    )
    p.add_argument(
        "--org",
        default=os.environ.get("NUBI_EMBED_ORG", "demo-org"),
        metavar="ORG_SLUG",
        help="Nubi org slug (default: demo-org or NUBI_EMBED_ORG env var).",
    )
    p.add_argument(
        "--sub",
        default="embed-service-account",
        metavar="SUBJECT",
        help="JWT sub claim — the end-user or service account ID.",
    )
    p.add_argument(
        "--iss",
        default=os.environ.get("NUBI_EMBED_ISS", "https://embed-host.local"),
        metavar="ISSUER_URI",
        help="Issuer URI (default: https://embed-host.local or NUBI_EMBED_ISS).",
    )
    p.add_argument(
        "--aud",
        default=os.environ.get("NUBI_EMBED_AUD", "nubi"),
        metavar="AUDIENCE",
        help="JWT aud claim (default: nubi or NUBI_EMBED_AUD).",
    )
    p.add_argument(
        "--origin",
        default=os.environ.get("NUBI_EMBED_ORIGIN", "http://localhost:8080"),
        metavar="ORIGIN",
        help="embed_origin claim — the exact Origin the host page sends (default: http://localhost:8080).",
    )
    p.add_argument(
        "--ttl",
        type=int,
        default=900,
        metavar="SECONDS",
        help="Token lifetime in seconds (default: 900 = 15 min).",
    )
    p.add_argument(
        "--scope",
        default="read:*",
        metavar="SCOPE",
        help="Space-separated scope string (default: 'read:*').",
    )
    p.add_argument(
        "--private-key",
        default=os.environ.get("NUBI_EMBED_PRIVATE_KEY", ""),
        metavar="PATH",
        help="Path to PEM RSA private key.  If omitted a fresh keypair is generated.",
    )
    p.add_argument(
        "--public-key",
        default=os.environ.get("NUBI_EMBED_PUBLIC_KEY", ""),
        metavar="PATH",
        help="Path to PEM RSA public key (required only when --private-key is given).",
    )
    p.add_argument(
        "--kid",
        default="embed-key-1",
        metavar="KID",
        help="Key ID placed in the JOSE header (default: embed-key-1).",
    )
    p.add_argument(
        "--raw",
        action="store_true",
        help="Print only the JWT string (no JSON wrapper).",
    )
    p.add_argument(
        "--save-keys",
        metavar="DIR",
        default="",
        help="Directory to write the generated PEM files (default: /tmp).",
    )
    return p


def main() -> None:
    """Entry point for the CLI."""
    # Ensure heavy deps present
    _require("cryptography", "cryptography")
    _require("jwt", "PyJWT")

    parser = _build_parser()
    args = parser.parse_args()

    # ── Key material ──────────────────────────────────────────────────────────
    if args.private_key:
        # Load caller-supplied key
        private_key = load_private_key(args.private_key)
        public_key = private_key.public_key()
        key_source = "supplied"
    else:
        # Generate a fresh keypair
        private_key, public_key = generate_rsa_keypair()
        key_source = "generated"

        # Optionally persist the generated keys
        save_dir = args.save_keys or tempfile.gettempdir()
        priv_path = Path(save_dir) / "nubi-embed-private.pem"
        pub_path = Path(save_dir) / "nubi-embed-public.pem"
        priv_path.write_text(private_key_to_pem(private_key))
        pub_path.write_text(public_key_to_pem(public_key))
        if not args.raw:
            print(f"[sign_embed_jwt] Generated keypair saved to:", file=sys.stderr)
            print(f"  Private key: {priv_path}", file=sys.stderr)
            print(f"  Public  key: {pub_path}", file=sys.stderr)

    # ── Build JWKS ────────────────────────────────────────────────────────────
    jwk_entry = public_key_to_jwk(public_key, kid=args.kid)
    jwks: dict[str, Any] = {"keys": [jwk_entry]}

    # ── Mint token ────────────────────────────────────────────────────────────
    scope_list = args.scope.split()
    token = mint_embed_jwt(
        private_key=private_key,
        kid=args.kid,
        iss=args.iss,
        aud=args.aud,
        sub=args.sub,
        org=args.org,
        tenant_id=args.tenant,
        embed_origin=args.origin,
        scope=scope_list,
        ttl_seconds=args.ttl,
    )

    # ── Output ────────────────────────────────────────────────────────────────
    if args.raw:
        print(token)
        return

    # Decode the payload for display (unverified — we just minted it)
    import jwt as pyjwt  # noqa: PLC0415
    claims = pyjwt.decode(token, options={"verify_signature": False}, algorithms=["RS256"])

    result = {
        "token": token,
        "jwks": jwks,
        "claims": claims,
        "meta": {
            "key_source": key_source,
            "iss": args.iss,
            "aud": args.aud,
            "tenant_id": args.tenant,
            "org": args.org,
            "embed_origin": args.origin,
            "ttl_seconds": args.ttl,
        },
    }

    print(json.dumps(result, indent=2))

    # ── Registration hint ─────────────────────────────────────────────────────
    print(
        "\n--- Backend registration (add to startup / seed script) ---",
        file=sys.stderr,
    )
    print(
        f"""
from app.auth.issuers import get_issuer_registry
registry = get_issuer_registry()
registry.register(
    {args.iss!r},
    jwks_uri={args.iss + '/.well-known/jwks.json'!r},
    aud={args.aud!r},
    allowed_origins=[{args.origin!r}],
    static_jwks={json.dumps(jwks)},
)
""",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
