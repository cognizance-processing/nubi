"""Compute endpoint — POST /compute/run (M4-A, M4-SEC hardened, M4-REMOTE).

Executes user Python code in an E2B/Modal remote sandbox or a local subprocess,
optionally pre-loading an Arrow table from a registered query as ``inputs['input']``.

Security contract
-----------------
* **Embed tokens are REJECTED** (kind != 'access' → 403).  Code execution
  is a privileged first-party operation.  Embed tokens only get read access
  to registered SQL queries.
* **exec:kernel scope** is required.  First-party access tokens currently
  receive ``['read:*', 'edit:*']`` by default (see auth/verify.py).  The
  scope check accepts ``exec:kernel`` OR ``edit:*`` OR ``*`` — the latter two
  imply exec:kernel for backwards compatibility with existing first-party UX.
  Embed tokens are already rejected by the kind check above; this scope check
  is an additional defence-in-depth layer for future token types.
* **Remote runner is the production path**: when ``KERNEL_REMOTE_PROVIDER=e2b``
  and ``E2B_API_KEY`` are set, E2BRunner is used in ALL environments (including
  production).  E2B sandboxes run in Firecracker microVMs with no access to the
  Nubi host, filesystem, network (IMDS), or secrets.
* **Local runner is dev-only**: ``LocalSubprocessRunner`` is only allowed when
  ``ENV != 'production'`` AND ``KERNEL_LOCAL_ENABLED=true``.  It is never used
  in production (raises 503 if production + no remote configured).
* **Code length cap**: requests with code > 100,000 characters are rejected
  with 413 before any runner is invoked.
* Output is size-capped at 64 MiB to prevent DoS.
* A hard timeout (default 30 s, max 120 s) kills the runner.

Runner selection (``_choose_runner``)
--------------------------------------
1. KERNEL_REMOTE_PROVIDER=='e2b' AND E2B_API_KEY set → E2BRunner (any env).
2. KERNEL_REMOTE_PROVIDER=='modal' AND MODAL creds set → ModalRunner (any env).
3. ENV != 'production' AND KERNEL_LOCAL_ENABLED → LocalSubprocessRunner.
4. else → 503 kernel_disabled.

Pipeline
--------
1. ``verified_identity`` dep validates the bearer token.
2. Reject embed tokens → 403.
3. Check exec:kernel scope (accepts edit:* or * as implied grants) → 403.
4. Parse and validate request body (``ComputeRunIn``).
5. Reject oversized code (> 100,000 chars) → 413.
6. Choose runner (see above).
7. If ``input_query_id`` is provided:
   a. Look up the registered query in ``QueryRegistry``.
   b. Execute it via the DuckDB demo connector to obtain a ``pyarrow.Table``.
   c. Bind the table as ``inputs['input']``.
8. Run the chosen runner.
9. Record kernel usage via ``record_kernel_usage``.
10. Return the result as an Arrow IPC stream with
    ``Content-Type: application/vnd.apache.arrow.stream``
    and ``X-Nubi-Tier: <tier>`` (``local_kernel`` or ``remote_kernel``).
"""

from __future__ import annotations

from typing import Annotated

import pyarrow as pa
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.auth.deps import verified_identity
from app.auth.scopes import has_scope
from app.auth.verify import VerifiedIdentity
from app.compute.metering import record_kernel_usage
from app.compute.runner import LocalSubprocessRunner, KernelResult
from app.connectors.arrow_io import table_to_ipc_bytes
from app.errors import AppError
from app.queries.registry import get_query_registry
from app.routes import api_router

# Remote runner classes imported lazily inside _choose_runner to keep them
# optional (e2b/modal packages may not be installed).


router = APIRouter(tags=["compute"])

_ARROW_STREAM_MEDIA_TYPE = "application/vnd.apache.arrow.stream"

# Hard cap on timeout_s to prevent runaway jobs.
_MAX_TIMEOUT_S: int = 120

# Hard cap on incoming code length (characters).  Reject before subprocess launch.
_MAX_CODE_CHARS: int = 100_000

# ---------------------------------------------------------------------------
# Demo DuckDB connector (reuse the module-level singleton from routes/query.py
# so both endpoints share the same seeded in-memory DB).
# ---------------------------------------------------------------------------


def _get_demo_connector():
    """Return the demo DuckDB connector (reuse from routes.query if loaded,
    else create a local one).  Avoids seeding twice in normal operation.
    """
    try:
        # Prefer the already-initialised singleton from the query route module.
        from app.routes.query import _get_demo_connector as _qdc

        return _qdc()
    except ImportError:
        # Fallback: create a fresh demo connector here.
        from app.connectors.duckdb_conn import DuckDBConnector

        conn = DuckDBConnector()
        demo_table = pa.table(
            {
                "id": pa.array([1, 2, 3, 4, 5], type=pa.int32()),
                "name": pa.array(
                    ["alpha", "beta", "gamma", "delta", "epsilon"],
                    type=pa.string(),
                ),
                "value": pa.array([1.1, 2.2, 3.3, 4.4, 5.5], type=pa.float64()),
                "active": pa.array([True, False, True, False, True], type=pa.bool_()),
            }
        )
        conn.register({"demo": demo_table})
        return conn


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------


class ComputeRunIn(BaseModel):
    """Request body for POST /compute/run.

    Attributes
    ----------
    code:
        Python source code to execute.  The code receives ``inputs`` (a dict
        of ``{str: pyarrow.Table}``) and ``pa`` (alias for ``pyarrow``) in its
        namespace.  The code MUST assign ``result`` to a ``pyarrow.Table`` (or
        a pandas ``DataFrame`` if pandas is installed).
        Maximum length: 100,000 characters (enforced at the route layer).
    input_query_id:
        Optional id of a server-registered query.  When provided the query is
        executed via the DuckDB demo connector and the resulting Arrow table is
        bound as ``inputs['input']`` before the user code runs.
    timeout_s:
        Hard wall-clock timeout in seconds.  Capped at 120.  Default 30.
    """

    code: str
    input_query_id: str | None = None
    timeout_s: Annotated[int, Field(ge=1, le=_MAX_TIMEOUT_S)] = 30


# ---------------------------------------------------------------------------
# Scope helper
# ---------------------------------------------------------------------------


def _has_exec_scope(scope: list[str]) -> bool:
    """Return True if *scope* grants code-execution capability.

    Explicit grant: ``exec:kernel``
    Implied grants (backwards-compat): ``edit:*`` or ``*``

    First-party access tokens currently receive ``['read:*', 'edit:*']`` by
    default (auth/verify.py ``_FIRST_PARTY_SCOPES``).  Rather than require a
    migration to add ``exec:kernel`` to every existing token, we treat
    ``edit:*`` as implying exec:kernel.  Future token versions should include
    ``exec:kernel`` explicitly.
    """
    return (
        has_scope(scope, "exec:kernel")
        or has_scope(scope, "edit:*")
        or "*" in scope
    )


# ---------------------------------------------------------------------------
# Runner selection
# ---------------------------------------------------------------------------


def _choose_runner():
    """Return the appropriate runner based on current settings.

    Priority
    --------
    1. If KERNEL_REMOTE_PROVIDER=='e2b' AND E2B_API_KEY is set
       → E2BRunner (works in ANY env, including production).
    2. Elif KERNEL_REMOTE_PROVIDER=='modal' AND MODAL_TOKEN_ID/SECRET are set
       → ModalRunner (works in ANY env, including production).
    3. Elif ENV != 'production' AND KERNEL_LOCAL_ENABLED
       → LocalSubprocessRunner (dev/test only).
    4. Else → raise AppError("kernel_disabled", 503).

    The remote runner takes precedence over local in all environments.  When
    a remote provider is configured, production code execution is safe (E2B /
    Modal run in isolated microVMs or containers).

    Raises
    ------
    AppError("kernel_disabled", 503)
        When no runner is available (production without remote configured, or
        local disabled).
    """
    from app.config import get_settings

    settings = get_settings()
    provider = (settings.KERNEL_REMOTE_PROVIDER or "").lower().strip()

    # ── 1. E2B remote runner ──────────────────────────────────────────────────
    if provider == "e2b" and settings.E2B_API_KEY:
        from app.compute.remote_e2b import E2BRunner  # lazy import

        return E2BRunner(api_key=settings.E2B_API_KEY, timeout_s=30)

    # ── 2. Modal remote runner ────────────────────────────────────────────────
    if provider == "modal" and settings.MODAL_TOKEN_ID and settings.MODAL_TOKEN_SECRET:
        from app.compute.remote_modal import ModalRunner  # lazy import

        return ModalRunner(
            token_id=settings.MODAL_TOKEN_ID,
            token_secret=settings.MODAL_TOKEN_SECRET,
            timeout_s=30,
        )

    # ── 3. Local subprocess runner (dev/test only) ────────────────────────────
    # Explicitly blocked in production regardless of KERNEL_LOCAL_ENABLED flag.
    if settings.KERNEL_LOCAL_ENABLED and settings.ENV != "production":
        return LocalSubprocessRunner()

    # ── 4. No runner available ────────────────────────────────────────────────
    raise AppError(
        "kernel_disabled",
        "No kernel runner is configured.  "
        "In production, set KERNEL_REMOTE_PROVIDER=e2b and E2B_API_KEY to "
        "enable safe remote code execution via E2B Firecracker microVMs.  "
        "For local development, set ENV=development and KERNEL_LOCAL_ENABLED=true.",
        503,
    )


# ---------------------------------------------------------------------------
# POST /compute/run
# ---------------------------------------------------------------------------


@router.post("/compute/run")
async def compute_run(
    body: ComputeRunIn,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> Response:
    """Execute Python code in a subprocess/remote runner and return Arrow IPC.

    Parameters
    ----------
    body:
        ``ComputeRunIn`` JSON body.
    identity:
        Verified identity (injected by ``verified_identity`` dep).

    Returns
    -------
    Response
        HTTP 200, ``Content-Type: application/vnd.apache.arrow.stream``,
        body = Arrow IPC stream bytes of the ``result`` table.
        Header ``X-Nubi-Tier`` = the compute tier (e.g. ``"local_kernel"``).

    Raises
    ------
    AppError("forbidden", 403)
        If the token is an embed token (kind != 'access').
    AppError("forbidden", 403)
        If the token lacks exec:kernel scope (or equivalent implied grant).
    AppError("code_too_large", 413)
        If ``code`` exceeds 100,000 characters.
    AppError("kernel_disabled", 503)
        If the local kernel is disabled in production and no remote runner is
        configured.
    AppError("query_not_found", 404)
        If ``input_query_id`` is given but not found in the registry.
    AppError("kernel_timeout", 504)
        If the subprocess exceeds ``timeout_s``.
    AppError("kernel_output_too_large", 413)
        If the result exceeds 64 MiB.
    AppError("kernel_error", 400)
        If the subprocess exits with a non-zero code.
    AppError("kernel_unavailable", 503)
        If the configured runner is not available.
    """
    # ── SECURITY: reject embed tokens ─────────────────────────────────────────
    # Code execution is a first-party-only capability.  Embed tokens (kind='embed')
    # may only read registered SQL queries via /query — they cannot run arbitrary
    # Python code, even if they carry broad scopes.
    if identity.kind != "access":
        raise AppError(
            "forbidden",
            "Code execution requires a first-party session.  "
            "Embed tokens cannot call /compute/run.",
            403,
        )

    # ── SECURITY: require exec:kernel scope (or implied edit:* / *) ───────────
    # First-party tokens receive ['read:*', 'edit:*'] by default, which implies
    # exec:kernel.  This check is defence-in-depth for future restricted tokens.
    if not _has_exec_scope(identity.scope):
        raise AppError(
            "forbidden",
            "Token does not grant code-execution capability.  "
            "Required scope: exec:kernel (or edit:* / *).",
            403,
        )

    # ── SECURITY: cap incoming code length ────────────────────────────────────
    if len(body.code) > _MAX_CODE_CHARS:
        raise AppError(
            "code_too_large",
            f"Code exceeds the {_MAX_CODE_CHARS:,} character limit "
            f"({len(body.code):,} chars submitted).",
            413,
        )

    # ── Choose runner (production guard) ──────────────────────────────────────
    runner = _choose_runner()

    # ── Resolve inputs ─────────────────────────────────────────────────────────
    inputs: dict[str, pa.Table] = {}

    if body.input_query_id is not None:
        registry = get_query_registry()
        registered = registry.get(body.input_query_id)
        if registered is None:
            raise AppError(
                "query_not_found",
                f"No registered query found for id={body.input_query_id!r}.",
                404,
            )

        # Execute via the DuckDB demo connector.
        from app.connectors import plan as planner_plan

        physical_plan = planner_plan(sql=registered.sql, claims={})
        demo_conn = _get_demo_connector()
        input_table = demo_conn.execute(physical_plan)
        inputs["input"] = input_table

    # ── Run the kernel ─────────────────────────────────────────────────────────
    timeout_s = min(body.timeout_s, _MAX_TIMEOUT_S)
    result: KernelResult = runner.run(body.code, inputs, timeout_s)

    # ── Serialise result to Arrow IPC bytes ────────────────────────────────────
    if result.table is None:
        raise AppError(
            "kernel_error",
            "Kernel returned no table.",
            400,
        )

    ipc_bytes = table_to_ipc_bytes(result.table)

    # ── Meter usage ───────────────────────────────────────────────────────────
    await record_kernel_usage(
        user_id=identity.user_id,
        tier=result.tier,
        elapsed_ms=result.elapsed_ms,
        output_bytes=len(ipc_bytes),
    )

    # ── Return Arrow IPC response ──────────────────────────────────────────────
    return Response(
        content=ipc_bytes,
        media_type=_ARROW_STREAM_MEDIA_TYPE,
        headers={"X-Nubi-Tier": result.tier},
    )


# ---------------------------------------------------------------------------
# Register this router on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
