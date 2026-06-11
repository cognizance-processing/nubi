"""Starter Python-cell templates for API ingestion (design §6 item 4).

"Templates, not framework" (design §6.4): rather than a declarative
``http_pull`` pagination DSL, we ship a small curated set of *starter snippets*
the builder can drop into a ``python`` cell.  Each one:

* reads credentials from ``secrets[...]`` (never inlined — design §6.3);
* reads ``watermark`` (the runtime injects ``ctx.watermark`` for incremental
  pulls) when relevant;
* lands large pulls as Parquet via ``staging.write(...)`` (server-pinned to
  ``orgs/<org>/staging/<run>/`` — design §6.2) rather than serialising rows
  through the task result; and
* returns ``{"rows": …, "watermark": …}`` so the runtime advances
  ``flow_watermarks`` ONLY on success (design §6.1).

These are intentionally minimal and dependency-light (``httpx`` + the injected
``staging`` writer).  They are served read-only via
``GET /flows/ingest-templates`` so the cell builder can present them as
selectable snippets without baking copy into the frontend.

Each template is a :class:`IngestTemplate` with a stable ``id`` (referenced by
the UI), a human ``title``/``description``, and the ``code`` body.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IngestTemplate:
    """One selectable ingest starter snippet (design §6.4)."""

    id: str
    title: str
    description: str
    code: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "code": self.code,
        }


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
#
# NB: kept as plain strings (not f-strings) so the ``{{ secrets.NAME }}`` and
# ``{ }`` JSON braces survive verbatim into the cell editor.

_OFFSET_PAGINATED = '''\
# Ingest: offset/limit-paginated REST API → staging Parquet.
# Creds come from a named secret (NEVER inline a token here).
import httpx

BASE = "https://api.example.com/v1/orders"
TOKEN = secrets["EXAMPLE_API_TOKEN"]          # configured in Connectors/Secrets
PAGE_SIZE = 500

rows = []
offset = 0
with httpx.Client(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30) as client:
    while True:
        resp = client.get(BASE, params={"limit": PAGE_SIZE, "offset": offset})
        resp.raise_for_status()
        batch = resp.json().get("data", [])
        if not batch:
            break
        rows.extend(batch)
        offset += PAGE_SIZE

# Large pulls land as Parquet in staging (server-pinned to this run's prefix),
# NOT serialised through the task result.
manifest = staging.write(rows, "orders/", format="parquet")
result = {"rows": manifest["row_counts"], "staging": manifest}
'''


_CURSOR_PAGINATED = '''\
# Ingest: cursor/next-token-paginated REST API → staging Parquet.
import httpx

BASE = "https://api.example.com/v1/events"
TOKEN = secrets["EXAMPLE_API_TOKEN"]

rows = []
cursor = None
with httpx.Client(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30) as client:
    while True:
        params = {"page_size": 500}
        if cursor:
            params["cursor"] = cursor
        resp = client.get(BASE, params=params)
        resp.raise_for_status()
        body = resp.json()
        rows.extend(body.get("data", []))
        cursor = body.get("next_cursor")
        if not cursor:
            break

manifest = staging.write(rows, "events/", format="parquet")
result = {"rows": manifest["row_counts"], "staging": manifest}
'''


_OAUTH_TOKEN_REFRESH = '''\
# Ingest: OAuth2 client-credentials token refresh, then pull → staging Parquet.
# client id/secret live in named secrets; the access token is minted at run time
# and held in memory only (never logged, never stored).
import httpx

TOKEN_URL = "https://auth.example.com/oauth/token"
DATA_URL = "https://api.example.com/v1/customers"
CLIENT_ID = secrets["EXAMPLE_CLIENT_ID"]
CLIENT_SECRET = secrets["EXAMPLE_CLIENT_SECRET"]

with httpx.Client(timeout=30) as client:
    tok = client.post(TOKEN_URL, data={
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    })
    tok.raise_for_status()
    access_token = tok.json()["access_token"]

    rows = []
    page = 1
    while True:
        resp = client.get(
            DATA_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"page": page, "per_page": 500},
        )
        resp.raise_for_status()
        batch = resp.json().get("data", [])
        if not batch:
            break
        rows.extend(batch)
        page += 1

manifest = staging.write(rows, "customers/", format="parquet")
result = {"rows": manifest["row_counts"], "staging": manifest}
'''


_SINCE_TIMESTAMP_INCREMENTAL = '''\
# Ingest: since-timestamp INCREMENTAL pull → staging Parquet, advancing the mark.
# `watermark` is the last-stored ISO timestamp (None on the first run); the
# runtime persists the returned `watermark` to flow_watermarks ONLY on success.
import httpx
from datetime import datetime, timezone

BASE = "https://api.example.com/v1/orders"
TOKEN = secrets["EXAMPLE_API_TOKEN"]

# First run: pull from epoch; thereafter from the stored mark.
since = watermark or "1970-01-01T00:00:00+00:00"

rows = []
with httpx.Client(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30) as client:
    resp = client.get(BASE, params={"updated_since": since, "limit": 10000})
    resp.raise_for_status()
    rows = resp.json().get("data", [])

# Advance the mark to the newest record seen (fall back to now when empty so a
# run with no new rows still moves time forward; omit `watermark` from the
# return instead if you prefer the stored mark to stay put).
new_mark = max((r["updated_at"] for r in rows), default=None) \\
    or datetime.now(timezone.utc).isoformat()

manifest = staging.write(rows, "orders_incremental/", format="parquet")
result = {"rows": manifest["row_counts"], "watermark": new_mark, "staging": manifest}
'''


INGEST_TEMPLATES: list[IngestTemplate] = [
    IngestTemplate(
        id="rest_offset_paginated",
        title="REST — offset pagination",
        description="Pull an offset/limit-paginated REST endpoint, stage as Parquet.",
        code=_OFFSET_PAGINATED,
    ),
    IngestTemplate(
        id="rest_cursor_paginated",
        title="REST — cursor pagination",
        description="Pull a cursor/next-token-paginated REST endpoint, stage as Parquet.",
        code=_CURSOR_PAGINATED,
    ),
    IngestTemplate(
        id="rest_oauth_refresh",
        title="REST — OAuth token refresh",
        description="Mint an OAuth2 client-credentials token, then pull and stage.",
        code=_OAUTH_TOKEN_REFRESH,
    ),
    IngestTemplate(
        id="rest_since_timestamp_incremental",
        title="REST — since-timestamp incremental",
        description="Incremental pull using ctx.watermark; advances the mark on success.",
        code=_SINCE_TIMESTAMP_INCREMENTAL,
    ),
]


def list_ingest_templates() -> list[dict[str, Any]]:
    """Return all ingest starter templates as JSON-serialisable dicts."""
    return [t.to_dict() for t in INGEST_TEMPLATES]


def get_ingest_template(template_id: str) -> IngestTemplate | None:
    """Return the template with *template_id*, or ``None``."""
    for t in INGEST_TEMPLATES:
        if t.id == template_id:
            return t
    return None
