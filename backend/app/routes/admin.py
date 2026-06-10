"""Super-admin routes — all endpoints under /admin, gated by require_superadmin.

Endpoints
---------
GET /admin/overview      Platform counts + 30-day signup/login series.
GET /admin/users         Paginated user list with last login / location / orgs.
GET /admin/orgs          Paginated org list with member/project counts.
GET /admin/orgs/{id}     One org with its members and projects.
GET /admin/geo/summary   Login-event country rollup (lazily geolocates IPs).

Security
--------
Every route depends on :func:`app.auth.superadmin.require_superadmin`, which
re-reads the caller's user row from the DB on each request and 403s unless
``users.is_superadmin`` is true.  The flag itself is NEVER writable through
any API endpoint — see the dependency module and the 0001_auth.sql header.

This module attaches itself to the shared ``api_router`` at import time, the
same way the other route modules do (main.py imports it for its side effect).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app import geo as geo_module
from app.auth.superadmin import require_superadmin
from app.db import fetch, fetchrow
from app.errors import AppError
from app.routes import api_router

router = APIRouter(prefix="/admin", tags=["admin"])

# Max uncached IPs geolocated per /admin/geo/summary call (best-effort).
_GEO_LAZY_BATCH = 50


# ── Serialization helpers ─────────────────────────────────────────────────────

def _iso(value: Any) -> Any:
    """ISO-8601-serialize datetimes; pass everything else through."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _location_string(city: Any, region: Any, country: Any) -> str | None:
    """Build a display location like ``'Cape Town, ZA'`` (or None)."""
    place = city or region
    if place and country:
        return f"{place}, {country}"
    if place:
        return str(place)
    if country:
        return str(country)
    return None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/overview")
async def admin_overview(
    _admin: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Platform-wide counts plus 30-day signup and login series.

    Returns
    -------
    200 {counts: {users, orgs, projects, boards, queries, flows, datastores},
         signups_by_day: [{day: 'YYYY-MM-DD', count}],
         logins_by_day:  [{day: 'YYYY-MM-DD', count}]}
    """
    counts_row = await fetchrow(
        """
        SELECT
            (SELECT count(*)::int FROM users)      AS users,
            (SELECT count(*)::int FROM orgs)       AS orgs,
            (SELECT count(*)::int FROM projects)   AS projects,
            (SELECT count(*)::int FROM boards)     AS boards,
            (SELECT count(*)::int FROM queries)    AS queries,
            (SELECT count(*)::int FROM flows)      AS flows,
            (SELECT count(*)::int FROM datastores) AS datastores
        """
    )
    counts = {
        key: int(dict(counts_row or {}).get(key) or 0)
        for key in ("users", "orgs", "projects", "boards", "queries", "flows", "datastores")
    }

    signup_rows = await fetch(
        """
        SELECT to_char((created_at AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD') AS day,
               count(*)::int AS count
        FROM users
        WHERE created_at >= now() - interval '30 days'
        GROUP BY 1
        ORDER BY 1
        """
    )
    login_rows = await fetch(
        """
        SELECT to_char((created_at AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD') AS day,
               count(*)::int AS count
        FROM login_events
        WHERE created_at >= now() - interval '30 days'
        GROUP BY 1
        ORDER BY 1
        """
    )

    return {
        "counts": counts,
        "signups_by_day": [{"day": r["day"], "count": int(r["count"])} for r in signup_rows],
        "logins_by_day": [{"day": r["day"], "count": int(r["count"])} for r in login_rows],
    }


@router.get("/users")
async def admin_users(
    search: str = "",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Paginated user list with last login, location, and org memberships.

    Returns
    -------
    200 {users: [{id, email, name, created_at, is_superadmin, last_login_at,
                  last_ip, last_location, orgs: [{id, name, role}]}], total}
    """
    needle = search.strip()

    total_row = await fetchrow(
        """
        SELECT count(*)::int AS total
        FROM users u
        WHERE ($1 = '' OR u.email ILIKE '%' || $1 || '%' OR u.name ILIKE '%' || $1 || '%')
        """,
        needle,
    )
    total = int(dict(total_row or {}).get("total") or 0)

    rows = await fetch(
        """
        SELECT u.id, u.email, u.name, u.created_at, u.is_superadmin,
               le.created_at AS last_login_at, le.ip AS last_ip,
               g.city AS geo_city, g.region AS geo_region, g.country AS geo_country
        FROM users u
        LEFT JOIN LATERAL (
            SELECT ip, created_at
            FROM login_events
            WHERE user_id = u.id
            ORDER BY created_at DESC
            LIMIT 1
        ) le ON true
        LEFT JOIN ip_geo g ON g.ip = le.ip
        WHERE ($1 = '' OR u.email ILIKE '%' || $1 || '%' OR u.name ILIKE '%' || $1 || '%')
        ORDER BY u.created_at DESC
        LIMIT $2 OFFSET $3
        """,
        needle,
        limit,
        offset,
    )

    user_ids = [str(r["id"]) for r in rows]
    orgs_by_user: dict[str, list[dict[str, Any]]] = {uid: [] for uid in user_ids}
    if user_ids:
        membership_rows = await fetch(
            """
            SELECT om.user_id, o.id AS org_id, o.name AS org_name, om.role
            FROM org_members om
            JOIN orgs o ON o.id = om.org_id
            WHERE om.user_id = ANY($1::uuid[])
            ORDER BY o.name
            """,
            user_ids,
        )
        for m in membership_rows:
            orgs_by_user.setdefault(str(m["user_id"]), []).append(
                {"id": str(m["org_id"]), "name": m["org_name"], "role": m["role"]}
            )

    users = [
        {
            "id": str(r["id"]),
            "email": str(r["email"]),
            "name": r["name"],
            "created_at": _iso(r["created_at"]),
            "is_superadmin": bool(r["is_superadmin"]),
            "last_login_at": _iso(r["last_login_at"]),
            "last_ip": r["last_ip"],
            "last_location": _location_string(
                dict(r).get("geo_city"), dict(r).get("geo_region"), dict(r).get("geo_country")
            ),
            "orgs": orgs_by_user.get(str(r["id"]), []),
        }
        for r in rows
    ]
    return {"users": users, "total": total}


@router.get("/orgs")
async def admin_orgs(
    search: str = "",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Paginated org list with member and project counts.

    Returns
    -------
    200 {orgs: [{id, name, slug, created_at, member_count, project_count}], total}
    """
    needle = search.strip()

    total_row = await fetchrow(
        """
        SELECT count(*)::int AS total
        FROM orgs o
        WHERE ($1 = '' OR o.name ILIKE '%' || $1 || '%' OR o.slug ILIKE '%' || $1 || '%')
        """,
        needle,
    )
    total = int(dict(total_row or {}).get("total") or 0)

    rows = await fetch(
        """
        SELECT o.id, o.name, o.slug, o.created_at,
               (SELECT count(*)::int FROM org_members om WHERE om.org_id = o.id) AS member_count,
               (SELECT count(*)::int FROM projects p WHERE p.org_id = o.id)      AS project_count
        FROM orgs o
        WHERE ($1 = '' OR o.name ILIKE '%' || $1 || '%' OR o.slug ILIKE '%' || $1 || '%')
        ORDER BY o.created_at DESC
        LIMIT $2 OFFSET $3
        """,
        needle,
        limit,
        offset,
    )

    orgs = [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "slug": r["slug"],
            "created_at": _iso(r["created_at"]),
            "member_count": int(r["member_count"] or 0),
            "project_count": int(r["project_count"] or 0),
        }
        for r in rows
    ]
    return {"orgs": orgs, "total": total}


@router.get("/orgs/{org_id}")
async def admin_org_detail(
    org_id: str,
    _admin: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """One org with its members and projects.

    Returns
    -------
    200 {org: {id, name, slug, created_at, member_count, project_count},
         members: [{user_id, email, name, role}],
         projects: [{id, name, slug, created_at}]}
    """
    org_row = await fetchrow(
        """
        SELECT o.id, o.name, o.slug, o.created_at,
               (SELECT count(*)::int FROM org_members om WHERE om.org_id = o.id) AS member_count,
               (SELECT count(*)::int FROM projects p WHERE p.org_id = o.id)      AS project_count
        FROM orgs o
        WHERE o.id = $1::uuid
        """,
        org_id,
    )
    if org_row is None:
        raise AppError("not_found", "Org not found.", 404)

    member_rows = await fetch(
        """
        SELECT om.user_id, u.email, u.name, om.role
        FROM org_members om
        JOIN users u ON u.id = om.user_id
        WHERE om.org_id = $1::uuid
        ORDER BY
            CASE om.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 WHEN 'member' THEN 2 ELSE 3 END,
            u.email
        """,
        org_id,
    )
    project_rows = await fetch(
        """
        SELECT id, name, slug, created_at
        FROM projects
        WHERE org_id = $1::uuid
        ORDER BY created_at
        """,
        org_id,
    )

    return {
        "org": {
            "id": str(org_row["id"]),
            "name": org_row["name"],
            "slug": org_row["slug"],
            "created_at": _iso(org_row["created_at"]),
            "member_count": int(org_row["member_count"] or 0),
            "project_count": int(org_row["project_count"] or 0),
        },
        "members": [
            {
                "user_id": str(m["user_id"]),
                "email": str(m["email"]),
                "name": m["name"],
                "role": m["role"],
            }
            for m in member_rows
        ],
        "projects": [
            {
                "id": str(p["id"]),
                "name": p["name"],
                "slug": p["slug"],
                "created_at": _iso(p["created_at"]),
            }
            for p in project_rows
        ],
    }


@router.get("/geo/summary")
async def admin_geo_summary(
    _admin: dict[str, Any] = Depends(require_superadmin),
) -> dict[str, Any]:
    """Country rollup of login events; lazily geolocates uncached IPs.

    Up to ~50 distinct, not-yet-cached IPs are geolocated per call via
    :func:`app.geo.lookup` (best-effort: failures are ignored; private IPs
    are skipped inside lookup()).

    Returns
    -------
    200 {countries: [{country, count}], total_located, total_events}
    """
    # ── Lazily geolocate uncached IPs (best-effort) ───────────────────────────
    try:
        uncached = await fetch(
            """
            SELECT DISTINCT le.ip
            FROM login_events le
            LEFT JOIN ip_geo g ON g.ip = le.ip
            WHERE le.ip IS NOT NULL AND g.ip IS NULL
            LIMIT $1
            """,
            _GEO_LAZY_BATCH,
        )
        for row in uncached:
            await geo_module.lookup(row["ip"])  # never raises
    except Exception:  # noqa: BLE001 — lazy enrichment must not break the summary
        pass

    country_rows = await fetch(
        """
        SELECT g.country, count(*)::int AS count
        FROM login_events le
        JOIN ip_geo g ON g.ip = le.ip
        WHERE g.country IS NOT NULL
        GROUP BY g.country
        ORDER BY count DESC, g.country
        """
    )
    totals_row = await fetchrow(
        """
        SELECT
            count(*)::int AS total_events,
            count(g.country)::int AS total_located
        FROM login_events le
        LEFT JOIN ip_geo g ON g.ip = le.ip
        """
    )
    totals = dict(totals_row or {})

    return {
        "countries": [
            {"country": r["country"], "count": int(r["count"])} for r in country_rows
        ],
        "total_located": int(totals.get("total_located") or 0),
        "total_events": int(totals.get("total_events") or 0),
    }


# ── Attach to the shared api_router (same pattern as the other modules) ──────
api_router.include_router(router)
