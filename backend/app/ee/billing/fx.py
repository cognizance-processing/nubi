"""USD → ZAR FX rate service for Nubi EE billing.

Architecture
------------
The FX rate is fetched from a free no-key API (frankfurter.app) and stored
in the dual InMemory + Pg ``FxRateStore``.  Billing never blocks on a live
network call — the last-known rate is always available as a fallback.

Rate derivation formula (from the pricing blueprint)
----------------------------------------------------
    zar_amount = ceil_to_nearest_10(usd * fx_rate * FX_BUFFER)

Where:
    ``fx_rate`` = live mid-market USD→ZAR rate from the FX provider.
    ``FX_BUFFER`` = 1.02  (2% buffer absorbs intraday drift; protects margin).
    ``ceil_to_nearest_10`` = always round UP to the nearest R10.

Daily refresh
-------------
A cron job (07:00 SAST = 05:00 UTC) calls :func:`refresh_fx_rate` and stores
the result.  If the refresh fails, the last successfully fetched rate is
retained.  If no fresh rate exists within ``STALENESS_THRESHOLD_HOURS``
(72 hours), the hardcoded ``EMERGENCY_FALLBACK_RATE`` is used and the result
is flagged as stale.

Cache hydration
---------------
The daily refresh may run in a *different* process than the one serving a
billing call (separate flows worker, multiple uvicorn workers), and the
module cache resets to the emergency fallback on every restart.  The sync
read paths therefore opportunistically re-read the single persisted
``fx_rates`` row (at most every ``_STORE_READ_INTERVAL``) so that every
process converges on the freshest persisted rate.  Async money paths should
prefer :func:`get_current_rate_async`, which awaits the hydration read
before returning.

Public API
----------
get_current_rate() -> dict
    Return ``{rate, fetched_at, stale}`` without triggering a network call.

get_current_rate_async() -> dict
    Like :func:`get_current_rate`, but first hydrates the cache from the
    persistent store.  Preferred at billing/charge time.

refresh_fx_rate() -> Decimal
    Fetch a live rate from the FX provider and upsert into the store.

hydrate_rate_cache_from_store() -> bool
    Read the persisted rate back into the module cache.

convert_usd_to_zar(usd) -> Decimal
    Convert *usd* to ZAR applying the 2% buffer and ceil-to-nearest-10 rule.

get_fx_rate_store() -> FxRateStore
    Return (or lazily create) the module-level ``FxRateStore`` singleton.

set_fx_rate_store_for_tests(store) -> None
    Inject a test double.

Usage
-----
>>> from app.ee.billing.fx import convert_usd_to_zar
>>> from decimal import Decimal
>>> convert_usd_to_zar(Decimal("9.00"))  # Starter ($9/mo) at ~R16.26 rate
Decimal('150')
"""

from __future__ import annotations

import asyncio
import logging
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 2% FX buffer absorbs intraday drift and protects margin during ZAR weakness.
FX_BUFFER: Decimal = Decimal("1.02")

# Default emergency fallback rate when no fresh rate has been fetched within
# the staleness window.  Set to the June 2026 reference rate.
_DEFAULT_EMERGENCY_FALLBACK_RATE: Decimal = Decimal("16.26")


def _emergency_fallback_rate_from_env() -> Decimal:
    """Resolve the emergency fallback rate, honouring ``FX_EMERGENCY_RATE``.

    Operators can override the hardcoded default per deployment via the
    ``FX_EMERGENCY_RATE`` environment variable (read once at import time).
    Invalid or non-positive values fall back to the hardcoded default.
    """
    raw = os.environ.get("FX_EMERGENCY_RATE")
    if raw is None:
        return _DEFAULT_EMERGENCY_FALLBACK_RATE
    try:
        rate = Decimal(raw)
    except InvalidOperation:
        rate = None
    if rate is None or rate <= 0:
        logger.warning(
            "FX: invalid FX_EMERGENCY_RATE=%r — using default R%s.",
            raw,
            _DEFAULT_EMERGENCY_FALLBACK_RATE,
        )
        return _DEFAULT_EMERGENCY_FALLBACK_RATE
    return rate


# Emergency fallback rate when no fresh rate has been fetched within the
# staleness window.  Update the default quarterly; override per deployment
# via the FX_EMERGENCY_RATE environment variable.
EMERGENCY_FALLBACK_RATE: Decimal = _emergency_fallback_rate_from_env()

# How long (hours) before a cached rate is considered stale.
STALENESS_THRESHOLD_HOURS: int = 72

# Plausibility band for fetched USD→ZAR rates.  A parseable-but-absurd value
# (zero, negative, or wildly out of band) is treated as a provider failure so
# the fetch falls through to the next provider / the cached rate, instead of
# poisoning the cache (the fx_rates table CHECK (rate > 0) would reject the
# row, but the module cache must never accept it either).  The band is
# deliberately generous: USD/ZAR has traded roughly R6–R20 this century, so
# 4–60 catches 10x/garbage errors without rejecting genuine market moves.
MIN_PLAUSIBLE_USD_ZAR_RATE: Decimal = Decimal("4")
MAX_PLAUSIBLE_USD_ZAR_RATE: Decimal = Decimal("60")

# How often the sync read paths re-check the persisted fx_rates row.  The
# daily refresh may run in a different process (flows worker / another uvicorn
# worker), so each process re-reads the single indexed Pg row at most this
# often to keep its cache aligned with the freshest persisted rate.
_STORE_READ_INTERVAL: timedelta = timedelta(minutes=15)

# FX API endpoint (no API key required; free tier, no attribution needed).
_FX_API_URL = "https://api.frankfurter.app/latest?from=USD&to=ZAR"

# Alternative: open.er-api.com (also free, no key).
_FX_API_URL_FALLBACK = "https://open.er-api.com/v6/latest/USD"


# ---------------------------------------------------------------------------
# FxRateStore interface + implementations
# ---------------------------------------------------------------------------


class FxRateStore:
    """Interface for FX rate storage.

    Stores the most recent USD→ZAR rate keyed by ``(base, quote)`` pair.
    Only the latest rate per pair is relevant; older rows are retained for
    audit purposes but never surfaced by :func:`get_latest_rate`.
    """

    async def upsert_rate(
        self,
        base: str,
        quote: str,
        rate: Decimal,
        source: str,
        fetched_at: datetime,
    ) -> dict[str, Any]:
        """Insert or update the FX rate for ``(base, quote)``.

        Parameters
        ----------
        base:
            Source currency ISO code (e.g. ``"USD"``).
        quote:
            Target currency ISO code (e.g. ``"ZAR"``).
        rate:
            Mid-market exchange rate (quote per base unit).
        source:
            Name of the API that provided the rate (e.g. ``"frankfurter"``).
        fetched_at:
            UTC timestamp when the rate was fetched.

        Returns
        -------
        dict
            The stored rate record.
        """
        raise NotImplementedError

    async def get_latest_rate(
        self,
        base: str = "USD",
        quote: str = "ZAR",
    ) -> dict[str, Any] | None:
        """Return the most recently stored rate for ``(base, quote)``.

        Returns
        -------
        dict | None
            Rate record with keys ``{id, base, quote, rate, source,
            fetched_at}`` or ``None`` if no rate has been stored.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# InMemory implementation (tests)
# ---------------------------------------------------------------------------


class InMemoryFxRateStore(FxRateStore):
    """Dict-backed FX rate store for tests.

    Usage::

        from app.ee.billing.fx import InMemoryFxRateStore, set_fx_rate_store_for_tests
        store = InMemoryFxRateStore()
        set_fx_rate_store_for_tests(store)
    """

    def __init__(self) -> None:
        # (base, quote) → list of rate records (append-only for audit)
        self._rates: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def reset(self) -> None:
        """Clear all stored state."""
        self._rates.clear()

    async def upsert_rate(
        self,
        base: str,
        quote: str,
        rate: Decimal,
        source: str,
        fetched_at: datetime,
    ) -> dict[str, Any]:
        import uuid  # noqa: PLC0415

        key = (base.upper(), quote.upper())
        record: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "base": base.upper(),
            "quote": quote.upper(),
            "rate": rate,
            "source": source,
            "fetched_at": fetched_at,
        }
        if key not in self._rates:
            self._rates[key] = []
        self._rates[key].append(record)
        return deepcopy(record)

    async def get_latest_rate(
        self,
        base: str = "USD",
        quote: str = "ZAR",
    ) -> dict[str, Any] | None:
        key = (base.upper(), quote.upper())
        records = self._rates.get(key)
        if not records:
            return None
        # Most recently appended = latest.
        return deepcopy(records[-1])


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PgFxRateStore(FxRateStore):
    """asyncpg-backed FX rate store using the ``fx_rates`` table.

    Reads/writes the table created by migration 0018_fx_rates.sql.
    """

    async def upsert_rate(
        self,
        base: str,
        quote: str,
        rate: Decimal,
        source: str,
        fetched_at: datetime,
    ) -> dict[str, Any]:
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            """
            INSERT INTO fx_rates (base, quote, rate, source, fetched_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (base, quote) DO UPDATE SET
                rate       = EXCLUDED.rate,
                source     = EXCLUDED.source,
                fetched_at = EXCLUDED.fetched_at
            RETURNING id::text, base, quote, rate, source, fetched_at
            """,
            base.upper(),
            quote.upper(),
            rate,
            source,
            fetched_at,
        )
        return dict(row)  # type: ignore[arg-type]

    async def get_latest_rate(
        self,
        base: str = "USD",
        quote: str = "ZAR",
    ) -> dict[str, Any] | None:
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            """
            SELECT id::text, base, quote, rate, source, fetched_at
            FROM fx_rates
            WHERE base = $1 AND quote = $2
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            base.upper(),
            quote.upper(),
        )
        return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Provider singleton
# ---------------------------------------------------------------------------

_fx_store: FxRateStore | None = None


def set_fx_rate_store_for_tests(store: FxRateStore | None) -> None:
    """Inject a test double or reset to default PgFxRateStore.

    Parameters
    ----------
    store:
        An :class:`InMemoryFxRateStore` instance for tests, or ``None``
        to restore the default production :class:`PgFxRateStore`.
    """
    global _fx_store  # noqa: PLW0603
    _fx_store = store


def get_fx_rate_store() -> FxRateStore:
    """Return the active :class:`FxRateStore` singleton.

    Lazily instantiates a :class:`PgFxRateStore` on first call if no
    override has been set via :func:`set_fx_rate_store_for_tests`.
    """
    global _fx_store  # noqa: PLW0603
    if _fx_store is None:
        _fx_store = PgFxRateStore()
    return _fx_store


# ---------------------------------------------------------------------------
# Rate conversion helpers
# ---------------------------------------------------------------------------


def _ceil_to_nearest_10(value: Decimal) -> Decimal:
    """Round *value* UP to the nearest R10 using pure Decimal arithmetic.

    Uses ``ROUND_CEILING`` (always towards +infinity) to protect margin —
    the ZAR charge is never less than the cost-basis would require.

    Examples
    --------
    >>> _ceil_to_nearest_10(Decimal("1303.1"))
    Decimal('1310')
    >>> _ceil_to_nearest_10(Decimal("1310.0"))
    Decimal('1310')
    >>> _ceil_to_nearest_10(Decimal("1310.1"))
    Decimal('1320')
    >>> _ceil_to_nearest_10(Decimal("1310.2308"))
    Decimal('1320')
    """
    # Pure Decimal arithmetic avoids the float-precision errors that arise when
    # converting to float for math.ceil.  ROUND_CEILING always rounds towards +∞.
    divided = value / Decimal("10")
    ceiled = divided.to_integral_value(rounding=ROUND_CEILING)
    return ceiled * Decimal("10")


def convert_usd_to_zar(
    usd: Decimal,
    *,
    fx_rate: Decimal | None = None,
) -> Decimal:
    """Convert *usd* to ZAR applying the 2% FX buffer and ceil-to-nearest-10 rule.

    This is a synchronous helper that uses the in-memory cached rate.  For
    billing time use, call :func:`get_current_rate_async` first to ensure the
    cache reflects the freshest persisted rate.

    Formula
    -------
    ::

        zar = ceil_to_nearest_10(usd * fx_rate * 1.02)

    Parameters
    ----------
    usd:
        USD amount to convert.
    fx_rate:
        Override the cached rate (primarily for tests or one-off calculations).
        When ``None``, uses :func:`_get_cached_rate_sync`.

    Returns
    -------
    Decimal
        ZAR amount rounded up to the nearest R10.
    """
    rate = fx_rate if fx_rate is not None else _get_cached_rate_sync()
    raw_zar = usd * rate * FX_BUFFER
    return _ceil_to_nearest_10(raw_zar)


def _get_cached_rate_sync() -> Decimal:
    """Return the best available rate synchronously (no live FX network call).

    Opportunistically hydrates the module cache from the persistent store
    (see :func:`_maybe_hydrate_cache_from_store`), then returns the cached
    rate — or the emergency fallback when the cache is stale beyond
    ``STALENESS_THRESHOLD_HOURS`` or has never been populated.

    This function is intentionally sync so it can be called from
    ``convert_usd_to_zar`` without requiring an async context.
    """
    _maybe_hydrate_cache_from_store()
    return _effective_rate()


# Module-level cache: updated each time refresh_fx_rate() succeeds in this
# process, or when the persisted fx_rates row is hydrated back into memory.
_cached_rate: Decimal = EMERGENCY_FALLBACK_RATE
_cached_fetched_at: datetime | None = None

# Hydration bookkeeping: last time this process read the persisted row, and
# the in-flight background hydration task (if any).
_last_store_read_at: datetime | None = None
_hydration_task: asyncio.Task[bool] | None = None


def _update_module_cache(rate: Decimal, fetched_at: datetime) -> None:
    """Update the module-level in-memory rate cache."""
    global _cached_rate, _cached_fetched_at  # noqa: PLW0603
    _cached_rate = rate
    _cached_fetched_at = fetched_at


def _is_stale(now: datetime | None = None) -> bool:
    """True when no rate was ever fetched, or the cache is beyond the threshold."""
    now = now if now is not None else datetime.now(timezone.utc)
    return (
        _cached_fetched_at is None
        or (now - _cached_fetched_at) > timedelta(hours=STALENESS_THRESHOLD_HOURS)
    )


def _effective_rate(now: datetime | None = None) -> Decimal:
    """Return the rate to serve: cached when fresh, emergency fallback when stale.

    Enforces the documented staleness policy — a rate older than
    ``STALENESS_THRESHOLD_HOURS`` is never used for conversion; the
    ``EMERGENCY_FALLBACK_RATE`` is served (and a warning logged) instead.
    """
    if not _is_stale(now):
        return _cached_rate
    if _cached_rate != EMERGENCY_FALLBACK_RATE:
        logger.warning(
            "FX: cached USD/ZAR rate R%s is stale (fetched_at=%s, threshold=%sh) — "
            "serving emergency fallback R%s.",
            _cached_rate,
            _cached_fetched_at.isoformat() if _cached_fetched_at else None,
            STALENESS_THRESHOLD_HOURS,
            EMERGENCY_FALLBACK_RATE,
        )
    return EMERGENCY_FALLBACK_RATE


# ---------------------------------------------------------------------------
# Cache hydration from the persistent store
# ---------------------------------------------------------------------------


async def hydrate_rate_cache_from_store(
    *,
    base: str = "USD",
    quote: str = "ZAR",
) -> bool:
    """Read the persisted rate back into the module cache.

    The daily refresh persists every fetched rate via the ``FxRateStore``,
    but may run in a different process than the one serving a billing call.
    This reads the single latest row and updates the module cache when the
    persisted rate is newer than what is cached.

    Returns
    -------
    bool
        ``True`` when the module cache was updated from the store.
    """
    global _last_store_read_at  # noqa: PLW0603
    _last_store_read_at = datetime.now(timezone.utc)
    row = await get_fx_rate_store().get_latest_rate(base, quote)
    if row is None:
        return False
    try:
        rate = Decimal(str(row["rate"]))
    except (InvalidOperation, KeyError):
        return False
    fetched_at = row.get("fetched_at")
    if rate <= 0 or fetched_at is None:
        return False
    if _cached_fetched_at is not None and fetched_at <= _cached_fetched_at:
        return False
    _update_module_cache(rate, fetched_at)
    logger.info(
        "FX: hydrated rate cache from store — USD/ZAR R%s (fetched_at=%s).",
        rate,
        fetched_at.isoformat(),
    )
    return True


async def _hydrate_cache_quietly() -> bool:
    """Hydrate from the store, never raising (used from sync read paths)."""
    try:
        return await hydrate_rate_cache_from_store()
    except Exception as exc:  # noqa: BLE001
        logger.warning("FX: could not hydrate rate cache from store — %s", exc)
        return False


def _maybe_hydrate_cache_from_store() -> None:
    """Opportunistically refresh the module cache from the persistent store.

    No-op when the cache is recent or the store was read within
    ``_STORE_READ_INTERVAL``.  Sync entry points cannot await, so inside a
    running event loop the store read is scheduled as a background task —
    the *current* caller still sees the existing cache, but subsequent
    callers get the persisted rate.  Outside an event loop the read runs
    inline.
    """
    global _hydration_task  # noqa: PLW0603
    now = datetime.now(timezone.utc)
    cache_recent = (
        _cached_fetched_at is not None
        and (now - _cached_fetched_at) <= _STORE_READ_INTERVAL
    )
    recently_read = (
        _last_store_read_at is not None
        and (now - _last_store_read_at) < _STORE_READ_INTERVAL
    )
    if cache_recent or recently_read:
        return
    if _hydration_task is not None and not _hydration_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        _hydration_task = loop.create_task(_hydrate_cache_quietly())
    else:
        try:
            asyncio.run(_hydrate_cache_quietly())
        except Exception as exc:  # noqa: BLE001
            logger.debug("FX: inline cache hydration failed — %s", exc)


# ---------------------------------------------------------------------------
# get_current_rate — synchronous rate info (no network)
# ---------------------------------------------------------------------------


def get_current_rate() -> dict[str, Any]:
    """Return the current cached rate without triggering a live FX fetch.

    Opportunistically hydrates the cache from the persistent store first
    (background task when called inside a running event loop — async money
    paths should prefer :func:`get_current_rate_async`, which awaits it).

    Returns
    -------
    dict
        ``{rate: Decimal, fetched_at: datetime | None, stale: bool}``

        ``stale`` is ``True`` when:
        - No rate has ever been fetched (using emergency fallback), or
        - The last fetch was more than ``STALENESS_THRESHOLD_HOURS`` hours ago.

        When ``stale`` is ``True`` the ``rate`` is the
        ``EMERGENCY_FALLBACK_RATE`` — a stale cached rate is never served.
        ``fetched_at`` still reports the last successful fetch (informational).

        Callers should log a warning when ``stale`` is ``True`` and surface
        this in the admin dashboard.
    """
    _maybe_hydrate_cache_from_store()
    now = datetime.now(timezone.utc)
    stale = _is_stale(now)
    return {
        "rate": _effective_rate(now),
        "fetched_at": _cached_fetched_at,
        "stale": stale,
    }


async def get_current_rate_async() -> dict[str, Any]:
    """Billing-time rate accessor: hydrate from the store, then report.

    Unlike :func:`get_current_rate`, this *awaits* the persisted-row read
    when the cache may be behind, so charge/invoice paths see the freshest
    persisted rate even right after a process restart or when the daily
    refresh ran in another process.

    Returns
    -------
    dict
        Same shape as :func:`get_current_rate`.
    """
    now = datetime.now(timezone.utc)
    cache_recent = (
        _cached_fetched_at is not None
        and (now - _cached_fetched_at) <= _STORE_READ_INTERVAL
    )
    if not cache_recent:
        await _hydrate_cache_quietly()
    stale = _is_stale()
    return {
        "rate": _effective_rate(),
        "fetched_at": _cached_fetched_at,
        "stale": stale,
    }


# ---------------------------------------------------------------------------
# refresh_fx_rate — live network fetch (async)
# ---------------------------------------------------------------------------


async def refresh_fx_rate(
    *,
    base: str = "USD",
    quote: str = "ZAR",
) -> Decimal:
    """Fetch a live FX rate and persist it to the store.

    Tries ``frankfurter.app`` first; falls back to ``open.er-api.com`` if the
    primary provider fails.  On total failure, logs a warning and returns the
    last-known rate (or the emergency fallback).

    The result is:
    1. Stored in the ``FxRateStore`` (Pg in production, InMemory in tests).
    2. Cached in the module-level ``_cached_rate`` for sync access.

    Parameters
    ----------
    base:
        Source currency ISO code (default ``"USD"``).
    quote:
        Target currency ISO code (default ``"ZAR"``).

    Returns
    -------
    Decimal
        The refreshed (or fallback) rate.
    """
    fetched_rate: Decimal | None = None
    source: str = "unknown"

    # ── Primary: frankfurter.app ─────────────────────────────────────────────
    try:
        candidate, candidate_source = await _fetch_from_frankfurter(base, quote)
        _validate_fetched_rate(candidate, candidate_source, base, quote)
        fetched_rate, source = candidate, candidate_source
    except Exception as exc:  # noqa: BLE001
        logger.warning("FX refresh: frankfurter.app failed — %s. Trying fallback.", exc)

    # ── Secondary: open.er-api.com ───────────────────────────────────────────
    if fetched_rate is None:
        try:
            candidate, candidate_source = await _fetch_from_open_er_api(base, quote)
            _validate_fetched_rate(candidate, candidate_source, base, quote)
            fetched_rate, source = candidate, candidate_source
        except Exception as exc:  # noqa: BLE001
            logger.warning("FX refresh: open.er-api.com also failed — %s.", exc)

    # ── All providers failed → use existing cached rate ───────────────────────
    if fetched_rate is None:
        info = get_current_rate()
        logger.error(
            "FX refresh: all providers failed; retaining served rate R%s (stale=%s).",
            info["rate"],
            info["stale"],
        )
        return info["rate"]

    fetched_at = datetime.now(timezone.utc)

    # ── Persist to store ──────────────────────────────────────────────────────
    try:
        store = get_fx_rate_store()
        await store.upsert_rate(base, quote, fetched_rate, source, fetched_at)
    except Exception as exc:  # noqa: BLE001
        logger.warning("FX refresh: failed to persist rate to store — %s.", exc)

    # ── Update module-level cache ─────────────────────────────────────────────
    _update_module_cache(fetched_rate, fetched_at)
    logger.info(
        "FX refresh: USD/ZAR = R%s (source=%s, fetched_at=%s).",
        fetched_rate,
        source,
        fetched_at.isoformat(),
    )
    return fetched_rate


def _validate_fetched_rate(
    rate: Decimal,
    source: str,
    base: str,
    quote: str,
) -> None:
    """Reject a fetched rate that cannot be a real market rate.

    A provider returning a parseable-but-invalid value (outage page, contract
    change, degraded payload) must be treated as a provider *failure* so the
    refresh falls through to the next provider — never persisted (the
    ``fx_rates`` CHECK would reject it) and never cached (a poisoned cache
    would convert every charge at the bogus rate until the next refresh).

    Raises
    ------
    RuntimeError
        When the rate is non-positive, or — for the USD→ZAR pair — outside
        the ``MIN_PLAUSIBLE_USD_ZAR_RATE``–``MAX_PLAUSIBLE_USD_ZAR_RATE`` band.
    """
    if rate <= 0:
        raise RuntimeError(
            f"{source}: non-positive rate {rate} for {base}/{quote}"
        )
    if (base.upper(), quote.upper()) == ("USD", "ZAR") and not (
        MIN_PLAUSIBLE_USD_ZAR_RATE <= rate <= MAX_PLAUSIBLE_USD_ZAR_RATE
    ):
        raise RuntimeError(
            f"{source}: implausible USD/ZAR rate {rate} (expected "
            f"{MIN_PLAUSIBLE_USD_ZAR_RATE}–{MAX_PLAUSIBLE_USD_ZAR_RATE})"
        )


async def _fetch_from_frankfurter(base: str, quote: str) -> tuple[Decimal, str]:
    """Fetch rate from frankfurter.app.

    Returns
    -------
    tuple[Decimal, str]
        ``(rate, source_name)``.

    Raises
    ------
    Exception
        On any network or parse error.
    """
    import httpx  # noqa: PLC0415

    url = f"https://api.frankfurter.app/latest?from={base}&to={quote}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)

    if not resp.is_success:
        raise RuntimeError(
            f"frankfurter.app returned {resp.status_code}: {resp.text[:200]}"
        )

    data = resp.json()
    # Response shape: {"rates": {"ZAR": 16.26, ...}, ...}
    rates = data.get("rates", {})
    raw = rates.get(quote.upper())
    if raw is None:
        raise RuntimeError(
            f"frankfurter.app: {quote!r} not in response rates: {list(rates)}"
        )

    try:
        rate = Decimal(str(raw))
    except InvalidOperation as exc:
        raise RuntimeError(f"frankfurter.app: could not parse rate {raw!r}") from exc

    return rate, "frankfurter.app"


async def _fetch_from_open_er_api(base: str, quote: str) -> tuple[Decimal, str]:
    """Fetch rate from open.er-api.com.

    Returns
    -------
    tuple[Decimal, str]
        ``(rate, source_name)``.

    Raises
    ------
    Exception
        On any network or parse error.
    """
    import httpx  # noqa: PLC0415

    url = f"https://open.er-api.com/v6/latest/{base}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)

    if not resp.is_success:
        raise RuntimeError(
            f"open.er-api.com returned {resp.status_code}: {resp.text[:200]}"
        )

    data = resp.json()
    # Response shape: {"result": "success", "rates": {"ZAR": 16.26, ...}, ...}
    # open.er-api signals degraded/error payloads via the "result" field —
    # never trust rates from a non-success body.
    result = data.get("result")
    if result != "success":
        raise RuntimeError(
            f"open.er-api.com: result={result!r} (expected 'success')"
        )
    rates = data.get("rates", {})
    raw = rates.get(quote.upper())
    if raw is None:
        raise RuntimeError(
            f"open.er-api.com: {quote!r} not in response rates: {list(rates)}"
        )

    try:
        rate = Decimal(str(raw))
    except InvalidOperation as exc:
        raise RuntimeError(f"open.er-api.com: could not parse rate {raw!r}") from exc

    return rate, "open.er-api.com"


# ---------------------------------------------------------------------------
# fx_refresh task handler (registered in EE billing setup)
# ---------------------------------------------------------------------------


async def _fx_refresh_handler(
    config: dict[str, Any],  # noqa: ARG001
    ctx: Any,                # noqa: ARG001  TaskContext — not needed here
    claims: dict[str, Any],  # noqa: ARG001
) -> dict[str, Any]:
    """Flow task handler for the daily FX rate refresh.

    Registered as task kind ``'fx_refresh'`` in the flows registry by
    :func:`app.ee.billing.setup`.  Called by the scheduled daily flow.

    Returns
    -------
    dict
        ``{rate, fetched_at, stale}`` from :func:`get_current_rate` after
        the refresh attempt.
    """
    try:
        rate = await refresh_fx_rate()
        info = get_current_rate()
        logger.info("fx_refresh task completed: rate=R%s stale=%s", rate, info["stale"])
        return {
            "rate": str(rate),
            "fetched_at": info["fetched_at"].isoformat() if info["fetched_at"] else None,
            "stale": info["stale"],
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("fx_refresh task failed: %s", exc)
        info = get_current_rate()
        return {
            "rate": str(info["rate"]),
            "fetched_at": info["fetched_at"].isoformat() if info["fetched_at"] else None,
            "stale": True,
            "error": str(exc),
        }
