"""Embed tokens are short-lived, bound to their origin and board, and revocable."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

import jwt as pyjwt  # noqa: E402
from cryptography.hazmat.backends import default_backend  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from jwt.algorithms import RSAAlgorithm  # noqa: E402

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
_JWK = json.loads(RSAAlgorithm.to_jwk(_KEY.public_key()))
_JWK.update({"kid": "k1", "use": "sig"})
JWKS = {"keys": [_JWK]}
ISS = "https://host.limits.example"
AUD = "nubi-embed"
ORG = str(uuid.uuid4())
ORIGIN = "https://app.host.example"


def _token(*, ttl=300, iat=True, **claims) -> str:
    now = datetime.now(timezone.utc)
    payload = {"iss": ISS, "aud": AUD, "sub": "viewer-1", "org": ORG, "exp": now + timedelta(seconds=ttl),
               "scope": "read:*", **claims}
    if iat:
        payload["iat"] = now
    return pyjwt.encode(payload, _KEY, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture(autouse=True)
def _issuer():
    from app.auth.denylist import InMemoryTokenDenylist, set_token_denylist_for_tests
    from app.auth.issuers import get_issuer_registry

    reg = get_issuer_registry()
    reg.clear()
    reg.register(ISS, jwks_uri="", aud=AUD, static_jwks=JWKS, org_id=ORG)
    set_token_denylist_for_tests(InMemoryTokenDenylist())
    yield reg
    reg.clear()
    set_token_denylist_for_tests(None)


async def _verify(token, origin=None):
    from app.auth.verify import verify_token_async

    return await verify_token_async(token, expected_origin=origin)


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"ttl": 16 * 60}, {"ttl": 24 * 3600}, {"iat": False}], ids=["16m", "1d", "no-iat"])
async def test_long_lived_or_undated_tokens_are_refused(kwargs):
    from app.errors import AppError

    with pytest.raises(AppError) as exc:
        await _verify(_token(**kwargs))
    assert exc.value.status == 401


@pytest.mark.asyncio
async def test_short_lived_token_verifies():
    assert (await _verify(_token(ttl=15 * 60))).org == ORG


@pytest.mark.asyncio
async def test_issuer_allowed_origins_are_enforced(_issuer):
    from app.errors import AppError

    _issuer.register(ISS, jwks_uri="", aud=AUD, static_jwks=JWKS, org_id=ORG, allowed_origins=[ORIGIN])
    assert (await _verify(_token(), origin=ORIGIN)).org == ORG
    for origin in ("https://evil.example", None):
        with pytest.raises(AppError) as exc:
            await _verify(_token(), origin=origin)
        assert exc.value.status == 403


def _identity(**claims):
    from app.auth.verify import VerifiedIdentity

    return VerifiedIdentity(
        kind="embed", user_id="viewer-1", org=ORG, project=None, roles=[], policies={},
        scope=["read:*"], embed_origin=None, datastore=None, raw_claims={"iss": ISS, **claims},
    )


def test_board_claim_limits_which_boards_a_token_opens():
    from app.auth.verify import embed_board_scope
    from app.errors import AppError
    from app.routes.embed import require_board_in_token

    assert embed_board_scope(_identity()) is None
    assert embed_board_scope(_identity(board="b1")) == {"b1"}
    assert embed_board_scope(_identity(boards=["b1", "b2"])) == {"b1", "b2"}
    assert embed_board_scope(_identity(dashboard="b3")) == {"b3"}

    require_board_in_token(_identity(), "anything")
    require_board_in_token(_identity(board="b1"), "b1")
    with pytest.raises(AppError) as exc:
        require_board_in_token(_identity(board="b1"), "b2")
    assert exc.value.status == 403
    # A malformed restriction fails closed.
    with pytest.raises(AppError):
        require_board_in_token(_identity(board={"x": 1}), "b1")


@pytest.mark.asyncio
async def test_board_claim_limits_which_queries_a_token_runs():
    from app.errors import AppError
    from app.repos.memory import InMemoryRepo
    from app.routes.query import require_query_in_token_boards

    repo = InMemoryRepo()
    board = await repo.create(
        resource="boards", org_id=ORG, created_by="u", name="B",
        config={"spec": {"title": "B", "widgets": [{"id": "w1", "type": "chart", "query_id": "q_on_board"}]}},
    )
    bid = str(board["id"])
    await require_query_in_token_boards(_identity(), "any_query", repo)  # unrestricted token
    await require_query_in_token_boards(_identity(board=bid), "q_on_board", repo)
    with pytest.raises(AppError) as exc:
        await require_query_in_token_boards(_identity(board=bid), "q_elsewhere", repo)
    assert exc.value.status == 403
    with pytest.raises(AppError):
        await require_query_in_token_boards(_identity(board="missing-board"), "q_on_board", repo)


def test_routes_apply_the_board_restriction():
    import inspect

    from app.routes import embed, query

    assert "require_board_in_token" in inspect.getsource(embed.get_embed_config)
    assert "require_query_in_token_boards" in inspect.getsource(query._resolve_request_plan)


@pytest.mark.asyncio
async def test_a_revoked_embed_token_stops_working():
    from fastapi.security import HTTPAuthorizationCredentials
    from starlette.requests import Request

    from app.auth.denylist import get_token_denylist
    from app.auth.deps import embed_revocation_key, verified_identity
    from app.errors import AppError

    token = _token(jti="tok-1")

    def _request():
        return Request({"type": "http", "method": "GET", "path": "/api/v1/query", "headers": [], "query_string": b""})

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    identity = await verified_identity(_request(), creds)
    assert identity.kind == "embed"

    await get_token_denylist().revoke(
        embed_revocation_key(ORG, ISS, "tok-1"), datetime.now(timezone.utc) + timedelta(minutes=15)
    )
    with pytest.raises(AppError) as exc:
        await verified_identity(_request(), creds)
    assert exc.value.status == 401
    # Another token from the same issuer is unaffected.
    other = HTTPAuthorizationCredentials(scheme="Bearer", credentials=_token(jti="tok-2"))
    assert (await verified_identity(_request(), other)).kind == "embed"


# ---------------------------------------------------------------------------
# A board-bound token is bound everywhere, not only on the two main routes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path,ok",
    [
        ("GET", "/api/v1/embed/config/b1", True),
        ("GET", "/api/v1/embed/frozen/b1", True),
        ("POST", "/api/v1/query", True),
        ("GET", "/api/v1/metrics", True),
        ("GET", "/api/v1/metrics/m1", True),
        ("POST", "/api/v1/metrics/m1/query", True),
        ("POST", "/api/v1/boards/b1/providers/p1/data", True),
        ("POST", "/api/v1/metrics/m1/sql", False),
        ("GET", "/api/v1/query/registry", False),
        ("GET", "/api/v1/query/registry/q1", False),
        ("GET", "/api/v1/health/freshness", False),
        ("GET", "/api/v1/health/drift", False),
        ("GET", "/api/v1/watches", False),
        ("POST", "/api/v1/ai/chat", False),
        ("POST", "/api/v1/compute/run", False),
        ("GET", "/api/v1/embed/config/b1/../../boards", False),
        ("DELETE", "/api/v1/metrics/m1", False),
    ],
)
def test_board_bound_tokens_reach_only_the_board_data_routes(method, path, ok):
    from app.auth.verify import board_bound_route_allowed

    assert board_bound_route_allowed(method, path) is ok


def test_every_token_is_checked_against_that_list():
    import inspect

    from app.auth import deps

    assert "require_board_bound_route" in inspect.getsource(deps.verified_identity)

    from app.auth.deps import require_board_bound_route
    from app.errors import AppError

    require_board_bound_route(_identity(), "GET", "/api/v1/watches")  # unrestricted
    require_board_bound_route(_identity(board="b1"), "POST", "/api/v1/query")
    for bad in (_identity(board="b1"), _identity(board={"x": 1})):
        with pytest.raises(AppError) as exc:
            require_board_bound_route(bad, "GET", "/api/v1/watches")
        assert exc.value.status == 403


@pytest.mark.asyncio
async def test_board_claim_limits_which_metrics_a_token_reads():
    from app.errors import AppError
    from app.repos.memory import InMemoryRepo
    from app.routes.query import require_ref_in_token_boards, token_board_refs

    repo = InMemoryRepo()
    board = await repo.create(
        resource="boards", org_id=ORG, created_by="u", name="B",
        config={"spec": {"title": "B", "widgets": [
            {"id": "w1", "type": "kpi", "metric": {"metric_id": "m_on_board"}},
        ]}},
    )
    bid = str(board["id"])
    keys = ("metric_id",)
    assert await token_board_refs(_identity(), repo, keys) is None  # unrestricted
    assert await token_board_refs(_identity(board=bid), repo, keys) == {"m_on_board"}
    await require_ref_in_token_boards(_identity(board=bid), ["m_on_board", "uuid-x"], repo, keys)
    with pytest.raises(AppError) as exc:
        await require_ref_in_token_boards(_identity(board=bid), ["m_elsewhere"], repo, keys)
    assert exc.value.status == 403


def test_metric_provider_and_frozen_routes_apply_the_board_restriction():
    import inspect

    from app.routes import dashboards, metrics, snapshot

    for fn in (metrics.list_metrics, metrics.get_metric, metrics.query_metric):
        assert "token_board_refs" in inspect.getsource(fn) or "_require_metric_on_token_boards" in inspect.getsource(fn)
    assert "require_board_in_token" in inspect.getsource(dashboards.get_provider_data)
    frozen = next(
        f for _, f in inspect.getmembers(snapshot, inspect.iscoroutinefunction)
        if "frozen" in f.__name__
    )
    assert "require_board_in_token" in inspect.getsource(frozen)
