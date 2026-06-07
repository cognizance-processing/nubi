"""Pure spec-transform operations for the chat assistant's editing tools.

These functions take a *current* ``DashboardSpec`` (as a plain JSON dict — the
shape the editor round-trips via ``onApplySpec``) and return an **updated** dict.
They never mutate the input in place: every operation deep-copies first, applies
the change, and returns the new spec.  The returned dict becomes the turn's
proposed spec, which the route surfaces in the final assistant message so the
editor applies it.

Why operate on dicts (not the Pydantic ``DashboardSpec``)?
----------------------------------------------------------
The canonical editor spec carries widget-level presentation fields the LLM needs
to drive (``style``, ``html``, ``params``) plus dashboard-level ``background`` —
a superset of the strict ``app.dashboards.spec.DashboardSpec`` model.  Operating
on dicts lets the assistant set/merge any of these without being blocked by the
stricter server model, while ``validate_spec`` is still used opportunistically to
surface warnings (never to hard-fail an edit).

Safety guarantees
-----------------
- **Id generation** (``_next_widget_id``): new widget ids are ``w<N>`` where N is
  the smallest positive integer not already used by an existing ``w<N>`` id, so
  ids are stable and never collide.
- **Free-spot positioning** (``_free_spot``): when no ``pos`` is supplied a new
  widget is dropped into the first empty grid cell (scanning row by row across
  ``layout.cols``), so widgets never overlap by default.
- Operations targeting a missing ``widget_id`` raise ``SpecOpError`` (the tool
  layer turns this into a structured ``{"error": ...}`` result for the model).

Public API
----------
``ensure_spec`` ``add_widget`` ``update_widget`` ``remove_widget``
``set_widget_style`` ``set_layout`` ``set_background`` ``add_variable``
``set_drilldown``
"""

from __future__ import annotations

import copy
from typing import Any

# Widget types the editor understands.
_WIDGET_TYPES = ("kpi", "table", "chart", "filter", "text")

_DEFAULT_LAYOUT: dict[str, Any] = {"cols": 12, "row_height": 60}


class SpecOpError(Exception):
    """Raised when a spec operation cannot be applied (e.g. unknown widget id)."""


# ---------------------------------------------------------------------------
# Spec normalisation
# ---------------------------------------------------------------------------


def ensure_spec(spec: Any) -> dict[str, Any]:
    """Return a well-formed spec dict, filling in defaults for missing keys.

    Accepts ``None`` (→ a blank dashboard), a partial dict, or a full spec.
    The result always carries ``version``, ``title``, ``layout`` (with ``cols``
    and ``row_height``), ``variables`` (list), and ``widgets`` (list).  A deep
    copy is returned so callers never alias the input.
    """
    if not isinstance(spec, dict):
        spec = {}
    out: dict[str, Any] = copy.deepcopy(spec)

    out.setdefault("version", 1)
    if not out.get("title"):
        out["title"] = "Dashboard"

    layout = out.get("layout")
    if not isinstance(layout, dict):
        layout = {}
    layout.setdefault("cols", _DEFAULT_LAYOUT["cols"])
    layout.setdefault("row_height", _DEFAULT_LAYOUT["row_height"])
    out["layout"] = layout

    if not isinstance(out.get("variables"), list):
        out["variables"] = []
    if not isinstance(out.get("widgets"), list):
        out["widgets"] = []

    return out


# ---------------------------------------------------------------------------
# Id generation + free-spot positioning
# ---------------------------------------------------------------------------


def _next_widget_id(widgets: list[dict[str, Any]]) -> str:
    """Return the smallest unused ``w<N>`` id for *widgets*."""
    used: set[int] = set()
    for w in widgets:
        wid = str(w.get("id", ""))
        if wid.startswith("w") and wid[1:].isdigit():
            used.add(int(wid[1:]))
    n = 1
    while n in used:
        n += 1
    return f"w{n}"


def _occupied_cells(widgets: list[dict[str, Any]]) -> set[tuple[int, int]]:
    """Return the set of (col, row) grid cells occupied by existing widgets."""
    cells: set[tuple[int, int]] = set()
    for w in widgets:
        pos = w.get("pos")
        if not isinstance(pos, dict):
            continue
        try:
            x = int(pos.get("x", 1))
            y = int(pos.get("y", 1))
            ww = int(pos.get("w", 1))
            hh = int(pos.get("h", 1))
        except (TypeError, ValueError):
            continue
        for cx in range(x, x + max(ww, 1)):
            for cy in range(y, y + max(hh, 1)):
                cells.add((cx, cy))
    return cells


def _free_spot(
    widgets: list[dict[str, Any]],
    cols: int,
    w: int,
    h: int,
) -> dict[str, int]:
    """Find the first empty grid rectangle of size *w* x *h*.

    Scans row by row (top-to-bottom), then column by column (left-to-right),
    returning the first 1-based (x, y) where a ``w`` x ``h`` block fits without
    overlapping any occupied cell.  Always succeeds: it expands downward as far
    as needed, so there is always room below the existing widgets.
    """
    cols = max(int(cols), 1)
    w = max(min(int(w), cols), 1)
    h = max(int(h), 1)
    occupied = _occupied_cells(widgets)

    # Bound the row scan generously: existing rows + the new height + 1.
    max_row = max((cy for _, cy in occupied), default=0) + h + 1
    for y in range(1, max_row + 1):
        for x in range(1, cols - w + 2):
            block = {
                (cx, cy)
                for cx in range(x, x + w)
                for cy in range(y, y + h)
            }
            if block.isdisjoint(occupied):
                return {"x": x, "y": y, "w": w, "h": h}
    # Unreachable in practice (the scan always finds room below) — defensive.
    return {"x": 1, "y": max_row + 1, "w": w, "h": h}


def _find_widget(spec: dict[str, Any], widget_id: str) -> dict[str, Any]:
    """Return the widget dict with id *widget_id* or raise ``SpecOpError``."""
    for w in spec.get("widgets", []):
        if str(w.get("id")) == str(widget_id):
            return w
    known = [str(w.get("id")) for w in spec.get("widgets", [])]
    raise SpecOpError(
        f"No widget with id {widget_id!r}. Known widget ids: {known or '[]'}."
    )


def _default_size(widget_type: str) -> tuple[int, int]:
    """Sensible default (w, h) span for a freshly-added widget by type."""
    return {
        "kpi": (3, 2),
        "table": (8, 4),
        "chart": (6, 4),
        "filter": (3, 1),
        "text": (12, 1),
    }.get(widget_type, (4, 3))


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def add_widget(
    spec: Any,
    type: str,
    query_id: str | None = None,
    chart_type: str | None = None,
    encoding: dict[str, Any] | None = None,
    props: dict[str, Any] | None = None,
    style: dict[str, Any] | None = None,
    pos: dict[str, Any] | None = None,
    **extra: Any,
) -> tuple[dict[str, Any], str]:
    """Append a new widget to *spec*; returns ``(new_spec, new_widget_id)``.

    *type* must be one of ``kpi|table|chart|filter|text``.  When *pos* is omitted
    the widget is dropped into the first free grid spot at a type-appropriate
    size.  A unique ``w<N>`` id is generated.  Extra keyword fields (e.g.
    ``subtype``, ``target_var``, ``content``, ``params``, ``html``,
    ``options_query_id``) are passed straight through onto the widget.
    """
    if type not in _WIDGET_TYPES:
        raise SpecOpError(
            f"Unknown widget type {type!r}. Must be one of {list(_WIDGET_TYPES)}."
        )

    out = ensure_spec(spec)
    widgets = out["widgets"]
    wid = _next_widget_id(widgets)

    if isinstance(pos, dict) and {"x", "y", "w", "h"} <= set(pos):
        widget_pos = {k: int(pos[k]) for k in ("x", "y", "w", "h")}
    else:
        dw, dh = _default_size(type)
        if isinstance(pos, dict):
            dw = int(pos.get("w", dw))
            dh = int(pos.get("h", dh))
        widget_pos = _free_spot(widgets, int(out["layout"]["cols"]), dw, dh)

    widget: dict[str, Any] = {
        "id": wid,
        "type": type,
        "query_id": query_id or "",
        "encoding": dict(encoding) if isinstance(encoding, dict) else {},
        "props": dict(props) if isinstance(props, dict) else {},
        "pos": widget_pos,
    }
    if chart_type:
        widget["chart_type"] = chart_type
    if isinstance(style, dict) and style:
        widget["style"] = dict(style)
    for key, val in extra.items():
        if val is not None:
            widget[key] = val

    widgets.append(widget)
    return out, wid


def update_widget(spec: Any, widget_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge *patch* into the widget *widget_id*; return the updated spec.

    Dict-valued fields (``props``, ``encoding``, ``style``, ``params``, ``pos``)
    are **shallow-merged** so a partial patch only changes the supplied keys.
    Scalar fields (``query_id``, ``chart_type``, ``type``, ``content``, …) are
    replaced.  The widget ``id`` cannot be changed via this op (ignored).
    """
    if not isinstance(patch, dict):
        raise SpecOpError("patch must be an object.")

    out = ensure_spec(spec)
    widget = _find_widget(out, widget_id)

    _MERGE_KEYS = {"props", "encoding", "style", "params", "pos"}
    for key, val in patch.items():
        if key == "id":
            continue  # never rename a widget through a patch
        if key in _MERGE_KEYS and isinstance(val, dict):
            existing = widget.get(key)
            merged = dict(existing) if isinstance(existing, dict) else {}
            merged.update(val)
            widget[key] = merged
        else:
            widget[key] = val
    return out


def remove_widget(spec: Any, widget_id: str) -> dict[str, Any]:
    """Remove the widget *widget_id* from *spec*; return the updated spec."""
    out = ensure_spec(spec)
    before = len(out["widgets"])
    out["widgets"] = [w for w in out["widgets"] if str(w.get("id")) != str(widget_id)]
    if len(out["widgets"]) == before:
        known = [str(w.get("id")) for w in out["widgets"]]
        raise SpecOpError(
            f"No widget with id {widget_id!r}. Known widget ids: {known or '[]'}."
        )
    return out


def set_widget_style(spec: Any, widget_id: str, style: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge *style* into the widget's ``style`` object.

    Use this for per-widget presentation: ``background`` (a CSS colour, gradient,
    or ``"transparent"``), ``border``, ``borderRadius``, ``padding``,
    ``boxShadow``, etc.  Passing ``{"background": "transparent"}`` makes the
    widget background transparent.
    """
    if not isinstance(style, dict):
        raise SpecOpError("style must be an object.")
    out = ensure_spec(spec)
    widget = _find_widget(out, widget_id)
    existing = widget.get("style")
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(style)
    widget["style"] = merged
    return out


def set_layout(spec: Any, options: dict[str, Any]) -> dict[str, Any]:
    """Merge grid-layout *options* into ``spec.layout``.

    Recognised keys: ``cols`` (column count), ``row_height`` (px),
    ``compaction`` (``"vertical"|"horizontal"|"none"``), ``margin``
    (``[x, y]`` gap in px).  Unknown keys are merged through as-is for forward
    compatibility.
    """
    if not isinstance(options, dict):
        raise SpecOpError("options must be an object.")
    out = ensure_spec(spec)
    out["layout"].update(options)
    return out


def set_background(spec: Any, background: Any) -> dict[str, Any]:
    """Set the dashboard-level ``background``.

    *background* may be a plain string (a CSS colour like ``"#0b0f1a"``, a
    ``linear-gradient(...)``, or ``"transparent"``) or an object describing a
    structured background, e.g. ``{"type": "image", "url": "..."}`` or
    ``{"type": "gradient", "from": "#111", "to": "#333"}``.
    """
    out = ensure_spec(spec)
    out["background"] = background
    return out


def add_variable(
    spec: Any,
    name: str,
    type: str = "text",
    default: Any = None,
) -> dict[str, Any]:
    """Add (or update) a dashboard variable.

    Variables are referenced by widget ``params`` via ``{"ref": "<name>"}`` and
    are the target of filter widgets / drilldowns.  Re-adding an existing name
    updates its type/default in place rather than duplicating it.  *type* is one
    of ``text|number|date|daterange|select|multiselect``.
    """
    if not name or not str(name).strip():
        raise SpecOpError("variable name must not be empty.")
    out = ensure_spec(spec)
    var = {"name": str(name), "type": type, "default": default}
    for i, existing in enumerate(out["variables"]):
        if isinstance(existing, dict) and existing.get("name") == name:
            out["variables"][i] = var
            return out
    out["variables"].append(var)
    return out


def set_drilldown(
    spec: Any,
    widget_id: str,
    target_var: str,
    value_field: str,
) -> dict[str, Any]:
    """Configure a click-to-drilldown interaction on a widget.

    When the user clicks a row/point/slice of *widget_id*, the value of
    *value_field* (a column in the widget's result) is written to the dashboard
    variable *target_var* (which other widgets can bind via ``params``).  Stored
    under the widget's ``props.drilldown`` so the editor/renderer can wire the
    click handler.  The target variable should be declared via ``add_variable``.
    """
    if not target_var:
        raise SpecOpError("target_var must not be empty.")
    if not value_field:
        raise SpecOpError("value_field must not be empty.")
    out = ensure_spec(spec)
    widget = _find_widget(out, widget_id)
    props = widget.get("props")
    if not isinstance(props, dict):
        props = {}
    props["drilldown"] = {"target_var": target_var, "value_field": value_field}
    widget["props"] = props
    return out


__all__ = [
    "SpecOpError",
    "ensure_spec",
    "add_widget",
    "update_widget",
    "remove_widget",
    "set_widget_style",
    "set_layout",
    "set_background",
    "add_variable",
    "set_drilldown",
]
