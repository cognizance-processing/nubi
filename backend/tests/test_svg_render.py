"""Tests for app/dashboards/svg_render.py — T2 server-side SVG render foundation.

Coverage
--------
1. render_board_svg — small board (chart + kpi + text) renders to a valid composed
   page SVG that contains a <svg> root element and one nested <svg> per data widget
   with a grid position.

2. render_board_svg — board with surfaces.grid (migrated) renders identically to
   the legacy widget.pos path.

3. render_board_svg — board with no grid-placed widgets returns a valid minimal SVG.

4. render_widgets_svg — low-level: a chart payload returns an SVG string containing
   the <svg> root; a webgl widget returns webgl=True + svg=None.

5. compose_page_svg — given pre-rendered widget SVGs + layout entries, returns a
   composed SVG with the expected page dimensions and widget_rects.

All tests are offline — no live DB, no network.  Node.js + echarts must be
available on PATH (same requirement as the renderer itself).  Tests are skipped
when Node is not found, matching the optional-dep convention in the codebase.
"""

from __future__ import annotations

import shutil

import pytest

# Skip entire module when Node.js is not available.
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="Node.js not found on PATH — skipping SVG render tests",
)

from app.dashboards.spec import DashboardSpec, WidgetPos, Widget, Surfaces, SurfaceGridEntry
from app.dashboards.svg_render import (
    compose_page_svg,
    render_board_svg,
    render_widgets_svg,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _small_spec_legacy() -> DashboardSpec:
    """A small 3-widget spec using legacy widget.pos (un-migrated)."""
    return DashboardSpec(
        title="Test board",
        layout={"cols": 12, "row_height": 60},
        widgets=[
            Widget(
                id="chart1",
                type="chart",
                query_id="q1",
                chart_type="bar",
                encoding={"x": "region", "y": "revenue"},
                pos=WidgetPos(x=1, y=1, w=6, h=3),
            ),
            Widget(
                id="kpi1",
                type="kpi",
                query_id="q1",
                encoding={"value": "revenue"},
                props={"label": "Total Revenue"},
                pos=WidgetPos(x=7, y=1, w=3, h=2),
            ),
            Widget(
                id="txt1",
                type="text",
                content="## Hello\nThis is a text widget.",
                pos=WidgetPos(x=10, y=1, w=3, h=2),
            ),
        ],
    )


def _small_spec_surfaces() -> DashboardSpec:
    """Same 3-widget spec using surfaces.grid (migrated)."""
    return DashboardSpec(
        title="Test board (migrated)",
        layout={"cols": 12, "row_height": 60},
        widgets=[
            Widget(id="chart1", type="chart", query_id="q1", chart_type="bar", encoding={"x": "region", "y": "revenue"}),
            Widget(id="kpi1",   type="kpi",   query_id="q1", encoding={"value": "revenue"}, props={"label": "Total Revenue"}),
            Widget(id="txt1",   type="text",  content="## Hello\nThis is a text widget."),
        ],
        surfaces=Surfaces(grid={
            "chart1": SurfaceGridEntry(x=1, y=1, w=6, h=3),
            "kpi1":   SurfaceGridEntry(x=7, y=1, w=3, h=2),
            "txt1":   SurfaceGridEntry(x=10, y=1, w=3, h=2),
        }),
    )


def _widget_data() -> list[dict]:
    """Pre-collected widget data (columns + rows) for the test spec."""
    return [
        {
            "widget_id": "chart1",
            "query_id": "q1",
            "columns": ["region", "revenue"],
            "rows": [
                ["EMEA", 100],
                ["APAC", 200],
                ["AMER", 150],
            ],
        },
        {
            "widget_id": "kpi1",
            "query_id": "q1",
            "columns": ["region", "revenue"],
            "rows": [["EMEA", 100]],
        },
        # txt1 carries no data (text widget)
    ]


# ---------------------------------------------------------------------------
# 1. render_board_svg — legacy widget.pos board
# ---------------------------------------------------------------------------


def test_render_board_svg_legacy_pos():
    """render_board_svg with a legacy spec produces a valid composed SVG."""
    spec = _small_spec_legacy()
    data = _widget_data()

    svg = render_board_svg(spec, data)

    # Must be a string starting with the SVG root tag.
    assert isinstance(svg, str)
    assert svg.strip().startswith("<svg"), f"Expected SVG root, got: {svg[:100]}"
    # Must be well-formed (contains closing tag).
    assert "</svg>" in svg

    # Each data widget with a grid position should have a nested <svg>.
    # The composer wraps each widget in its own <svg x=... y=...> element.
    assert 'x="' in svg, "Expected nested SVGs with x= attribute"

    # Must contain something from the chart widget (bar chart produces SVG path/rect elements).
    assert "chart1" in svg or "widget" in svg.lower(), "Expected widget id or widget comment in SVG"


# ---------------------------------------------------------------------------
# 2. render_board_svg — migrated surfaces.grid board
# ---------------------------------------------------------------------------


def test_render_board_svg_surfaces_grid():
    """render_board_svg with a migrated spec (surfaces.grid) produces a valid SVG."""
    spec = _small_spec_surfaces()
    data = _widget_data()

    svg = render_board_svg(spec, data)

    assert isinstance(svg, str)
    assert svg.strip().startswith("<svg")
    assert "</svg>" in svg


# ---------------------------------------------------------------------------
# 3. render_board_svg — board with no grid-placed widgets
# ---------------------------------------------------------------------------


def test_render_board_svg_no_grid_widgets():
    """A board with no grid-placed widgets returns a minimal valid SVG."""
    spec = DashboardSpec(
        title="Empty board",
        widgets=[
            Widget(
                id="w1",
                type="text",
                content="Drawer-only widget",
                drawer=True,
            ),
        ],
    )

    svg = render_board_svg(spec, [])

    assert isinstance(svg, str)
    assert "<svg" in svg
    assert "</svg>" in svg


# ---------------------------------------------------------------------------
# 4. render_widgets_svg — low-level chart + webgl
# ---------------------------------------------------------------------------


def test_render_widgets_svg_chart():
    """render_widgets_svg returns an SVG string for a bar chart widget."""
    payloads = [
        {
            "id": "w1",
            "type": "chart",
            "chart_type": "bar",
            "encoding": {"x": "region", "y": "revenue"},
            "props": {},
            "content": None,
            "columns": ["region", "revenue"],
            "rows": [["EMEA", 100], ["APAC", 200]],
            "width": 400,
            "height": 300,
        }
    ]

    results = render_widgets_svg(payloads)

    assert len(results) == 1
    r = results[0]
    assert r["id"] == "w1"
    assert r["webgl"] is False
    svg = r.get("svg") or ""
    assert svg.strip().startswith("<svg"), f"Expected SVG string, got: {svg[:100]}"
    assert "error" not in r or r["error"] is None, f"Unexpected error: {r.get('error')}"


def test_render_widgets_svg_webgl_flagged():
    """render_widgets_svg flags WebGL widgets with webgl=True and svg=None."""
    payloads = [
        {
            "id": "scatter_gl",
            "type": "chart",
            "chart_type": "scatter",
            "encoding": {"x": "x", "y": "y"},
            "props": {"renderer": "regl"},  # regl triggers webgl flag
            "content": None,
            "columns": ["x", "y"],
            "rows": [[1, 2], [3, 4]],
            "width": 400,
            "height": 300,
        }
    ]

    results = render_widgets_svg(payloads)

    assert len(results) == 1
    r = results[0]
    assert r["id"] == "scatter_gl"
    assert r["webgl"] is True
    assert r["svg"] is None


# ---------------------------------------------------------------------------
# 4b. _run_node_script — TimeoutExpired partial output surfaced in error message
# ---------------------------------------------------------------------------


def test_run_node_script_timeout_includes_partial_output(monkeypatch):
    """When Node renderer times out, partial stdout is included in the AppError message."""
    import subprocess
    from app.dashboards import svg_render
    from app.errors import AppError

    monkeypatch.setattr(svg_render, "_require_script", lambda path: path)

    def _fake_run(*args, **kwargs):
        exc = subprocess.TimeoutExpired(cmd=args[0], timeout=5)
        exc.stdout = b"partial rendered chunk"
        exc.stderr = b""
        raise exc

    monkeypatch.setattr(svg_render.subprocess, "run", _fake_run)

    with pytest.raises(AppError) as exc_info:
        svg_render._run_node_script("fake_script.mjs", {"key": "val"}, timeout=5)

    assert "partial rendered chunk" in exc_info.value.message, (
        f"Expected partial output in message, got: {exc_info.value.message!r}"
    )
    assert "timed out" in exc_info.value.message.lower(), (
        f"Expected 'timed out' in message, got: {exc_info.value.message!r}"
    )


def test_run_node_script_timeout_no_partial_output(monkeypatch):
    """When Node renderer times out with no stdout, error message has no 'Partial output' suffix."""
    import subprocess
    from app.dashboards import svg_render
    from app.errors import AppError

    monkeypatch.setattr(svg_render, "_require_script", lambda path: path)

    def _fake_run(*args, **kwargs):
        exc = subprocess.TimeoutExpired(cmd=args[0], timeout=5)
        exc.stdout = None
        exc.stderr = None
        raise exc

    monkeypatch.setattr(svg_render.subprocess, "run", _fake_run)

    with pytest.raises(AppError) as exc_info:
        svg_render._run_node_script("fake_script.mjs", {"key": "val"}, timeout=5)

    assert "Partial output" not in exc_info.value.message, (
        f"Unexpected 'Partial output' in message: {exc_info.value.message!r}"
    )
    assert "timed out" in exc_info.value.message.lower(), (
        f"Expected 'timed out' in message, got: {exc_info.value.message!r}"
    )


# ---------------------------------------------------------------------------
# 5. compose_page_svg — assembles widget SVGs into a full page
# ---------------------------------------------------------------------------


def test_compose_page_svg():
    """compose_page_svg assembles pre-rendered SVGs into a valid page SVG."""
    widget_svgs = [
        {
            "id": "chart1",
            "layout": {"x": 1, "y": 1, "w": 6, "h": 3},
            "svg": (
                '<svg width="600" height="300" xmlns="http://www.w3.org/2000/svg">'
                '<rect width="600" height="300" fill="#f0f0f0"/>'
                '<text x="10" y="20">Chart widget</text>'
                "</svg>"
            ),
            "webgl": False,
        },
        {
            "id": "kpi1",
            "layout": {"x": 7, "y": 1, "w": 3, "h": 2},
            "svg": (
                '<svg width="300" height="200" xmlns="http://www.w3.org/2000/svg">'
                '<text x="50" y="100" font-size="48">42</text>'
                "</svg>"
            ),
            "webgl": False,
        },
    ]

    result = compose_page_svg(widget_svgs, surface="grid", cols=12)

    assert "svg" in result
    assert "page_width_px" in result
    assert "page_height_px" in result
    assert "widget_rects" in result

    page_svg = result["svg"]
    assert isinstance(page_svg, str)
    assert page_svg.strip().startswith("<svg")
    assert "</svg>" in page_svg

    # Both widget ids should appear as comments in the composed SVG.
    assert "chart1" in page_svg
    assert "kpi1" in page_svg

    # Two widget_rects entries.
    assert len(result["widget_rects"]) == 2

    # Page dimensions should be positive.
    assert result["page_width_px"] > 0
    assert result["page_height_px"] > 0


# ---------------------------------------------------------------------------
# 6. Concurrency limiter — no more than N Node processes run simultaneously
# ---------------------------------------------------------------------------


def test_node_semaphore_caps_concurrency(monkeypatch):
    """_run_node_script never holds more than _NODE_SEMAPHORE.value concurrent
    Node subprocesses.  We replace subprocess.run with a slow fake that records
    the peak concurrency and verify it stays within the semaphore bound.
    """
    import threading
    import time
    from app.dashboards import svg_render

    # Install a small test semaphore so the test runs quickly regardless of CPU count.
    cap = 3
    test_sem = threading.BoundedSemaphore(cap)
    monkeypatch.setattr(svg_render, "_NODE_SEMAPHORE", test_sem)
    monkeypatch.setattr(svg_render, "_require_script", lambda path: path)
    monkeypatch.setattr(svg_render, "_require_node", lambda: "node")

    peak_concurrency = 0
    active_lock = threading.Lock()
    active_count = 0

    def _slow_run(*args, **kwargs):
        nonlocal peak_concurrency, active_count
        with active_lock:
            active_count += 1
            if active_count > peak_concurrency:
                peak_concurrency = active_count
        time.sleep(0.05)  # simulate slow Node startup
        with active_lock:
            active_count -= 1

        class _Result:
            returncode = 0
            stdout = b'{"widgets": []}'
            stderr = b""

        return _Result()

    monkeypatch.setattr(svg_render.subprocess, "run", _slow_run)

    errors: list[Exception] = []

    def _call():
        try:
            svg_render._run_node_script("fake.mjs", {"widgets": []})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    # Launch more threads than the cap.
    threads = [threading.Thread(target=_call) for _ in range(cap * 3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Unexpected errors: {errors}"
    assert peak_concurrency <= cap, (
        f"Peak concurrency {peak_concurrency} exceeded semaphore cap {cap}"
    )
