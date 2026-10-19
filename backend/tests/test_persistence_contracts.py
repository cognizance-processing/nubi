"""Structural "saved but gone after refresh" guards (hermetic, no live stack).

The live, end-to-end version is ``test_persistence_roundtrip.py`` (opt-in). These
are the cheap static/in-process checks for the same bug classes:

* a write body field the matching read model doesn't declare (FastAPI filters
  the response through ``response_model``, so the value is stored but never
  comes back — e.g. ``PatchOrgIn.avatar_url`` vs ``OrgDetailResponse``);
* a Pydantic spec model silently dropping keys the editor writes, because a
  server-side path persists ``model_dump()`` (``Widget.style`` / ``Widget.config``
  / ``DashboardSpec.background`` all went missing this way before);
* a route that returns a URL to a stored asset which then doesn't serve;
* a background side effect landing in the caller's DEFAULT org instead of the
  org it was asked to act in.
"""

from __future__ import annotations

import types
import uuid
from typing import Any, get_args, get_origin

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# 1. Write models vs. read models
# ---------------------------------------------------------------------------

#: Body fields that are write-only by design (credentials, confirmations).
_WRITE_ONLY = {"password", "secret", "auth_token", "token", "confirm_name", "api_key"}


def _model_fields(tp: Any) -> set[str] | None:
    """Fields of a response model, unwrapping ``{items: list[Item]}`` envelopes."""
    if not (isinstance(tp, type) and issubclass(tp, BaseModel)):
        return None
    fields = set(tp.model_fields)
    for f in tp.model_fields.values():
        ann = f.annotation
        if get_origin(ann) is list and get_args(ann):
            inner = _model_fields(get_args(ann)[0])
            if inner:
                fields |= inner
    return fields


def _write_routes(app) -> list[tuple[APIRoute, type[BaseModel]]]:
    out = []
    for r in app.routes:
        if not isinstance(r, APIRoute) or not (r.methods & {"PUT", "PATCH"}) or r.body_field is None:
            continue
        body = r.body_field.field_info.annotation
        if isinstance(body, type) and issubclass(body, BaseModel):
            out.append((r, body))
    return out


@pytest.mark.asyncio
async def test_every_writable_field_is_declared_on_the_typed_read_models(app):
    """For each PUT/PATCH with a typed body, every non-secret body field must be
    present on each TYPED read of that resource: the item GET (same path), the
    collection GET (parent path) and the write's own response model.

    Untyped (``dict``) responses can't be checked statically — the live round-trip
    covers those.
    """
    gets = {r.path: r for r in app.routes if isinstance(r, APIRoute) and "GET" in r.methods}
    checked = 0
    problems: list[str] = []
    for route, body in _write_routes(app):
        readers = {
            "own response": route.response_model,
            f"GET {route.path}": getattr(gets.get(route.path), "response_model", None),
            f"GET {route.path.rsplit('/', 1)[0]}": getattr(
                gets.get(route.path.rsplit("/", 1)[0]), "response_model", None
            ),
        }
        for label, model in readers.items():
            fields = _model_fields(model)
            if fields is None:
                continue
            checked += 1
            missing = set(body.model_fields) - fields - _WRITE_ONLY
            if missing:
                problems.append(f"{sorted(route.methods)} {route.path}: {sorted(missing)} not on {label} ({model.__name__})")
    assert checked, "no typed read models found — the check would be vacuous"
    assert not problems, "Written but never read back:\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# 2. Dashboard / flow specs keep what the editor writes
# ---------------------------------------------------------------------------

#: A spec carrying every key the board editor / renderer write that the
#: Pydantic model did not declare at the time of writing (found by auditing
#: ``src/editor`` + ``src/dashboards``), plus an arbitrary unknown key per level
#: so future editor fields are covered too.
_EDITOR_SPEC: dict[str, Any] = {
    "title": "Round trip",
    "description": "board description",
    "stylePreset": "glass",
    "tab_bar": {"variant": "pill", "accent": "#ff0000", "align": "center", "size": "sm"},
    "responsive": {"sm": {"w1": {"x": 1, "y": 1, "w": 4, "h": 2}}},
    "colorMode": "dark",
    "themeAdapt": True,
    "refresh_interval_ms": 60000,
    "future_board_key": {"anything": 1},
    "background": {"kind": "gradient"},
    "tabs": [{"id": "t1", "label": "One", "icon": "chart", "background": {"kind": "solid"}, "future_tab_key": 1}],
    "variables": [{"name": "region", "type": "text", "url_bind": True, "future_var_key": 1}],
    "widgets": [
        {
            "id": "w1",
            "type": "text",
            "content": "hello",
            "style": {"backgroundColor": "#000"},
            "config": {"k": "v"},
            "pos": {"x": 1, "y": 1, "w": 4, "h": 2, "static": True, "future_pos_key": 1},
            "future_widget_key": {"x": 1},
        }
    ],
}


def _assert_superset(original: Any, dumped: Any, path: str = "spec") -> None:
    if isinstance(original, dict):
        assert isinstance(dumped, dict), f"{path}: became {type(dumped).__name__}"
        for k, v in original.items():
            assert k in dumped, f"{path}.{k} was silently dropped"
            _assert_superset(v, dumped[k], f"{path}.{k}")
    elif isinstance(original, list):
        assert isinstance(dumped, list) and len(dumped) == len(original), f"{path}: list changed"
        for i, (a, b) in enumerate(zip(original, dumped)):
            _assert_superset(a, b, f"{path}[{i}]")
    else:
        assert dumped == original, f"{path}: {original!r} -> {dumped!r}"


def test_dashboard_spec_round_trip_keeps_every_editor_key():
    """``validate_spec(...).model_dump()`` is what AI/MCP board edits persist."""
    from app.dashboards.spec import validate_spec  # noqa: PLC0415

    parsed, issues = validate_spec(_EDITOR_SPEC)
    assert parsed is not None, issues
    _assert_superset(_EDITOR_SPEC, parsed.model_dump())


def test_flow_spec_round_trip_keeps_view_and_runtime_config():
    """PUT /flows persists ``validate_flow_spec(...).model_dump()``; the builder
    reopens in ``spec.view`` and reads ``runtime_config`` toggles from it."""
    from app.flows.spec import validate_flow_spec  # noqa: PLC0415

    raw = {
        "version": 1,
        "name": "f",
        "view": "notebook",
        "runtime_config": {"auto_rebuild_downstream": True},
        "tasks": [{"key": "a", "kind": "noop", "ui": {"x": 5, "y": 7}, "cell_type": "markdown"}],
    }
    spec, issues = validate_flow_spec(raw)
    assert spec is not None, issues
    _assert_superset(raw, spec.model_dump())


# ---------------------------------------------------------------------------
# 3. A returned asset URL actually serves
# ---------------------------------------------------------------------------

_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c4944415478da63f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)


@pytest.mark.asyncio
async def test_uploaded_image_url_is_servable(client, fake_db, monkeypatch, tmp_path):
    """POST /images returns a ``url``; fetching THAT url (no auth, like an
    ``<img src>``) must return the same bytes and type."""
    from app.auth.jwt import mint_access_token  # noqa: PLC0415

    monkeypatch.setenv("NUBI_IMAGE_STORAGE_DIR", str(tmp_path))
    uid = str(uuid.uuid4())
    fake_db.users[uid] = {
        "id": uid, "email": "img@example.com", "name": "I", "avatar_url": None,
        "email_verified": True, "created_at": "2024-01-01T00:00:00+00:00",
    }
    resp = await client.post(
        "/api/v1/images",
        files={"file": ("p.png", _PNG_1PX, "image/png")},
        headers={"Authorization": f"Bearer {mint_access_token(uid)}"},
    )
    assert resp.status_code == 201, resp.text
    url = resp.json()["url"]
    assert url.startswith("/"), url

    served = await client.get(url)
    assert served.status_code == 200, f"{url} -> {served.status_code}"
    assert served.content == _PNG_1PX
    assert served.headers["content-type"].startswith("image/png")


# ---------------------------------------------------------------------------
# 4. Side effects land in the org the caller acted in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watch_breach_feed_uses_the_watch_org_not_the_default(monkeypatch):
    """``POST /watches/{id}/evaluate`` resolves the watch in the X-Org-Id org; the
    in-app breach notification must go to that org's feed, not the evaluating
    user's default org."""
    from app.notify import dispatch  # noqa: PLC0415
    from app.routes import _org  # noqa: PLC0415
    from app.routes.watches import _feed_breach  # noqa: PLC0415

    sent: list[str] = []

    async def _notify(org_id, event):
        sent.append(org_id)

    async def _default_org(user_id, repo):
        return "default-org"

    monkeypatch.setattr(dispatch, "notify_event", _notify)
    monkeypatch.setattr(_org, "get_user_org", _default_org)
    watch = types.SimpleNamespace(id="w1", name="W", metric_id="m")
    identity = types.SimpleNamespace(user_id="u1")

    await _feed_breach({"breached": True}, watch, identity=identity, org_id="org-b")
    assert sent == ["org-b"]

    # Request-less callers (background tick) still fall back to the default org.
    await _feed_breach({"breached": True}, watch, identity=identity)
    assert sent == ["org-b", "default-org"]


# ---------------------------------------------------------------------------
# 5. jsonb columns come back as objects, not strings
# ---------------------------------------------------------------------------


def test_project_git_binding_reads_back_as_an_object():
    """asyncpg returns jsonb as text; the projects repo must decode ``git`` or a
    saved binding reads back as a string and ``/git/*`` reports "not connected"."""
    from app.repos.projects import _row_to_dict  # noqa: PLC0415

    row = _row_to_dict({"id": uuid.uuid4(), "git": '{"provider": "github", "repo_url": "https://x/r.git"}'})
    assert row["git"] == {"provider": "github", "repo_url": "https://x/r.git"}
    assert _row_to_dict({"git": None})["git"] is None
