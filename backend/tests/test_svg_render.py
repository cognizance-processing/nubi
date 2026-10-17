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

import pytest

from app.dashboards.svg_render import renderer_available

# Skip the entire module when Node.js or echarts is not available.
# renderer_available() probes the actual echarts SSR script (not just `node`),
# so the suite skips cleanly in environments where echarts isn't installed
# (e.g. Python-only CI, git worktrees without node_modules) rather than
# failing with a confusing "echarts not found" error.
pytestmark = pytest.mark.skipif(
    not renderer_available(),
    reason="Node.js / echarts not available — skipping SVG render tests",
)

from app.dashboards.spec import DashboardSpec, WidgetPos, Widget, Surfaces, SurfaceGridEntry, validate_spec
from app.dashboards.svg_render import (
    compose_page_svg,
    render_board_svg,
    render_widgets_svg,
    resolve_page_bg,
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


def test_render_widgets_svg_choropleth_draws_the_map():
    """A map widget renders real geometry, not a labelled placeholder.

    Server renders used to skip choropleths entirely — there was no geometry
    registry on this side, so a thumbnail or an exported PDF showed a titled
    placeholder where the map belongs. The geometry ships in the repo under
    src/lib/maps, so the honest answer is to register it and draw.
    """
    payloads = [
        {
            "id": "m1",
            "type": "chart",
            "chart_type": "map",
            "encoding": {"name": "province", "value": "net_sales"},
            "props": {},
            "config": {"map": "south-africa", "lowColor": "#EAF1F4", "highColor": "#0E2A38"},
            "content": None,
            "columns": ["province", "net_sales"],
            "rows": [["Gauteng", 18_605_395], ["Western Cape", 9_868_123],
                     ["Northern Cape", 468_804]],
            "width": 500,
            "height": 400,
        }
    ]

    results = render_widgets_svg(payloads)
    assert len(results) == 1
    r = results[0]
    assert not r.get("error"), f"Unexpected error: {r.get('error')}"
    svg = r.get("svg") or ""
    assert svg.strip().startswith("<svg")
    # Province outlines are paths; a placeholder has none.
    assert svg.count("<path") >= 9, "expected one path per province, got a placeholder"
    assert "[chart]" not in svg


def test_render_widgets_svg_unknown_map_still_falls_back():
    """A map name with no geometry on disk must not draw some other chart."""
    payloads = [
        {
            "id": "m2",
            "type": "chart",
            "chart_type": "map",
            "encoding": {"name": "region", "value": "v"},
            "props": {},
            "config": {"map": "atlantis"},
            "content": None,
            "columns": ["region", "v"],
            "rows": [["A", 1], ["B", 2]],
            "width": 400,
            "height": 300,
        }
    ]
    results = render_widgets_svg(payloads)
    svg = (results[0].get("svg") or "")
    # Falls back to the honest placeholder rather than inventing a bar chart.
    assert "<path" not in svg or svg.count("<path") < 9


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


# ---------------------------------------------------------------------------
# Tab partition + spec-driven row height
# ---------------------------------------------------------------------------


def _tabbed_spec() -> DashboardSpec:
    """A 2-tab board whose tabs REUSE the same grid coordinates.

    That reuse is normal and is exactly what broke the renderer: tabs are a
    render partition, so two widgets legitimately share cell (1,1).
    """
    return DashboardSpec(
        title="Tabbed board",
        layout={"cols": 12, "row_height": 60},
        tabs=[{"id": "t_one", "label": "One"}, {"id": "t_two", "label": "Two"}],
        widgets=[
            # tab_id None → implicitly the FIRST tab (renderer contract).
            Widget(id="legacy_kpi", type="kpi", query_id="q1",
                   encoding={"value": "v"}, pos=WidgetPos(x=1, y=1, w=3, h=2)),
            Widget(id="one_kpi", type="kpi", query_id="q1", tab_id="t_one",
                   encoding={"value": "v"}, pos=WidgetPos(x=4, y=1, w=3, h=2)),
            # Same cell as legacy_kpi — different tab.
            Widget(id="two_kpi", type="kpi", query_id="q1", tab_id="t_two",
                   encoding={"value": "v"}, pos=WidgetPos(x=1, y=1, w=3, h=2)),
        ],
    )


class TestTabPartition:
    """render_board_svg must draw ONE tab, not every tab stacked.

    Regression: it iterated spec.widgets wholesale. On a real 5-tab
    board that put 31 widgets into the 9 widgets' worth of space a viewer sees —
    five different widgets all at grid cell (1,1) — which read as a broken layout
    engine but was a missing partition. Hit the PDF export too, not just
    thumbnails.
    """

    def test_widgets_for_tab_defaults_to_the_first_tab(self):
        from app.dashboards.svg_render import widgets_for_tab

        ids = [w.id for w in widgets_for_tab(_tabbed_spec())]
        # tab_id None belongs to the first tab; the second tab's widget is out.
        assert ids == ["legacy_kpi", "one_kpi"]

    def test_widgets_for_tab_selects_an_explicit_tab(self):
        from app.dashboards.svg_render import widgets_for_tab

        ids = [w.id for w in widgets_for_tab(_tabbed_spec(), "t_two")]
        assert ids == ["two_kpi"]

    def test_untabbed_board_renders_every_widget(self):
        from app.dashboards.svg_render import widgets_for_tab

        spec = _small_spec_legacy()
        assert len(widgets_for_tab(spec)) == len(spec.widgets)

    @pytest.mark.skipif(not renderer_available(), reason="Node.js / echarts not available")
    def test_rendered_svg_contains_only_the_active_tab(self):
        from app.dashboards.svg_render import render_board_svg

        svg = render_board_svg(_tabbed_spec(), [])
        assert "widget: legacy_kpi" in svg
        assert "widget: one_kpi" in svg
        assert "widget: two_kpi" not in svg, "a second tab's widget must not be stacked in"


class TestSpecRowHeight:
    """The board's own row_height drives the render, not the module default."""

    @pytest.mark.skipif(not renderer_available(), reason="Node.js / echarts not available")
    def test_spec_row_height_is_honoured(self):
        import re

        from app.dashboards.svg_render import render_board_svg

        spec = _small_spec_legacy()          # row_height 60, tallest widget h=3
        svg = render_board_svg(spec, [])
        height = float(re.search(r'<svg[^>]*height="([\d.]+)"', svg).group(1))
        # 3 rows * 60 + 2*16 margin = 212. With the old hardcoded 120 this was 392.
        assert height == pytest.approx(3 * 60 + 32, abs=1)

    @pytest.mark.skipif(not renderer_available(), reason="Node.js / echarts not available")
    def test_explicit_row_height_argument_still_wins(self):
        import re

        from app.dashboards.svg_render import render_board_svg

        svg = render_board_svg(_small_spec_legacy(), [], row_height_px=120)
        height = float(re.search(r'<svg[^>]*height="([\d.]+)"', svg).group(1))
        assert height == pytest.approx(3 * 120 + 32, abs=1)


class TestDriverNativeTypes:
    """Rows arrive as real driver objects — the renderer must not choke on them.

    Regression: ``_run_node_script`` did a plain ``json.dumps(payload)``, so the
    first ``datetime.date`` on a day axis raised "Object of type date is not JSON
    serializable" and the whole render 500'd. It hid for a long time because the
    boards that were exercised either had no date axis on their first tab, or
    their SQL already did ``CAST(... AS CHAR)``. It took down the PDF export the
    same way, not just thumbnails.
    """

    def test_json_default_coerces_driver_types(self):
        import datetime
        import decimal
        import uuid

        from app.dashboards.svg_render import _json_default

        # Decimal → float, NOT str: it's a measure. A stringified number would be
        # plotted by ECharts as a category.
        assert _json_default(decimal.Decimal("12.5")) == 12.5
        assert isinstance(_json_default(decimal.Decimal("12.5")), float)

        assert _json_default(datetime.date(2026, 7, 1)) == "2026-07-01"
        assert _json_default(datetime.datetime(2026, 7, 1, 10, 30)) == "2026-07-01T10:30:00"
        assert _json_default(datetime.time(10, 30)) == "10:30:00"
        assert _json_default(datetime.timedelta(seconds=90)) == 90.0
        assert _json_default(uuid.UUID("00000000-0000-0000-0000-000000000001")) == (
            "00000000-0000-0000-0000-000000000001"
        )
        assert _json_default(b"hi") == "hi"
        assert sorted(_json_default({"b", "a"})) == ["a", "b"]

    def test_json_default_never_emits_unserialisable_decimal_edges(self):
        import decimal
        import json

        from app.dashboards.svg_render import _json_default

        # NaN/Infinity would round-trip through json.dumps as bare NaN/Infinity,
        # which is not valid JSON for the Node side to parse.
        for bad in (decimal.Decimal("NaN"), decimal.Decimal("Infinity")):
            out = _json_default(bad)
            assert isinstance(out, str), f"{bad} must degrade to a string, got {out!r}"
        json.dumps({"v": _json_default(decimal.Decimal("NaN"))})  # must not raise

    @pytest.mark.skipif(not renderer_available(), reason="Node.js / echarts not available")
    def test_board_with_a_date_axis_renders(self):
        import datetime
        import decimal

        from app.dashboards.svg_render import render_board_svg

        spec = DashboardSpec(
            title="Date axis",
            layout={"cols": 12, "row_height": 60},
            widgets=[
                Widget(
                    id="c1", type="chart", query_id="q1", chart_type="line",
                    encoding={"x": "day", "y": "n"},
                    pos=WidgetPos(x=1, y=1, w=6, h=3),
                ),
            ],
        )
        data = [{
            "widget_id": "c1", "query_id": "q1", "columns": ["day", "n"],
            "rows": [
                [datetime.date(2026, 7, 1), decimal.Decimal("12.5")],
                [datetime.date(2026, 7, 2), decimal.Decimal("18.0")],
            ],
        }]
        svg = render_board_svg(spec, data, page_width_px=1000)   # used to raise TypeError
        assert "<svg" in svg
        assert svg.count("<path") > 0, "the line must actually be drawn"


class TestWidgetStyleAndTheme:
    """A board is authored once to look right in light AND dark.

    Widget styles are written against CSS theme TOKENS (`var(--surface)`), which
    only a browser can resolve. Two things had to be true for a server render to
    honour them, and neither was:

    1. The `Widget` model had NO `style` field at all, so `validate_spec`
       silently DROPPED it — the renderer never saw a style to honour.
    2. Even with it, a raw `var(--surface)` in an SVG `fill=` paints nothing, so
       the renderer has to resolve tokens for a chosen theme itself.
    """

    def test_widget_model_keeps_style(self):
        """Regression: style was dropped on the floor by validation."""
        from app.dashboards.spec import validate_spec

        spec, _ = validate_spec({
            "title": "styled",
            "layout": {"cols": 12, "row_height": 60},
            "widgets": [{
                "id": "k1", "type": "kpi", "query_id": "q1",
                "encoding": {"value": "v"},
                "style": {"background": "var(--surface)", "color": "var(--fg)"},
                "pos": {"x": 1, "y": 1, "w": 3, "h": 2},
            }],
        })
        assert spec is not None
        assert spec.widgets[0].style == {"background": "var(--surface)", "color": "var(--fg)"}

    @pytest.mark.skipif(not renderer_available(), reason="Node.js / echarts not available")
    def test_theme_tokens_resolve_and_never_leak_into_paint(self):
        from app.dashboards.svg_render import render_board_svg

        spec = DashboardSpec(
            title="Themed",
            layout={"cols": 12, "row_height": 60},
            widgets=[
                Widget(
                    id="k1", type="kpi", query_id="q1", encoding={"value": "v"},
                    style={"background": "var(--surface)", "color": "var(--fg)"},
                    pos=WidgetPos(x=1, y=1, w=3, h=2),
                ),
            ],
        )
        data = [{"widget_id": "k1", "query_id": "q1", "columns": ["v"], "rows": [[42]]}]

        light = render_board_svg(spec, data, page_width_px=600, theme="light")
        dark = render_board_svg(spec, data, page_width_px=600, theme="dark")

        # An unresolved var() in a fill= paints nothing — it must never survive.
        assert "var(--" not in light
        assert "var(--" not in dark
        # Tokens resolved to each theme's real values (src/index.css).
        assert "#ffffff" in light and "#0b0c0e" in light      # --surface / --fg(→--text)
        assert "#141518" in dark and "#f4f5f7" in dark
        # The two themes must genuinely differ — a cache keyed by theme is only
        # worth having if the picture actually changes.
        assert light != dark

    @pytest.mark.skipif(not renderer_available(), reason="Node.js / echarts not available")
    def test_a_hardcoded_colour_is_honoured_verbatim_in_both_themes(self):
        """Fidelity means obeying the widget, not imposing the theme.

        A widget that hard-codes its background renders that colour in dark mode
        too — exactly as it does in the app.
        """
        from app.dashboards.svg_render import render_board_svg

        spec = DashboardSpec(
            title="Fixed",
            layout={"cols": 12, "row_height": 60},
            widgets=[
                Widget(
                    id="k1", type="kpi", query_id="q1", encoding={"value": "v"},
                    style={"background": "#161b22", "color": "#ffffff"},
                    pos=WidgetPos(x=1, y=1, w=3, h=2),
                ),
            ],
        )
        data = [{"widget_id": "k1", "query_id": "q1", "columns": ["v"], "rows": [[42]]}]
        for theme in ("light", "dark"):
            svg = render_board_svg(spec, data, page_width_px=600, theme=theme)
            assert "#161b22" in svg, f"{theme}: the widget's own colour must win"


# ---------------------------------------------------------------------------
# 6. Widget.config passthrough — deliberately exercised with data that shares
#    nothing (no column names, no domain) with any other fixture in this
#    file, and parametrized across several unrelated metrics, so a pass
#    proves the mechanism generalises rather than happening to fit the
#    region/revenue shape everything else here uses.
# ---------------------------------------------------------------------------


class TestWidgetConfigPassthrough:
    """widget.config (palette/legend/stack/gaugeStyle/...) was silently
    dropped by validate_spec the same way `style` once was, so every
    server-side render (thumbnail, PDF) fell back to the generic default
    palette no matter what the author configured.
    """

    def test_widget_model_keeps_config(self):
        """Regression: config was dropped on the floor by validation."""
        spec, errors = validate_spec({
            "title": "configured",
            "layout": {"cols": 12, "row_height": 60},
            "widgets": [{
                "id": "c1", "type": "chart", "query_id": "q1",
                "chart_type": "bar",
                "encoding": {"x": "sensor", "y": "temperature_c"},
                "config": {"palette": ["#ff00aa"], "legend": True},
                "pos": {"x": 1, "y": 1, "w": 6, "h": 3},
            }],
        })
        assert spec is not None
        assert not any("config" in e for e in errors)
        assert spec.widgets[0].config == {"palette": ["#ff00aa"], "legend": True}

    def test_config_conflicts_with_ref_like_props_and_style(self):
        """Same mutual-exclusivity rule already enforced for props/style
        (validate_spec step 14) now also covers config: a widget can't be
        both a library reference and carry its own inline config."""
        spec, errors = validate_spec({
            "title": "conflict",
            "layout": {"cols": 12, "row_height": 60},
            "widgets": [{
                "id": "c1", "ref": "lib1",
                "config": {"palette": ["#ff00aa"]},
                "pos": {"x": 1, "y": 1, "w": 6, "h": 3},
            }],
        })
        assert any("config" in e for e in errors)

    @pytest.mark.skipif(not renderer_available(), reason="Node.js / echarts not available")
    @pytest.mark.parametrize(
        "category_col,metric_col,custom_color",
        [
            ("sensor", "temperature_c", "#ff00aa"),
            ("endpoint", "latency_ms", "#00c2ff"),
            ("assembly_line", "defect_rate", "#7cff00"),
            ("campaign", "signups", "#ffae00"),
        ],
    )
    def test_custom_palette_is_honoured_for_any_metric(self, category_col, metric_col, custom_color):
        """The fix can't be coupled to one dataset's shape: a board about
        sensor temperatures, API latency, factory defects, or marketing
        signups must all honour their own config identically — there is no
        "one true metric" this only happens to work for.
        """
        spec = DashboardSpec(
            title="Custom-palette board",
            layout={"cols": 12, "row_height": 60},
            widgets=[
                Widget(
                    id="c1", type="chart", query_id="q1", chart_type="bar",
                    encoding={"x": category_col, "y": metric_col},
                    config={"palette": [custom_color]},
                    pos=WidgetPos(x=1, y=1, w=6, h=3),
                ),
            ],
        )
        data = [{
            "widget_id": "c1", "query_id": "q1",
            "columns": [category_col, metric_col],
            "rows": [["A", 3], ["B", 7], ["C", 5]],
        }]

        svg = render_board_svg(spec, data)

        assert custom_color in svg.lower(), (
            f"Expected config.palette colour {custom_color} to reach the "
            f"render for metric={metric_col!r}, got no trace of it"
        )


# ---------------------------------------------------------------------------
# 7. DashboardSpec.background passthrough — same treatment: a pure-function
#    matrix of every background type resolve_page_bg understands, plus one
#    render-level check with a metric/domain not used anywhere else here.
# ---------------------------------------------------------------------------


class TestBackgroundPassthrough:
    """spec.background was silently dropped the same way `style` and
    `Widget.config` once were, so a board authored on a custom canvas got a
    plain theme-default thumbnail with no visible relation to how viewers
    actually see it.
    """

    @pytest.mark.parametrize(
        "background,theme,expected",
        [
            (None, "light", "#ffffff"),
            (None, "dark", "#141518"),
            ({"type": "transparent"}, "light", "#ffffff"),
            ({"type": "solid", "color": "#123456"}, "light", "#123456"),
            ({"type": "solid", "color": "#123456"}, "dark", "#123456"),
            ({"type": "gradient", "from": "#abcdef", "to": "#000000"}, "light", "#abcdef"),
            ({"type": "solid"}, "light", "#ffffff"),  # no colour set -> falls back
            ({"type": "image", "imageUrl": "https://example.com/x.png"}, "dark", "#141518"),
            ({"type": "css", "css": "repeating-linear-gradient(...)"}, "light", "#ffffff"),
        ],
    )
    def test_resolve_page_bg_matrix(self, background, theme, expected):
        spec = DashboardSpec(title="bg", background=background, widgets=[])
        assert resolve_page_bg(spec, theme) == expected

    @pytest.mark.skipif(not renderer_available(), reason="Node.js / echarts not available")
    def test_custom_background_reaches_the_composed_render(self):
        """Uses a fresh metric/category unrelated to every other fixture in
        this file, so the assertion isn't riding on a coincidence of the
        region/revenue shape."""
        spec = DashboardSpec(
            title="Custom-bg board",
            layout={"cols": 12, "row_height": 60},
            background={"type": "solid", "color": "#2a1f4d"},
            widgets=[
                Widget(
                    id="k1", type="kpi", query_id="q1",
                    encoding={"value": "queue_depth"},
                    props={"label": "Queue depth"},
                    pos=WidgetPos(x=1, y=1, w=3, h=2),
                ),
            ],
        )
        data = [{"widget_id": "k1", "query_id": "q1", "columns": ["queue_depth"], "rows": [[12]]}]

        svg = render_board_svg(spec, data, theme="light")

        assert "#2a1f4d" in svg.lower(), "The board's own background colour must reach the page render"


# ---------------------------------------------------------------------------
# Server-side chart-type coverage
# ---------------------------------------------------------------------------


class TestServerSideChartTypes:
    """Every chart type must render as ITSELF server-side.

    The SSR renderer keeps its own option builders (it runs as plain Node and
    cannot import the TypeScript builder the browser uses), and its dispatch had
    fallen behind: any type without a local case fell through to a BAR chart, so
    a treemap or a sankey came out of a thumbnail or a PDF export as bars. These
    assertions pin the silhouette, which is the part a fallback would get wrong.
    """

    # chart_type -> (payload fields, substring proving the right series ran)
    CASES = {
        "sankey": (
            {"encoding": {"source": "s", "target": "t", "value": "v"},
             "columns": ["s", "t", "v"], "rows": [["A", "B", 5], ["A", "C", 3]]},
        ),
        "funnel": (
            {"encoding": {"x": "c", "y": "v"},
             "columns": ["c", "v"], "rows": [["a", 10], ["b", 6]]},
        ),
        "waterfall": (
            {"encoding": {"x": "c", "y": "v"},
             "columns": ["c", "v"], "rows": [["a", 10], ["b", -6], ["c", 3]]},
        ),
        "heatmap": (
            {"encoding": {"x": "x", "color": "y", "value": "v"},
             "columns": ["x", "y", "v"],
             "rows": [["Mon", "AM", 5], ["Mon", "PM", 8], ["Tue", "AM", 3]]},
        ),
        "radar": (
            {"encoding": {"x": "c", "y": "v"},
             "columns": ["c", "v"], "rows": [["a", 10], ["b", 6], ["c", 8]]},
        ),
        "treemap": (
            {"encoding": {"x": "c", "y": "v"},
             "columns": ["c", "v"], "rows": [["a", 10], ["b", 6]]},
        ),
        "boxplot": (
            {"encoding": {"x": "c", "y": "v"},
             "columns": ["c", "v"], "rows": [["g", 1], ["g", 5], ["g", 9], ["h", 2], ["h", 4]]},
        ),
        "candlestick": (
            {"encoding": {"x": "d", "open": "o", "close": "c", "low": "l", "high": "h"},
             "columns": ["d", "o", "c", "l", "h"],
             "rows": [["Jan", 1, 2, 0.5, 2.5], ["Feb", 2, 1.5, 1, 2.2]]},
        ),
        "fan": (
            {"encoding": {"x": "t", "y": "m", "lower": "lo", "upper": "hi"},
             "columns": ["t", "m", "lo", "hi"],
             "rows": [["Q1", 100, 90, 110], ["Q2", 120, 105, 135]]},
        ),
        "polar": (
            {"encoding": {"x": "c", "y": "v"},
             "columns": ["c", "v"], "rows": [["a", 10], ["b", 6]]},
        ),
        "radialbar": (
            {"encoding": {"x": "c", "y": "v"},
             "columns": ["c", "v"], "rows": [["a", 80], ["b", 60]]},
        ),
        "bubble": (
            {"encoding": {"x": "x", "y": "y", "size": "s"},
             "columns": ["x", "y", "s"], "rows": [[1, 2, 3], [2, 4, 5]]},
        ),
    }

    @pytest.mark.skipif(not renderer_available(), reason="Node.js / echarts not available")
    def test_every_chart_type_renders_without_error(self):
        payloads = [
            {
                "id": ct,
                "type": "chart",
                "chart_type": ct,
                "props": {},
                "content": None,
                "width": 320,
                "height": 240,
                **fields[0],
            }
            for ct, fields in self.CASES.items()
        ]
        results = {r["id"]: r for r in render_widgets_svg(payloads)}
        assert set(results) == set(self.CASES)
        for ct, r in results.items():
            svg = r.get("svg") or ""
            assert svg.strip().startswith("<svg"), f"{ct}: no SVG — {r.get('error')}"
            assert not r.get("error"), f"{ct}: {r.get('error')}"

    @pytest.mark.skipif(not renderer_available(), reason="Node.js / echarts not available")
    def test_a_treemap_is_not_silently_drawn_as_bars(self):
        """The specific regression: unlisted types fell through to buildBar."""
        tree = {
            "id": "t", "type": "chart", "chart_type": "treemap",
            "encoding": {"x": "c", "y": "v"}, "props": {}, "content": None,
            "columns": ["c", "v"], "rows": [["alpha", 10], ["beta", 6]],
            "width": 320, "height": 240,
        }
        bar = {**tree, "id": "b", "chart_type": "bar"}
        out = {r["id"]: (r.get("svg") or "") for r in render_widgets_svg([tree, bar])}
        assert out["t"] and out["b"]
        assert out["t"] != out["b"], "treemap rendered identically to a bar chart"

    @pytest.mark.skipif(not renderer_available(), reason="Node.js / echarts not available")
    def test_map_degrades_to_a_placeholder_not_a_wrong_chart(self):
        """No server-side GeoJSON registry exists, so there is no map to draw."""
        payload = {
            "id": "m", "type": "chart", "chart_type": "map",
            "encoding": {"name": "n", "value": "v"}, "props": {}, "content": None,
            "columns": ["n", "v"], "rows": [["ZAF", 5]],
            "width": 320, "height": 240,
        }
        svg = (render_widgets_svg([payload])[0].get("svg") or "")
        assert svg.strip().startswith("<svg")
        # The placeholder is a labelled chip, not an echarts render.
        assert "[chart]" in svg or "[map]" in svg, svg[:300]


# ---------------------------------------------------------------------------
# Per-role text colours
# ---------------------------------------------------------------------------


class TestTextColorRoles:
    """A board's authored text colours must survive into thumbnails and PDFs.

    Widgets carry three text roles — `style.titleColor` (headings),
    `style.color` (body) and `style.mutedColor` (captions, axis and legend
    labels). The server renderer used to know only about `style.color`, so a
    board that coloured its headings or captions came out of an export with
    them in the default ink.
    """

    @pytest.mark.skipif(not renderer_available(), reason="Node.js / echarts not available")
    def test_section_title_and_subtitle_take_their_own_roles(self):
        payload = {
            "id": "s", "type": "section",
            "props": {"title": "Head", "subtitle": "Sub"},
            "style": {"titleColor": "#ff0000", "mutedColor": "#0000ff"},
            "content": None, "columns": [], "rows": [], "width": 400, "height": 80,
        }
        svg = render_widgets_svg([payload])[0].get("svg") or ""
        assert "#ff0000" in svg, "heading colour missing"
        assert "#0000ff" in svg, "subtitle colour missing"

    @pytest.mark.skipif(not renderer_available(), reason="Node.js / echarts not available")
    def test_kpi_caption_is_the_muted_role(self):
        payload = {
            "id": "k", "type": "kpi",
            "encoding": {"value": "v"}, "props": {"label": "Cap"},
            "style": {"color": "#00ff00", "mutedColor": "#0000ff"},
            "content": None, "columns": ["v"], "rows": [[42]], "width": 300, "height": 150,
        }
        svg = render_widgets_svg([payload])[0].get("svg") or ""
        assert "#00ff00" in svg, "value colour missing"
        assert "#0000ff" in svg, "caption colour missing"

    @pytest.mark.skipif(not renderer_available(), reason="Node.js / echarts not available")
    def test_chart_title_is_rendered_and_uses_the_title_role(self):
        """config.title was dropped outright — a titled chart exported untitled."""
        payload = {
            "id": "c", "type": "chart", "chart_type": "bar",
            "encoding": {"x": "c", "y": "v"},
            "config": {"title": "Revenue by region", "subtitle": "FY25"},
            "style": {"titleColor": "#ff0000", "mutedColor": "#0000ff"},
            "props": {}, "content": None,
            "columns": ["c", "v"], "rows": [["a", 1], ["b", 2]],
            "width": 400, "height": 300,
        }
        svg = render_widgets_svg([payload])[0].get("svg") or ""
        assert "Revenue by region" in svg, "chart title not rendered"
        assert "#ff0000" in svg, "title colour missing"
        assert "#0000ff" in svg, "axis/legend colour missing"

    @pytest.mark.skipif(not renderer_available(), reason="Node.js / echarts not available")
    def test_a_body_colour_alone_still_drives_the_other_roles(self):
        """Back-compat: one authored colour must not leave grey axis labels on
        a dark tile — the roles fall back to the body colour before the theme."""
        payload = {
            "id": "c", "type": "chart", "chart_type": "bar",
            "encoding": {"x": "c", "y": "v"},
            "config": {"title": "T"},
            "style": {"color": "#ffcc00", "background": "#101010"},
            "props": {}, "content": None,
            "columns": ["c", "v"], "rows": [["a", 1]],
            "width": 400, "height": 300,
        }
        svg = render_widgets_svg([payload])[0].get("svg") or ""
        assert "#ffcc00" in svg
