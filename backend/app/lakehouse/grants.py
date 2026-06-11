"""Ephemeral staging grants — write-only, prefix-pinned, short-TTL (design §7).

When a bridge agent claims an ingest task over the authenticated tunnel, the
control plane mints it a **grant** to write its output into the run's staging
prefix and nothing else. The grant is delivered over the tunnel, held in agent
memory only, never persisted — and crucially NEVER carries a stored connector
secret (hard rule, design §9).

A grant is three things at once (design §7):

1. **write-only**   — the agent can PUT bytes but cannot list, read, or delete.
2. **prefix-pinned** — every URL is scoped to ``orgs/<org>/staging/<run>/…``;
                       the agent cannot touch another org's or another run's
                       prefix. The prefix is server-pinned from trusted ids
                       (:func:`app.lakehouse.managed.org_staging_prefix`).
3. **short-TTL**    — 15–60 min, default 30. S3 presigned URLs are NOT revocable
                       mid-TTL (design §7), so the TTL is the only bound on a
                       leaked grant — keep it short.

Blast radius of a fully-compromised customer machine (design §7): write-only
access to ONE run's staging prefix until the grant expires. No read of org data,
no connector creds, no cross-org or cross-run reach.

Backends
--------
- **S3 staging store** — presigned ``PutObject`` URLs, one per declared relative
  file path (``presign_put`` on :class:`~app.storage.s3.S3StorageClient`). This
  is the managed-cloud posture.
- **Local/file staging store** (self-host/dev) — no presigner exists; we return
  a ``"local"`` grant carrying the pinned absolute prefix. The agent (in dev,
  same machine) writes through the StagingArea writer instead of an HTTP PUT.
  Documented weaker posture, identical code path (design §5).

The task→bridge binding check (:func:`task_belongs_to_bridge`) is the gate: a
grant is only minted for a run whose task_run is bound to the claiming bridge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.lakehouse.managed import org_staging_prefix, resolve_staging_storage

#: TTL bounds for a staging grant (design §7: 15–60 min). Presigned URLs are not
#: revocable mid-TTL, so we clamp the requested TTL into this window.
MIN_GRANT_TTL = timedelta(minutes=15)
MAX_GRANT_TTL = timedelta(minutes=60)
DEFAULT_GRANT_TTL = timedelta(minutes=30)


class StagingGrantError(Exception):
    """Raised when a staging grant cannot be minted.

    Carries a stable ``code`` so the tunnel / handler can surface an actionable
    reason (e.g. ``task_not_bound`` when the claimed task does not belong to the
    claiming bridge, ``staging_unconfigured`` when no staging store exists).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class StagingGrant:
    """A write-only, prefix-pinned, short-TTL grant to a run's staging prefix.

    Wire shape (delivered over the tunnel; the CLI agent reads exactly this)::

        {
            "kind": "s3_presigned" | "local",
            "org_id": "<uuid>",
            "run_id": "<run_id>",
            "bridge_id": "<uuid>",
            "prefix": "orgs/<org>/staging/<run>/",   # server-pinned, never user input
            "base_uri": "s3://<staging-bucket>",
            "expires_at": "<iso8601 utc>",
            "uploads": {                              # rel-path -> capability
                "<rel-path>": {"method": "PUT", "url": "<presigned>"},
                ...
            }
        }

    For the ``local`` kind, ``uploads[...].url`` is absent and ``base_uri`` is a
    ``file://`` root — the agent writes through the StagingArea writer instead.
    """

    kind: str
    org_id: str
    run_id: str
    bridge_id: str
    prefix: str
    base_uri: str
    expires_at: datetime
    uploads: dict[str, dict[str, str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "org_id": self.org_id,
            "run_id": self.run_id,
            "bridge_id": self.bridge_id,
            "prefix": self.prefix,
            "base_uri": self.base_uri,
            "expires_at": self.expires_at.isoformat(),
            "uploads": {k: dict(v) for k, v in self.uploads.items()},
        }


def _clamp_ttl(ttl: timedelta | None) -> timedelta:
    """Clamp *ttl* into [MIN_GRANT_TTL, MAX_GRANT_TTL]; default when None."""
    if ttl is None:
        return DEFAULT_GRANT_TTL
    if ttl < MIN_GRANT_TTL:
        return MIN_GRANT_TTL
    if ttl > MAX_GRANT_TTL:
        return MAX_GRANT_TTL
    return ttl


def _pin_rel(prefix: str, rel_path: str) -> str:
    """Join *rel_path* under *prefix*, refusing ``..`` / absolute escapes.

    Identical discipline to :meth:`StagingArea._key`: the resulting key can
    never climb above the run's staging prefix even with a hostile rel path.
    """
    rel = str(rel_path).strip().lstrip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
    return prefix + "/".join(parts)


async def task_belongs_to_bridge(
    org_id: str,
    run_id: str,
    bridge_id: str,
    *,
    store: Any | None = None,
) -> bool:
    """Verify the task_run *run_id* is bound to *bridge_id* within *org_id*.

    This is the gate before minting a grant (design §7 step 2: "validates
    bridge ↔ org ↔ task binding"). A task is bound to a bridge when its run row
    (or owning flow_run) records ``bridge_id`` AND the run's ``org_id`` matches.
    A bridge can only ever receive a grant for a run it actually claimed.

    *store* is the flows task-run store (duck-typed: ``get_task_run`` /
    ``get_flow_run``). When omitted, the active flows store is used. Returns
    ``False`` — never raises — on any lookup miss so callers fail closed.
    """
    if store is None:
        try:
            from app.flows.store import get_flow_store  # noqa: PLC0415

            store = get_flow_store()
        except Exception:  # noqa: BLE001
            return False

    getter = getattr(store, "get_task_run", None)
    if getter is None:
        return False
    try:
        run = await getter(run_id)
    except Exception:  # noqa: BLE001
        return False
    if not run:
        return False
    if str(run.get("org_id")) != str(org_id):
        return False

    # The claim may record the bridge on the task_run directly or on the run's
    # claims/lease metadata. Accept either location.
    claimed_bridge = (
        run.get("bridge_id")
        or run.get("claimed_by_bridge")
        or (run.get("claims") or {}).get("bridge_id")
    )
    return claimed_bridge is not None and str(claimed_bridge) == str(bridge_id)


async def mint_staging_grant(
    org_id: str,
    run_id: str,
    bridge_id: str,
    *,
    rel_paths: list[str] | None = None,
    ttl: timedelta | None = None,
    verify_binding: bool = True,
    store: Any | None = None,
    now: datetime | None = None,
) -> StagingGrant:
    """Mint a write-only, prefix-pinned, short-TTL grant for a bridge (design §7).

    Steps (control-plane side of design §7.2):

    1. If *verify_binding*, confirm the run is bound to *bridge_id* in *org_id*
       (:func:`task_belongs_to_bridge`); else raise ``task_not_bound``. A grant
       is NEVER minted for a task not bound to the claiming bridge.
    2. Resolve the staging store and the SERVER-PINNED prefix
       ``orgs/<org>/staging/<run>/`` (never user input).
    3. For an S3 store, presign one ``PutObject`` URL per declared *rel_path*
       (write-only, expires with the grant). For a local store, return a
       ``local`` grant with the pinned prefix and no URLs.

    The grant carries NO connector secret and NO read/list/delete capability —
    only the right to PUT into the run's own staging prefix until it expires.

    Parameters
    ----------
    rel_paths:
        The relative output paths the agent intends to write (e.g.
        ``["part-0000.parquet"]``). Each is pinned under the staging prefix and
        gets its own presigned PUT URL. Required (and non-empty) for an S3 grant
        — a presigned URL is per-key.
    ttl:
        Requested grant lifetime, clamped to [15, 60] min (default 30).
    """
    if verify_binding and not await task_belongs_to_bridge(
        org_id, run_id, bridge_id, store=store
    ):
        raise StagingGrantError(
            "task_not_bound",
            f"Run {run_id!r} is not bound to bridge {bridge_id!r} in org "
            f"{org_id!r}; refusing to mint a staging grant.",
        )

    staging = resolve_staging_storage()
    if staging is None:
        raise StagingGrantError(
            "staging_unconfigured",
            "No staging store is configured; cannot mint a staging grant.",
        )

    prefix = org_staging_prefix(org_id, run_id)  # server-pinned from trusted ids
    effective_ttl = _clamp_ttl(ttl)
    now = now or datetime.now(tz=timezone.utc)
    expires_at = now + effective_ttl

    if staging.scheme == "s3":
        paths = [p for p in (rel_paths or []) if str(p).strip()]
        if not paths:
            raise StagingGrantError(
                "no_paths",
                "An S3 staging grant requires at least one relative output path "
                "(a presigned URL is minted per object key).",
            )
        from app.storage.s3 import S3StorageClient  # noqa: PLC0415

        client = S3StorageClient(staging.bucket, staging.creds or None)
        uploads: dict[str, dict[str, str]] = {}
        for rel in paths:
            key = _pin_rel(prefix, rel)
            url = client.presign_put(key, expires_in=int(effective_ttl.total_seconds()))
            uploads[rel] = {"method": "PUT", "url": url}
        return StagingGrant(
            kind="s3_presigned",
            org_id=str(org_id),
            run_id=str(run_id),
            bridge_id=str(bridge_id),
            prefix=prefix,
            base_uri=staging.base_uri(),
            expires_at=expires_at,
            uploads=uploads,
        )

    # Local / file staging store (self-host/dev): no presigner. Hand back the
    # pinned prefix; the agent writes through the StagingArea writer. Weaker
    # posture, documented (design §5).
    return StagingGrant(
        kind="local",
        org_id=str(org_id),
        run_id=str(run_id),
        bridge_id=str(bridge_id),
        prefix=prefix,
        base_uri=staging.base_uri(),
        expires_at=expires_at,
        uploads={},
    )
