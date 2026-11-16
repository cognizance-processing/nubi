"""Tests for REFERENCE-BASED widget reuse.

Coverage
--------
1. ``Widget`` model / ``validate_spec`` (pure):
   - a bare {id, ref, overrides, pos, tab_id} reference widget parses and
     validates with no hard errors.
   - a widget with neither 'type' nor 'ref' is a hard error (step 13).
   - 'ref' + a non-board-local inline field (query_id, encoding, ...) is a
     hard error (step 14); board-local fields (pos, tab_id, placement, order,
     drawer, drawer_group) do NOT conflict.
   - 'overrides' set without 'ref' is a soft '[warn]' (step 15), not a hard
     error.

2. ``app.dashboards.widget_refs.resolve_widget_refs`` (pure):
   - inline widgets pass through unchanged.
   - a ref widget resolves to library config merged with overrides.
   - sparse deep merge: only overridden keys change; nested dicts
     (encoding/props/style/params) merge key-by-key; lists in overrides
     REPLACE the library list wholesale, not concatenate.
   - board-local fields (id/pos/tab_id/placement/order/drawer/drawer_group)
     always come from the spec entry, even when the library config carries
     its own (stale) values for them.
   - a missing/deleted library row degrades to a visible broken placeholder
     widget (never raises) and appends a soft '[warn]' issue.
   - a library row with a non-dict/missing config also degrades cleanly.
   - resolving is a pure function: the input DashboardSpec is not mutated.

3. ``app.dashboards.widget_refs.widget_ref_usage`` (pure):
   - collects {widget_id, ref} pairs, optionally filtered to one ref id.
   - de-dupes and walks into stepper props.steps[].widget.

4. GET /widgets/{id}/usage and GET /queries/{id}/usage (route, InMemoryRepo):
   - counts + boards list widgets referencing a library id / query id.
   - boards with no matches are omitted; org-scoped (a board in a different
     org never appears).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.dashboards.spec import DashboardSpec, Widget, validate_spec
from app.dashboards.widget_refs import (
    resolve_widget_refs,
    widget_ref_usage,
)
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pos(x: int = 1, y: int = 1, w: int = 4, h: int = 3) -> dict[str, int]:
    return {"x": x, "y": y, "w": w, "h": h}


def _ref_widget(
    wid: str = "w1",
    *,
    ref: str = "lib1",
    overrides: dict[str, Any] | None = None,
    pos: dict[str, int] | None = None,
    tab_id: str | None = None,
    placement: str = "grid",
    order: int = 0,
    drawer: bool = False,
    drawer_group: str | None = None,
) -> dict[str, Any]:
    w: dict[str, Any] = {"id": wid, "ref": ref, "pos": pos or _pos()}
    if overrides is not None:
        w["overrides"] = overrides
    if tab_id is not None:
        w["tab_id"] = tab_id
    if placement != "grid":
        w["placement"] = placement
    if order:
        w["order"] = order
    if drawer:
        w["drawer"] = True
    if drawer_group:
        w["drawer_group"] = drawer_group
    return w


def _spec_dict(widgets: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": 1, "title": "T", "widgets": widgets}


# ---------------------------------------------------------------------------
# 1. Widget model / validate_spec
# ---------------------------------------------------------------------------


class TestRefValidation:
    def test_bare_reference_widget_parses_and_validates(self):
        spec, issues = validate_spec(_spec_dict([_ref_widget()]))
        assert spec is not None
        assert issues == []
        assert spec.widgets[0].ref == "lib1"
        assert spec.widgets[0].type is None

    def test_neither_type_nor_ref_is_hard_error(self):
        w = {"id": "w1", "pos": _pos()}
        spec, issues = validate_spec(_spec_dict([w]))
        assert spec is not None
        assert any(
            "must set either 'type'" in i and "w1" in i for i in issues
        )
        # Hard error, not prefixed [warn].
        assert not any(i.startswith("[warn]") for i in issues if "w1" in i)

    @pytest.mark.parametrize(
        "extra,field_name",
        [
            ({"query_id": "demo_all"}, "query_id"),
            ({"chart_type": "bar"}, "chart_type"),
            ({"encoding": {"x": "a", "y": "b"}}, "encoding"),
            ({"props": {"label": "x"}}, "props"),
            ({"style": {"color": "red"}}, "style"),
            ({"subtype": "select"}, "subtype"),
            ({"options_query_id": "q2"}, "options_query_id"),
            ({"target_var": "region"}, "target_var"),
            ({"content": "hi"}, "content"),
            ({"params": {"p": 1}}, "params"),
            ({"type": "kpi"}, "type"),
        ],
    )
    def test_ref_plus_inline_field_is_hard_error(self, extra, field_name):
        w = _ref_widget()
        w.update(extra)
        spec, issues = validate_spec(_spec_dict([w]))
        assert spec is not None
        matches = [i for i in issues if "w1" in i and "ref" in i and "must not be" in i]
        assert matches, f"expected a mutual-exclusivity error for {field_name}: {issues}"
        assert field_name in matches[0]

    def test_ref_plus_board_local_fields_is_fine(self):
        """pos/tab_id/placement/order/drawer/drawer_group never conflict with ref."""
        w = _ref_widget(
            pos=_pos(2, 2, 6, 4),
            tab_id="t1",
            placement="grid",
            order=3,
            drawer=True,
            drawer_group="filters",
        )
        spec, issues = validate_spec(
            {"version": 1, "title": "T", "tabs": [{"id": "t1", "label": "Tab 1"}], "widgets": [w]}
        )
        assert spec is not None
        assert issues == []

    def test_overrides_without_ref_is_soft_warning(self):
        w = {"id": "w1", "type": "text", "content": "hi", "pos": _pos(), "overrides": {"title": "x"}}
        spec, issues = validate_spec(_spec_dict([w]))
        assert spec is not None
        assert any(
            i.startswith("[warn]") and "overrides" in i and "w1" in i for i in issues
        )

    def test_overrides_with_ref_no_warning(self):
        w = _ref_widget(overrides={"props": {"label": "Custom"}})
        spec, issues = validate_spec(_spec_dict([w]))
        assert spec is not None
        assert issues == []


# ---------------------------------------------------------------------------
# 2. resolve_widget_refs
# ---------------------------------------------------------------------------


class TestResolveWidgetRefs:
    def _lib(self, config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {"lib1": {"id": "lib1", "config": config}}

    def test_inline_widgets_pass_through_unchanged(self):
        inline = {
            "id": "w1", "type": "kpi", "query_id": "q1", "pos": _pos(),
        }
        spec = DashboardSpec.model_validate(_spec_dict([inline]))
        resolved, issues = resolve_widget_refs(spec, {})
        assert issues == []
        assert resolved.widgets[0].id == "w1"
        assert resolved.widgets[0].query_id == "q1"

    def test_ref_resolves_to_merged_library_config(self):
        library = self._lib({
            "type": "kpi",
            "query_id": "lib_query",
            "encoding": {"value": "revenue"},
            "props": {"label": "Revenue", "format": "currency"},
        })
        w = _ref_widget()
        spec = DashboardSpec.model_validate(_spec_dict([w]))
        resolved, issues = resolve_widget_refs(spec, library)
        assert issues == []
        rw = resolved.widgets[0]
        assert rw.type == "kpi"
        assert rw.query_id == "lib_query"
        assert rw.props == {"label": "Revenue", "format": "currency"}

    def test_sparse_overrides_only_change_named_keys(self):
        library = self._lib({
            "type": "kpi",
            "query_id": "lib_query",
            "props": {"label": "Revenue", "format": "currency"},
        })
        w = _ref_widget(overrides={"props": {"label": "Custom Revenue"}})
        spec = DashboardSpec.model_validate(_spec_dict([w]))
        resolved, _ = resolve_widget_refs(spec, library)
        rw = resolved.widgets[0]
        # 'label' overridden, 'format' survives from the library config.
        assert rw.props == {"label": "Custom Revenue", "format": "currency"}
        # query_id untouched by overrides.
        assert rw.query_id == "lib_query"

    def test_nested_dict_merges_key_by_key_recursively(self):
        library = self._lib({
            "type": "chart",
            "chart_type": "bar",
            "query_id": "q1",
            "encoding": {"x": "month", "y": "revenue", "color": "region"},
        })
        w = _ref_widget(overrides={"encoding": {"y": "profit"}})
        spec = DashboardSpec.model_validate(_spec_dict([w]))
        resolved, _ = resolve_widget_refs(spec, library)
        assert resolved.widgets[0].encoding == {
            "x": "month", "y": "profit", "color": "region",
        }

    def test_list_values_replace_wholesale_not_concatenate(self):
        library = self._lib({
            "type": "table",
            "query_id": "q1",
            "props": {"columns": ["a", "b", "c"]},
        })
        w = _ref_widget(overrides={"props": {"columns": ["x"]}})
        spec = DashboardSpec.model_validate(_spec_dict([w]))
        resolved, _ = resolve_widget_refs(spec, library)
        assert resolved.widgets[0].props["columns"] == ["x"]

    def test_board_local_fields_always_forced_from_spec_entry(self):
        # Library config carries its OWN (stale) board-local-looking values —
        # none of them may survive into the resolved widget.
        library = self._lib({
            "type": "kpi",
            "query_id": "q1",
            "id": "STALE_ID",
            "pos": {"x": 99, "y": 99, "w": 1, "h": 1},
            "tab_id": "STALE_TAB",
            "placement": "header",
            "order": 999,
            "drawer": True,
            "drawer_group": "STALE_GROUP",
        })
        w = _ref_widget(
            wid="w1", pos=_pos(2, 3, 5, 6), tab_id="t1", order=7,
        )
        spec = DashboardSpec.model_validate(
            {"version": 1, "title": "T", "tabs": [{"id": "t1", "label": "T1"}], "widgets": [w]}
        )
        resolved, _ = resolve_widget_refs(spec, library)
        rw = resolved.widgets[0]
        assert rw.id == "w1"
        assert rw.pos.model_dump() == _pos(2, 3, 5, 6)
        assert rw.tab_id == "t1"
        assert rw.placement == "grid"
        assert rw.order == 7
        assert rw.drawer is False
        assert rw.drawer_group is None

    def test_missing_library_row_degrades_to_broken_placeholder(self):
        w = _ref_widget(ref="does_not_exist")
        spec = DashboardSpec.model_validate(_spec_dict([w]))
        resolved, issues = resolve_widget_refs(spec, {})
        assert len(issues) == 1
        assert issues[0].startswith("[warn]")
        assert "does_not_exist" in issues[0]
        rw = resolved.widgets[0]
        # Still occupies its slot; never raises; visibly broken.
        assert rw.id == "w1"
        assert rw.type == "text"
        assert rw.pos.model_dump() == _pos()
        assert "does_not_exist" in (rw.content or "")

    def test_library_row_with_non_dict_config_degrades_cleanly(self):
        w = _ref_widget()
        library = {"lib1": {"id": "lib1", "config": "not-a-dict"}}
        spec = DashboardSpec.model_validate(_spec_dict([w]))
        resolved, issues = resolve_widget_refs(spec, library)
        assert len(issues) == 1
        assert resolved.widgets[0].type == "text"

    def test_library_config_missing_type_degrades_cleanly(self):
        w = _ref_widget()
        library = {"lib1": {"id": "lib1", "config": {"query_id": "q1"}}}
        spec = DashboardSpec.model_validate(_spec_dict([w]))
        resolved, issues = resolve_widget_refs(spec, library)
        assert len(issues) == 1
        assert "without a widget 'type'" in issues[0]
        assert resolved.widgets[0].type == "text"

    def test_does_not_mutate_input_spec(self):
        library = self._lib({"type": "kpi", "query_id": "q1"})
        w = _ref_widget()
        spec = DashboardSpec.model_validate(_spec_dict([w]))
        resolve_widget_refs(spec, library)
        # The original spec's widget is still the unresolved ref entry.
        assert spec.widgets[0].ref == "lib1"
        assert spec.widgets[0].type is None

    def test_mixed_inline_and_ref_widgets(self):
        inline = {"id": "w0", "type": "text", "content": "hi", "pos": _pos()}
        ref = _ref_widget(wid="w1")
        library = self._lib({"type": "kpi", "query_id": "q1"})
        spec = DashboardSpec.model_validate(_spec_dict([inline, ref]))
        resolved, issues = resolve_widget_refs(spec, library)
        assert issues == []
        assert [w.id for w in resolved.widgets] == ["w0", "w1"]
        assert resolved.widgets[0].type == "text"
        assert resolved.widgets[1].type == "kpi"


# ---------------------------------------------------------------------------
# 3. widget_ref_usage
# ---------------------------------------------------------------------------


class TestWidgetRefUsage:
    def test_collects_all_ref_widgets_when_unfiltered(self):
        spec = _spec_dict([_ref_widget("w1", ref="lib1"), _ref_widget("w2", ref="lib2")])
        targets = widget_ref_usage(spec)
        assert {"widget_id": "w1", "ref": "lib1"} in targets
        assert {"widget_id": "w2", "ref": "lib2"} in targets

    def test_filters_to_only_ref_id(self):
        spec = _spec_dict([_ref_widget("w1", ref="lib1"), _ref_widget("w2", ref="lib2")])
        targets = widget_ref_usage(spec, only_ref_id="lib1")
        assert targets == [{"widget_id": "w1", "ref": "lib1"}]

    def test_ignores_inline_widgets(self):
        inline = {"id": "w0", "type": "text", "content": "hi", "pos": _pos()}
        spec = _spec_dict([inline, _ref_widget("w1", ref="lib1")])
        targets = widget_ref_usage(spec)
        assert targets == [{"widget_id": "w1", "ref": "lib1"}]

    def test_walks_into_stepper_children(self):
        stepper = {
            "id": "s1",
            "type": "stepper",
            "pos": _pos(),
            "props": {
                "steps": [
                    {"widget": _ref_widget("child1", ref="lib1")},
                    {"widget": {"id": "child2", "type": "text", "content": "hi"}},
                ]
            },
        }
        spec = _spec_dict([stepper])
        targets = widget_ref_usage(spec)
        assert targets == [{"widget_id": "child1", "ref": "lib1"}]

    def test_empty_spec_returns_empty_list(self):
        assert widget_ref_usage({"widgets": []}) == []
        assert widget_ref_usage({}) == []


# ---------------------------------------------------------------------------
# 4. GET /widgets/{id}/usage, GET /queries/{id}/usage (route level)
# ---------------------------------------------------------------------------


def _make_user(user_id: str, email: str = "alice@example.com") -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def usage_app(app):
    repo = InMemoryRepo()
    set_repo(repo)
    yield app, repo
    set_repo(None)


@pytest_asyncio.fixture
async def usage_client(usage_app, fake_db):
    app, repo = usage_app

    alice_id = str(uuid.uuid4())
    alice_org_id = str(uuid.uuid4())
    fake_db.users[alice_id] = _make_user(alice_id)
    repo.seed_org_member(org_id=alice_org_id, user_id=alice_id)

    # A second org/user to prove usage scanning stays org-scoped.
    bob_id = str(uuid.uuid4())
    bob_org_id = str(uuid.uuid4())
    fake_db.users[bob_id] = _make_user(bob_id, email="bob@example.com")
    repo.seed_org_member(org_id=bob_org_id, user_id=bob_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False,
    ) as ac:
        yield ac, alice_id, alice_org_id, bob_id, bob_org_id, repo


class TestUsageEndpoints:
    @pytest.mark.asyncio
    async def test_widget_usage_counts_across_boards(self, usage_client):
        client, alice_id, _org, _bob_id, _bob_org, _repo = usage_client
        headers = _auth_headers(alice_id)

        spec_a = _spec_dict([_ref_widget("w1", ref="lib1"), _ref_widget("w2", ref="lib1")])
        spec_b = _spec_dict([_ref_widget("w3", ref="lib1")])
        spec_c = _spec_dict([_ref_widget("w4", ref="lib_other")])

        for name, spec in (("Board A", spec_a), ("Board B", spec_b), ("Board C", spec_c)):
            resp = await client.post(
                "/api/v1/boards",
                json={"name": name, "config": {"spec": spec}},
                headers=headers,
            )
            assert resp.status_code == 201, resp.text

        resp = await client.get("/api/v1/widgets/lib1/usage", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["widget_id"] == "lib1"
        assert body["count"] == 3
        board_names = {b["board_name"] for b in body["boards"]}
        assert board_names == {"Board A", "Board B"}
        board_a = next(b for b in body["boards"] if b["board_name"] == "Board A")
        assert sorted(board_a["widget_ids"]) == ["w1", "w2"]

    @pytest.mark.asyncio
    async def test_widget_usage_is_org_scoped(self, usage_client):
        client, alice_id, _org, bob_id, _bob_org, _repo = usage_client

        spec = _spec_dict([_ref_widget("w1", ref="shared_lib_id")])
        resp = await client.post(
            "/api/v1/boards",
            json={"name": "Alice Board", "config": {"spec": spec}},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201

        # Bob (different org) must see zero usage for the same library id.
        resp = await client.get(
            "/api/v1/widgets/shared_lib_id/usage", headers=_auth_headers(bob_id)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["boards"] == []

    @pytest.mark.asyncio
    async def test_widget_usage_with_no_matches_returns_zero(self, usage_client):
        client, alice_id, _org, _bob_id, _bob_org, _repo = usage_client
        resp = await client.get(
            "/api/v1/widgets/nonexistent/usage", headers=_auth_headers(alice_id)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"widget_id": "nonexistent", "count": 0, "boards": []}

    @pytest.mark.asyncio
    async def test_query_usage_counts_across_boards(self, usage_client):
        client, alice_id, _org, _bob_id, _bob_org, _repo = usage_client
        headers = _auth_headers(alice_id)

        spec_a = _spec_dict([
            {"id": "w1", "type": "kpi", "query_id": "q1", "pos": _pos()},
            {"id": "w2", "type": "table", "query_id": "q2", "pos": _pos()},
        ])
        spec_b = _spec_dict([
            {"id": "w3", "type": "chart", "chart_type": "bar", "query_id": "q1",
             "encoding": {"x": "a", "y": "b"}, "pos": _pos()},
        ])

        for name, spec in (("Board A", spec_a), ("Board B", spec_b)):
            resp = await client.post(
                "/api/v1/boards",
                json={"name": name, "config": {"spec": spec}},
                headers=headers,
            )
            assert resp.status_code == 201, resp.text

        resp = await client.get("/api/v1/queries/q1/usage", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["query_id"] == "q1"
        assert body["count"] == 2
        board_names = {b["board_name"] for b in body["boards"]}
        assert board_names == {"Board A", "Board B"}

    @pytest.mark.asyncio
    async def test_usage_requires_auth(self, usage_client):
        client, *_ = usage_client
        resp = await client.get("/api/v1/widgets/lib1/usage")
        assert resp.status_code == 401
        resp = await client.get("/api/v1/queries/q1/usage")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 5. Consumer wiring: resolve_board_spec / resolve_spec_refs / collect_board_data
#    (OUTPUT consumers must resolve; the board READ endpoint must NOT)
# ---------------------------------------------------------------------------


from app.dashboards.collect import collect_board_data, resolve_board_spec, resolve_spec_refs  # noqa: E402


@pytest_asyncio.fixture
async def wiring_repo():
    repo = InMemoryRepo()
    set_repo(repo)
    yield repo
    set_repo(None)


class TestResolveBoardSpecFastPath:
    @pytest.mark.asyncio
    async def test_no_ref_widgets_returns_same_object_unchanged(self, wiring_repo):
        """The fast path must be a true no-op: same dict, zero library lookups."""
        spec_dict = _spec_dict([
            {"id": "w1", "type": "kpi", "query_id": "q1", "pos": _pos()},
        ])
        board = {"id": "b1", "config": {"spec": spec_dict}}

        calls: list[Any] = []
        original_list = wiring_repo.list

        async def _spy_list(*args, **kwargs):
            calls.append(args)
            return await original_list(*args, **kwargs)

        wiring_repo.list = _spy_list  # type: ignore[method-assign]
        result = await resolve_board_spec(board, "org-1", wiring_repo)
        assert result is spec_dict
        assert calls == []  # never touched the widgets library table

    @pytest.mark.asyncio
    async def test_empty_widgets_returns_same_object(self, wiring_repo):
        spec_dict = {"title": "T", "widgets": []}
        board = {"id": "b1", "config": {"spec": spec_dict}}
        result = await resolve_board_spec(board, "org-1", wiring_repo)
        assert result is spec_dict


class TestResolveBoardSpecResolvesRefs:
    @pytest.mark.asyncio
    async def test_resolves_ref_against_seeded_library_row(self, wiring_repo):
        await wiring_repo.create(
            "widgets", org_id="org-1", created_by="u1", name="Revenue KPI",
            config={"type": "kpi", "query_id": "lib_query", "props": {"label": "Revenue"}},
            id="lib1",
        )
        spec_dict = _spec_dict([_ref_widget("w1", ref="lib1")])
        board = {"id": "b1", "config": {"spec": spec_dict}}

        result = await resolve_board_spec(board, "org-1", wiring_repo)
        assert result is not spec_dict
        rw = result["widgets"][0]
        assert rw["id"] == "w1"
        assert rw["type"] == "kpi"
        assert rw["query_id"] == "lib_query"
        assert "ref" not in rw or rw.get("ref") is None

    @pytest.mark.asyncio
    async def test_dangling_ref_degrades_to_broken_widget_not_a_crash(self, wiring_repo, caplog):
        spec_dict = _spec_dict([_ref_widget("w1", ref="ghost")])
        board = {"id": "b1", "config": {"spec": spec_dict}}

        with caplog.at_level("WARNING"):
            result = await resolve_board_spec(board, "org-1", wiring_repo)

        rw = result["widgets"][0]
        assert rw["type"] == "text"
        assert rw["id"] == "w1"
        assert any("ghost" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_org_scoped_a_library_row_in_another_org_is_not_visible(self, wiring_repo):
        await wiring_repo.create(
            "widgets", org_id="org-OTHER", created_by="u1", name="Foreign",
            config={"type": "kpi", "query_id": "q"}, id="lib1",
        )
        spec_dict = _spec_dict([_ref_widget("w1", ref="lib1")])
        board = {"id": "b1", "config": {"spec": spec_dict}}

        result = await resolve_board_spec(board, "org-1", wiring_repo)
        # lib1 is invisible from org-1 → same as a missing ref.
        assert result["widgets"][0]["type"] == "text"

    @pytest.mark.asyncio
    async def test_resolve_spec_refs_dict_level_helper_matches_board_level(self, wiring_repo):
        await wiring_repo.create(
            "widgets", org_id="org-1", created_by="u1", name="X",
            config={"type": "table", "query_id": "q1"}, id="lib1",
        )
        spec_dict = _spec_dict([_ref_widget("w1", ref="lib1")])
        resolved = await resolve_spec_refs(spec_dict, "org-1", wiring_repo, log_label="b1")
        assert resolved["widgets"][0]["type"] == "table"
        assert resolved["widgets"][0]["query_id"] == "q1"


class TestCollectBoardDataResolvesRefs:
    @pytest.mark.asyncio
    async def test_ref_widget_query_id_is_collected(self, wiring_repo):
        """Decisive end-to-end check: without resolution, widget_query_targets
        would see query_id="" on a ref widget and silently DROP it (targets=[]),
        so collect_board_data would report zero entries. With resolution wired
        in (collect.py's collect_board_data -> resolve_board_spec), the widget
        appears — here with an 'error' key because 'lib_query' is not a
        registered query, which still proves its query_id was picked up.
        """
        await wiring_repo.create(
            "widgets", org_id="org-1", created_by="u1", name="KPI",
            config={"type": "kpi", "query_id": "lib_query_not_registered"},
            id="lib1",
        )
        spec_dict = _spec_dict([_ref_widget("w1", ref="lib1")])
        board = {
            "id": "b1",
            "org_id": "org-1",
            "config": {"spec": spec_dict},
        }

        out = await collect_board_data("b1", "org-1", claims={}, repo=wiring_repo, board=board)
        assert len(out) == 1
        assert out[0]["widget_id"] == "w1"
        assert out[0]["query_id"] == "lib_query_not_registered"
        # Query isn't registered, so it best-effort errors rather than
        # silently vanishing — the point being it was ATTEMPTED at all.
        assert "error" in out[0]

    @pytest.mark.asyncio
    async def test_board_with_no_refs_is_unaffected(self, wiring_repo):
        spec_dict = _spec_dict([
            {"id": "w1", "type": "kpi", "query_id": "still_not_registered", "pos": _pos()},
        ])
        board = {"id": "b1", "org_id": "org-1", "config": {"spec": spec_dict}}
        out = await collect_board_data("b1", "org-1", claims={}, repo=wiring_repo, board=board)
        assert len(out) == 1
        assert out[0]["widget_id"] == "w1"


class TestBoardReadEndpointDoesNotResolve:
    """The editor's GET /boards/{id} must return `ref`/`overrides` untouched —
    resolving there would make the reference invisible/uneditable in the UI.
    """

    @pytest.mark.asyncio
    async def test_get_board_returns_ref_and_overrides_unresolved(self, usage_client):
        client, alice_id, _org, _bob_id, _bob_org, _repo = usage_client
        headers = _auth_headers(alice_id)

        spec_dict = _spec_dict([_ref_widget("w1", ref="lib1", overrides={"props": {"label": "X"}})])
        create_resp = await client.post(
            "/api/v1/boards",
            json={"name": "Ref Board", "config": {"spec": spec_dict}},
            headers=headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        board_id = create_resp.json()["id"]

        get_resp = await client.get(f"/api/v1/boards/{board_id}", headers=headers)
        assert get_resp.status_code == 200
        got_widgets = get_resp.json()["config"]["spec"]["widgets"]
        assert got_widgets[0]["ref"] == "lib1"
        assert got_widgets[0]["overrides"] == {"props": {"label": "X"}}
        # And crucially: type/query_id are NOT filled in from any library row —
        # the read path never touched the widgets library table at all.
        assert "type" not in got_widgets[0] or got_widgets[0]["type"] is None
