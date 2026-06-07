"""Nubi FastAPI application entry point.

Start the server with::

    uvicorn main:app --reload --host 0.0.0.0 --port 8000

Environment variables are read from a ``.env`` file (or the real environment)
via ``app.config.Settings``.  See ``app/config.py`` for the full list.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

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

# Import flows route BEFORE resources so the /flows prefix routes are registered
# ahead of the generic /{resource} catch-all in resources.py.
import app.routes.flows  # noqa: F401, E402

# Import git sync + chat gateway + connectors + bridges routes (prefixed) BEFORE
# resources so they register ahead of the generic /{resource} catch-all.
import app.routes.git  # noqa: F401, E402
import app.routes.chat  # noqa: F401, E402
import app.routes.connectors  # noqa: F401, E402
import app.routes.bridges  # noqa: F401, E402
import app.routes.orgs  # noqa: F401, E402

# Import projects route BEFORE resources so the /projects prefix routes are
# registered ahead of the generic /{resource} catch-all in resources.py.
import app.routes.projects  # noqa: F401, E402

# Portability (YAML export/import) — self-registers on api_router; before the catch-all.
import app.routes.portability  # noqa: F401, E402

# Query authoring tools (validate / complete / schema) — standalone router,
# included explicitly. Registered before the /{resource} catch-all.
from app.routes.query_tools import router as query_tools_router  # noqa: E402
from app.routes.export_share import router as export_share_router  # noqa: E402

api_router.include_router(query_tools_router)
api_router.include_router(export_share_router)

# Import resources route so it registers itself on api_router at import time.
import app.routes.resources  # noqa: F401, E402


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle resources.

    On startup: open the asyncpg connection pool; optionally start the
    background job scheduler (if ``JOBS_SCHEDULER_ENABLED=true``).
    On shutdown: stop the scheduler then drain and close the pool.
    """
    from app.flows.runtime import start_flow_worker, stop_flow_worker
    from app.jobs.runtime import start_scheduler, stop_scheduler
    from app.queries.registry import load_persisted_queries

    await init_db()

    # Load persisted queries (from the `queries` table) into the runtime
    # registry so that dashboard widgets referencing only a query_id execute
    # against their bound datastore.  Best-effort: never crashes startup.
    await load_persisted_queries()

    settings = get_settings()
    if settings.JOBS_SCHEDULER_ENABLED:
        start_scheduler(_app)
    # The flows engine is the single home for scheduled automation.  Its
    # background worker (flow_tick loop) is gated by FLOWS_SCHEDULER_ENABLED,
    # which defaults to the jobs scheduler flag (resolved in app.config) so a
    # single switch turns on all scheduled automation.
    if getattr(settings, "FLOWS_SCHEDULER_ENABLED", False):
        start_flow_worker(_app)

    try:
        yield
    finally:
        await stop_flow_worker()
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

    # ── Static SPA (combined image) ───────────────────────────────────────────
    # When a built frontend is present (STATIC_DIR or <repo>/dist), serve it on
    # the same origin as the API. Inert when absent — so tests/dev are unaffected.
    static_dir = os.getenv("STATIC_DIR") or str(Path(__file__).resolve().parents[1] / "dist")
    if os.path.isdir(static_dir):
        assets_dir = os.path.join(static_dir, "assets")
        if os.path.isdir(assets_dir):
            application.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        index_html = os.path.join(static_dir, "index.html")

        @application.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str) -> FileResponse:
            """Serve a static file if it exists, else the SPA index (client routing).

            API and health routes are registered earlier and take precedence; this
            only catches everything else.
            """
            if full_path.startswith(("api/", "health", "docs", "redoc", "openapi")):
                raise HTTPException(status_code=404, detail="Not found")
            candidate = os.path.join(static_dir, full_path)
            if full_path and os.path.isfile(candidate):
                return FileResponse(candidate)
            return FileResponse(index_html)

    return application


app = create_app()
