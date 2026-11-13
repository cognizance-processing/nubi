"""Provider-bound widgets (BET-3 ``widget.source``) in server-side collection.

Covers:
1. widget_provider_targets — picks source-bound widgets without query_id,
   walks stepper children, ignores query widgets / incomplete bindings.
2. widget_query_targets is unchanged (provider-only widgets never appear).
3. collect_board_data(include_providers=False) — default — never resolves
   providers (snapshots / thumbnails / CSV / JSON unchanged).
4. include_providers=True — one resolve per distinct provider, rows sliced per
   widget result, RLS from claims, metering not skipped, inline errors.
5. The collected entry feeds _image_effective_url like a query entry does.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")

import unittest.mock as mock
from typing import Any

import pyarrow as pa
import pytest

from app.dashboards.collect import (
    collect_board_data,
    widget_provider_targets,
    widget_query_targets,
)
from app.errors import AppError

_ORG = "org-provider-test"
_BOARD_ID = "board-p"

_IMG = {
    "id": "img1",
    "type": "image",
    "source": {"provider": "p1", "result": "logos"},
    "encoding": {"value": "url"},
    "props": {"source": "query", "row": 1},
}

_SPEC: dict[str, Any] = {
    "title": "Provider board",
    "widgets": [
        _IMG,
        # Same provider, other result — must share one resolve.
        {"id": "t1", "type": "table", "source": {"provider": "p1", "result": "other"}},
        # Second provider.
        {"id": "k1", "type": "kpi", "source": {"provider": "p2", "result": "kpis"}},
        # Incomplete binding — ignored.
        {"id": "bad", "type": "kpi", "source": {"provider": "p2"}},
        # Stepper child bound to a provider.
        {
            "id": "st",
            "type": "stepper",
            "props": {"steps": [{"widget": {"id": "child", "type": "image",
                                            "source": {"provider": "p2", "result": "kpis"}}}]},
        },
    ],
}

_BOARD_ROW: dict[str, Any] = {
    "id": _BOARD_ID,
    "org_id": _ORG,
    "name": "Provider board",
    "config": {"spec": _SPEC},
}


def test_widget_provider_targets_picks_source_bound_widgets() -> None:
    targets = widget_provider_targets(_SPEC)
    assert targets == [
        {"widget_id": "img1", "provider": "p1", "result": "logos"},
        {"widget_id": "t1", "provider": "p1", "result": "other"},
        {"widget_id": "k1", "provider": "p2", "result": "kpis"},
        {"widget_id": "child", "provider": "p2", "result": "kpis"},
    ]


def test_widget_provider_targets_skips_query_bound_widgets() -> None:
    spec = {"widgets": [{"id": "q", "type": "image", "query_id": "q1",
                         "source": {"provider": "p1", "result": "r"}}]}
    assert widget_provider_targets(spec) == []
    assert widget_provider_targets({}) == []


def test_widget_query_targets_unchanged_for_provider_widgets() -> None:
    assert widget_query_targets(_SPEC, None) == []


def _tables(provider_id: str) -> dict[str, pa.Table]:
    if provider_id == "p1":
        return {
            "logos": pa.table({"name": ["a", "b"],
                               "url": ["/api/v1/images/a.png", "/api/v1/images/b.png"]}),
            "other": pa.table({"x": [1]}),
        }
    return {"kpis": pa.table({"v": [42]})}


@pytest.mark.asyncio
async def test_include_providers_off_by_default() -> None:
    with mock.patch("app.dashboards.board_data.resolve_provider_data") as rpd:
        out = await collect_board_data(
            _BOARD_ID, _ORG, claims={}, repo=mock.MagicMock(), board=_BOARD_ROW
        )
    rpd.assert_not_called()
    assert out == []


@pytest.mark.asyncio
async def test_include_providers_collects_rows_once_per_provider() -> None:
    calls: list[dict[str, Any]] = []

    async def _fake(**kwargs: Any) -> dict[str, pa.Table]:
        calls.append(kwargs)
        return _tables(kwargs["provider_id"])

    claims = {"policies": {"tenant": "t-1"}}
    with mock.patch("app.dashboards.board_data.resolve_provider_data", side_effect=_fake):
        out = await collect_board_data(
            _BOARD_ID, _ORG, claims=claims, repo=mock.MagicMock(),
            board=_BOARD_ROW, include_providers=True,
        )

    assert sorted(c["provider_id"] for c in calls) == ["p1", "p2"]
    for c in calls:
        assert c["board_id"] == _BOARD_ID
        assert c["org_id"] == _ORG
        assert c["params"] == {}
        assert c["claims"]["policies"] == {"tenant": "t-1"}
        assert c["skip_metering"] is False

    by_id = {e["widget_id"]: e for e in out}
    assert set(by_id) == {"img1", "t1", "k1", "child"}
    img = by_id["img1"]
    assert img["query_id"] is None
    assert img["provider"] == "p1" and img["result"] == "logos"
    assert img["columns"] == ["name", "url"]
    assert img["rows"] == [["a", "/api/v1/images/a.png"], ["b", "/api/v1/images/b.png"]]
    assert by_id["child"]["rows"] == [[42]]


@pytest.mark.asyncio
async def test_include_providers_ignored_with_only_query_id() -> None:
    with mock.patch("app.dashboards.board_data.resolve_provider_data") as rpd:
        await collect_board_data(
            _BOARD_ID, _ORG, claims={}, repo=mock.MagicMock(), board=_BOARD_ROW,
            only_query_id="q1", include_providers=True,
        )
    rpd.assert_not_called()


@pytest.mark.asyncio
async def test_provider_errors_are_inline() -> None:
    async def _fake(**kwargs: Any) -> dict[str, pa.Table]:
        if kwargs["provider_id"] == "p2":
            raise AppError("provider_not_found", "nope", 404)
        return {"logos": pa.table({"url": ["/api/v1/images/a.png"]})}  # no "other"

    with mock.patch("app.dashboards.board_data.resolve_provider_data", side_effect=_fake):
        out = await collect_board_data(
            _BOARD_ID, _ORG, claims={}, repo=mock.MagicMock(),
            board=_BOARD_ROW, include_providers=True,
        )

    by_id = {e["widget_id"]: e for e in out}
    assert "error" not in by_id["img1"]
    assert by_id["t1"]["error"] == "provider_result_not_found"
    assert by_id["k1"]["error"] == "provider_not_found"
    assert by_id["child"]["error"] == "provider_not_found"


@pytest.mark.asyncio
async def test_provider_entry_feeds_image_effective_url() -> None:
    from app.dashboards.svg_render import _image_effective_url

    async def _fake(**kwargs: Any) -> dict[str, pa.Table]:
        return {
            "logos": pa.table({"url": ["/api/v1/images/a.png", "https://example.com/b.png"]}),
            "other": pa.table({"x": [1]}),
            "kpis": pa.table({"v": [1]}),
        }

    with mock.patch("app.dashboards.board_data.resolve_provider_data", side_effect=_fake):
        out = await collect_board_data(
            _BOARD_ID, _ORG, claims={}, repo=mock.MagicMock(),
            board=_BOARD_ROW, include_providers=True,
        )
    img = next(e for e in out if e["widget_id"] == "img1")
    props, enc = _IMG["props"], _IMG["encoding"]
    # Row 1 is an external URL: resolved as-is here; inlining rules
    # (_resolve_image_data_uri) decide placeholder vs data URI downstream.
    assert _image_effective_url(props, enc, img["columns"], img["rows"]) == "https://example.com/b.png"
    assert _image_effective_url({**props, "row": 0}, enc, img["columns"], img["rows"]) == "/api/v1/images/a.png"
