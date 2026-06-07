"""Scheduled jobs REST endpoints — M11-A.

Endpoints
---------
POST   /jobs              — create a job (201)
GET    /jobs              — list jobs for caller's org
GET    /jobs/{id}         — get job (404 on cross-org or missing)
DELETE /jobs/{id}         — delete job (204)
POST   /jobs/{id}/run     — run a job immediately, return the job_run
GET    /jobs/{id}/runs    — list runs for a job

All endpoints require a valid first-party Bearer token (``current_user``).
Jobs are org-scoped: callers can only see and operate on jobs that belong to
their own org.  Cross-org access returns 404 (no information leak).

Organisation resolution
-----------------------
We reuse the ``get_user_org`` helper from ``routes.resources``.  For tests
that inject an ``InMemoryRepo``, the helper automatically delegates to
``repo.get_org_for_user(user_id)``; for production it queries
``org_members`` via the DB.

Job store
---------
All job state is held in an ``InMemoryJobStore`` (singleton via
``get_job_store()``).  Tests may inject their own store via
``set_job_store(store)`` before issuing requests.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, EmailStr, field_validator, model_validator

from app.auth.deps import current_user
from app.db import fetchrow
from app.errors import AppError
from app.jobs.executor import execute_job
from app.jobs.schedule import next_run
from app.jobs.store import InMemoryJobStore, get_job_store
from app.repos.provider import Repo, get_repo
from app.routes import api_router

# ---------------------------------------------------------------------------
# Sub-router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/jobs", tags=["jobs"])


# ---------------------------------------------------------------------------
# Org resolution helper (replicated from routes/resources.py to avoid the
# circular import that would arise if we imported from resources here, which
# causes resources.py's module-level api_router.include_router() to fire
# before our own, putting the generic /{resource} catch-all ahead of /jobs).
# ---------------------------------------------------------------------------


async def _get_user_org(user_id: str, repo: Repo) -> str:
    """Return the org_id for the user's first membership.

    Mirrors ``routes.resources.get_user_org`` without importing it.
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


# ---------------------------------------------------------------------------
# Pydantic request/response schemas
# ---------------------------------------------------------------------------


class ReportTarget(BaseModel):
    """Validated shape of ``target`` for ``kind='report'`` jobs.

    Attributes
    ----------
    board_id:
        UUID string of the board to render.
    params:
        Named param overrides passed to the board's widget queries.
    format:
        Output format — ``'csv'`` or ``'pdf'``.
    recipients:
        List of recipient email addresses (at least one required).
    subject:
        Email subject line.
    body:
        Plain-text email body.
    apply_user_permissions:
        When ``True``, the executor injects per-recipient locked params from
        ``locked_params`` before rendering so each recipient sees only their
        own data.
    locked_params:
        Optional per-recipient param overrides: ``{email: {param_name: value}}``.
        Only used when ``apply_user_permissions=True``.
    """

    board_id: str
    params: dict[str, Any] = {}
    format: str = "csv"
    recipients: list[str]
    subject: str = "Nubi Report"
    body: str = ""
    apply_user_permissions: bool = False
    locked_params: dict[str, dict[str, Any]] = {}

    @field_validator("format")
    @classmethod
    def _validate_format(cls, v: str) -> str:
        if v not in ("csv", "pdf"):
            raise ValueError("format must be 'csv' or 'pdf'")
        return v

    @field_validator("recipients")
    @classmethod
    def _validate_recipients(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("recipients must contain at least one email address")
        return v


class CreateJobIn(BaseModel):
    """Request body for POST /jobs.

    The ``target`` field is polymorphic:

    - For ``kind='query'`` or ``kind='python'``: a plain string (query_id or
      Python source code).
    - For ``kind='report'``: a dict matching :class:`ReportTarget`.
    """

    name: str
    kind: str
    target: str | dict[str, Any]
    schedule: str
    enabled: bool = True

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, v: str) -> str:
        if v not in ("query", "python", "report"):
            raise ValueError("kind must be 'query', 'python', or 'report'")
        return v

    @model_validator(mode="after")
    def _validate_target_for_kind(self) -> "CreateJobIn":
        """Validate that target matches the expected shape for the given kind."""
        if self.kind in ("query", "python"):
            if not isinstance(self.target, str):
                raise ValueError(
                    f"target must be a string for kind={self.kind!r}"
                )
        elif self.kind == "report":
            if isinstance(self.target, str):
                raise ValueError(
                    "target must be an object (dict) for kind='report'"
                )
            # Validate via ReportTarget — raises ValueError on bad shape.
            try:
                ReportTarget.model_validate(self.target)
            except Exception as exc:
                raise ValueError(f"invalid report target: {exc}") from exc
        return self

    def target_as_str(self) -> str:
        """Return target serialised as a string (for storage in JobStore)."""
        if isinstance(self.target, str):
            return self.target
        return json.dumps(self.target)

    def target_dict(self) -> dict[str, Any] | None:
        """Return target as a dict if it is one, else None."""
        if isinstance(self.target, dict):
            return self.target
        return None


# ---------------------------------------------------------------------------
# Helper: resolve the store dependency
# ---------------------------------------------------------------------------


def _get_store() -> InMemoryJobStore:
    """FastAPI dependency: return the active job store."""
    return get_job_store()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_job(job: dict[str, Any]) -> dict[str, Any]:
    """Convert a job dict to a JSON-serialisable form."""
    return {
        "id": job["id"],
        "org_id": job["org_id"],
        "created_by": job["created_by"],
        "name": job["name"],
        "kind": job["kind"],
        "target": job["target"],
        "schedule": job["schedule"],
        "enabled": job["enabled"],
        "next_run_at": _dt_iso(job.get("next_run_at")),
        "last_run_at": _dt_iso(job.get("last_run_at")),
        "created_at": _dt_iso(job.get("created_at")),
        "updated_at": _dt_iso(job.get("updated_at")),
    }


def _serialize_run(run: dict[str, Any]) -> dict[str, Any]:
    """Convert a job_run dict to a JSON-serialisable form."""
    return {
        "id": run["id"],
        "job_id": run["job_id"],
        "status": run["status"],
        "started_at": _dt_iso(run.get("started_at")),
        "finished_at": _dt_iso(run.get("finished_at")),
        "row_count": run.get("row_count", 0),
        "message": run.get("message", ""),
        "created_at": _dt_iso(run.get("created_at")),
    }


def _dt_iso(dt: datetime | None) -> str | None:
    """Convert a datetime to ISO-8601 string, or None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _require_job_in_org(
    job_id: str,
    org_id: str,
    store: InMemoryJobStore,
) -> dict[str, Any]:
    """Return the job if it exists and belongs to *org_id*, else raise 404."""
    job = store.get_job(job_id)
    if job is None or str(job["org_id"]) != str(org_id):
        raise AppError("not_found", "Job not found.", 404)
    return job


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
async def create_job(
    body: CreateJobIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
    store: InMemoryJobStore = Depends(_get_store),
) -> dict[str, Any]:
    """Create a new scheduled job.

    Validates the schedule string by computing the first ``next_run_at``
    (starting from now).  Returns 400 if the schedule is invalid.

    Returns 201 with the created job on success.
    """
    org_id = await _get_user_org(str(user["id"]), repo)

    # Validate the schedule up front; raises AppError("bad_schedule", 400) on failure.
    now = datetime.now(timezone.utc)
    first_next = next_run(body.schedule, now)

    # For report jobs, embed org_id into the target dict so the executor can
    # resolve the board without needing an async context (the executor is sync).
    target_payload: str
    if body.kind == "report" and isinstance(body.target, dict):
        target_with_org = {**body.target, "org_id": org_id}
        target_payload = json.dumps(target_with_org)
    else:
        target_payload = body.target_as_str()

    job = store.create_job(
        org_id=org_id,
        created_by=str(user["id"]),
        name=body.name,
        kind=body.kind,
        target=target_payload,
        schedule=body.schedule,
        enabled=body.enabled,
        next_run_at=first_next,
    )
    return _serialize_job(job)


@router.get("")
async def list_jobs(
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
    store: InMemoryJobStore = Depends(_get_store),
) -> list[dict[str, Any]]:
    """List all jobs for the caller's org."""
    org_id = await _get_user_org(str(user["id"]), repo)
    jobs = store.list_jobs(org_id)
    return [_serialize_job(j) for j in jobs]


@router.get("/{job_id}")
async def get_job(
    job_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
    store: InMemoryJobStore = Depends(_get_store),
) -> dict[str, Any]:
    """Get a single job by ID.

    Returns 404 if the job does not exist or belongs to a different org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    job = _require_job_in_org(job_id, org_id, store)
    return _serialize_job(job)


@router.delete("/{job_id}", status_code=204)
async def delete_job(
    job_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
    store: InMemoryJobStore = Depends(_get_store),
) -> Response:
    """Delete a job and all its runs.

    Returns 204 on success; 404 if the job does not exist or is cross-org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    _require_job_in_org(job_id, org_id, store)
    store.delete_job(job_id)
    return Response(status_code=204)


@router.post("/{job_id}/run", status_code=200)
async def run_job_now(
    job_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
    store: InMemoryJobStore = Depends(_get_store),
) -> dict[str, Any]:
    """Run a job immediately (outside of schedule).

    Executes the job synchronously, records the run, and advances
    ``next_run_at`` and ``last_run_at``.

    Returns the job_run dict.  The run status is ``'success'`` or ``'error'``.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    job = _require_job_in_org(job_id, org_id, store)

    now = datetime.now(timezone.utc)
    run = execute_job(job, now=now)
    store.add_run(job_id, run)

    # Advance timestamps
    try:
        new_next = next_run(job["schedule"], now)
    except AppError:
        new_next = None

    update_fields: dict[str, Any] = {"last_run_at": now}
    if new_next is not None:
        update_fields["next_run_at"] = new_next
    store.update_job(job_id, update_fields)

    return _serialize_run(run)


@router.get("/{job_id}/runs")
async def list_runs(
    job_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
    store: InMemoryJobStore = Depends(_get_store),
) -> list[dict[str, Any]]:
    """List all runs for a job (oldest first).

    Returns 404 if the job does not exist or belongs to a different org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    _require_job_in_org(job_id, org_id, store)
    runs = store.list_runs(job_id)
    return [_serialize_run(r) for r in runs]


# ---------------------------------------------------------------------------
# Register on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
