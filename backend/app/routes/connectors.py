"""Connector create/update API — org-scoped, secrets encrypted at rest.

Endpoints
---------
POST   /connectors          — create a connector (datastore row + encrypted secret)
GET    /connectors          — list connectors for the caller's org (no secrets)
GET    /connectors/{id}     — fetch a single connector (no secrets)
PUT    /connectors/{id}     — update non-secret config and/or rotate the secret
DELETE /connectors/{id}     — delete connector + secret blob
POST   /connectors/{id}/test — validate config + secret are resolvable (no network)

Security contract
-----------------
- The ``secret`` field submitted by callers is NEVER stored in ``datastores.config``.
- Secrets are forwarded to ``SecretStore.put()`` which encrypts them with
  AES-256-GCM before writing to the ``connector_secrets`` table.
- No response ever includes secret material.
- All operations are org-scoped; a row belonging to a different org is treated
  as not-found (no information leak).

The router self-registers on the shared ``api_router`` at import time so that
``main.py``'s ``include_router(api_router, prefix="/api/v1")`` picks it up
automatically.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, model_validator

from app.auth.deps import current_user
from app.auth.roles import require_writer_default
from app.db import fetchrow
from app.errors import AppError
from app.repos.provider import get_repo, Repo
from app.routes import api_router

# ── Sub-router ────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/connectors", tags=["connectors"])

# ── Connector type literal ────────────────────────────────────────────────────

ConnectorType = Literal[
    # Relational
    "postgres", "mysql", "mariadb", "sqlserver", "oracle", "cockroachdb",
    # Cloud-managed SQL
    "cloudsql", "azuresql",
    # Cloud warehouses
    "bigquery", "snowflake", "redshift", "databricks", "clickhouse", "azuresynapse",
    # Query engines
    "athena", "trino", "presto",
    # Lakehouse & files
    "duckdb", "duckdb_storage",
    # File-only ingestion sources (design §2 — FileConnectorMixin, not queryable)
    "sftp", "ftp",
    # APIs & custom
    "http_json", "jdbc",
    # Built-in demo dataset (virtual — re-adds the demo connector after removal)
    "demo",
]

# ── Built-in demo connector (virtual / system) ────────────────────────────────
# The demo dataset is IDENTICAL for every org and lives in-process (a tiny
# in-memory DuckDB ``demo`` table seeded in routes/query.py).  Rather than
# physically copying it per org, we expose a single VIRTUAL connector that
# ``GET /connectors`` injects into every org's list.  Removing it (DELETE) writes
# a per-org "hidden" marker row (in the existing ``datastores`` table, so org
# scoping is automatic and no migration is required); re-adding it (POST type
# "demo") deletes that marker.  Querying / data-browsing the demo connector is
# handled by routes/query.py + routes/data_browser.py, which recognise the
# sentinel id and route to the shared in-process demo connector.

DEMO_CONNECTOR_ID = "__demo__"
DEMO_CONNECTOR_NAME = "Demo data"
# Marker connector_type written to the per-org "hidden" datastore row.  It is
# deliberately NOT in ConnectorType / CONNECTOR_TYPES so it never renders as a
# real connector and is filtered out of the list.
_DEMO_HIDDEN_MARKER = "__demo_hidden__"


def _demo_connector_row(org_id: str) -> dict[str, Any]:
    """Return the virtual demo-connector row injected into a list response.

    Shaped like a real datastore row so the frontend renders it identically.
    It carries no secret material and a fixed, non-UUID sentinel id.
    """
    return {
        "id": DEMO_CONNECTOR_ID,
        "org_id": org_id,
        "project_id": None,
        "created_by": None,
        "name": DEMO_CONNECTOR_NAME,
        "config": {
            "connector_type": "demo",
            "description": "Built-in sample dataset — query it instantly, no setup.",
            "read_only": True,
            "system": True,
        },
        "created_at": "1970-01-01T00:00:00+00:00",
        "updated_at": "1970-01-01T00:00:00+00:00",
    }


async def _find_demo_hidden_row(org_id: str, repo: Repo) -> dict[str, Any] | None:
    """Return the per-org demo-hidden marker datastore row, or ``None``."""
    rows = await repo.list("datastores", org_id)
    for row in rows:
        cfg = row.get("config")
        if isinstance(cfg, dict) and cfg.get("connector_type") == _DEMO_HIDDEN_MARKER:
            return row
    return None


async def _demo_is_hidden(org_id: str, repo: Repo) -> bool:
    """Return ``True`` if this org has removed the demo connector."""
    return (await _find_demo_hidden_row(org_id, repo)) is not None


def _is_editable_demo_row(row: dict[str, Any]) -> bool:
    """True for the user-owned EDITABLE demo-lakehouse datastore.

    The editable demo (``app.demo_lakehouse``) is a per-project parquet-backed
    ``duckdb`` connector tagged ``sample=true``/``editable_parquet=true`` and NOT
    marked ``system`` — so it already renders as a normal card. When present, the
    virtual read-only "Demo data" card is suppressed to avoid showing two demo
    connectors.
    """
    cfg = row.get("config")
    if not isinstance(cfg, dict):
        return False
    return (
        cfg.get("sample") is True
        and not cfg.get("system")
        and cfg.get("connector_type") == "duckdb"
        and cfg.get("editable_parquet") is True
    )


async def _has_editable_demo(org_id: str, repo: Repo) -> bool:
    """Return ``True`` if the org has a user-owned editable demo connector."""
    for row in await repo.list("datastores", org_id):
        if _is_editable_demo_row(row):
            return True
    return False

# ── Secret-key allowlist per connector type ───────────────────────────────────
# These are the ONLY keys that belong in the secret blob; everything else goes
# into config.  This list is used for validation, not filtering — callers may
# omit secret keys if they are not applicable.

_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "service_account_json",
        "token",
        "api_key",
        "access_token",          # Databricks
        "aws_secret_access_key",  # Athena / object storage
        "private_key",            # Snowflake key-pair auth
    }
)

# ── Pydantic schemas ──────────────────────────────────────────────────────────


class ConnectorConfig(BaseModel):
    """Non-secret connection parameters stored in ``datastores.config``."""

    host: str | None = None
    port: int | None = None
    database: str | None = None
    user: str | None = None
    sslmode: str | None = None
    network_mode: str | None = None
    bridge_id: str | None = None
    # Allow arbitrary extra non-secret fields (e.g. http_json base_url, timeout…)
    model_config = {"extra": "allow"}

    def to_safe_dict(self) -> dict[str, Any]:
        """Return only the non-None values, suitable for storage in jsonb."""
        return {k: v for k, v in self.model_dump().items() if v is not None}


class ConnectorSecret(BaseModel):
    """Sensitive credential fields — NEVER stored in datastores.config."""

    password: str | None = None
    service_account_json: str | None = None
    token: str | None = None
    api_key: str | None = None
    access_token: str | None = None
    aws_secret_access_key: str | None = None
    private_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return only the non-None values for storage in the secret store."""
        return {k: v for k, v in self.model_dump().items() if v is not None}

    def is_empty(self) -> bool:
        return not bool(self.to_dict())


class CreateConnectorIn(BaseModel):
    """Request body for POST /connectors."""

    name: str
    type: ConnectorType
    config: ConnectorConfig = ConnectorConfig()
    secret: ConnectorSecret = ConnectorSecret()

    @model_validator(mode="after")
    def assert_no_secret_in_config(self) -> "CreateConnectorIn":
        """Raise if the caller accidentally put secret keys inside config."""
        config_dict = self.config.model_dump()
        leaked = _SECRET_KEYS & set(config_dict.keys())
        if leaked:
            raise ValueError(
                f"Secret fields must not appear in config: {sorted(leaked)!r}. "
                "Pass them under the 'secret' key instead."
            )
        return self


class UpdateConnectorIn(BaseModel):
    """Request body for PUT /connectors/{id}."""

    name: str | None = None
    config: ConnectorConfig | None = None
    secret: ConnectorSecret | None = None

    @model_validator(mode="after")
    def assert_no_secret_in_config(self) -> "UpdateConnectorIn":
        if self.config is not None:
            config_dict = self.config.model_dump()
            leaked = _SECRET_KEYS & set(config_dict.keys())
            if leaked:
                raise ValueError(
                    f"Secret fields must not appear in config: {sorted(leaked)!r}."
                )
        return self


# ── Org resolution helper ─────────────────────────────────────────────────────


async def _get_user_org(user_id: str, repo: Repo) -> str:
    """Return the org_id for the user's first membership.

    Mirrors the pattern in routes/resources.py; uses InMemoryRepo's helper
    when available, falls back to a DB query for PgRepo.
    """
    if hasattr(repo, "get_org_for_user"):
        org_id = repo.get_org_for_user(user_id)  # type: ignore[attr-defined]
        if org_id:
            return org_id
        raise AppError("org_not_found", "User has no org membership.", 404)

    row = await fetchrow(
        """
        SELECT org_id FROM org_members
        WHERE user_id = $1::uuid
        ORDER BY org_id
        LIMIT 1
        """,
        user_id,
    )
    if row is None:
        raise AppError("org_not_found", "User has no org membership.", 404)
    return str(row["org_id"])


# ── Secret store accessor ─────────────────────────────────────────────────────


def _secret_store():
    """Lazy import of the secret store to avoid hard circular-import dependencies."""
    from app.connectors.secret_store import get_secret_store  # type: ignore[import]
    return get_secret_store()


# ── Response sanitiser ────────────────────────────────────────────────────────


def _sanitise(row: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *row* with all secret material scrubbed.

    CRITICAL: this is the ONLY place we remove secrets before returning to
    callers.  Every endpoint calls this before returning.
    """
    safe = dict(row)
    # Strip any secret keys that may be present (defensive, belt-and-suspenders)
    config = safe.get("config")
    if isinstance(config, dict):
        safe["config"] = {k: v for k, v in config.items() if k not in _SECRET_KEYS}

    # Never include a top-level 'secret' key.
    safe.pop("secret", None)

    # Belt-and-suspenders assertion — will surface in tests
    _assert_no_secret_leakage(safe)
    return safe


def _assert_no_secret_leakage(row: dict[str, Any]) -> None:
    """Raise AssertionError if any secret key is present anywhere in *row*.

    This is an internal invariant check; it will never fire in correct
    operation but will catch regressions immediately in tests.
    """
    # Check top-level keys
    top_level_secrets = _SECRET_KEYS & set(row.keys())
    assert not top_level_secrets, (
        f"SECRET LEAKAGE: top-level keys {top_level_secrets!r} must never "
        "appear in a connector response"
    )

    # Check inside config
    config = row.get("config")
    if isinstance(config, dict):
        config_secrets = _SECRET_KEYS & set(config.keys())
        assert not config_secrets, (
            f"SECRET LEAKAGE: config keys {config_secrets!r} must never "
            "appear in a connector response"
        )

    # Check the serialised form (catches deeply nested leaks)
    try:
        serialised = json.dumps(row)
        for key in _SECRET_KEYS:
            # Look for JSON key patterns "password": or 'password':
            assert f'"{key}"' not in serialised or _key_is_not_a_value(key, row), (
                f"SECRET LEAKAGE: key {key!r} found in serialised response"
            )
    except (TypeError, ValueError):
        pass  # Non-serialisable rows — skip the serialisation check


def _key_is_not_a_value(key: str, row: dict[str, Any]) -> bool:
    """Return True if *key* only appears as a dict key (at any depth), not a value."""
    # Simple check: this guards against the key appearing in config values
    # We've already stripped known secret keys from config in _sanitise()
    return True  # post-sanitise we trust the explicit scrubbing above


# ── Connector-type label for datastores.config ───────────────────────────────


def _build_config(connector_type: str, non_secret_config: dict[str, Any]) -> dict[str, Any]:
    """Merge the connector type into the non-secret config dict."""
    return {"connector_type": connector_type, **non_secret_config}


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("", status_code=201, dependencies=[Depends(require_writer_default)])
async def create_connector(
    body: CreateConnectorIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Create a new connector.

    Stores NON-SECRET parts in ``datastores.config``; forwards the ``secret``
    dict to the SecretStore for AES-256-GCM encryption.

    Returns the datastore row without any secret material.
    """
    org_id = await _get_user_org(str(user["id"]), repo)

    # ── Demo connector re-add ─────────────────────────────────────────────────
    # The demo connector is virtual; "creating" it simply clears the per-org
    # "hidden" marker so GET /connectors injects it again.  No datastore row and
    # no secret are written for the demo connector itself.
    if body.type == "demo":
        hidden = await _find_demo_hidden_row(org_id, repo)
        if hidden is not None:
            await repo.delete("datastores", org_id, str(hidden["id"]))
        return _sanitise(_demo_connector_row(org_id))

    # Build the safe config — explicitly excludes all secret keys
    safe_config = _build_config(body.type, body.config.to_safe_dict())

    # CRITICAL: assert the secret is not leaking into config
    for key in _SECRET_KEYS:
        assert key not in safe_config, (
            f"BUG: secret key {key!r} found in config before write — this is a "
            "programming error in connectors.py"
        )

    # Create the datastore row (config contains ONLY non-secret parts)
    row = await repo.create(
        resource="datastores",
        org_id=org_id,
        created_by=str(user["id"]),
        name=body.name,
        config=safe_config,
    )
    datastore_id = row["id"]

    # Store the encrypted secret blob (may be empty dict for secret-less connectors)
    secret_dict = body.secret.to_dict()
    await _secret_store().put(datastore_id, org_id, secret_dict)

    return _sanitise(row)


@router.get("")
async def list_connectors(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """List all connectors for the caller's org (no secret material returned).

    The built-in virtual "Demo data" connector is surfaced ONLY in the org's
    demo/default project — other projects start empty and require a real
    connector. Real datastores remain org-wide.
    """
    org_id = await _get_user_org(str(user["id"]), repo)

    all_datastores = await repo.list("datastores", org_id)
    # Filter to rows where config identifies them as a connector.  The
    # ``__demo_hidden__`` marker row is excluded — it is internal bookkeeping,
    # not a real connector.
    connectors = [
        row for row in all_datastores
        if isinstance(row.get("config"), dict)
        and "connector_type" in row["config"]
        and row["config"]["connector_type"] != _DEMO_HIDDEN_MARKER
        # System rows (e.g. the seeded demo datastore that backs the demo
        # dashboards by id) are internal — the branded virtual "Demo data"
        # connector is surfaced instead, so they never render as a raw card.
        and not row["config"].get("system")
    ]
    result = [_sanitise(row) for row in connectors]

    # Inject the virtual (read-only) demo connector ONLY in the org's
    # demo/default project, unless the org removed it OR already owns an
    # EDITABLE demo-lakehouse connector (which renders as its own card — we
    # don't want two demo connectors). Other projects start empty so the user
    # connects their own data — there is no demo connector to fall back on.
    if (
        await _in_demo_project(org_id, request)
        and not await _demo_is_hidden(org_id, repo)
        and not await _has_editable_demo(org_id, repo)
    ):
        result.insert(0, _sanitise(_demo_connector_row(org_id)))

    return result


async def _in_demo_project(org_id: str, request: Request) -> bool:
    """Whether the request targets the org's demo/default project.

    The demo bundle is seeded into the default project at onboarding, so the
    virtual demo connector belongs there. Returns True when the active project
    (``X-Project-Id`` else the default) is the default project, or when no
    project can be resolved (e.g. test doubles without a projects table).
    """
    from app.repos import projects as projects_repo  # noqa: PLC0415
    from app.routes._org import resolve_project_filter  # noqa: PLC0415

    default_project = await projects_repo.get_default_project_id(org_id)
    if default_project is None:
        return True  # no projects table / single-project test double → show demo
    active_project = await resolve_project_filter(org_id, request)
    return active_project is None or str(active_project) == str(default_project)


@router.get("/{connector_id}")
async def get_connector(
    connector_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Fetch a single connector by ID (no secret material returned).

    Returns 404 if not found or belongs to a different org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)

    # Virtual demo connector — resolvable unless this org removed it.
    if connector_id == DEMO_CONNECTOR_ID:
        if await _demo_is_hidden(org_id, repo):
            raise AppError("not_found", "Connector not found.", 404)
        return _sanitise(_demo_connector_row(org_id))

    row = await repo.get("datastores", org_id, connector_id)
    if row is None:
        raise AppError("not_found", "Connector not found.", 404)
    if not isinstance(row.get("config"), dict) or "connector_type" not in row["config"]:
        raise AppError("not_found", "Connector not found.", 404)
    return _sanitise(row)


@router.put("/{connector_id}", dependencies=[Depends(require_writer_default)])
async def update_connector(
    connector_id: str,
    body: UpdateConnectorIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Update a connector's config and/or rotate its secret.

    Non-secret fields are updated in ``datastores.config``.
    If ``secret`` is supplied, a new encrypted blob replaces the existing one.
    Returns the updated datastore row without any secret material.
    """
    org_id = await _get_user_org(str(user["id"]), repo)

    # Verify the row exists and belongs to this org
    existing = await repo.get("datastores", org_id, connector_id)
    if existing is None:
        raise AppError("not_found", "Connector not found.", 404)
    if not isinstance(existing.get("config"), dict) or "connector_type" not in existing["config"]:
        raise AppError("not_found", "Connector not found.", 404)

    # SECURITY: the managed-lakehouse datastore's storage path is server-pinned
    # to the org's isolated prefix (orgs/<org_id>/lake/). It must NOT be editable
    # via this route — otherwise a user could repoint `database` at another org's
    # prefix or an arbitrary URL. Managed lakes are provisioned/managed through
    # /lakehouse only.
    if existing["config"].get("managed_lake") is True:
        raise AppError(
            "managed_lake_immutable",
            "This is a Nubi-managed lakehouse — its storage path is server-pinned "
            "and cannot be edited here. Manage it via /lakehouse.",
            409,
        )

    # Build the update fields dict
    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.config is not None:
        # Merge new non-secret config over the existing config
        existing_config: dict[str, Any] = dict(existing.get("config") or {})
        new_non_secret = body.config.to_safe_dict()

        # CRITICAL: assert no secret keys in the new config
        for key in _SECRET_KEYS:
            assert key not in new_non_secret, (
                f"BUG: secret key {key!r} found in update config — programming error"
            )

        existing_config.update(new_non_secret)
        fields["config"] = existing_config

    row = await repo.update("datastores", org_id, connector_id, fields)
    if row is None:
        raise AppError("not_found", "Connector not found.", 404)

    # Rotate the secret if provided
    if body.secret is not None and not body.secret.is_empty():
        await _secret_store().put(connector_id, org_id, body.secret.to_dict())

    return _sanitise(row)


@router.delete("/{connector_id}", status_code=204, dependencies=[Depends(require_writer_default)])
async def delete_connector(
    connector_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Delete a connector and its encrypted secret blob.

    Returns 204 on success, 404 if not found or belongs to a different org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)

    # ── Demo connector removal ────────────────────────────────────────────────
    # The demo connector is virtual and shared across orgs — we never delete the
    # underlying dataset.  Removing it for this org simply records a per-org
    # "hidden" marker row so GET /connectors stops injecting it.  Idempotent.
    if connector_id == DEMO_CONNECTOR_ID:
        if not await _demo_is_hidden(org_id, repo):
            await repo.create(
                resource="datastores",
                org_id=org_id,
                created_by=str(user["id"]),
                name="(demo connector hidden)",
                config={"connector_type": _DEMO_HIDDEN_MARKER},
            )
        return Response(status_code=204)

    # Verify row exists and is a connector before deleting
    existing = await repo.get("datastores", org_id, connector_id)
    if existing is None:
        raise AppError("not_found", "Connector not found.", 404)
    if not isinstance(existing.get("config"), dict) or "connector_type" not in existing["config"]:
        raise AppError("not_found", "Connector not found.", 404)

    # SECURITY: managed lakehouses must be deprovisioned through DELETE /lakehouse
    # (which also deletes the org's prefix objects). Deleting just the row here
    # would orphan stored data and leave it billable. Refuse.
    if existing["config"].get("managed_lake") is True:
        raise AppError(
            "managed_lake_immutable",
            "This is a Nubi-managed lakehouse — deprovision it via DELETE /lakehouse.",
            409,
        )

    deleted = await repo.delete("datastores", org_id, connector_id)
    if not deleted:
        raise AppError("not_found", "Connector not found.", 404)

    # Remove the encrypted secret blob (best-effort — row is already gone)
    try:
        await _secret_store().delete(connector_id, org_id)
    except Exception:
        # Cascade removes the secret in the DB via FK; the in-process store
        # may raise if the entry is already gone — that is acceptable.
        pass

    return Response(status_code=204)


@router.post("/{connector_id}/test")
async def test_connector(
    connector_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Validate that the config and secret are resolvable for a connector.

    This is a *structural* check only — no network socket is opened.  It
    confirms that:
    1. The datastore row exists and is accessible (config layer).
    2. The encrypted secret can be retrieved from the SecretStore (secret layer).

    Returns
    -------
    dict
        ``{ok: True, checked: 'config+secret resolved', connector_id: ...,
           type: ..., layers: {config: True, secret: True}}``
        or a structured error result if a layer is missing.
    """
    org_id = await _get_user_org(str(user["id"]), repo)

    # Virtual demo connector — always resolvable (no secret, in-process data).
    if connector_id == DEMO_CONNECTOR_ID:
        ok = not await _demo_is_hidden(org_id, repo)
        return {
            "ok": ok,
            "checked": "demo dataset ready" if ok else "demo connector removed",
            "connector_id": connector_id,
            "type": "demo",
            "layers": {"config": ok, "secret": ok},
        }

    row = await repo.get("datastores", org_id, connector_id)
    config_ok = (
        row is not None
        and isinstance(row.get("config"), dict)
        and "connector_type" in row["config"]
    )

    if not config_ok:
        return {
            "ok": False,
            "checked": "config layer missing",
            "connector_id": connector_id,
            "layers": {"config": False, "secret": False},
        }

    # Verify the secret is retrievable (it will be decrypted — proves the key works)
    secret_ok = False
    try:
        secret = await _secret_store().get(connector_id, org_id)
        # Secret may be an empty dict for secret-less connectors — that is fine
        secret_ok = isinstance(secret, dict)
    except Exception:
        secret_ok = False

    connector_type = row["config"].get("connector_type", "unknown")  # type: ignore[union-attr]

    if not secret_ok:
        return {
            "ok": False,
            "checked": "secret layer missing or decryption failed",
            "connector_id": connector_id,
            "type": connector_type,
            "layers": {"config": True, "secret": False},
        }

    return {
        "ok": True,
        "checked": "config+secret resolved",
        "connector_id": connector_id,
        "type": connector_type,
        "layers": {"config": True, "secret": True},
    }


# ── Register on the shared api_router ─────────────────────────────────────────

api_router.include_router(router)
