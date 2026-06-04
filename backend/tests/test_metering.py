"""Tests for the metering provider pattern (PROD).

Coverage
--------
1. ``set_sink(InMemorySink())`` + ``record_kernel_usage`` → ``get_usage()``
   reflects the recorded event.
2. ``clear_usage()`` empties the log.
3. Multiple events accumulate correctly.
4. ``PgSink.record`` calls ``app.db.execute`` with a parameterised INSERT (no
   f-string interpolation) — verified via monkeypatching ``app.db.execute`` and
   capturing the SQL + positional args.
5. ``get_sink()`` returns ``InMemorySink`` by default (no env var set) and
   ``PgSink`` when ``METERING_PERSIST=1``.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from app.compute.metering import (
    InMemorySink,
    PgSink,
    clear_usage,
    get_sink,
    get_usage,
    record_kernel_usage,
    set_sink,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_sink() -> InMemorySink:
    """Create a fresh InMemorySink and inject it."""
    sink = InMemorySink()
    set_sink(sink)
    return sink


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_sink():
    """Reset the metering sink to None before and after each test."""
    set_sink(None)
    # Clear METERING_PERSIST so we don't accidentally pick PgSink
    os.environ.pop("METERING_PERSIST", None)
    yield
    set_sink(None)
    os.environ.pop("METERING_PERSIST", None)


# ---------------------------------------------------------------------------
# InMemorySink tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_kernel_usage_appends_event():
    """record_kernel_usage via InMemorySink adds one event to get_usage()."""
    _fresh_sink()

    await record_kernel_usage(
        user_id="user-abc",
        tier="local_kernel",
        elapsed_ms=123,
        output_bytes=4096,
    )

    events = get_usage()
    assert len(events) == 1
    evt = events[0]
    assert evt["user_id"] == "user-abc"
    assert evt["tier"] == "local_kernel"
    assert evt["elapsed_ms"] == 123
    assert evt["output_bytes"] == 4096
    assert evt["kind"] == "kernel"


@pytest.mark.asyncio
async def test_multiple_events_accumulate():
    """Multiple record_kernel_usage calls accumulate in order."""
    _fresh_sink()

    await record_kernel_usage(user_id="u1", tier="local_kernel", elapsed_ms=10, output_bytes=100)
    await record_kernel_usage(user_id="u2", tier="remote_kernel", elapsed_ms=20, output_bytes=200)

    events = get_usage()
    assert len(events) == 2
    assert events[0]["user_id"] == "u1"
    assert events[1]["user_id"] == "u2"
    assert events[1]["tier"] == "remote_kernel"


@pytest.mark.asyncio
async def test_clear_usage_empties_log():
    """clear_usage() empties the InMemorySink log."""
    _fresh_sink()

    await record_kernel_usage(user_id="u1", tier="local_kernel", elapsed_ms=5, output_bytes=50)
    assert len(get_usage()) == 1

    clear_usage()
    assert get_usage() == []


@pytest.mark.asyncio
async def test_in_memory_sink_units_default():
    """InMemorySink computes units = elapsed_ms / 1000 by default."""
    _fresh_sink()

    await record_kernel_usage(user_id="u1", tier="local_kernel", elapsed_ms=2000, output_bytes=0)
    evt = get_usage()[0]
    assert evt["units"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# PgSink unit test — verifies parameterised INSERT, no f-string interpolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pg_sink_calls_execute_with_parameterised_sql():
    """PgSink.record calls app.db.execute with positional $N args, not f-strings.

    We monkeypatch app.db.execute to capture the (sql, *args) call and assert:
    - The SQL uses $1..$7 placeholders (no value literals injected into the string).
    - The user_id, tier, elapsed_ms, output_bytes, org_id, kind, units are passed
      as positional arguments (not interpolated into the SQL string).
    """
    captured: list[tuple] = []

    async def _fake_execute(sql: str, *args):
        captured.append((sql, args))
        return "INSERT 0 1"

    sink = PgSink()

    with patch("app.db.execute", side_effect=_fake_execute):
        await sink.record(
            user_id="user-pg-1",
            tier="remote_kernel",
            elapsed_ms=500,
            output_bytes=8192,
            org_id="org-xyz",
            kind="kernel",
        )

    assert len(captured) == 1, "execute must be called exactly once"
    sql, args = captured[0]

    # ── Verify parameterisation ───────────────────────────────────────────────
    # SQL must contain $1 through $7 placeholders.
    for n in range(1, 8):
        assert f"${n}" in sql, f"SQL must use ${n} placeholder"

    # The actual values must NOT appear as literals in the SQL string.
    assert "user-pg-1" not in sql, "user_id must not be interpolated into SQL"
    assert "remote_kernel" not in sql, "tier must not be interpolated into SQL"
    assert "org-xyz" not in sql, "org_id must not be interpolated into SQL"
    assert "8192" not in sql, "output_bytes must not be interpolated into SQL"

    # ── Verify positional args contain the correct values ─────────────────────
    # Expected arg order: org_id, user_id, kind, tier, elapsed_ms, output_bytes, units
    assert args[0] == "org-xyz"       # $1 = org_id
    assert args[1] == "user-pg-1"     # $2 = user_id
    assert args[2] == "kernel"        # $3 = kind
    assert args[3] == "remote_kernel" # $4 = tier
    assert args[4] == 500             # $5 = elapsed_ms
    assert args[5] == 8192            # $6 = output_bytes
    assert args[6] == pytest.approx(0.5)  # $7 = units (500 ms / 1000)


@pytest.mark.asyncio
async def test_pg_sink_none_org_id_is_passed_as_none():
    """PgSink.record passes None for org_id when not provided (no default substitution)."""
    captured: list[tuple] = []

    async def _fake_execute(sql: str, *args):
        captured.append((sql, args))
        return "INSERT 0 1"

    sink = PgSink()

    with patch("app.db.execute", side_effect=_fake_execute):
        await sink.record(
            user_id="u2",
            tier="local_kernel",
            elapsed_ms=100,
            output_bytes=512,
            # org_id not provided → should default to None
        )

    _, args = captured[0]
    assert args[0] is None  # $1 = org_id must be None


# ---------------------------------------------------------------------------
# Provider selection tests
# ---------------------------------------------------------------------------


def test_get_sink_returns_in_memory_by_default():
    """get_sink() without METERING_PERSIST returns InMemorySink."""
    # Ensure env var is absent and singleton is reset.
    os.environ.pop("METERING_PERSIST", None)
    set_sink(None)
    sink = get_sink()
    assert isinstance(sink, InMemorySink)


def test_get_sink_returns_pg_when_env_set():
    """get_sink() with METERING_PERSIST=1 returns PgSink."""
    os.environ["METERING_PERSIST"] = "1"
    set_sink(None)  # reset so get_sink re-evaluates
    try:
        sink = get_sink()
        assert isinstance(sink, PgSink)
    finally:
        os.environ.pop("METERING_PERSIST", None)
        set_sink(None)


def test_set_sink_overrides_env():
    """set_sink() takes precedence over the METERING_PERSIST env var."""
    os.environ["METERING_PERSIST"] = "1"
    my_sink = InMemorySink()
    set_sink(my_sink)
    assert get_sink() is my_sink
