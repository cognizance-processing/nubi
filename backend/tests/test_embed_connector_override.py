"""Mode 2: id-based whole-dashboard connector override for LIVE embeds.

What this suite verifies
------------------------
(a) An embed token carrying a ``datastore`` claim whose id is a datastore IN
    the token's org routes execution at THAT datastore — overriding the
    registered query's default binding (here: the demo connector).
(b) A ``datastore`` claim whose id is NOT in the token's org (a cross-org id,
    or simply absent) is rejected with 404 ``datastore_not_found`` BEFORE
    execution — the org-scoped repo lookup is the enforcement point.
(c) First-party (kind='access') tokens IGNORE the claim entirely (it can only
    ride on an embed token; a first-party caller routes at the registered
    query's default / the demo path as before).

Test strategy
-------------
- Reuses the RS256 embed-token + static-JWKS + InMemoryRepo patterns from
  ``tests/test_embed_rls.py`` and ``tests/test_query_connectors.py``.
- A synthetic ``_ProbeConnector`` (registered under type ``override_probe``)
  returns a one-row table carrying the connector-config marker it was built
  with, so the test can assert WHICH datastore the query routed at.
- The registered query ``demo_all`` has no datastore binding (demo path); the
  override claim must redirect it to the probe datastore.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Environment bootstrap (mirror test_embed_rls.py for standalone isolation).
# ---------------------------------------------------------------------------

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from jwt.algorithms import RSAAlgorithm
import jwt as pyjwt

from app.connectors.base import Connector
from app.connectors.plan import PhysicalPlan
from app.connectors.registry import get_connector_registry
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# ---------------------------------------------------------------------------
# RSA keypair + static JWKS (module-level, generated once)
# ---------------------------------------------------------------------------

_PRIVATE_KEY = rsa.generate_private_key(
    public_exponent=65537, key_size=2048, backend=default_backend()
)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
_JWKS_KEY: dict = json.loads(RSAAlgorithm.to_jwk(_PUBLIC_KEY))
_JWKS_KEY["kid"] = "override-test-key"
_JWKS_KEY["use"] = "sig"
_STATIC_JWKS: dict = {"keys": [_JWKS_KEY]}

_HOST_ISS = "https://override-host.example"
_HOST_AUD = "nubi"
_EMBED_ORIGIN = "https://override-host.example"
_KID = "override-test-key"
_ORG_ID = "override-org"


# ---------------------------------------------------------------------------
# Synthetic probe connector — returns the config marker it was built with so a
# test can assert WHICH datastore the query routed at.
# ---------------------------------------------------------------------------


class _ProbeConnector(Connector):
    """Connector that echoes its config's ``marker`` field as a single row."""

    def __init__(self, config: dict) -> None:
        self._marker = str((config or {}).get("marker", "unknown"))
        self.validate_capabilities()

    def capabilities(self) -> dict[str, bool]:
        return {
            "native_arrow": False,
            "predicate_pushdown": False,
            "projection_pushdown": False,
            "partition_pushdown": False,
            "predicate_rls": True,
            "column_masking": False,
            "streaming_cdc": False,
        }

    def execute(self, plan: PhysicalPlan) -> pa.Table:  # noqa: ARG002
        return pa.table({"marker": pa.array([self._marker], type=pa.string())})

    def execute_stream(self, plan: PhysicalPlan):  # noqa: ARG002
        yield from []  # pragma: no cover


get_connector_registry().register(
    "override_probe", lambda config: _ProbeConnector(config)
)


# ---------------------------------------------------------------------------
# Token mint helper
# ---------------------------------------------------------------------------


def _mint_embed_token(
    *,
    datastore: str | None = None,
    org: str = _ORG_ID,
    scope: list[str] | None = None,
    embed_origin: str | None = _EMBED_ORIGIN,
) -> str:
    """Mint a test embed JWT, optionally carrying a ``datastore`` claim."""
    now = datetime.now(tz=timezone.utc)
    payload: dict = {
        "iss": _HOST_ISS,
        "aud": _HOST_AUD,
        "sub": "embed-user-1",
        "org": org,
        "roles": ["viewer"],
        "policies": {},
        "scope": scope or ["read:query"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=300)).timestamp()),
    }
    if embed_origin is not None:
        payload["embed_origin"] = embed_origin
    if datastore is not None:
        payload["datastore"] = datastore
    return pyjwt.encode(payload, _PRIVATE_KEY, algorithm="RS256", headers={"kid": _KID})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _register_embed_issuer():
    from app.auth.issuers import get_issuer_registry
    from app.auth.jwks_cache import clear_cache
    from app.config import get_settings

    get_settings.cache_clear()
    registry = get_issuer_registry()
    registry.register(
        _HOST_ISS,
        jwks_uri=f"{_HOST_ISS}/.well-known/jwks.json",
        aud=_HOST_AUD,
        allowed_origins=[_EMBED_ORIGIN],
        static_jwks=_STATIC_JWKS,
    )
    yield
    registry.unregister(_HOST_ISS)
    clear_cache()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_query_cache():
    from app.connectors.cache import get_cache

    get_cache().clear()
    yield
    get_cache().clear()


@pytest_asyncio.fixture
async def override_repo():
    """InMemoryRepo with a probe datastore seeded in the token's org."""
    repo = InMemoryRepo()
    set_repo(repo)
    ds = await repo.create(
        "datastores",
        org_id=_ORG_ID,
        created_by="embed-user-1",
        name="Probe DS (in-org)",
        config={"type": "override_probe", "marker": "in-org-datastore"},
    )
    yield repo, ds["id"]
    set_repo(None)


def _parse_arrow(content: bytes) -> pa.Table:
    return pa_ipc.open_stream(BytesIO(content)).read_all()


# ===========================================================================
# (a) Embed token datastore claim (in org) routes execution at that datastore
# ===========================================================================


@pytest.mark.asyncio
async def test_embed_datastore_claim_overrides_routing(client, override_repo):
    """An in-org ``datastore`` claim redirects ALL queries to that datastore.

    ``demo_all`` has no datastore binding (would hit the demo path); the claim
    must override that and route at the probe datastore instead.
    """
    _repo, ds_id = override_repo
    token = _mint_embed_token(datastore=ds_id)

    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_all"},
        headers={"Authorization": f"Bearer {token}", "Origin": _EMBED_ORIGIN},
    )
    assert resp.status_code == 200, resp.text
    table = _parse_arrow(resp.content)
    assert table.column_names == ["marker"]
    assert table.to_pylist() == [{"marker": "in-org-datastore"}], (
        "Override did not route execution at the claimed datastore."
    )


# ===========================================================================
# (b) A datastore id NOT in the token's org is rejected
# ===========================================================================


@pytest.mark.asyncio
async def test_embed_datastore_claim_cross_org_rejected(client, override_repo):
    """A ``datastore`` claim id absent from the token's org → 404.

    Seed a probe datastore in a DIFFERENT org and put its id in the claim; the
    org-scoped repo lookup (scoped to identity.org) misses → datastore_not_found.
    Proves cross-org ids cannot be routed via the override claim.
    """
    repo, _in_org_id = override_repo
    other_ds = await repo.create(
        "datastores",
        org_id="other-org",
        created_by="someone-else",
        name="Probe DS (other org)",
        config={"type": "override_probe", "marker": "other-org-datastore"},
    )
    token = _mint_embed_token(datastore=other_ds["id"])

    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_all"},
        headers={"Authorization": f"Bearer {token}", "Origin": _EMBED_ORIGIN},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "datastore_not_found"


@pytest.mark.asyncio
async def test_embed_datastore_claim_unknown_id_rejected(client, override_repo):
    """A ``datastore`` claim with a completely unknown id → 404."""
    _repo, _ds_id = override_repo
    token = _mint_embed_token(datastore=str(uuid.uuid4()))

    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_all"},
        headers={"Authorization": f"Bearer {token}", "Origin": _EMBED_ORIGIN},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "datastore_not_found"


# ===========================================================================
# (c) First-party tokens IGNORE the claim
# ===========================================================================


@pytest.mark.asyncio
async def test_first_party_token_ignores_datastore_claim(client, override_repo):
    """A first-party HS256 token never carries the claim → demo path, not probe.

    First-party tokens are kind='access' and have no ``datastore`` claim, so
    ``demo_all`` runs against the demo connector and returns its rows — never
    the probe marker.
    """
    from app.auth.jwt import mint_access_token

    _repo, _ds_id = override_repo
    token = mint_access_token("user-fp-1")

    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_all"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    table = _parse_arrow(resp.content)
    # The demo path returns the demo dataset, NOT the single probe marker row.
    assert "marker" not in table.column_names
