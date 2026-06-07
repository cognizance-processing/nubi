"""Test configuration and fixtures for the Nubi backend test suite.

Strategy
--------
There is no live Neon DB in CI.  We patch ``app.db.fetchrow``, ``app.db.fetch``,
``app.db.execute``, and ``app.db.get_connection`` with in-memory fakes backed by
plain Python dicts.  ``rotate_refresh`` uses ``get_connection()`` to acquire a raw
connection (for ``SELECT … FOR UPDATE`` locking), so we provide a fake connection
object with ``fetchrow`` / ``execute`` / ``transaction`` methods.

Settings are injected via environment variables before any app module is imported.
``pydantic-settings 2.12`` tries to JSON-decode ``List`` fields; we avoid this by
not setting ``CORS_ORIGINS`` and letting it default to ``[]``.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Environment must be set BEFORE importing ANY app modules.
# ---------------------------------------------------------------------------

# Tests are hermetic: never read the shared root .env file (it would inject
# optional vars and make tests environment-dependent). Point ENV_FILE at a path
# that does not exist so pydantic-settings falls back to process env only.
os.environ.setdefault("ENV_FILE", "/nonexistent/nubi-tests.env")

# When RUN_PG_TESTS=1, honor a real DATABASE_URL provided by the caller (the PG
# integration suite connects to a live Postgres). Otherwise use the in-memory fake.
if not (os.getenv("RUN_PG_TESTS") and os.getenv("DATABASE_URL")):
    os.environ["DATABASE_URL"] = "postgresql://fake:fake@localhost/fake"
os.environ["JWT_SECRET"] = "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef"
os.environ["JWT_ACCESS_TTL_MIN"] = "15"
os.environ["GOOGLE_CLIENT_ID"] = "fake-google-client-id"
os.environ["GOOGLE_CLIENT_SECRET"] = "fake-google-client-secret"
os.environ["GOOGLE_REDIRECT_URI"] = "http://localhost:8000/api/v1/auth/google/callback"
os.environ["FRONTEND_URL"] = "http://localhost:3000"
os.environ["COOKIE_SECURE"] = "false"
os.environ["ENV"] = "test"
os.environ.pop("CORS_ORIGINS", None)


# ---------------------------------------------------------------------------
# In-memory fake database
# ---------------------------------------------------------------------------

class FakeDB:
    """Dict-backed in-memory substitute for asyncpg helpers.

    Each table is a plain dict keyed by primary key (id or composite key).
    The fake dispatches on SQL text using string pattern matching.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Clear all tables."""
        self.users: dict[str, dict[str, Any]] = {}
        self.oauth_accounts: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.orgs: dict[str, dict[str, Any]] = {}
        self.org_members: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Index helpers
    # ------------------------------------------------------------------

    def _user_by_email(self, email: str) -> dict[str, Any] | None:
        for row in self.users.values():
            if str(row["email"]).lower() == email.lower():
                return row
        return None

    def _user_by_id(self, user_id: str) -> dict[str, Any] | None:
        uid = str(user_id).replace("::uuid", "").strip()
        return self.users.get(uid)

    def _session_by_token_hash(self, token_hash: str) -> dict[str, Any] | None:
        for row in self.sessions.values():
            if row["token_hash"] == token_hash:
                return row
        return None

    def _session_by_parent_id(self, parent_id: str) -> dict[str, Any] | None:
        for row in self.sessions.values():
            if str(row.get("parent_id") or "") == parent_id:
                return row
        return None

    def _oauth_by_provider(self, provider: str, provider_account_id: str) -> dict[str, Any] | None:
        for row in self.oauth_accounts.values():
            if row["provider"] == provider and row["provider_account_id"] == provider_account_id:
                return row
        return None

    # ------------------------------------------------------------------
    # SQL text helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _table_from_query(query: str) -> str:
        """Return the table name from a SQL statement (best-effort)."""
        upper = query.upper()
        for kw in ("FROM ", "INTO ", "UPDATE "):
            idx = upper.find(kw)
            if idx != -1:
                rest = query[idx + len(kw):].strip()
                token = rest.split()[0].strip("(,\n\r\t").lower()
                return token
        return ""

    # ------------------------------------------------------------------
    # Core query implementations (shared by top-level and conn methods)
    # ------------------------------------------------------------------

    def _do_fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        table = self._table_from_query(query)
        q = query.upper().strip()

        if table == "users":
            if "WHERE ID = " in q or "WHERE ID =" in q or "WHERE ID=$" in q.replace(" ", ""):
                return self._user_by_id(str(args[0]))
            if "WHERE EMAIL = " in q or "WHERE EMAIL =" in q:
                return self._user_by_email(str(args[0]))

        if table == "sessions":
            if "WHERE TOKEN_HASH" in q:
                return self._session_by_token_hash(str(args[0]))
            if "WHERE PARENT_ID" in q:
                return self._session_by_parent_id(str(args[0]))

        if "SELECT 1" in q:
            return {"ping": 1}

        return None

    def _do_execute(self, query: str, *args: Any) -> str:
        table = self._table_from_query(query)
        q = query.upper().strip()

        # ── INSERT users ───────────────────────────────────────────────────
        if q.startswith("INSERT") and table == "users":
            # Two actual call signatures from the app code:
            #
            #   register path (4 args, email_verified=false hardcoded in SQL):
            #     ($1=id, $2=email, $3=pw_hash, $4=name)
            #
            #   oauth path (5 args, password_hash=NULL hardcoded in SQL):
            #     ($1=id, $2=email, $3=name, $4=avatar_url, $5=email_verified)
            row: dict[str, Any] = {
                "id": str(args[0]),
                "email": str(args[1]).lower(),
                "created_at": datetime.now(tz=timezone.utc),
                "updated_at": datetime.now(tz=timezone.utc),
            }
            if len(args) == 4:
                # register: id, email, pw_hash, name  (+false hardcoded)
                row["password_hash"] = args[2]
                row["name"] = args[3]
                row["avatar_url"] = None
                row["email_verified"] = False
            elif len(args) == 5:
                # oauth: id, email, name, avatar_url, email_verified  (+NULL hardcoded)
                row["password_hash"] = None
                row["name"] = args[2]
                row["avatar_url"] = args[3]
                row["email_verified"] = bool(args[4])
            else:
                # Fallback: best-effort
                row["password_hash"] = args[2] if len(args) > 2 else None
                row["name"] = args[3] if len(args) > 3 else None
                row["avatar_url"] = None
                row["email_verified"] = False
            self.users[row["id"]] = row
            return "INSERT 0 1"

        # ── INSERT sessions ────────────────────────────────────────────────
        if q.startswith("INSERT") and table == "sessions":
            # id, user_id, token_hash, family_id, parent_id, expires_at, user_agent, ip
            row = {
                "id": str(args[0]),
                "user_id": str(args[1]),
                "token_hash": str(args[2]),
                "family_id": str(args[3]),
                "parent_id": str(args[4]) if args[4] is not None else None,
                "expires_at": args[5],
                "revoked_at": None,
                "user_agent": args[6] if len(args) > 6 else None,
                "ip": args[7] if len(args) > 7 else None,
                "created_at": datetime.now(tz=timezone.utc),
            }
            self.sessions[row["id"]] = row
            return "INSERT 0 1"

        # ── INSERT orgs ────────────────────────────────────────────────────
        if q.startswith("INSERT") and table == "orgs":
            row = {
                "id": str(args[0]),
                "name": str(args[1]),
                "slug": str(args[2]),
                "created_at": datetime.now(tz=timezone.utc),
            }
            self.orgs[row["id"]] = row
            return "INSERT 0 1"

        # ── INSERT org_members ─────────────────────────────────────────────
        if q.startswith("INSERT") and table == "org_members":
            key = f"{args[0]}:{args[1]}"
            self.org_members[key] = {
                "org_id": str(args[0]),
                "user_id": str(args[1]),
                "role": str(args[2]) if len(args) > 2 else "owner",
            }
            return "INSERT 0 1"

        # ── INSERT oauth_accounts ──────────────────────────────────────────
        # The SQL has 'google' hardcoded; args = (id, user_id, provider_account_id)
        if q.startswith("INSERT") and table == "oauth_accounts":
            # Determine provider from SQL text
            provider = "google" if "'GOOGLE'" in q else "unknown"
            # args: id, user_id, provider_account_id
            provider_account_id = str(args[2])
            existing = self._oauth_by_provider(provider, provider_account_id)
            if existing:
                existing["user_id"] = str(args[1])
            else:
                row = {
                    "id": str(args[0]),
                    "user_id": str(args[1]),
                    "provider": provider,
                    "provider_account_id": provider_account_id,
                    "created_at": datetime.now(tz=timezone.utc),
                }
                self.oauth_accounts[row["id"]] = row
            return "INSERT 0 1"

        # ── UPDATE sessions SET revoked_at ─────────────────────────────────
        if q.startswith("UPDATE SESSIONS") and "REVOKED_AT" in q:
            if "WHERE ID = " in q or "WHERE ID =" in q or "WHERE ID=$" in q.replace(" ", ""):
                session_id = str(args[0])
                if session_id in self.sessions:
                    self.sessions[session_id]["revoked_at"] = datetime.now(tz=timezone.utc)
                return "UPDATE 1"
            if "WHERE FAMILY_ID" in q:
                family_id = str(args[0])
                count = 0
                for sess in self.sessions.values():
                    if str(sess["family_id"]) == family_id and sess["revoked_at"] is None:
                        sess["revoked_at"] = datetime.now(tz=timezone.utc)
                        count += 1
                return f"UPDATE {count}"

        # ── UPDATE users SET avatar_url ────────────────────────────────────
        if q.startswith("UPDATE USERS") and "AVATAR_URL" in q:
            user_id = str(args[1])
            if user_id in self.users:
                self.users[user_id]["avatar_url"] = args[0]
                self.users[user_id]["updated_at"] = datetime.now(tz=timezone.utc)
            return "UPDATE 1"

        return "OK"

    # ------------------------------------------------------------------
    # Async module-level wrappers (patched onto app.db.*)
    # ------------------------------------------------------------------

    async def fake_fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        return self._do_fetchrow(query, *args)

    async def fake_fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        return []

    async def fake_execute(self, query: str, *args: Any) -> str:
        return self._do_execute(query, *args)

    # ------------------------------------------------------------------
    # Fake connection context manager (for get_connection / transactions)
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def fake_get_connection(self):
        """Yield a fake connection object that mimics asyncpg's Connection API."""
        yield FakeConnection(self)


class FakeTransaction:
    """Mimics asyncpg's Transaction context manager (no-op in tests)."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeConnection:
    """Minimal asyncpg Connection-like object for use inside get_connection()."""

    def __init__(self, db: FakeDB) -> None:
        self._db = db

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        return self._db._do_fetchrow(query, *args)

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        return []

    async def execute(self, query: str, *args: Any) -> str:
        return self._db._do_execute(query, *args)


# Singleton FakeDB instance — reset between tests by the autouse fixture
_fake_db = FakeDB()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_state():
    """Reset all mutable module-level singletons before and after every test.

    Singletons reset
    ----------------
    - FakeDB (in-memory auth store)
    - pydantic-settings cache
    - Content-addressed cache (entries + stats)
    - Connector registry (cleared + re-bootstrapped to postgres/duckdb/http_json)
    - Query registry (cleared + re-seeded to demo defaults)
    - Query log ring buffer (deque cleared)
    - Metering usage log (list cleared)
    - Job store singleton (replaced with fresh InMemoryJobStore)
    - Repo provider singleton (reset to None so next get_repo() lazily creates PgRepo)

    Each reset is wrapped in try/except so a missing hook never breaks the
    fixture — test failures are still propagated normally.
    """
    def _do_reset():
        # ── FakeDB ────────────────────────────────────────────────────────────
        _fake_db.reset()

        # ── pydantic-settings lru_cache ───────────────────────────────────────
        try:
            from app.config import get_settings
            get_settings.cache_clear()
        except Exception:
            pass

        # ── Content-addressed cache ───────────────────────────────────────────
        try:
            from app.connectors.cache import get_cache
            get_cache().clear()
        except Exception:
            pass

        # ── Connector registry ────────────────────────────────────────────────
        try:
            from app.connectors.registry import reset_for_tests as _reset_conn_reg
            _reset_conn_reg()
        except Exception:
            pass

        # ── Query registry ────────────────────────────────────────────────────
        try:
            from app.queries.registry import reset_for_tests as _reset_query_reg
            _reset_query_reg()
        except Exception:
            pass

        # ── Query log ─────────────────────────────────────────────────────────
        try:
            from app.connectors.query_log import reset_for_tests as _reset_query_log
            _reset_query_log()
        except Exception:
            pass

        # ── Metering usage log ────────────────────────────────────────────────
        try:
            from app.compute.metering import clear_usage
            clear_usage()
        except Exception:
            pass

        # ── Job store ─────────────────────────────────────────────────────────
        try:
            from app.jobs.store import set_job_store, InMemoryJobStore
            set_job_store(InMemoryJobStore())
        except Exception:
            pass

        # ── Flow store ────────────────────────────────────────────────────────
        try:
            from app.flows.store import set_flow_store, InMemoryFlowStore
            set_flow_store(InMemoryFlowStore())
        except Exception:
            pass

        # ── Task-kind registry ────────────────────────────────────────────────
        try:
            from app.flows.registry import reset_for_tests as _reset_task_reg
            _reset_task_reg()
        except Exception:
            pass

        # ── Repo provider ─────────────────────────────────────────────────────
        try:
            from app.repos.provider import set_repo
            set_repo(None)
        except Exception:
            pass

    _do_reset()
    yield
    _do_reset()


@pytest_asyncio.fixture
async def app():
    """Yield a FastAPI app with all DB I/O routed to the in-memory fake."""
    patches = [
        patch("app.db.fetchrow",              side_effect=_fake_db.fake_fetchrow),
        patch("app.db.fetch",                 side_effect=_fake_db.fake_fetch),
        patch("app.db.execute",               side_effect=_fake_db.fake_execute),
        patch("app.db.get_connection",        new=_fake_db.fake_get_connection),
        patch("app.routes.auth.fetchrow",     side_effect=_fake_db.fake_fetchrow),
        patch("app.routes.auth.execute",      side_effect=_fake_db.fake_execute),
        patch("app.auth.sessions.fetchrow",   side_effect=_fake_db.fake_fetchrow),
        patch("app.auth.sessions.execute",    side_effect=_fake_db.fake_execute),
        patch("app.auth.sessions.get_connection", new=_fake_db.fake_get_connection),
        patch("app.auth.deps.fetchrow",       side_effect=_fake_db.fake_fetchrow),
        patch("app.db.init_db",               new=AsyncMock()),
        patch("app.db.close_db",              new=AsyncMock()),
    ]
    for p in patches:
        p.start()
    try:
        import main as main_module
        _app = main_module.create_app()
        yield _app
    finally:
        for p in patches:
            p.stop()


@pytest_asyncio.fixture
async def client(app):
    """Async HTTPX client using ASGITransport against the fake-DB FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac


@pytest.fixture
def fake_db() -> FakeDB:
    """Expose the FakeDB singleton for per-test inspection."""
    return _fake_db


# ---------------------------------------------------------------------------
# Opt-in PG fixtures (only active when RUN_PG_TESTS=1 + DATABASE_URL set)
#
# These fixtures do NOT affect the default in-memory test suite:
# - They are NOT autouse.
# - They are skipped when RUN_PG_TESTS is unset.
# - test_pg_integration.py declares its own session-scoped copies; these
#   function-scoped versions are available for ad-hoc use in other tests
#   that want a real PG connection without the full session-scoped setup.
# ---------------------------------------------------------------------------

_RUN_PG = bool(os.getenv("RUN_PG_TESTS"))


@pytest_asyncio.fixture
async def pg_pool():
    """Function-scoped asyncpg pool against a real PG.

    Skipped when RUN_PG_TESTS is not set.  Use DATABASE_URL to configure the
    connection.  The pool is closed after the test finishes.
    """
    if not _RUN_PG:
        pytest.skip("Set RUN_PG_TESTS=1 + DATABASE_URL to use the pg_pool fixture.")

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url or "fake" in db_url:
        pytest.skip("DATABASE_URL is not a real PG URL — skipping pg_pool fixture.")

    import asyncpg  # noqa: PLC0415

    pool = await asyncpg.create_pool(dsn=db_url, min_size=1, max_size=3)
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def pg_db(pg_pool):
    """Function-scoped fixture: run migrations then yield the pool.

    Depends on pg_pool; also skipped when RUN_PG_TESTS is not set.
    Migrations run against the schema referenced by DATABASE_URL (typically
    the public schema of a throwaway DB).
    """
    from pathlib import Path  # noqa: PLC0415

    migrations_dir = (
        Path(__file__).parent.parent.parent / "database" / "migrations"
    )

    async with pg_pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    text        PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        applied = {
            r["version"]
            for r in await conn.fetch("SELECT version FROM schema_migrations")
        }
        for sql_file in sorted(migrations_dir.glob("*.sql")):
            if sql_file.name in applied:
                continue
            sql = sql_file.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)",
                    sql_file.name,
                )

    yield pg_pool
