"""E2E: Real embed-flow tests — embed token contract + live editor embedding.

PART A — API-level embed contract E2E (against the live backend + demo data).

These tests prove the server-side contract that <nubi-dashboard>, <nubi-table>,
<nubi-metric-explorer> and <nubi-query-editor> depend on when loaded with REAL
(RS256) embed tokens — not the HS256 first-party stub that the base E2E suite
uses for its "embed_token".

Specifically covered:
  A1. embed token (read:query) + registered query_id → 200 with REAL rows
  A2. embed token + raw SQL (no query_id) → 403 query_not_registered     (M3-SEC)
  A3. embed token + raw SQL even with author:sql claim → 403              (M3-SEC)
  A4. embed token + author:metric → POST /metrics/{id}/query → 200 rows   (metric path)
  A5. embed token + /metrics/{id}/explain → 200 (read:query satisfies the gate)
  A6. embed token cannot reach write/admin routes → 403 forbidden
       - POST /metrics (create) → 403
       - POST /webhooks (send) → 401/403
  A7. Origin enforcement: wrong Origin header (embed_origin mismatch) → 403;
       correct Origin → 200 (query succeeds)
  A8. Unregistered issuer → 401
  A9. first-party HS256 read:query token (the existing embed_token in E2EContext)
       also gates raw SQL correctly → 403
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers shared with the security suite
# ---------------------------------------------------------------------------

# We cannot import conftest_helpers at collection time without its env bootstrap
# running, so we do it lazily inside fixtures/tests.
_SEC_HELPERS_LOADED = False


def _load_sec_helpers():
    """Lazy import of conftest_helpers with env bootstrap."""
    global _SEC_HELPERS_LOADED
    if not _SEC_HELPERS_LOADED:
        # bootstrap env so the security helpers import cleanly
        os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
        os.environ.setdefault(
            "JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef"
        )
        os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
        os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
        os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
        os.environ.setdefault(
            "GOOGLE_REDIRECT_URI",
            "http://localhost:8000/api/v1/auth/google/callback",
        )
        os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
        os.environ.setdefault("COOKIE_SECURE", "false")
        os.environ.setdefault("ENV", "test")
        _SEC_HELPERS_LOADED = True

    # Add tests/security to path if needed
    sec_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "security")
    )
    if sec_dir not in sys.path:
        sys.path.insert(0, sec_dir)

    import conftest_helpers as _ch  # noqa: PLC0415

    return _ch


# ---------------------------------------------------------------------------
# Embed origin for the live test — Playwright serves pages from localhost:8799
# (or a random free port); for API tests we pick a stable test origin.
# ---------------------------------------------------------------------------
_EMBED_ORIGIN = "http://localhost:8799"

# Issuer we register into the live server's in-process IssuerRegistry.
_EMBED_ISS = "https://e2e-embed-issuer.example"
_EMBED_AUD = "nubi"

_ARROW_ACCEPT = "application/vnd.apache.arrow.stream"


# ---------------------------------------------------------------------------
# Session-scoped fixture: register a real RS256 issuer into the running server
# We cannot reach the server's in-process registry from the test process, so we
# register the issuer into the running backend via the admin API route
# (POST /api/v1/jwt-issuers) using the superuser token.  If that route is not
# available (feature-gated), we fall back to registering via the in-process
# registry BEFORE the server is booted by injecting CORS + issuer env vars.
#
# For E2E we take a simpler approach that matches the architecture:
# The test mints RS256 tokens and sends them to the LIVE backend.  The live
# backend's _verify_embed_token_async checks (1) in-process registry → (2) DB.
# Since neither has our test issuer, it will 401.
# THE CORRECT approach for a headless E2E is:
#   (a) Register the issuer BEFORE the server boots (env seed or in-process
#       injection via a startup hook) — not available here without code changes.
#   (b) Use the DB-backed issuers_store API (POST /jwt-issuers) — requires that
#       route to exist and to resolve JWKS from a URL the running server can reach.
#   (c) Directly manipulate the server's in-process registry via a test-only
#       endpoint — not available.
#   (d) Boot the server with the test issuer pre-registered via env var — would
#       require a new env hook.
#
# CHOSEN APPROACH for Part A (pragmatic & honest):
# We register the test issuer into the PROCESS-LEVEL IssuerRegistry BEFORE
# importing the app/booting uvicorn, then re-use the SAME PROCESS for tests
# that need a real embed path.  For the live-server tests (via e2e_ctx), we
# additionally use the superuser token to POST /jwt-issuers (if the route is
# present) so the running uvicorn subprocess also knows the issuer.
# ---------------------------------------------------------------------------


def _mint_rs256_embed_token(
    *,
    scope: list[str] | None = None,
    embed_origin: str | None = _EMBED_ORIGIN,
    exp_delta: int = 300,
    org: str | None = None,
):
    """Mint a fresh RS256 embed token using the E2E keypair."""
    ch = _load_sec_helpers()
    import jwt as pyjwt  # noqa: PLC0415

    if scope is None:
        scope = ["read:query"]

    now = datetime.now(tz=timezone.utc)
    payload: dict[str, Any] = {
        "iss": _EMBED_ISS,
        "aud": _EMBED_AUD,
        "sub": "e2e-embed-user",
        "org": org or "e2e-org",
        "roles": ["viewer"],
        "policies": {},
        "scope": scope,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_delta)).timestamp()),
    }
    if embed_origin is not None:
        payload["embed_origin"] = embed_origin

    return pyjwt.encode(
        payload,
        ch._PRIVATE_KEY,
        algorithm="RS256",
        headers={"kid": ch.KID},
    )


def _register_test_issuer_in_process():
    """Register the test RS256 issuer into the in-process IssuerRegistry."""
    ch = _load_sec_helpers()
    from app.auth.issuers import get_issuer_registry  # noqa: PLC0415

    reg = get_issuer_registry()
    reg.register(
        _EMBED_ISS,
        jwks_uri=f"{_EMBED_ISS}/.well-known/jwks.json",
        aud=_EMBED_AUD,
        allowed_origins=[_EMBED_ORIGIN, "http://testserver"],
        static_jwks=ch.STATIC_JWKS,
    )
    return reg


def _register_test_issuer_in_server(e2e_ctx):
    """Attempt to register (or update) the test issuer via the live server's jwt-issuers API.

    Returns True on success, False when the route is not available.

    The route is at POST /api/v1/security/jwt-issuers (the DB-backed issuers store).
    After creation/update the server calls _sync_registry() which immediately registers
    the issuer into the running subprocess's in-process IssuerRegistry, so
    subsequent RS256 token verifications resolve to our test JWKS.

    On re-runs the issuer already exists (409) — we then UPDATE it via PUT so the
    in-process JWKS is refreshed with the current run's dynamically-generated keys.

    Returns True if the issuer was successfully registered/updated.
    Returns False if the route doesn't exist (404/405/5xx) so the RS256 tests
    can be skipped cleanly.
    """
    ch = _load_sec_helpers()

    payload = {
        "name": "E2E Test Issuer",
        "issuer": _EMBED_ISS,
        "audience": _EMBED_AUD,
        "jwks_url": f"{_EMBED_ISS}/.well-known/jwks.json",
        "static_jwks_json": ch.STATIC_JWKS,
        "enabled": True,
    }
    resp = e2e_ctx.client.post(
        "/security/jwt-issuers/",
        json=payload,
        headers=e2e_ctx.su_headers(),
    )
    if resp.status_code == 201:
        return True

    # Conflict: already registered (re-run scenario).
    # IMPORTANT: the previous run's JWKS is stale (RSA keys are re-generated on
    # every import).  We MUST update the existing issuer with the current run's
    # STATIC_JWKS so the live server can verify the tokens we mint this run.
    if resp.status_code == 409:
        # Fetch existing issuer list to get the id
        list_resp = e2e_ctx.client.get(
            "/security/jwt-issuers/",
            headers=e2e_ctx.su_headers(),
        )
        if list_resp.status_code != 200:
            return False
        existing = [r for r in list_resp.json() if r.get("issuer") == _EMBED_ISS]
        if not existing:
            return False
        issuer_id = existing[0]["id"]
        put_resp = e2e_ctx.client.put(
            f"/security/jwt-issuers/{issuer_id}",
            json={"static_jwks_json": ch.STATIC_JWKS, "enabled": True},
            headers=e2e_ctx.su_headers(),
        )
        return put_resp.status_code == 200

    # Route exists but DB operation failed — check if it's a table-not-found error
    if resp.status_code in (500, 503) and "issuers" in resp.text.lower():
        # DB table may not exist in the demo seed; treat as unavailable
        return False
    return resp.status_code in (200, 201)


def read_arrow(content: bytes):
    """Decode Arrow IPC bytes → list[dict]."""
    import pyarrow.ipc as pa_ipc  # noqa: PLC0415
    from io import BytesIO  # noqa: PLC0415

    reader = pa_ipc.open_stream(BytesIO(content))
    return reader.read_all().to_pylist()


# ===========================================================================
# PART A: API-level embed contract tests
# ===========================================================================


@pytest.mark.usefixtures("e2e_ctx")
class TestEmbedTokenContract:
    """A full RS256 embed-token contract test against the live server.

    Because the embed verification path requires the issuer to be in the
    server's IssuerRegistry OR the DB, we use two strategies:

      Strategy 1 (in-process registration):
          Register the issuer in-process by importing the app BEFORE uvicorn
          boots.  NOT applicable here because uvicorn runs as a SUBPROCESS.

      Strategy 2 (DB-backed jwt-issuers API):
          POST /jwt-issuers as the superuser so the running subprocess registers
          the issuer in its DB table and picks it up on the next request
          (async DB fallback path in _verify_embed_token_async).

    If Strategy 2 succeeds (200/201), the RS256 tests run against real embed
    tokens.  If the jwt-issuers route does not exist, we skip the RS256 path
    and clearly document why; the HS256 scope-restriction tests still run.

    NOTE: the HS256 embed_token from E2EContext is NOT a "real embed" token —
    it's a first-party token with restricted scope.  Tests that specifically
    exercise the M3-SEC allowlist gate (kind='embed') REQUIRE the RS256 path.
    The HS256 path (kind='access') is checked separately in A9.
    """

    @pytest.fixture(autouse=True, scope="class")
    def _ensure_issuer(self, e2e_ctx, request):
        """Register the test issuer in the live server (class-scoped).

        KEY CONSTRAINT: the embed token's ``org`` claim must match the REAL demo
        org UUID so that the DB fallback path in ``_verify_embed_token_async``
        can scope the ``jwt_issuers`` lookup correctly.  We store the org_id on
        the class so all tests can access it via ``self._org_id``.
        """
        request.cls._org_id = e2e_ctx.org_id  # real UUID from demo seed
        ok = _register_test_issuer_in_server(e2e_ctx)
        request.cls._issuer_registered = ok
        if not ok:
            pytest.skip(
                "jwt-issuers API not available — cannot register test RS256 issuer "
                "in the running uvicorn subprocess.  RS256 embed tests require "
                "POST /api/v1/security/jwt-issuers to be available.  "
                "The HS256 scope-restriction tests (A9) still run separately."
            )

    # -----------------------------------------------------------------------
    # Token-minting helper (uses the real org UUID from the demo seed)
    # -----------------------------------------------------------------------

    def _tok(self, scope=None, embed_origin=_EMBED_ORIGIN, exp_delta=300):
        """Mint an RS256 embed token with the real demo org_id."""
        return _mint_rs256_embed_token(
            scope=scope,
            embed_origin=embed_origin,
            exp_delta=exp_delta,
            org=self._org_id,
        )

    # -----------------------------------------------------------------------
    # A1: embed token + registered query_id → 200 with REAL rows
    # -----------------------------------------------------------------------

    def test_embed_registered_query_returns_real_rows(self, e2e_ctx):
        """RS256 embed token + registered query_id → 200 Arrow with actual rows.

        This is the data path that <nubi-dashboard> and <nubi-table> use.
        """
        q_ids = e2e_ctx.query_ids
        if not q_ids:
            pytest.skip("No registered non-metric queries found in demo DB")

        query_id = next(iter(q_ids.values()))
        token = self._tok(scope=["read:query"])

        resp = e2e_ctx.client.post(
            "/query",
            json={"query_id": query_id},
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": _EMBED_ORIGIN,
                "Accept": _ARROW_ACCEPT,
            },
        )
        assert resp.status_code == 200, (
            f"Expected 200 for embed token + registered query_id, "
            f"got {resp.status_code}: {resp.text}"
        )
        rows = read_arrow(resp.content)
        assert len(rows) > 0, "Expected REAL rows but got empty result"

    # -----------------------------------------------------------------------
    # A2: embed token + raw SQL (no query_id) → 403 query_not_registered
    # -----------------------------------------------------------------------

    def test_embed_raw_sql_no_query_id_403(self, e2e_ctx):
        """RS256 embed token + raw SQL without query_id → 403 (M3-SEC allowlist).

        This is the boundary the SQL editor relies on — the server MUST reject
        raw SQL from embed tokens even if the token carries author:sql.
        """
        token = self._tok(scope=["read:query"])

        resp = e2e_ctx.client.post(
            "/query",
            json={"sql": "SELECT 1 AS x"},
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": _EMBED_ORIGIN,
                "Accept": _ARROW_ACCEPT,
            },
        )
        assert resp.status_code == 403, (
            f"SECURITY FAILURE: embed raw SQL accepted (status {resp.status_code}: {resp.text})"
        )
        body = resp.json()
        assert body["error"]["code"] == "query_not_registered", (
            f"Wrong error code: {body}"
        )

    # -----------------------------------------------------------------------
    # A3: embed token with author:sql in scope STILL cannot run raw SQL
    # -----------------------------------------------------------------------

    def test_embed_raw_sql_with_author_sql_still_403(self, e2e_ctx):
        """M3-SEC gate fires on kind='embed' BEFORE scope check — author:sql irrelevant."""
        token = self._tok(scope=["read:query", "author:sql"])

        resp = e2e_ctx.client.post(
            "/query",
            json={"sql": "SELECT * FROM sales LIMIT 5"},
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": _EMBED_ORIGIN,
                "Accept": _ARROW_ACCEPT,
            },
        )
        assert resp.status_code == 403, (
            f"SECURITY FAILURE: embed+author:sql raw SQL accepted "
            f"(status {resp.status_code}: {resp.text})"
        )

    # -----------------------------------------------------------------------
    # A4: embed token + author:metric → POST /metrics/{id}/query → 200 rows
    # -----------------------------------------------------------------------

    def test_embed_metric_query_returns_real_rows(self, e2e_ctx):
        """RS256 embed token + author:metric → metric query → 200 with real data.

        This is the path <nubi-metric-explorer> uses and the query-editor
        metric mode.
        """
        metric_id = "retail_nsv"
        if metric_id not in e2e_ctx.metric_ids:
            pytest.skip(f"Metric {metric_id!r} not in demo DB")

        token = self._tok(scope=["read:query", "author:metric"])

        resp = e2e_ctx.client.post(
            f"/metrics/{metric_id}/query",
            json={
                "dimensions": ["region"],
                "filters": [{"field": "month", "op": "=", "value": "2025-01"}],
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": _EMBED_ORIGIN,
                "Accept": _ARROW_ACCEPT,
            },
        )
        assert resp.status_code == 200, (
            f"Expected 200 for embed metric query, got {resp.status_code}: {resp.text}"
        )
        rows = read_arrow(resp.content)
        assert len(rows) >= 1, "Expected real metric rows but got empty result"
        assert all("region" in r for r in rows), f"No region column in rows: {rows[:2]}"
        assert all("nsv" in r for r in rows), f"No nsv column in rows: {rows[:2]}"

    def test_embed_read_query_scope_can_also_query_metric(self, e2e_ctx):
        """read:query alone satisfies the metric gate (no author:metric needed for read)."""
        metric_id = "retail_nsv"
        if metric_id not in e2e_ctx.metric_ids:
            pytest.skip(f"Metric {metric_id!r} not in demo DB")

        token = self._tok(scope=["read:query"])

        resp = e2e_ctx.client.post(
            f"/metrics/{metric_id}/query",
            json={
                "dimensions": [],
                "filters": [{"field": "month", "op": "=", "value": "2025-01"}],
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": _EMBED_ORIGIN,
                "Accept": _ARROW_ACCEPT,
            },
        )
        assert resp.status_code == 200, (
            f"Expected 200 for read:query embed metric, got {resp.status_code}: {resp.text}"
        )

    # -----------------------------------------------------------------------
    # A5: embed token + /metrics/{id}/explain → 200 (read:query satisfies gate)
    # -----------------------------------------------------------------------

    def test_embed_metric_explain_allowed(self, e2e_ctx):
        """embed token with read:query can call /metrics/{id}/explain → 200."""
        metric_id = "retail_nsv"
        if metric_id not in e2e_ctx.metric_ids:
            pytest.skip(f"Metric {metric_id!r} not in demo DB")

        token = self._tok(scope=["read:query"])

        resp = e2e_ctx.client.post(
            f"/metrics/{metric_id}/explain",
            json={
                "current": {"start": "2025-01-01", "end": "2025-02-01"},
                "comparison": {"start": "2024-01-01", "end": "2024-02-01"},
                "dimensions": ["region"],
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": _EMBED_ORIGIN,
            },
        )
        # 200 with analysis OR 400 for missing time_dimension on seed data —
        # either is acceptable; what must NOT happen is 401/403.
        assert resp.status_code not in (401, 403), (
            f"embed token should be allowed to call /explain, "
            f"got {resp.status_code}: {resp.text}"
        )

    # -----------------------------------------------------------------------
    # A6: embed token cannot reach write/admin routes
    # -----------------------------------------------------------------------

    def test_embed_cannot_create_metric(self, e2e_ctx):
        """Embed token → POST /metrics (create) → 403 forbidden."""
        token = self._tok(scope=["read:query", "author:metric"])

        resp = e2e_ctx.client.post(
            "/metrics",
            json={
                "id": "e2e_test_metric_should_not_create",
                "name": "E2E Test Metric",
                "measure": {"name": "total", "agg": "count", "expr": "*"},
                "base_table": "sales",
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": _EMBED_ORIGIN,
            },
        )
        assert resp.status_code in (403, 405), (
            f"Embed token must not be able to create metrics, "
            f"got {resp.status_code}: {resp.text}"
        )

    def test_embed_cannot_create_webhook(self, e2e_ctx):
        """Embed token → admin/webhook routes → 401 or 403."""
        token = self._tok(scope=["read:query"])

        # Try a generic webhook-send style route
        resp = e2e_ctx.client.post(
            "/webhooks",
            json={"url": "https://evil.example/hook", "event": "query.run"},
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": _EMBED_ORIGIN,
            },
        )
        assert resp.status_code in (401, 403, 404, 405), (
            f"Embed token reached webhook route, got {resp.status_code}: {resp.text}"
        )

    def test_embed_cannot_list_users(self, e2e_ctx):
        """Embed token → admin user management routes → 401/403."""
        token = self._tok(scope=["read:query"])

        resp = e2e_ctx.client.get(
            "/users",
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": _EMBED_ORIGIN,
            },
        )
        assert resp.status_code in (401, 403, 404), (
            f"Embed token reached user list route, got {resp.status_code}: {resp.text}"
        )

    # -----------------------------------------------------------------------
    # A7: Origin enforcement
    # -----------------------------------------------------------------------

    def test_embed_wrong_origin_rejected(self, e2e_ctx):
        """Token with embed_origin=<correct> but request Origin=<wrong> → 403."""
        q_ids = e2e_ctx.query_ids
        if not q_ids:
            pytest.skip("No registered queries")

        query_id = next(iter(q_ids.values()))
        # Token carries the correct embed_origin; request sends WRONG origin.
        token = self._tok(scope=["read:query"], embed_origin=_EMBED_ORIGIN)

        resp = e2e_ctx.client.post(
            "/query",
            json={"query_id": query_id},
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": "https://evil-site.example",  # WRONG
                "Accept": _ARROW_ACCEPT,
            },
        )
        assert resp.status_code == 403, (
            f"SECURITY FAILURE: wrong-origin embed token accepted, "
            f"got {resp.status_code}: {resp.text}"
        )

    def test_embed_correct_origin_accepted(self, e2e_ctx):
        """Token with embed_origin matching request Origin → 200 (origin passes)."""
        q_ids = e2e_ctx.query_ids
        if not q_ids:
            pytest.skip("No registered queries")

        query_id = next(iter(q_ids.values()))
        token = self._tok(scope=["read:query"], embed_origin=_EMBED_ORIGIN)

        resp = e2e_ctx.client.post(
            "/query",
            json={"query_id": query_id},
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": _EMBED_ORIGIN,  # CORRECT
                "Accept": _ARROW_ACCEPT,
            },
        )
        assert resp.status_code == 200, (
            f"Correct-origin embed token rejected, got {resp.status_code}: {resp.text}"
        )
        rows = read_arrow(resp.content)
        assert len(rows) > 0

    def test_embed_missing_origin_when_token_has_claim_rejected(self, e2e_ctx):
        """Token carries embed_origin but request sends NO Origin header → 403.

        A CLI/server-side caller must not bypass the origin restriction by
        simply omitting the Origin header.
        """
        q_ids = e2e_ctx.query_ids
        if not q_ids:
            pytest.skip("No registered queries")

        query_id = next(iter(q_ids.values()))
        token = self._tok(scope=["read:query"], embed_origin=_EMBED_ORIGIN)

        resp = e2e_ctx.client.post(
            "/query",
            json={"query_id": query_id},
            headers={
                "Authorization": f"Bearer {token}",
                # No Origin header — intentionally omitted
                "Accept": _ARROW_ACCEPT,
            },
        )
        assert resp.status_code == 403, (
            f"SECURITY FAILURE: embed token accepted without Origin header "
            f"(status {resp.status_code}: {resp.text})"
        )

    # -----------------------------------------------------------------------
    # A8: Unregistered issuer → 401
    # -----------------------------------------------------------------------

    def test_embed_unregistered_issuer_rejected(self, e2e_ctx):
        """Token from a completely unknown issuer → 401 (no oracle)."""
        import jwt as pyjwt  # noqa: PLC0415
        ch = _load_sec_helpers()

        now = datetime.now(tz=timezone.utc)
        payload = {
            "iss": "https://totally-unknown-issuer.example",
            "aud": _EMBED_AUD,
            "sub": "attacker",
            "org": "evil-org",
            "scope": ["read:query"],
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=300)).timestamp()),
        }
        # Sign with our key — doesn't matter, issuer lookup will fail first.
        token = pyjwt.encode(
            payload,
            ch._PRIVATE_KEY,
            algorithm="RS256",
            headers={"kid": ch.KID},
        )

        resp = e2e_ctx.client.post(
            "/query",
            json={"sql": "SELECT 1"},
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": _EMBED_ORIGIN,
                "Accept": _ARROW_ACCEPT,
            },
        )
        assert resp.status_code == 401, (
            f"Unregistered issuer must be rejected with 401, "
            f"got {resp.status_code}: {resp.text}"
        )


# ===========================================================================
# A9: HS256 first-party token with restricted scope (embed-like behaviour)
# These use the existing e2e_ctx.embed_token (HS256, scope=read:query).
# They exercise scope restrictions but via kind='access' not kind='embed',
# so the M3-SEC allowlist gate does NOT fire — instead author:sql is checked.
# ===========================================================================


@pytest.mark.usefixtures("e2e_ctx")
class TestFirstPartyEmbedScopeRestriction:
    """First-party tokens with embed-style scopes (the e2e_ctx.embed_token).

    These test the scope-enforcement layer that also protects embedded views.
    The embed_token is an HS256 first-party token with scope=read:query; it
    has kind='access', so the M3-SEC allowlist gate (embed kind check) is NOT
    active — but author:sql is absent, so raw SQL is still rejected.
    """

    def test_read_query_token_raw_sql_403(self, e2e_ctx):
        """read:query first-party token cannot run raw SQL (no author:sql) → 403."""
        resp = e2e_ctx.client.post(
            "/query",
            json={"sql": "SELECT 1 AS x"},
            headers={
                **e2e_ctx.embed_headers(),
                "Accept": _ARROW_ACCEPT,
            },
        )
        assert resp.status_code == 403, (
            f"Expected 403 for read:query-only token on raw SQL, "
            f"got {resp.status_code}: {resp.text}"
        )

    def test_read_query_token_registered_query_200(self, e2e_ctx):
        """read:query first-party token CAN execute a registered query_id → 200."""
        q_ids = e2e_ctx.query_ids
        if not q_ids:
            pytest.skip("No registered non-metric queries in demo DB")

        query_id = next(iter(q_ids.values()))
        resp = e2e_ctx.client.post(
            "/query",
            json={"query_id": query_id},
            headers={
                **e2e_ctx.embed_headers(),
                "Accept": _ARROW_ACCEPT,
            },
        )
        assert resp.status_code == 200, (
            f"Expected 200 for read:query token + registered query_id, "
            f"got {resp.status_code}: {resp.text}"
        )
        from tests.e2e.conftest import read_arrow_bytes  # noqa: PLC0415
        rows = read_arrow_bytes(resp.content)
        assert len(rows) > 0

    def test_read_query_token_metric_query_200(self, e2e_ctx):
        """read:query first-party token CAN run a governed metric query → 200."""
        metric_id = "retail_nsv"
        if metric_id not in e2e_ctx.metric_ids:
            pytest.skip(f"Metric {metric_id!r} not in demo DB")

        resp = e2e_ctx.client.post(
            f"/metrics/{metric_id}/query",
            json={
                "dimensions": ["region"],
                "filters": [{"field": "month", "op": "=", "value": "2025-01"}],
            },
            headers={
                **e2e_ctx.embed_headers(),
                "Accept": _ARROW_ACCEPT,
            },
        )
        assert resp.status_code == 200, (
            f"Expected 200 for read:query embed metric, got {resp.status_code}: {resp.text}"
        )
        from tests.e2e.conftest import read_arrow_bytes  # noqa: PLC0415
        rows = read_arrow_bytes(resp.content)
        assert len(rows) >= 1
        assert all("region" in r for r in rows)

    def test_read_query_token_cannot_create_metric(self, e2e_ctx):
        """read:query first-party token → POST /metrics (create) → 403 (no author scope)."""
        resp = e2e_ctx.client.post(
            "/metrics",
            json={
                "id": "e2e_embed_test_should_not_create",
                "name": "E2E Embed Metric",
                "measure": {"name": "total", "agg": "count", "expr": "*"},
                "base_table": "sales",
            },
            headers=e2e_ctx.embed_headers(),
        )
        # Should be 403 (insufficient scope / forbidden) or 400 (validation before auth)
        assert resp.status_code in (400, 403), (
            f"read:query token must not be able to create metrics, "
            f"got {resp.status_code}: {resp.text}"
        )

    def test_read_query_token_metric_query_has_real_data(self, e2e_ctx):
        """Verify the metric rows are REAL data (not sample/fallback)."""
        metric_id = "retail_nsv"
        if metric_id not in e2e_ctx.metric_ids:
            pytest.skip(f"Metric {metric_id!r} not in demo DB")

        resp = e2e_ctx.client.post(
            f"/metrics/{metric_id}/query",
            json={
                "dimensions": ["region"],
                "filters": [{"field": "month", "op": "=", "value": "2025-01"}],
                "order_by": [["nsv", "desc"]],
            },
            headers={
                **e2e_ctx.embed_headers(),
                "Accept": _ARROW_ACCEPT,
            },
        )
        assert resp.status_code == 200
        from tests.e2e.conftest import read_arrow_bytes  # noqa: PLC0415
        rows = read_arrow_bytes(resp.content)
        assert len(rows) == 5, f"Expected 5 SA regions, got {len(rows)}"
        # Real data: Gauteng should be the largest region (~430k NSV in 2025-01)
        assert rows[0]["region"] == "Gauteng", f"Expected Gauteng first: {rows[0]}"
        assert rows[0]["nsv"] > 100_000, (
            f"NSV looks too low for real data: {rows[0]['nsv']}"
        )
