"""Nubi FastAPI application entry point.

Start the server with::

    uvicorn main:app --reload --host 0.0.0.0 --port 8000

Environment variables are read from a ``.env`` file (or the real environment)
via ``app.config.Settings``.  See ``app/config.py`` for the full list.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import close_db, fetchrow, init_db
from app.errors import register_handlers
from app.routes import api_router

# Import auth routes so they register themselves on api_router at import time.
import app.routes.auth  # noqa: F401, E402

# Import query route so it registers itself on api_router at import time.
import app.routes.query  # noqa: F401, E402

# Import insights route so it registers itself on api_router at import time.
import app.routes.insights  # noqa: F401, E402

# Import preagg route so it registers itself on api_router at import time.
import app.routes.preagg  # noqa: F401, E402

# Import embed route so it registers itself on api_router at import time.
import app.routes.embed  # noqa: F401, E402

# Import compute route so it registers itself on api_router at import time.
import app.routes.compute  # noqa: F401, E402

# Import lineage route BEFORE resources so its concrete /lineage prefix routes
# are registered ahead of the generic /{resource} catch-all in resources.py.
import app.routes.lineage  # noqa: F401, E402

# Import AI grounding route so it registers itself on api_router at import time.
import app.routes.ai  # noqa: F401, E402

# Import jobs route BEFORE resources so the /jobs prefix routes are registered
# ahead of the generic /{resource} catch-all in resources.py.
import app.routes.jobs  # noqa: F401, E402

# Import resources route so it registers itself on api_router at import time.
import app.routes.resources  # noqa: F401, E402


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle resources.

    On startup: open the asyncpg connection pool; optionally start the
    background job scheduler (if ``JOBS_SCHEDULER_ENABLED=true``).
    On shutdown: stop the scheduler then drain and close the pool.
    """
    from app.jobs.runtime import start_scheduler, stop_scheduler

    await init_db()

    settings = get_settings()
    if settings.JOBS_SCHEDULER_ENABLED:
        start_scheduler(_app)

    try:
        yield
    finally:
        await stop_scheduler()
        await close_db()


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application.

    Returns
    -------
    FastAPI
        Fully configured application instance.
    """
    settings = get_settings()

    application = FastAPI(
        title="Nubi API",
        version="0.1.0",
        docs_url="/docs" if settings.ENV != "production" else None,
        redoc_url="/redoc" if settings.ENV != "production" else None,
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Error handlers ────────────────────────────────────────────────────────
    register_handlers(application)

    # ── Routers ───────────────────────────────────────────────────────────────
    # Mount the shared api_router; Wave B auth routes are attached to it
    # before or after this call — order doesn't matter for inclusion.
    application.include_router(api_router, prefix="/api/v1")

    # ── Health endpoint ───────────────────────────────────────────────────────
    @application.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        """Return service liveness and database reachability.

        Returns
        -------
        dict
            ``{"status": "ok", "db": "ok" | "error"}``
        """
        db_status = "error"
        try:
            row = await fetchrow("SELECT 1 AS ping")
            if row is not None and row["ping"] == 1:
                db_status = "ok"
        except Exception:
            # Deliberately swallow — we return "error" rather than 500.
            # Never log exception details here (could contain connection info).
            pass

        return {"status": "ok", "db": db_status}

    return application


app = create_app()
