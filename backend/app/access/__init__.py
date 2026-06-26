"""Access-control governance: access_grants store + scope resolution helpers.

The ``access_grants`` table (migration 0022) records explicit, org-scoped grants
of a ``(dimension, value)`` pair to a subject (a first-party user, a role, or an
embed-token ``sub``).  Grants are an OPTIONAL companion to token policies: a host
may either mint the policies directly into the embed token, or store grants here
and let ``GET /auth/scope`` merge them into the caller's effective policies.

SECURITY CONTRACT (enforced by ``grants_store`` and the routes):
- Every query is org-scoped (``WHERE org_id = $1``) — cross-org rows are
  invisible by construction.
- The org always comes from the VERIFIED token / caller's membership, never the
  request body.
- Writes are gated to approver roles (owner/admin), mirroring routes/admin.py.
- Cross-org access returns 404 (not 403) so a grant's existence never leaks
  across tenants.
"""

from __future__ import annotations
