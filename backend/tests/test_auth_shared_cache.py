"""Cross-request auth cache (``request_memo.shared_memoized``).

Pins the C2 contract: answers that grant access are reused across requests for
AUTH_CACHE_TTL_SECONDS, "not a member" is never cached, local writes drop the
entry at once, a load that raced an invalidation is not stored, and nothing is
shared outside a request.
"""

from __future__ import annotations

import time

import pytest

from app import request_memo
from app.routes import _org


def _new_request():
    """Open a fresh request memo, as RequestMemoMiddleware does."""
    return request_memo._memo.set({})


class _Loader:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        return self.value


@pytest.mark.asyncio
async def test_answer_is_reused_by_the_next_request() -> None:
    load = _Loader("admin")
    for _ in range(3):
        tok = _new_request()
        try:
            assert await request_memo.shared_memoized(("org_role", "u", "o"), load) == "admin"
        finally:
            request_memo._memo.reset(tok)
    assert load.calls == 1


@pytest.mark.asyncio
async def test_none_is_never_shared() -> None:
    load = _Loader(None)
    for _ in range(2):
        tok = _new_request()
        try:
            assert await request_memo.shared_memoized(("org_member", "u", "o"), load) is None
        finally:
            request_memo._memo.reset(tok)
    assert load.calls == 2


@pytest.mark.asyncio
async def test_entry_expires_after_ttl(monkeypatch) -> None:
    load = _Loader(True)
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    for advance, expected_calls in ((0, 1), (29, 1), (2, 2)):
        now[0] += advance
        tok = _new_request()
        try:
            await request_memo.shared_memoized(("jti_revoked", "j"), load)
        finally:
            request_memo._memo.reset(tok)
        assert load.calls == expected_calls


@pytest.mark.asyncio
async def test_ttl_zero_disables_sharing(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_CACHE_TTL_SECONDS", "0")
    from app.config import get_settings

    get_settings.cache_clear()
    load = _Loader("member")
    for _ in range(2):
        tok = _new_request()
        try:
            await request_memo.shared_memoized(("org_role", "u", "o"), load)
        finally:
            request_memo._memo.reset(tok)
    assert load.calls == 2


@pytest.mark.asyncio
async def test_no_sharing_outside_a_request() -> None:
    load = _Loader("admin")
    await request_memo.shared_memoized(("org_role", "u", "o"), load)
    await request_memo.shared_memoized(("org_role", "u", "o"), load)
    assert load.calls == 2


@pytest.mark.asyncio
async def test_forget_membership_drops_role_and_membership() -> None:
    tok = _new_request()
    try:
        await request_memo.shared_memoized(("org_role", "u1", "o1"), _Loader("admin"))
        await request_memo.shared_memoized(("org_member", "u1", "o1"), _Loader(True))
        await request_memo.shared_memoized(("org_role", "u2", "o1"), _Loader("viewer"))
        await request_memo.shared_memoized(("org_role", "u1", "o2"), _Loader("owner"))
        _org.forget_membership("u1", "o1")
    finally:
        request_memo._memo.reset(tok)
    assert request_memo._shared_get(("org_role", "u1", "o1")) is None
    assert request_memo._shared_get(("org_member", "u1", "o1")) is None
    assert request_memo._shared_get(("org_role", "u2", "o1")) == "viewer"
    assert request_memo._shared_get(("org_role", "u1", "o2")) == "owner"

    _org.forget_membership(org_id="o1")
    assert request_memo._shared_get(("org_role", "u2", "o1")) is None
    assert request_memo._shared_get(("org_role", "u1", "o2")) == "owner"


@pytest.mark.asyncio
async def test_load_racing_an_invalidation_is_not_stored() -> None:
    async def slow_load():
        # The role changes (on this worker) while the old value is in flight.
        _org.forget_membership("u", "o")
        return "admin"

    tok = _new_request()
    try:
        assert await request_memo.shared_memoized(("org_role", "u", "o"), slow_load) == "admin"
    finally:
        request_memo._memo.reset(tok)
    assert request_memo._shared_get(("org_role", "u", "o")) is None


@pytest.mark.asyncio
async def test_user_row_copy_is_private(monkeypatch) -> None:
    from app.auth import deps

    async def fetchrow(_sql, _uid):
        return {"id": "u", "email": "u@example.com"}

    monkeypatch.setattr(deps, "fetchrow", fetchrow)
    tok = _new_request()
    try:
        row = await deps.load_user_row("u")
        row["_api_key_org_id"] = "o"
    finally:
        request_memo._memo.reset(tok)
    tok = _new_request()
    try:
        again = await deps.load_user_row("u")
    finally:
        request_memo._memo.reset(tok)
    assert "_api_key_org_id" not in again
    deps.forget_user_row("u")
    assert request_memo._shared_get(("user_row", "u")) is None


@pytest.mark.asyncio
async def test_values_deepcopy_refuses_are_handed_out_as_is() -> None:
    """asyncpg Records cannot be deep-copied; the memo must not raise on them."""

    class _RecordLike:
        def __deepcopy__(self, _memo):
            raise TypeError("cannot pickle Record")

    rec = _RecordLike()
    tok = _new_request()
    try:
        assert await request_memo.memoized(("connector_secret", "d", "o"), _Loader(rec)) is rec
        assert await request_memo.memoized(("connector_secret", "d", "o"), _Loader(None)) is rec
    finally:
        request_memo._memo.reset(tok)
