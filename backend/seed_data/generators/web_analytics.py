"""``web_analytics`` dataset — marketing-site traffic over ~24 months.

Sessions grow ~5.5%/month with seasonality.  Each session walks a 5-step
signup funnel ("/" → /features → /pricing → /signup → /welcome) with per-step
drop-off modulated by acquisition source quality and device, so conversion
dashboards show meaningful differences by source/device.  A share of sessions
also hits a content page (blog/docs) for the "top pages" view.

Schema
------
``web_sessions``  : session_id PK, session_date, month, utm_source, utm_medium,
                    device, country, browser, landing_page, pageviews,
                    max_step (1–5), converted (0/1), bounced (0/1), duration_sec
``web_pageviews`` : pageview_id PK, session_id FK, month, page,
                    step (1–5, NULL for content pages), device, utm_source
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from seed_data.generators._common import iter_months, noise, seasonality, weighted_pick

if TYPE_CHECKING:
    import pyarrow as pa

TABLES = ("web_sessions", "web_pageviews")

# (source, traffic weight, default medium)
SOURCES = [
    ("google", 0.32, "organic"),
    ("direct", 0.20, "(none)"),
    ("newsletter", 0.12, "email"),
    ("linkedin", 0.10, "social"),
    ("twitter", 0.08, "social"),
    ("facebook", 0.07, "social"),
    ("producthunt", 0.06, "referral"),
    ("bing", 0.05, "organic"),
]
_SOURCE_W = [(s, w) for s, w, _ in SOURCES]
_SOURCE_MEDIUM = {s: m for s, _, m in SOURCES}
# Funnel quality multiplier per source (newsletter converts best, social worst).
_SOURCE_QUALITY = {
    "google": 1.05, "direct": 1.00, "newsletter": 1.35, "linkedin": 0.95,
    "twitter": 0.80, "facebook": 0.75, "producthunt": 1.20, "bing": 0.90,
}

DEVICES = [("desktop", 0.56), ("mobile", 0.37), ("tablet", 0.07)]
_DEVICE_QUALITY = {"desktop": 1.0, "mobile": 0.85, "tablet": 0.9}

COUNTRIES = [
    ("South Africa", 0.28), ("United States", 0.22), ("United Kingdom", 0.12),
    ("Germany", 0.08), ("Nigeria", 0.07), ("Kenya", 0.06), ("India", 0.06),
    ("Netherlands", 0.05), ("Brazil", 0.03), ("Australia", 0.03),
]
BROWSERS = [("Chrome", 0.62), ("Safari", 0.18), ("Edge", 0.11), ("Firefox", 0.09)]

# 5-step signup funnel: page path per step.
FUNNEL_PAGES = ["/", "/features", "/pricing", "/signup", "/welcome"]
# Base probability of continuing from step k to k+1 (before quality modifiers).
_STEP_CONTINUE = [0.50, 0.45, 0.35, 0.72]

CONTENT_PAGES = ["/blog/launch", "/blog/benchmarks", "/docs/quickstart", "/docs/connectors", "/changelog"]

_BASE_SESSIONS = 380          # sessions in the first month
_MONTHLY_GROWTH = 1.055       # ~3.4x over 24 months


def build_tables() -> "dict[str, pa.Table]":
    """Build the web-analytics dataset as Arrow tables (deterministic)."""
    import pyarrow as pa
    from datetime import date as _date

    sessions: list[tuple] = []
    pageviews: list[tuple] = []

    sid = 0
    pv_id = 0
    for idx, first, month_str in iter_months():
        n = int(_BASE_SESSIONS * (_MONTHLY_GROWTH ** idx) * seasonality(first.month)
                * (0.95 + 0.10 * noise("volume", month_str)))
        for i in range(n):
            sid += 1
            source = weighted_pick(_SOURCE_W, "src", sid)
            medium = _SOURCE_MEDIUM[source]
            if source in ("google", "bing") and noise("cpc", sid) < 0.30:
                medium = "cpc"
            device = weighted_pick(DEVICES, "dev", sid)
            country = weighted_pick(COUNTRIES, "geo", sid)
            browser = weighted_pick(BROWSERS, "ua", sid)
            day = 1 + int(noise("day", sid) * 28)
            session_date = _date(first.year, first.month, day)

            quality = _SOURCE_QUALITY[source] * _DEVICE_QUALITY[device]

            # Walk the funnel.
            max_step = 1
            for k, p in enumerate(_STEP_CONTINUE):
                if noise("step", sid, k) < min(0.97, p * quality):
                    max_step = k + 2
                else:
                    break

            # Content-page visit for ~25% of sessions (blog/docs traffic).
            content_page = None
            if noise("content", sid) < 0.25:
                content_page = CONTENT_PAGES[int(noise("cpage", sid) * len(CONTENT_PAGES)) % len(CONTENT_PAGES)]

            landing_page = content_page if (content_page and noise("land", sid) < 0.4) else "/"
            converted = 1 if max_step == 5 else 0
            n_pageviews = max_step + (1 if content_page else 0)
            bounced = 1 if n_pageviews == 1 else 0
            duration = int(max_step * 40 + noise("dur", sid) * 140) + (45 if content_page else 0)

            sessions.append((
                sid, session_date, month_str, source, medium, device, country, browser,
                landing_page, n_pageviews, max_step, converted, bounced, duration,
            ))

            for k in range(max_step):
                pv_id += 1
                pageviews.append((pv_id, sid, month_str, FUNNEL_PAGES[k], k + 1, device, source))
            if content_page:
                pv_id += 1
                pageviews.append((pv_id, sid, month_str, content_page, None, device, source))

    def col(rows: list[tuple], names: list[str]) -> dict[str, list]:
        return {n_: [r[i] for r in rows] for i, n_ in enumerate(names)}

    s_cols = col(sessions, [
        "session_id", "session_date", "month", "utm_source", "utm_medium", "device",
        "country", "browser", "landing_page", "pageviews", "max_step", "converted",
        "bounced", "duration_sec",
    ])
    return {
        "web_sessions": pa.table({
            **s_cols,
            "session_date": pa.array(s_cols["session_date"], type=pa.date32()),
        }),
        "web_pageviews": pa.table(col(
            pageviews, ["pageview_id", "session_id", "month", "page", "step", "device", "utm_source"]
        )),
    }
