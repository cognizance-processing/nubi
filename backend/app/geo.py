"""IP geolocation helper backed by ipinfo.io with a DB cache (ip_geo table).

Public API
----------
lookup(ip)
    Resolve an IP to ``{country, region, city, org}`` or ``None``.

    Behaviour:
    - Private / loopback / link-local / unparseable IPs short-circuit to None.
    - The ``ip_geo`` cache table is consulted first; a cached row (even one
      with all-null fields, recorded after a failed upstream lookup) is
      returned without any HTTP call.
    - On cache miss, IF ``IPINFO_TOKEN`` is configured, a single GET to
      ``https://ipinfo.io/{ip}?token=...`` is made (httpx, 3s timeout).  The
      result is upserted into ``ip_geo``.
    - This function NEVER raises — any failure returns None.

Self-hosters: set ``IPINFO_TOKEN`` in the root ``.env`` (free tier token from
https://ipinfo.io) to enable lookups.  Without it, locations stay null.
"""

from __future__ import annotations

import ipaddress
from typing import Any

import httpx

from app import db
from app.config import get_settings

_IPINFO_URL = "https://ipinfo.io/{ip}"
_TIMEOUT_S = 3.0


def is_public_ip(ip: str | None) -> bool:
    """True only for syntactically valid, globally-routable IPs."""
    if not ip:
        return False
    try:
        parsed = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    return not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    )


def _row_to_geo(row: Any) -> dict[str, Any] | None:
    """Convert an ip_geo row to the public dict shape (None when all-null)."""
    geo = {
        "country": row["country"],
        "region": row["region"],
        "city": row["city"],
        "org": row["org"],
    }
    if not any(geo.values()):
        return None
    return geo


async def lookup(ip: str | None) -> dict[str, Any] | None:
    """Resolve *ip* to ``{country, region, city, org}`` (or None). Never raises."""
    if not is_public_ip(ip):
        return None
    ip = str(ip).strip()

    # ── Cache first ───────────────────────────────────────────────────────────
    try:
        cached = await db.fetchrow(
            "SELECT ip, country, region, city, org FROM ip_geo WHERE ip = $1", ip
        )
    except Exception:  # noqa: BLE001 — cache trouble must never break callers
        cached = None
    if cached is not None:
        return _row_to_geo(cached)

    # ── Miss: call ipinfo.io only when a token is configured ─────────────────
    token = get_settings().IPINFO_TOKEN.strip()
    if not token:
        return None

    country = region = city = org = None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(_IPINFO_URL.format(ip=ip), params={"token": token})
            if resp.status_code == 200:
                data = resp.json()
                country = data.get("country") or None
                region = data.get("region") or None
                city = data.get("city") or None
                org = data.get("org") or None
    except Exception:  # noqa: BLE001 — upstream/geo failures are best-effort
        return None

    # ── Upsert cache (also negative results, so we don't re-query bad IPs) ───
    try:
        await db.execute(
            """
            INSERT INTO ip_geo (ip, country, region, city, org, looked_up_at)
            VALUES ($1, $2, $3, $4, $5, now())
            ON CONFLICT (ip) DO UPDATE SET
                country = EXCLUDED.country,
                region = EXCLUDED.region,
                city = EXCLUDED.city,
                org = EXCLUDED.org,
                looked_up_at = EXCLUDED.looked_up_at
            """,
            ip, country, region, city, org,
        )
    except Exception:  # noqa: BLE001
        pass

    if not any((country, region, city, org)):
        return None
    return {"country": country, "region": region, "city": city, "org": org}
