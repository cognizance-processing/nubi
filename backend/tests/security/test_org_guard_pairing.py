"""The role guard on a route must match how that route resolves its org.

Every org-scoped route resolves an org and then checks the caller's role in an
org. If those are different orgs, the check is meaningless: a writer in their
default workspace who is only a *viewer* in workspace B passes
``require_writer_default`` and then writes to B, because the handler resolved B
from ``X-Org-Id``.

That mismatch was live across ai / bridges / data_browser / flows / access_grants
until 2026-08-26, and it is invisible in review because the two halves sit in
different places — the guard in the decorator, the resolution ten lines into the
body. This test makes the pairing a build-time invariant instead.

``app/auth/roles.py`` keeps both variants on purpose. A module that genuinely
means "the caller's own default org regardless of where they are working" may
use a ``*_default`` guard — but it must then resolve its org the same way, and
it must be listed in ``DEFAULT_ORG_MODULES`` below with a reason.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ROUTES = pathlib.Path(__file__).resolve().parents[2] / "app" / "routes"

#: Modules allowed to call ``get_user_org`` / use a ``*_default`` guard, each
#: with the reason. Adding an entry here is a deliberate decision, not a way to
#: silence the test — say why the DEFAULT org is the right answer.
DEFAULT_ORG_MODULES: dict[str, str] = {
    "ai.py": (
        "Correct already: _resolve_org_id() uses resolve_org_id whenever a "
        "Request is available and only falls back to get_user_org for "
        "request-less callers (background metering/attribution)."
    ),
    "query.py": (
        "Same shape as ai.py — _resolve_caller_org() prefers resolve_org_id and "
        "falls back only when no Request was passed."
    ),
    "watches.py": (
        "_feed_breach() dispatches a breach notification and may run from a "
        "background tick with no request at all. The RIGHT fix is to take the "
        "org from the watch rather than the evaluating user, which needs "
        "app.ai.watch.Watch to carry org_id; until then the default org is the "
        "only thing available. TODO: add Watch.org_id and scope it there."
    ),
}

DEFAULT_GUARDS = ("Depends(require_writer_default)", "Depends(require_approver_default)")


def _modules():
    return [p for p in sorted(ROUTES.glob("*.py")) if not p.name.startswith("_")]


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_default_org_guard_is_not_paired_with_header_aware_resolution(path):
    src = path.read_text()
    uses_default_guard = any(g in src for g in DEFAULT_GUARDS)
    if not uses_default_guard:
        return
    assert path.name in DEFAULT_ORG_MODULES, (
        f"{path.name} guards with a *_default role check. Either switch to the "
        f"header-aware guard (require_writer / require_approver), or add it to "
        f"DEFAULT_ORG_MODULES with the reason it genuinely wants the default org."
    )
    assert "resolve_org_id" not in src, (
        f"{path.name} checks the role in the caller's DEFAULT org but resolves "
        f"the acting org with resolve_org_id (the X-Org-Id header). A writer in "
        f"workspace A who is a viewer in workspace B would pass the guard and "
        f"write to B."
    )


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_handlers_that_resolve_an_org_do_not_use_the_default_helper(path):
    """``get_user_org`` ignores ``X-Org-Id`` — a route serving a user in a
    workspace must use ``resolve_org_id``, which verifies membership first."""
    # AST, not substring: a docstring mentioning the helper is not a call.
    tree = ast.parse(path.read_text())
    called = any(
        isinstance(n, ast.Call)
        and (getattr(n.func, "id", None) or getattr(n.func, "attr", None))
        in ("get_user_org", "_get_user_org")
        for n in ast.walk(tree)
    )
    if not called:
        return
    assert path.name in DEFAULT_ORG_MODULES, (
        f"{path.name} calls get_user_org(), which always returns the caller's "
        f"DEFAULT org and silently ignores X-Org-Id. Use resolve_org_id(...), or "
        f"add the module to DEFAULT_ORG_MODULES with a reason."
    )
