"""REGRESSION — guards proven in prior audits must still hold.

This file complements (does NOT replace) the existing ``test_sec_*`` files.
It re-asserts a few cross-cutting invariants at the unit level so a refactor in
the custody work cannot silently regress them:

  - Embed tokens cannot author metrics (kind='embed' → 403).
  - First-party tokens with an explicit non-author scope cannot author metrics.
  - The author:metric / author:sql wildcard semantics are intact.
  - Webhook SSRF guard (guard_url + resolve_and_pin) blocks metadata / private.
"""

from __future__ import annotations

import pytest

from app.auth.scopes import (
    SCOPE_AUTHOR_METRIC,
    SCOPE_AUTHOR_SQL,
    has_scope,
)
from app.auth.verify import VerifiedIdentity
from app.connectors.ssrf import guard_url, resolve_and_pin
from app.errors import AppError
from app.routes.metrics import _require_first_party_write


def _identity(kind: str, scope: list[str]) -> VerifiedIdentity:
    return VerifiedIdentity(
        kind=kind,
        user_id="u",
        org="org",
        project=None,
        roles=[],
        policies={},
        scope=scope,
        embed_origin=None,
        datastore=None,
        raw_claims={},
    )


# ---------------------------------------------------------------------------
# author:metric create-gate
# ---------------------------------------------------------------------------


def test_embed_cannot_author_metrics():
    with pytest.raises(AppError) as ei:
        _require_first_party_write(_identity("embed", [SCOPE_AUTHOR_METRIC]))
    assert ei.value.status == 403


def test_restricted_first_party_cannot_author_metrics():
    # Explicit read-only scope → blocked.
    with pytest.raises(AppError) as ei:
        _require_first_party_write(_identity("access", ["read:query"]))
    assert ei.value.code == "insufficient_scope"


def test_full_session_can_author_metrics():
    # A full first-party session carries author:metric (via _FIRST_PARTY_SCOPES).
    _require_first_party_write(_identity("access", [SCOPE_AUTHOR_METRIC]))  # no raise


def test_author_wildcard_semantics():
    assert has_scope(["author:*"], SCOPE_AUTHOR_METRIC) is True
    assert has_scope(["author:*"], SCOPE_AUTHOR_SQL) is True
    assert has_scope(["read:query"], SCOPE_AUTHOR_METRIC) is False
    assert has_scope([], SCOPE_AUTHOR_SQL) is False


# ---------------------------------------------------------------------------
# Webhook SSRF guard
# ---------------------------------------------------------------------------

_SSRF_BLOCKED = [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1/internal",
    "http://0.0.0.0/",
    "ftp://example.com/x",
    "file:///etc/passwd",
    "http://[fd00:ec2::254]/",
]


@pytest.mark.parametrize("url", _SSRF_BLOCKED)
def test_guard_url_blocks(url):
    with pytest.raises(AppError) as ei:
        guard_url(url)
    assert ei.value.code == "ssrf_blocked"


@pytest.mark.parametrize("url", _SSRF_BLOCKED)
def test_resolve_and_pin_blocks(url):
    with pytest.raises(AppError) as ei:
        resolve_and_pin(url)
    assert ei.value.code == "ssrf_blocked"


def test_resolve_and_pin_pins_public_host():
    pinned = resolve_and_pin("https://example.com/hook")
    assert pinned.host == "example.com"
    assert pinned.port == 443
    assert pinned.ip  # a concrete validated IP literal
