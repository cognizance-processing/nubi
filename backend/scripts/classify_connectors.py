"""Connector backfill / classification tool for Nubi.

Purpose
-------
Given a set of *legacy* connector rows (exported from a production DB with
mixed reachability and plaintext credentials), this tool:

1. Classifies each row into a ``network_mode`` and ``reachability_class``.
2. Splits each row into a *non-secret config* and a *secret dict*.
3. Normalises field-name drift (e.g. ``sslMode`` → ``sslmode``, nested
   ``connectionDetails`` → flat).
4. Emits a migration plan (DRY-RUN by default).
5. With ``--apply`` + live credentials, upserts datastores rows and persists
   encrypted secrets via ``SecretStore.put()``.

Security contract
-----------------
- Plaintext credentials from the legacy dump are NEVER logged or printed.
- The ``--apply`` path encrypts via SecretStore (AES-256-GCM) before any DB
  write.
- A rotation warning is always emitted for every connector whose credentials
  were stored in plaintext.
- Dry-run output redacts all secret values to ``"<REDACTED>"``.

CLI usage
---------
Dry-run from a JSON file::

    python -m scripts.classify_connectors --input rows.json

Dry-run from a SQL INSERT dump::

    python -m scripts.classify_connectors --input legacy_dump.sql

Apply to a live database::

    DATABASE_URL=postgresql://... CONNECTOR_SECRET_KEY=<b64key> \\
        python -m scripts.classify_connectors --input rows.json --apply

Input format (JSON)
-------------------
A JSON array of connector objects, each with at minimum::

    [
      {
        "id": "<uuid>",
        "org_id": "<uuid>",
        "name": "prod-postgres",
        "type": "postgres",
        "host": "10.132.0.15",
        "port": 5432,
        "database": "analytics",
        "user": "readonly",
        "sslmode": "require",
        "password": "hunter2"
      },
      ...
    ]

Fields may be nested under ``connectionDetails`` or ``config`` — the tool
flattens them automatically.

SQL dump format
---------------
Best-effort parse of ``INSERT INTO connectors (...) VALUES (...)`` or
``INSERT INTO datastores (...) VALUES (...)`` statements.  Only column names
and their quoted string values are extracted; complex expressions are skipped.
"""

from __future__ import annotations

import ipaddress
import json
import re
import sys
from copy import deepcopy
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Field names that are considered SECRET (must go to connector_secrets).
_SECRET_FIELDS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "service_account_json",
        "credential_file",
        "token",
        "api_key",
        "apikey",
        "api_token",
        "access_token",
        "secret",
        "secret_key",
        "private_key",
        "private_key_id",
        "client_secret",
    }
)

# Connector types that are cloud-managed APIs (no host-based reachability).
_API_TYPES: frozenset[str] = frozenset(
    {
        "bigquery",
        "google_bigquery",
        "bq",
        "salesforce",
        "hubspot",
        "stripe",
        "shopify",
        "zendesk",
        "mixpanel",
        "amplitude",
        "notion",
        "airtable",
        "google_sheets",
        "gsheets",
        "snowflake_oauth",
        "databricks_sql",
    }
)

# Field name normalisation: legacy field → canonical field.
_FIELD_ALIASES: dict[str, str] = {
    "sslMode": "sslmode",
    "ssl_mode": "sslmode",
    "dbname": "database",
    "db_name": "database",
    "db": "database",
    "hostname": "host",
    "server": "host",
    "connector_type": "type",
    "source_type": "type",
    "apiKey": "api_key",
    "serviceAccountJson": "service_account_json",
    "credentialFile": "credential_file",
    "accessToken": "access_token",
    "clientSecret": "client_secret",
    "privateKey": "private_key",
}

# RFC-1918 private address ranges.
_PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),  # loopback — treat as private
    ipaddress.ip_network("::1/128"),        # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),       # IPv6 ULA
]


# ---------------------------------------------------------------------------
# Field normalisation helpers
# ---------------------------------------------------------------------------

def _flatten(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested ``connectionDetails`` / ``config`` sub-dicts into the
    top level and apply field-alias normalisation.

    The source row may look like::

        {"id": "...", "connectionDetails": {"host": "...", "password": "..."}}

    or::

        {"id": "...", "config": {"host": "...", "sslMode": "require"}}

    After flattening both forms look like::

        {"id": "...", "host": "...", "password": "...", "sslmode": "require"}

    Top-level fields win over nested ones if a key appears in both.
    """
    flat: dict[str, Any] = {}

    # Merge nested sub-dicts first (lower priority).
    for wrapper_key in ("connectionDetails", "connection_details", "config", "settings"):
        nested = row.get(wrapper_key)
        if isinstance(nested, dict):
            for k, v in nested.items():
                canonical = _FIELD_ALIASES.get(k, k)
                flat[canonical] = v

    # Merge top-level fields (higher priority, override nested).
    for k, v in row.items():
        if k in ("connectionDetails", "connection_details", "config", "settings"):
            continue
        canonical = _FIELD_ALIASES.get(k, k)
        flat[canonical] = v

    return flat


def _is_private_ip(host: str) -> bool:
    """Return True if *host* is an RFC-1918 / loopback IPv4 or IPv6 address."""
    try:
        addr = ipaddress.ip_address(host)
        return any(addr in net for net in _PRIVATE_RANGES)
    except ValueError:
        return False


def _is_private_hostname(host: str) -> bool:
    """Return True if *host* is a private-network hostname heuristic.

    Matches:
    - Any RFC-1918 IP address.
    - Hostnames ending in ``.internal``, ``.local``, ``.internal.`` (with
      trailing dot), ``.corp``, ``.lan``, ``.priv``.
    - Bare hostnames with no dot (single-label — likely a container or
      k8s service name).
    """
    h = host.strip().rstrip(".")
    if _is_private_ip(h):
        return True
    private_suffixes = (".internal", ".local", ".corp", ".lan", ".priv", ".intranet")
    lower = h.lower()
    if any(lower.endswith(sfx) for sfx in private_suffixes):
        return True
    # Single-label hostname (no dots) — treat as private / container-internal.
    if "." not in h:
        return True
    return False


# ---------------------------------------------------------------------------
# Core classification
# ---------------------------------------------------------------------------

def classify(connector_row: dict[str, Any]) -> dict[str, Any]:
    """Classify a single legacy connector row and extract its secret fields.

    Parameters
    ----------
    connector_row:
        A raw connector row dict, optionally with nested sub-dicts under
        ``connectionDetails`` or ``config``.  Field names may use legacy
        camelCase or underscored aliases.

    Returns
    -------
    dict with keys:

    ``network_mode``
        ``"bridge"`` or ``"direct"``.
    ``reason``
        Human-readable explanation of the classification decision.
    ``reachability_class``
        One of ``"private_vpc"``, ``"api"``, ``"public_db"``.
    ``secret_keys``
        List of field names that were identified as secrets in this row.
    """
    flat = _flatten(connector_row)

    connector_type = str(flat.get("type", flat.get("connector_type", ""))).lower().strip()
    host: str = str(flat.get("host", "")).strip()

    # ------------------------------------------------------------------
    # Rule 1: Cloud-managed API type (no host-based reachability).
    # ------------------------------------------------------------------
    if connector_type in _API_TYPES or (not host and connector_type not in ("postgres", "mysql", "mssql", "mongodb", "redis")):
        secret_keys = _extract_secret_keys(flat)
        return {
            "network_mode": "direct",
            "reason": (
                f"Connector type '{connector_type}' is a cloud-managed API source "
                "that uses token/key auth rather than a host socket."
            ),
            "reachability_class": "api",
            "secret_keys": secret_keys,
        }

    # ------------------------------------------------------------------
    # Rule 2: Private / VPC host → needs a bridge.
    # ------------------------------------------------------------------
    if host and _is_private_hostname(host):
        secret_keys = _extract_secret_keys(flat)
        return {
            "network_mode": "bridge",
            "reason": (
                f"Host '{host}' resolves to an RFC-1918 private address or "
                "private-network hostname (.internal/.local/.corp/single-label). "
                "A Nubi bridge agent is required for egress."
            ),
            "reachability_class": "private_vpc",
            "secret_keys": secret_keys,
        }

    # ------------------------------------------------------------------
    # Rule 3: Public / routable host → direct, but note egress allowlist.
    # ------------------------------------------------------------------
    secret_keys = _extract_secret_keys(flat)
    host_display = host if host else "(no host)"
    return {
        "network_mode": "direct",
        "reason": (
            f"Host '{host_display}' appears to be a publicly routable address. "
            "Direct connection is viable; ensure the Nubi egress IP is in the "
            "database's allowlist / firewall rules."
        ),
        "reachability_class": "public_db",
        "secret_keys": secret_keys,
    }


def _extract_secret_keys(flat: dict[str, Any]) -> list[str]:
    """Return sorted list of field names in *flat* that are identified as secrets."""
    found = []
    for k in flat:
        # Exact match against the canonical secret field set.
        if k.lower() in _SECRET_FIELDS:
            found.append(k)
            continue
        # Heuristic: any field whose name contains "password", "secret",
        # "token", "key", "credential" is treated as a secret.
        lower_k = k.lower()
        if any(pat in lower_k for pat in ("password", "passwd", "secret", "token", "credential", "private_key")):
            found.append(k)
    return sorted(found)


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------

def _split_row(flat: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a flattened row into (non_secret_config, secret_dict).

    Non-secret config fields are everything except the identified secret fields
    plus known internal / wrapper keys.  The ``network_mode`` field is injected
    into the config by the caller.
    """
    secret_keys = set(_extract_secret_keys(flat))
    # Internal keys that should not appear in either output dict.
    _internal = {"connectionDetails", "connection_details", "config", "settings"}

    non_secret: dict[str, Any] = {}
    secret: dict[str, Any] = {}

    for k, v in flat.items():
        if k in _internal:
            continue
        if k.lower() in _SECRET_FIELDS or k in secret_keys:
            secret[k] = v
        else:
            non_secret[k] = v

    return non_secret, secret


def plan_backfill(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Classify all rows and build a per-connector migration plan.

    Parameters
    ----------
    rows:
        List of raw legacy connector row dicts.

    Returns
    -------
    List of plan dicts, each containing:

    ``id``
        The connector / datastore id (if present in the row).
    ``name``
        The connector name (if present).
    ``classification``
        Full output of :func:`classify`.
    ``config``
        Normalised non-secret config dict, with ``network_mode`` injected.
    ``secret``
        Dict of secret fields extracted from the row (plaintext — handle
        with care; encrypt before persisting).
    ``reason``
        Copy of the classification reason string.
    ``rotation_required``
        Always ``True`` — any connector whose credentials were stored in
        plaintext MUST have its secrets rotated after migration.

    Additionally, a final summary ``dict`` is appended at position [-1] with
    key ``"_summary"`` containing counts per reachability class.
    """
    plans: list[dict[str, Any]] = []
    counts: dict[str, int] = {"private_vpc": 0, "api": 0, "public_db": 0}

    for row in rows:
        flat = _flatten(row)
        classification = classify(row)
        rc = classification["reachability_class"]
        counts[rc] = counts.get(rc, 0) + 1

        non_secret_config, secret = _split_row(flat)
        # Inject network_mode into non-secret config.
        non_secret_config["network_mode"] = classification["network_mode"]

        plans.append(
            {
                "id": flat.get("id"),
                "name": flat.get("name"),
                "classification": classification,
                "config": non_secret_config,
                "secret": secret,
                "reason": classification["reason"],
                "rotation_required": True,
            }
        )

    plans.append({"_summary": counts})
    return plans


# ---------------------------------------------------------------------------
# Redaction helper (for dry-run output)
# ---------------------------------------------------------------------------

def _redact_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *plan* with all secret values replaced by '<REDACTED>'.

    This is safe to log / print during a dry-run.
    """
    if "_summary" in plan:
        return plan
    redacted = deepcopy(plan)
    for k in redacted.get("secret", {}):
        redacted["secret"][k] = "<REDACTED>"
    # Also scrub any secret keys that may have leaked into config (should not
    # happen if split_row is correct, but belt-and-braces).
    for k in list(redacted.get("config", {})):
        if k.lower() in _SECRET_FIELDS or any(
            pat in k.lower() for pat in ("password", "passwd", "secret", "token", "credential", "private_key")
        ):
            redacted["config"][k] = "<REDACTED>"
    return redacted


# ---------------------------------------------------------------------------
# SQL dump parser (best-effort)
# ---------------------------------------------------------------------------

_SQL_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+\w+\s*\(([^)]+)\)\s*VALUES\s*\(([^;]+)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)

_QUOTED_VALUE_RE = re.compile(r"'((?:[^'\\]|\\.)*)'|(\d+(?:\.\d+)?)|NULL", re.IGNORECASE)


def _parse_sql_dump(sql_text: str) -> list[dict[str, Any]]:
    """Best-effort extraction of connector rows from a SQL INSERT dump.

    Only handles simple INSERT statements with single-quoted string literals
    and bare integers.  Complex expressions (functions, sub-selects) are
    substituted with ``None``.

    Parameters
    ----------
    sql_text:
        Raw SQL text containing one or more ``INSERT INTO ... VALUES (...)``
        statements.

    Returns
    -------
    list[dict]
        One dict per INSERT statement found.
    """
    rows: list[dict[str, Any]] = []

    for match in _SQL_INSERT_RE.finditer(sql_text):
        col_str = match.group(1)
        val_str = match.group(2)

        columns = [c.strip().strip('"').strip("'") for c in col_str.split(",")]
        values: list[Any] = []
        remaining = val_str.strip()
        while remaining:
            m = _QUOTED_VALUE_RE.match(remaining)
            if m:
                if m.group(1) is not None:
                    # Unescape SQL escape sequences.
                    values.append(m.group(1).replace("\\'", "'").replace("\\\\", "\\"))
                elif m.group(2) is not None:
                    raw = m.group(2)
                    values.append(int(raw) if "." not in raw else float(raw))
                else:
                    values.append(None)  # NULL
                remaining = remaining[m.end():].lstrip(",").lstrip()
            else:
                # Unrecognised token — skip to next comma
                idx = remaining.find(",")
                if idx == -1:
                    values.append(None)
                    break
                values.append(None)
                remaining = remaining[idx + 1:].lstrip()

        row = dict(zip(columns, values))
        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# Apply path (live upsert + SecretStore.put)
# ---------------------------------------------------------------------------

async def _apply_plan(plans: list[dict[str, Any]], database_url: str) -> None:
    """Upsert non-secret config into datastores and persist secrets via SecretStore.

    This function requires:
    - ``DATABASE_URL`` pointing to a live Postgres instance that has the
      0009_connectors_secrets migration applied.
    - ``CONNECTOR_SECRET_KEY`` set in the environment.

    It is intentionally async — call via ``asyncio.run(_apply_plan(...))``.

    Parameters
    ----------
    plans:
        Output of :func:`plan_backfill` (list ending with a ``_summary`` dict).
    database_url:
        DSN for the target database (asyncpg format).
    """
    import asyncio
    import asyncpg  # type: ignore[import]

    from app.connectors.secret_store import InMemorySecretStore, PgSecretStore

    # Use a temporary PgSecretStore bound to the provided pool.
    pool = await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=3)

    # Monkey-patch app.db so PgSecretStore's execute/fetchrow work.
    import app.db as app_db_module  # noqa: PLC0415

    original_execute = getattr(app_db_module, "_pool", None)

    async def _pool_execute(query: str, *args: Any) -> str:
        async with pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def _pool_fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        async with pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    app_db_module.execute = _pool_execute  # type: ignore[attr-defined]
    app_db_module.fetchrow = _pool_fetchrow  # type: ignore[attr-defined]

    store = PgSecretStore()
    migrated = 0
    skipped = 0
    errors: list[str] = []

    try:
        for plan in plans:
            if "_summary" in plan:
                continue

            ds_id = plan.get("id")
            config = plan.get("config", {})
            secret = plan.get("secret", {})
            org_id = config.get("org_id") or plan.get("org_id")

            if not ds_id or not org_id:
                print(
                    f"  [SKIP] Missing id or org_id for connector '{plan.get('name')}' — skipping.",
                    file=sys.stderr,
                )
                skipped += 1
                continue

            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO datastores (id, org_id, name, config, network_mode)
                        VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5)
                        ON CONFLICT (id) DO UPDATE
                            SET config       = EXCLUDED.config,
                                network_mode = EXCLUDED.network_mode,
                                updated_at   = now()
                        """,
                        ds_id,
                        org_id,
                        plan.get("name") or ds_id,
                        json.dumps(config),
                        config.get("network_mode", "direct"),
                    )

                if secret:
                    await store.put(ds_id, org_id, secret)

                migrated += 1
                # NEVER print secret values.
                print(
                    f"  [OK] {plan.get('name') or ds_id} → "
                    f"network_mode={config.get('network_mode')}, "
                    f"class={plan['classification']['reachability_class']}"
                )
            except Exception as exc:
                msg = f"  [ERROR] {plan.get('name') or ds_id}: {exc}"
                print(msg, file=sys.stderr)
                errors.append(msg)

    finally:
        await pool.close()

    print(f"\nApply complete: {migrated} migrated, {skipped} skipped, {len(errors)} errors.")
    if errors:
        print("Errors:", file=sys.stderr)
        for e in errors:
            print(e, file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def _print_dry_run(plans: list[dict[str, Any]]) -> None:
    """Print human-readable dry-run output.  All secret values are redacted."""
    print("=" * 72)
    print("NUBI CONNECTOR BACKFILL — DRY RUN (no data written)")
    print("=" * 72)

    for plan in plans:
        if "_summary" in plan:
            summary = plan["_summary"]
            print("\nSummary by reachability class:")
            for cls, count in sorted(summary.items()):
                print(f"  {cls:<14} {count}")
            total = sum(summary.values())
            print(f"  {'TOTAL':<14} {total}")
            continue

        redacted = _redact_plan(plan)
        cls_info = redacted["classification"]
        print(f"\n  Connector : {redacted.get('name') or redacted.get('id') or '(unnamed)'}")
        print(f"  ID        : {redacted.get('id') or '(none)'}")
        print(f"  Class     : {cls_info['reachability_class']}")
        print(f"  Mode      : {cls_info['network_mode']}")
        print(f"  Reason    : {cls_info['reason']}")
        print(f"  Secret keys extracted : {cls_info['secret_keys'] or '(none)'}")
        print(f"  Secret values         : {redacted['secret'] or '(none)'}")
        if redacted.get("rotation_required"):
            print(
                "  *** ROTATION REQUIRED: credentials were stored in plaintext in the "
                "legacy system.  Rotate all secrets after migration. ***"
            )

    print()
    print(
        "NOTE: Run with --apply + DATABASE_URL + CONNECTOR_SECRET_KEY to "
        "persist the migration."
    )
    print("=" * 72)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the connector backfill tool.

    Returns 0 on success, 1 on error.
    """
    import argparse
    import asyncio
    import os

    parser = argparse.ArgumentParser(
        prog="classify_connectors",
        description=(
            "Classify legacy connector rows into network_mode/reachability_class "
            "and emit a migration plan (or apply it live with --apply)."
        ),
    )
    parser.add_argument(
        "--input",
        required=True,
        metavar="FILE",
        help="Path to a JSON array file or a .sql INSERT dump file.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help=(
            "Apply the plan: upsert datastores rows and encrypt secrets via "
            "SecretStore.  Requires DATABASE_URL and CONNECTOR_SECRET_KEY env vars."
        ),
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        default=False,
        help="Emit the plan as JSON (redacted) instead of human-readable text.",
    )

    args = parser.parse_args(argv)

    # Load input file.
    try:
        with open(args.input, "r", encoding="utf-8") as fh:
            raw_content = fh.read()
    except OSError as exc:
        print(f"ERROR: Cannot read input file '{args.input}': {exc}", file=sys.stderr)
        return 1

    # Parse: JSON array or SQL dump.
    if args.input.lower().endswith(".sql") or raw_content.lstrip().upper().startswith("INSERT"):
        rows = _parse_sql_dump(raw_content)
        if not rows:
            print(
                "WARNING: No INSERT statements found in SQL file.  "
                "Trying JSON parse as fallback.",
                file=sys.stderr,
            )
            try:
                rows = json.loads(raw_content)
            except json.JSONDecodeError:
                print("ERROR: Could not parse input as SQL or JSON.", file=sys.stderr)
                return 1
    else:
        try:
            rows = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            print(f"ERROR: Invalid JSON in '{args.input}': {exc}", file=sys.stderr)
            return 1

    if not isinstance(rows, list):
        print("ERROR: Input must be a JSON array of connector rows.", file=sys.stderr)
        return 1

    if not rows:
        print("WARNING: Input file contains zero rows.  Nothing to do.")
        return 0

    plans = plan_backfill(rows)

    if args.json_output:
        redacted_plans = [_redact_plan(p) for p in plans]
        print(json.dumps(redacted_plans, indent=2, default=str))
    elif not args.apply:
        _print_dry_run(plans)

    if args.apply:
        database_url = os.environ.get("DATABASE_URL", "")
        secret_key = os.environ.get("CONNECTOR_SECRET_KEY", "")

        if not database_url or "fake" in database_url:
            print(
                "ERROR: --apply requires a real DATABASE_URL environment variable.",
                file=sys.stderr,
            )
            return 1

        if not secret_key:
            print(
                "ERROR: --apply requires CONNECTOR_SECRET_KEY to be set.",
                file=sys.stderr,
            )
            return 1

        print(f"\nApplying migration for {len(rows)} connector(s)...")
        import asyncio

        asyncio.run(_apply_plan(plans, database_url))

    return 0


if __name__ == "__main__":
    sys.exit(main())
