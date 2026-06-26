"""Scope resolution — compute a caller's effective RLS policies from the token.

``resolve_scope`` is the single source of truth behind ``GET /auth/scope``.  It
takes a VERIFIED identity (never request input) and produces:

- ``policies``            — the RAW policy claims carried by the token.
- ``effective_policies``  — the raw policies, hierarchy-expanded AND merged with
                            any non-expired ``access_grants`` for the subject,
                            normalised to ``{dimension: [values]}``.

SECURITY
--------
* The org and policies are taken ONLY from the verified token (caller may not
  override them via the request body).
* Org-scoped: hierarchy expansion and grant lookup both filter on the token's
  org_id — cross-org data cannot leak.
* FAIL-CLOSED: if expansion/merge raises for any reason, we return the NARROWER
  raw policies (never a widened set).  An empty/over-cap expansion never widens
  access because effective_policies is only ever a subset-or-equal of what the
  raw token already authorised plus explicitly-stored same-org grants.
"""

from __future__ import annotations

from typing import Any

from app.access.grants_store import get_grants_store
from app.auth.verify import VerifiedIdentity


def subject_for_identity(identity: VerifiedIdentity) -> tuple[str, str]:
    """Return ``(subject_type, subject_id)`` for *identity*.

    - First-party access token → ``("user", user_id)``.
    - Embed token              → ``("embed_sub", sub)``.

    The subject is derived ONLY from the verified token's ``kind`` / ``sub``.
    """
    if identity.kind == "embed":
        return "embed_sub", str(identity.user_id)
    return "user", str(identity.user_id)


def _as_value_list(value: Any) -> list[str]:
    """Normalise a policy value to a list of string values.

    - scalar  → ``[str(value)]``
    - list    → ``[str(v) for v in value]``
    - dict (range band) → ``[]`` (ranges are not value-enumerable; they stay in
      ``policies`` but are not merged into the value-list ``effective_policies``).
    """
    if value is None:
        # A null policy value is not an enumerable constraint — omit the
        # dimension rather than emitting the literal string "None" (which would
        # inject `col IN ('None')`).
        return []
    if isinstance(value, dict):
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return [str(value)]


async def resolve_scope(identity: VerifiedIdentity) -> dict[str, Any]:
    """Resolve the caller's effective scope from the verified *identity*.

    Returns a dict with ``org``, ``scope``, ``policies``, ``effective_policies``
    and ``expanded`` (whether effective differs from raw).
    """
    org_id = identity.org
    raw_policies: dict[str, Any] = dict(identity.policies or {})
    scope = list(identity.scope or [])

    # Baseline effective = raw policies normalised to value-lists.  This is the
    # value we fall back to if expansion/merge fails (NARROWER — never widened).
    baseline: dict[str, list[str]] = {
        col: _as_value_list(val) for col, val in raw_policies.items()
    }

    if not org_id:
        # No org context (e.g. an org-less first-party token mid-onboarding):
        # we cannot expand or look up grants safely — return raw, fail-closed.
        return {
            "org": org_id,
            "scope": scope,
            "policies": raw_policies,
            "effective_policies": baseline,
            "expanded": False,
        }

    effective: dict[str, list[str]] = {k: list(v) for k, v in baseline.items()}
    expanded = False

    # ── 1. Hierarchy expansion (org-scoped, token-only) ──────────────────────
    try:
        from app.connectors.planner import expand_rls_policies  # noqa: PLC0415

        hier = await expand_rls_policies(raw_policies, org_id)
        for col, val in hier.items():
            vals = _as_value_list(val)
            if vals:
                merged = list(dict.fromkeys([*effective.get(col, []), *vals]))
                if merged != effective.get(col):
                    expanded = True
                effective[col] = merged
    except Exception:  # noqa: BLE001 — fail-closed: keep narrower baseline
        effective = {k: list(v) for k, v in baseline.items()}
        expanded = False

    # ── 2. Merge non-expired access_grants for the subject (org-scoped) ───────
    try:
        subject_type, subject_id = subject_for_identity(identity)
        grants = await get_grants_store().effective_for_subject(
            org_id, subject_type, subject_id
        )
        for dim, values in grants.items():
            if not values:
                continue
            before = effective.get(dim, [])
            merged = list(dict.fromkeys([*before, *[str(v) for v in values]]))
            if merged != before:
                expanded = True
            effective[dim] = merged
    except Exception:  # noqa: BLE001 — fail-closed: grants merge is best-effort
        pass

    return {
        "org": org_id,
        "scope": scope,
        "policies": raw_policies,
        "effective_policies": effective,
        "expanded": expanded,
    }
