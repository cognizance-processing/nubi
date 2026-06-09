"""Nubi FastAPI application entry point.

Start the server with::

    uvicorn main:app --reload --host 0.0.0.0 --port 8000

Environment variables are read from a ``.env`` file (or the real environment)
via ``app.config.Settings``.  See ``app/config.py`` for the full list.

Required / notable environment variables
-----------------------------------------
NUBI_SECRETS_KEY
    Fernet key used to encrypt/decrypt named secrets at rest.
    Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    Must be set in production; if absent, any call to encrypt/decrypt raises a
    clear RuntimeError (startup itself does not fail so non-secrets features work).

FLOWS_WORKER_CONCURRENCY
    Number of concurrent task slots in the external worker process (backend/worker.py).
    Read by ``run_worker_pool`` in worker.py; not used by this process directly.
    Default: 4.

FLOWS_WORKER_INTERVAL_S
    Seconds between flow-worker ticks.  Used both by the in-process dev worker
    (when FLOWS_INPROCESS_WORKER=true) and the external worker process.
    Default: 5.  See also app/config.py.

FLOWS_INPROCESS_WORKER
    Set to ``true`` to run the task-execution worker inside this API process
    (convenience for local dev / single-dyno deployments).  In production set
    this to ``false`` (default) and run backend/worker.py separately so the
    heavy compute does not block request handling.
    When false the lifespan still starts the scheduler (flow_tick: schedule +
    reap) so scheduled flows are materialised — but task execution is done
    exclusively by the external worker pool.

FLOWS_SCHEDULER_ENABLED
    Master switch for the in-process scheduler tick (schedule + reap pass of
    flow_tick).  Defaults to inheriting FLOWS_WORKER_ENABLED or
    JOBS_SCHEDULER_ENABLED (see app/config.py for precedence rules).

EE / Commercial environment variables (optional — OSS build works without them)
---------------------------------------------------------------------------------
NUBI_LICENSE_KEY
    License key that activates EE features (billing, paid_tiers, SSO, etc.).
    When absent or invalid, the EE loader returns False silently and all
    commercial features remain disabled (open-source defaults apply).
    Contact sales@nubi.dev for a key.  Format: JWT issued by Nubi license server.
    Read by: backend/app/ee/licensing/license.py → get_license().

PAYSTACK_SECRET_KEY
    Paystack secret key (sk_live_… or sk_test_…) used by the EE billing module
    to charge ZAR-denominated subscriptions.  Never read by OSS core code.
    Read lazily by: backend/app/ee/billing/ at first billing call.
    Optional even within EE — billing features degrade gracefully when absent.
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

# Register asset-serving + PATCH /auth/me from the assets package.
# Must be imported after auth so the /auth prefix group is already present.
from app.assets.routes import router as _assets_router  # noqa: E402

api_router.include_router(_assets_router)

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

# Import data-browser route (the /data/* table + row endpoints the Data page
# calls) so it self-registers on api_router. Without this import the routes are
# never mounted and /data/* falls through to the /{resource} catch-all, 404-ing
# as "Unknown resource: 'data'". Must be before resources.py's catch-all below.
import app.routes.data_browser  # noqa: F401, E402

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

# Integrations (Slack/WhatsApp alerts + chat) — self-registers on api_router with
# its /integrations prefix; before the generic /{resource} catch-all.
import app.routes.integrations  # noqa: F401, E402

# Import projects route BEFORE resources so the /projects prefix routes are
# registered ahead of the generic /{resource} catch-all in resources.py.
import app.routes.projects  # noqa: F401, E402

# Portability (YAML export/import) — self-registers on api_router; before the catch-all.
import app.routes.portability  # noqa: F401, E402

# Secrets (org-scoped named secret management) — self-registers on api_router
# with its /secrets prefix; before the generic /{resource} catch-all.
# Depends on NUBI_SECRETS_KEY being set in the environment (see module docstring).
from app.routes.secrets import router as secrets_router  # noqa: E402

api_router.include_router(secrets_router)

# Features (open-core feature-flag endpoint) — returns the list of enabled
# commercial/registered feature names so the frontend can gate paid-tier UI.
# Sits next to the secrets router; before the generic /{resource} catch-all.
from app.routes.features import router as features_router  # noqa: E402

api_router.include_router(features_router)

# Query authoring tools (validate / complete / schema) — standalone router,
# included explicitly. Registered before the /{resource} catch-all.
from app.routes.query_tools import router as query_tools_router  # noqa: E402
from app.routes.export_share import router as export_share_router  # noqa: E402

api_router.include_router(query_tools_router)
api_router.include_router(export_share_router)

# Import JWT issuers route (org-scoped CRUD for embed JWKS configs) BEFORE
# resources so the /security prefix routes are registered ahead of the generic
# /{resource} catch-all in resources.py.
import app.routes.jwt_issuers  # noqa: F401, E402

# Import datasets route (lakehouse CSV upload + materialise + catalog) BEFORE
# resources so the /datasets prefix routes are registered ahead of the generic
# /{resource} catch-all in resources.py.
import app.routes.datasets  # noqa: F401, E402

# Import resources route so it registers itself on api_router at import time.
import app.routes.resources  # noqa: F401, E402


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle resources.

    On startup: open the asyncpg connection pool; optionally start the
    background job scheduler (if ``JOBS_SCHEDULER_ENABLED=true``) and the
    flows scheduler tick (if ``FLOWS_SCHEDULER_ENABLED=true``).

    Worker architecture
    -------------------
    Task *execution* (run_one_ready_task / run_worker_pool) runs in a SEPARATE
    process — ``backend/worker.py`` — to avoid blocking request handling with
    heavy compute.  This process only runs the *scheduler* pass of flow_tick
    (schedule due flows, advance readiness, reap timed-out tasks) which is fast
    and I/O-light.

    For local dev or single-dyno deployments set ``FLOWS_INPROCESS_WORKER=true``
    to also run task execution inside this process (identical to the old
    behaviour).  In production leave it unset / ``false`` and run worker.py
    separately.

    On shutdown: stop all background tasks, then drain and close the pool.
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

    # ── Flows scheduler (schedule + reap pass) ─────────────────────────────────
    # ``FLOWS_SCHEDULER_ENABLED`` controls whether the in-process background loop
    # runs at all.  It defaults to inheriting FLOWS_WORKER_ENABLED or
    # JOBS_SCHEDULER_ENABLED (see app/config.py).
    #
    # ``FLOWS_INPROCESS_WORKER`` additionally enables task *execution* inside
    # this process.  Default: off (false) — production deploys run worker.py.
    # When false the scheduler tick still materialises scheduled flows and reaps
    # timed-out task_runs; heavy execution is delegated to the external worker.
    #
    # Environment variable reference (full list in module docstring above):
    #   FLOWS_SCHEDULER_ENABLED   — master on/off for the scheduler tick
    #   FLOWS_INPROCESS_WORKER    — also run task execution in this process
    #   FLOWS_WORKER_INTERVAL_S   — seconds between ticks (default 5)
    #   FLOWS_WORKER_CONCURRENCY  — concurrent task slots in worker.py (default 4)
    #   NUBI_SECRETS_KEY          — Fernet key for secret encryption (required in prod)
    #
    # NOTE: start_flow_worker() gains an ``execute_tasks`` keyword once
    # WorkPoolAgent lands the parameter in runtime.py.  We detect it via
    # inspect.signature so this call is forward-compatible without crashing
    # when the param is absent in the current runtime build.
    _flows_inprocess = os.getenv("FLOWS_INPROCESS_WORKER", "false").lower() in (
        "1", "true", "yes",
    )
    if getattr(settings, "FLOWS_SCHEDULER_ENABLED", False):
        _start_kwargs: dict = {}
        import inspect  # noqa: PLC0415
        if "execute_tasks" in inspect.signature(start_flow_worker).parameters:
            _start_kwargs["execute_tasks"] = _flows_inprocess
        start_flow_worker(_app, **_start_kwargs)

        # ── Auto-preagg scheduled flow ─────────────────────────────────────────
        # Register the preagg_refresh flow for the system/default org so the
        # suggest → materialize pass runs on schedule via the flows work-pool.
        # This is an OSS core feature; no EE code involved.
        #
        # The flow is idempotent (first-write-wins): calling ensure_preagg_flow
        # on every startup is safe — it returns the existing flow if already
        # registered.
        #
        # The org_id "__system__" is a sentinel used for global/system-level flows
        # that are not scoped to a specific tenant.  Per-tenant preagg flows can
        # be registered via the POST /preagg/schedule endpoint (future work).
        #
        # PREAGG_SCHEDULE   — cron expression (default "0 * * * *", hourly)
        # PREAGG_MIN_HITS   — minimum query-log frequency to trigger rollup (default 3)
        # PREAGG_ORG_ID     — org_id for the system-wide preagg flow (default "__system__")
        try:
            from app.preagg import ensure_preagg_flow  # noqa: PLC0415

            _preagg_org_id = os.getenv("PREAGG_ORG_ID", "__system__")
            _preagg_schedule = os.getenv("PREAGG_SCHEDULE", "0 * * * *")
            _preagg_min_hits = int(os.getenv("PREAGG_MIN_HITS", "3"))

            await ensure_preagg_flow(
                org_id=_preagg_org_id,
                created_by="__system__",
                schedule=_preagg_schedule,
                min_hits=_preagg_min_hits,
            )
        except Exception as _preagg_exc:  # noqa: BLE001
            # Never crash core startup because of preagg registration failure.
            import logging  # noqa: PLC0415
            logging.getLogger(__name__).warning(
                "preagg scheduled-flow registration failed (non-fatal): %s", _preagg_exc
            )

        # EE startup tasks that need a live DB pool (e.g. the daily FX-refresh
        # scheduled flow).  Core never imports EE business logic — only this
        # guarded hook, which no-ops when the ee/ tree is absent.  Runs AFTER
        # init_db() so the asyncpg pool is live.
        try:
            from app.ee import ee_startup  # noqa: PLC0415

            await ee_startup()
        except Exception as _ee_exc:  # noqa: BLE001
            import logging  # noqa: PLC0415
            logging.getLogger(__name__).warning(
                "EE startup tasks failed (non-fatal): %s", _ee_exc
            )

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

    # ── EE loader ─────────────────────────────────────────────────────────────
    # Load the EE sub-package (licensing, billing, SSO, …) AFTER all core
    # routers are registered so that EE sub-modules can safely mount their own
    # additional routes (e.g. /ee/billing) without conflicting with core paths.
    #
    # CONTRACT (open-core invariant):
    # - Core code NEVER imports from app.ee — only load_ee() does so internally.
    # - If the ee/ tree is absent, misconfigured, or any EE dep is missing,
    #   load_ee() returns False and logs at WARNING level.  The OSS build
    #   continues with all commercial features disabled (feature_enabled returns
    #   False for 'billing' / 'paid_tiers').
    # - load_ee() mounts EE routers (e.g. /ee/billing) from inside itself; core
    #   does NOT call application.include_router for any EE router.
    #
    # Relevant env vars (EE only — OSS build ignores them):
    #   NUBI_LICENSE_KEY      — activates EE tier; see module docstring above
    #   PAYSTACK_SECRET_KEY   — EE billing integration; see module docstring above
    try:
        from app.ee import load_ee  # noqa: PLC0415

        _ee_loaded = load_ee(application)
        if not _ee_loaded:
            import logging  # noqa: PLC0415
            logging.getLogger(__name__).debug(
                "Nubi EE not loaded — running in OSS mode (commercial features disabled)"
            )
    except Exception as _ee_exc:  # noqa: BLE001
        import logging  # noqa: PLC0415
        logging.getLogger(__name__).warning(
            "Nubi EE loader raised an unexpected error (non-fatal, OSS mode): %s",
            _ee_exc,
        )

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
