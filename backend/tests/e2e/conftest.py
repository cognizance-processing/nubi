"""E2E test conftest — boots a REAL uvicorn against a REAL Postgres + DuckDB.

Gate
----
Set RUN_E2E=1 (and run `npm run db:reset:demo` first) before running this suite.
Without RUN_E2E the whole package is skipped with a clear message.

Fixture
-------
``e2e_ctx`` (session-scoped) does:
1. Skip unless RUN_E2E=1.
2. Verify the demo database is seeded (finds superuser + org + datastore + metrics).
3. Boot uvicorn on a free port as a subprocess.
4. Wait for /openapi.json readiness (retry loop, 30 s timeout).
5. Mint tokens (superuser/author, read-only, embed).
6. Yield an ``E2EContext`` dataclass with: client, base_url, tokens, org_id,
   datastore_id, metric IDs, superuser_id.
7. Tear down uvicorn in finally.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

# ---------------------------------------------------------------------------
# Gate: skip the whole package unless RUN_E2E=1
# ---------------------------------------------------------------------------

_RUN_E2E = os.getenv("RUN_E2E", "").strip() in ("1", "true", "yes", "on")


def _real_database_url() -> str:
    """Return the real DATABASE_URL for E2E tests.

    The outer tests/conftest.py sets DATABASE_URL=postgresql://fake:fake@localhost/fake
    for unit tests. We must override that with the real URL when running E2E.
    Priority:
      1. E2E_DATABASE_URL env var (explicit override)
      2. Root .env file DATABASE_URL
      3. Hardcoded default (nubi/nubi@localhost:5432/nubi)
    """
    # 1. Explicit E2E override
    if os.getenv("E2E_DATABASE_URL"):
        return os.environ["E2E_DATABASE_URL"]

    # 2. Read from root .env file (bypasses the fake set by tests/conftest.py)
    root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
    )
    env_path = os.path.join(root, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.partition("=")[2].strip()

    # 3. Check if the current DATABASE_URL looks real (not the fake test one)
    current = os.getenv("DATABASE_URL", "")
    if current and "fake" not in current:
        return current

    # 4. Default local dev URL
    return "postgresql://nubi:nubi@localhost:5432/nubi?sslmode=disable"


_DATABASE_URL = _real_database_url()


def pytest_collection_modifyitems(config, items):
    """Skip ALL e2e tests when RUN_E2E is not set."""
    if _RUN_E2E:
        return
    skip_marker = pytest.mark.skip(
        reason=(
            "E2E suite skipped — set RUN_E2E=1 and run `npm run db:reset:demo` first.\n"
            "  export RUN_E2E=1\n"
            "  npm run db:reset:demo   # one-time demo seed\n"
            "  cd backend && python -m pytest tests/e2e -q"
        )
    )
    for item in items:
        if item.fspath.dirname.endswith("/e2e") or "/e2e/" in str(item.fspath):
            item.add_marker(skip_marker)


# ---------------------------------------------------------------------------
# Arrow IPC reader helper
# ---------------------------------------------------------------------------


def read_arrow_bytes(content: bytes) -> list[dict[str, Any]]:
    """Decode Arrow IPC stream bytes → list of row dicts."""
    import pyarrow.ipc as pa_ipc
    from io import BytesIO

    reader = pa_ipc.open_stream(BytesIO(content))
    table = reader.read_all()
    return table.to_pylist()


# ---------------------------------------------------------------------------
# Token helpers (need the real JWT_SECRET from the running environment)
# ---------------------------------------------------------------------------


def _mint(user_id: str, extra: dict | None = None) -> str:
    """Mint an HS256 access token using the app's JWT machinery."""
    import importlib

    # Ensure settings are loaded from the real env, not test overrides.
    # We import lazily so we don't pollute unit-test env at collection time.
    jwt_mod = importlib.import_module("app.auth.jwt")
    return jwt_mod.mint_access_token(user_id, extra_claims=extra)


# ---------------------------------------------------------------------------
# Context dataclass yielded to tests
# ---------------------------------------------------------------------------


@dataclass
class E2EContext:
    client: httpx.Client
    base_url: str
    superuser_id: str
    org_id: str
    datastore_id: str
    # Token strings
    su_token: str          # superuser, default scopes (includes author:sql)
    read_token: str        # read:* scope only (no author:sql)
    embed_token: str       # embed token (read:query scope, kind=embed via embed claim stub)
    # Metric ids (slugs)
    metric_ids: dict[str, str] = field(default_factory=dict)
    # Registered query ids
    query_ids: dict[str, str] = field(default_factory=dict)

    def auth_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def su_headers(self) -> dict[str, str]:
        return self.auth_headers(self.su_token)

    def read_headers(self) -> dict[str, str]:
        return self.auth_headers(self.read_token)

    def embed_headers(self) -> dict[str, str]:
        return self.auth_headers(self.embed_token)


# ---------------------------------------------------------------------------
# Free-port helper
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Discover demo seed from Postgres
# ---------------------------------------------------------------------------


def _build_local_view_sql() -> str:
    """Build a DuckDB view_sql pointing at the local parquet files in seed_data/parquet/.

    The demo datastore config stores s3:// URIs which require MinIO.  For E2E
    tests running without MinIO we patch the config to use local parquet paths
    instead.  This mirrors what `local_parquet_datastore_config()` in demo_bundle.py
    does, but using the seed_data/parquet/ files that already exist in the repo.
    """
    backend_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    parquet_dir = os.path.join(backend_dir, "seed_data", "parquet")

    # Table → dataset mapping (mirrors seed_data/generators.py DATASET_TABLES)
    dataset_tables: dict[str, list[str]] = {
        "retail_sales": ["sales", "budget", "targets", "dim_regions", "dim_products", "dim_customers"],
        "saas_metrics": ["saas_plans", "saas_accounts", "saas_invoices", "saas_subscriptions", "saas_subscription_events"],
        "web_analytics": ["web_sessions", "web_pageviews"],
        "finance_ops": ["fin_expenses", "fin_invoices", "fin_payments", "fin_headcount"],
    }

    stmts: list[str] = []
    for dataset, tables in dataset_tables.items():
        for table in tables:
            path = os.path.join(parquet_dir, dataset, f"{table}.parquet")
            if os.path.exists(path):
                stmts.append(
                    f"CREATE OR REPLACE VIEW {table} AS SELECT * FROM read_parquet('{path}')"
                )
    return ";\n".join(stmts)


def _patch_datastore_to_local_parquet(dsn: str, datastore_id: str) -> None:
    """Update the demo datastore in Postgres to use local parquet paths.

    The seeded demo datastore uses s3:// URIs (MinIO).  E2E tests run without
    MinIO, so we patch the config to use the local seed_data/parquet/ files.
    This is a test-only mutation; run npm run db:reset:demo to restore.
    """
    import asyncpg
    import asyncio
    import json

    view_sql = _build_local_view_sql()
    if not view_sql:
        return  # no parquet files found — skip patch

    new_config = {
        "connector_type": "duckdb",
        "database": ":memory:",
        "view_sql": view_sql,
        "sample": True,
        "editable_parquet": False,
        "description": "Demo Lakehouse (E2E: local parquet backend)",
    }

    async def _patch():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "UPDATE datastores SET config = $1::jsonb WHERE id = $2::uuid",
                json.dumps(new_config),
                datastore_id,
            )
        finally:
            await conn.close()

    asyncio.run(_patch())


def _discover_demo_data() -> dict[str, Any]:
    """Connect to Postgres and discover the demo seed IDs.

    Returns a dict with: user_id, org_id, datastore_id, metric_ids, query_ids.
    Also patches the demo datastore config to use local parquet paths (no MinIO needed).
    Raises RuntimeError with seeding instructions if data is missing.
    """
    try:
        import asyncpg
        import asyncio

        async def _fetch() -> dict[str, Any]:
            dsn = _DATABASE_URL
            conn = await asyncpg.connect(dsn)
            try:
                # Superuser
                user_row = await conn.fetchrow(
                    "SELECT id FROM users WHERE email = 'admin@nubi.dev'"
                )
                if user_row is None:
                    raise RuntimeError(
                        "Demo superuser 'admin@nubi.dev' not found.\n"
                        "Run: npm run db:reset:demo"
                    )
                user_id = str(user_row["id"])

                # Org
                org_row = await conn.fetchrow(
                    "SELECT o.id FROM orgs o "
                    "JOIN org_members m ON m.org_id = o.id "
                    "WHERE m.user_id = $1::uuid LIMIT 1",
                    user_id,
                )
                if org_row is None:
                    raise RuntimeError(
                        "No org found for admin@nubi.dev.\nRun: npm run db:reset:demo"
                    )
                org_id = str(org_row["id"])

                # Datastore
                ds_row = await conn.fetchrow(
                    "SELECT id FROM datastores WHERE org_id = $1::uuid LIMIT 1",
                    org_id,
                )
                if ds_row is None:
                    raise RuntimeError(
                        "No datastore found. Run: npm run db:reset:demo"
                    )
                datastore_id = str(ds_row["id"])

                # Query-backed metrics
                metric_rows = await conn.fetch(
                    "SELECT id, config->'metric'->>'slug' AS slug "
                    "FROM queries WHERE config ? 'metric' AND org_id = $1::uuid",
                    org_id,
                )
                metric_ids = {row["slug"]: str(row["id"]) for row in metric_rows if row["slug"]}
                if "retail_nsv" not in metric_ids:
                    raise RuntimeError(
                        "Demo metric 'retail_nsv' not found.\nRun: npm run db:reset:demo"
                    )

                # Some registered queries (non-metric)
                q_rows = await conn.fetch(
                    "SELECT id, name FROM queries WHERE org_id = $1::uuid "
                    "AND NOT config ? 'metric' LIMIT 5",
                    org_id,
                )
                query_ids = {row["name"]: str(row["id"]) for row in q_rows}

                return {
                    "user_id": user_id,
                    "org_id": org_id,
                    "datastore_id": datastore_id,
                    "metric_ids": metric_ids,
                    "query_ids": query_ids,
                }
            finally:
                await conn.close()

        result = asyncio.run(_fetch())

        # Patch the datastore to use local parquet files (avoids MinIO dependency)
        _patch_datastore_to_local_parquet(_DATABASE_URL, result["datastore_id"])

        return result

    except ImportError:
        raise RuntimeError(
            "asyncpg not installed. Run: pip install asyncpg"
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to discover demo data from DB ({_DATABASE_URL}):\n{exc}\n\n"
            "Ensure Postgres is running and run: npm run db:reset:demo"
        ) from exc


# ---------------------------------------------------------------------------
# Boot uvicorn
# ---------------------------------------------------------------------------


def _load_dot_env_into(env: dict[str, str]) -> None:
    """Load the root .env file into *env* dict (without overriding existing keys).

    conftest.py lives at backend/tests/e2e/conftest.py; the project root .env
    is three directories up at nubi/.env.
    """
    root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..")
    )
    env_path = os.path.join(root, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if key and key not in env:
                env[key] = val


def _boot_uvicorn(port: int) -> subprocess.Popen:
    """Start uvicorn on *port* as a subprocess; return the process handle."""
    env = dict(os.environ)
    # Load .env first (so real credentials are available)
    _load_dot_env_into(env)
    # Force the real DATABASE_URL (not the fake test one)
    env["DATABASE_URL"] = _DATABASE_URL
    # Disable the rate limiter so E2E calls don't exhaust the per-IP bucket.
    env["NUBI_RATELIMIT_ENABLED"] = "false"
    # Disable background workers so they don't interfere.
    env["FLOWS_SCHEDULER_ENABLED"] = "false"
    env["JOBS_SCHEDULER_ENABLED"] = "false"

    # Add backend/ to PYTHONPATH so `import main` resolves.
    backend_dir = os.path.join(
        os.path.dirname(__file__), "..", ".."
    )
    backend_dir = os.path.abspath(backend_dir)
    existing_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{backend_dir}:{existing_path}" if existing_path else backend_dir

    cmd = [
        sys.executable, "-m", "uvicorn",
        "main:app",
        "--host", "127.0.0.1",
        "--port", str(port),
        "--no-access-log",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=backend_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc


def _wait_for_ready(base_url: str, timeout: float = 40.0, interval: float = 0.5) -> None:
    """Poll /openapi.json until the server responds 200 or *timeout* elapses."""
    deadline = time.time() + timeout
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{base_url}/openapi.json", timeout=2.0)
            if resp.status_code == 200:
                return
        except Exception as exc:
            last_exc = exc
        time.sleep(interval)
    raise RuntimeError(
        f"Uvicorn never became ready at {base_url} within {timeout}s. "
        f"Last error: {last_exc}"
    )


# ---------------------------------------------------------------------------
# Session-scoped fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def e2e_ctx() -> "E2EContext":  # type: ignore[return]
    """Boot the stack and yield a context; skip if RUN_E2E not set."""
    if not _RUN_E2E:
        pytest.skip(
            "E2E suite skipped — set RUN_E2E=1 and run `npm run db:reset:demo` first."
        )

    # 1. Discover demo seed data
    demo = _discover_demo_data()
    user_id = demo["user_id"]
    org_id = demo["org_id"]
    datastore_id = demo["datastore_id"]
    metric_ids = demo["metric_ids"]
    query_ids = demo["query_ids"]

    # We need the JWT_SECRET to be set in the environment for token minting.
    # The real .env file sets it; if not already in env, load it.
    if not os.getenv("JWT_SECRET"):
        _load_env_file()

    # 2. Boot uvicorn
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}/api/v1"
    proc = _boot_uvicorn(port)

    try:
        _wait_for_ready(f"http://127.0.0.1:{port}", timeout=45.0)

        # 3. Mint tokens
        su_token = _mint(user_id)
        read_token = _mint(user_id, extra={"scope": "read:*"})
        # Embed token: scope + embed kind marker in claims
        # The embed auth path verifies kind='embed' from the token issuer (RS256).
        # For E2E we use a first-party HS256 token with scope=read:query — this
        # exercises the embed-like scope restriction (no author:sql).
        embed_token = _mint(user_id, extra={"scope": "read:query"})

        client = httpx.Client(
            base_url=base_url,
            timeout=30.0,
            headers={"Accept": "application/json"},
        )

        ctx = E2EContext(
            client=client,
            base_url=base_url,
            superuser_id=user_id,
            org_id=org_id,
            datastore_id=datastore_id,
            su_token=su_token,
            read_token=read_token,
            embed_token=embed_token,
            metric_ids=metric_ids,
            query_ids=query_ids,
        )
        yield ctx
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _load_env_file() -> None:
    """Best-effort: load JWT_SECRET etc. from the root .env file."""
    root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
    )
    env_path = os.path.join(root, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if key and key not in os.environ:
                os.environ[key] = val
