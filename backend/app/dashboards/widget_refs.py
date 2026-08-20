"""widget_refs.py — resolver for REFERENCE-BASED widget reuse in DashboardSpec.

A spec widget may be a REFERENCE (``Widget.ref`` set to a ``widgets`` library
row id) instead of fully inline — see ``app.dashboards.spec.Widget`` for the
field contract and ``validate_spec`` steps 13-15 for the static validation
rules (mutual exclusivity, "must set type or ref", dangling ``overrides``).

This module resolves references into effective, renderable widgets:

    effective = deep_merge(library_row.config, widget.overrides)

then FORCES the board-local fields (``id``, ``pos``, ``tab_id``,
``placement``, ``order``, ``drawer``, ``drawer_group``) from the SPEC entry —
never from the library row — because those describe where this particular
instance sits on THIS board, not what the widget is.

Kept out of ``app/dashboards/spec.py`` deliberately: ``spec.py`` has no DB/org
context (``validate_spec`` takes a raw dict, nothing else), while resolving a
``ref`` needs the caller's actual org-scoped ``widgets`` library rows.
``resolve_widget_refs`` itself is a PURE function over its arguments — no DB
calls, no imports of ``app.repos`` / ``app.routes`` — so it stays trivially
testable without a repo/DB. ``load_library_rows`` below is the one exception:
a thin, NOT-pure async loader (it calls ``repo.list``) that callers use to
build the ``library_rows`` dict this module's pure functions expect.

Missing / deleted references never raise: a ``ref`` that misses in
``library_rows`` degrades to a visible "broken" placeholder widget (a ``text``
widget that occupies the same board-local slot) and appends a soft
``"[warn] ..."`` issue — mirroring how ``query_id`` forward-references are
treated as warnings, not hard failures, elsewhere in this codebase.

Who resolves, who doesn't
--------------------------
``app.dashboards.collect.resolve_board_spec`` is the shared entry point that
wires ``load_library_rows`` + ``resolve_widget_refs`` together for a board row
— OUTPUT consumers (query/data collection, SVG/PDF render, embed snapshots,
CSV/JSON export) go through it so refs are never visible past that point. The
board READ endpoint (``GET /boards/{id}`` in ``app/routes/resources.py``) must
NOT resolve — the editor needs ``ref``/``overrides`` intact to show and edit
the reference itself.
"""

from __future__ import annotations

import copy
from typing import Any

from app.dashboards.spec import DashboardSpec, Widget

# Board-local fields: describe WHERE a widget instance sits on THIS board, not
# what the widget IS. Always forced from the spec's widget entry — a library
# row's own value for any of these (if one somehow leaked in when the entry
# was saved to the library) is never used. Values here are each field's
# Widget-model default, used when the spec entry omits the field outright.
_BOARD_LOCAL_DEFAULTS: dict[str, Any] = {
    "tab_id": None,
    "placement": "grid",
    "order": 0,
    "drawer": False,
    "drawer_group": None,
}


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Sparse deep merge: only keys present in *overrides* win.

    Dict-valued keys present in both *base* and *overrides* are merged
    key-by-key, recursively. Any other value in *overrides* (including lists)
    REPLACES the corresponding *base* value wholesale — lists never
    concatenate. Neither input is mutated; a fresh dict is returned.
    """
    result: dict[str, Any] = copy.deepcopy(base)
    for key, val in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def _broken_ref_widget(widget: Widget) -> Widget:
    """Return a visible 'broken reference' placeholder for *widget*.

    Keeps every board-local field so the widget still occupies its layout
    slot, but becomes a plain ``text`` widget with a short message — a type
    that can never itself fail to render (no query, no chart encoding, no
    data source of any kind), which is the point: a dangling reference must
    degrade gracefully, never crash the render pipeline.
    """
    return Widget(
        id=widget.id,
        type="text",
        ref=widget.ref,
        tab_id=widget.tab_id,
        pos=widget.pos,
        placement=widget.placement,
        drawer=widget.drawer,
        drawer_group=widget.drawer_group,
        order=widget.order,
        content=f"Widget reference unavailable: {widget.ref}",
        props={"broken_ref": True},
    )


def resolve_widget_refs(
    spec: DashboardSpec, library_rows: dict[str, dict[str, Any]]
) -> tuple[DashboardSpec, list[str]]:
    """Resolve every reference widget in *spec* against *library_rows*.

    Parameters
    ----------
    spec:
        A parsed :class:`~app.dashboards.spec.DashboardSpec`. Not mutated.
    library_rows:
        ``{library_widget_id: row}`` — the caller's ``widgets`` resource rows
        (e.g. from ``repo.list("widgets", org_id)``, keyed by ``row["id"]``).
        Each row is expected to carry a ``config`` dict (the reusable widget
        shape — see ``toLibraryConfig`` on the frontend). A row missing or
        with a non-dict ``config`` is treated the same as a missing ref.

    Returns
    -------
    tuple[DashboardSpec, list[str]]
        ``(resolved_spec, issues)``. ``resolved_spec`` is a NEW
        ``DashboardSpec`` — every inline widget passes through unchanged;
        every ``ref`` widget is replaced by its resolved effective widget (or
        a broken placeholder). ``issues`` collects one ``"[warn] ..."`` string
        per widget whose ``ref`` did not resolve — empty when every reference
        resolved cleanly. This function never raises for a bad/missing ref.
    """
    issues: list[str] = []
    resolved_widgets: list[Widget] = []

    for widget in spec.widgets:
        if widget.ref is None:
            resolved_widgets.append(widget)
            continue

        row = library_rows.get(widget.ref)
        config = row.get("config") if isinstance(row, dict) else None
        if not isinstance(config, dict):
            issues.append(
                f"[warn] Widget {widget.id!r}: ref {widget.ref!r} does not "
                "match any widget in the library (deleted, wrong org, or a "
                "forward reference) — rendering a broken placeholder."
            )
            resolved_widgets.append(_broken_ref_widget(widget))
            continue

        merged = _deep_merge(config, widget.overrides or {})

        # Meta fields describing the *unresolved* spec entry have no meaning
        # on the effective (resolved) widget — drop them before validating so
        # a library row that itself carries a stray ref/overrides (shouldn't
        # happen, but config is caller-controlled) can't chain/recurse.
        merged.pop("ref", None)
        merged.pop("overrides", None)

        # Force board-local fields from the SPEC entry, never the library row.
        merged["id"] = widget.id
        merged["pos"] = widget.pos
        for field, default in _BOARD_LOCAL_DEFAULTS.items():
            merged[field] = getattr(widget, field, default)

        if not isinstance(merged.get("type"), str):
            issues.append(
                f"[warn] Widget {widget.id!r}: ref {widget.ref!r} resolved "
                "without a widget 'type' (malformed library row) — rendering "
                "a broken placeholder."
            )
            resolved_widgets.append(_broken_ref_widget(widget))
            continue

        try:
            resolved_widgets.append(Widget.model_validate(merged))
        except Exception as exc:  # noqa: BLE001 — never crash the resolver
            issues.append(
                f"[warn] Widget {widget.id!r}: ref {widget.ref!r} resolved to "
                f"an invalid widget shape ({exc}) — rendering a broken "
                "placeholder."
            )
            resolved_widgets.append(_broken_ref_widget(widget))

    resolved_spec = spec.model_copy(update={"widgets": resolved_widgets})
    return resolved_spec, issues


def widget_ref_usage(
    spec: dict[str, Any], only_ref_id: str | None = None
) -> list[dict[str, str]]:
    """Collect ``{widget_id, ref}`` reference targets from a RAW spec dict.

    Mirrors ``app.dashboards.collect.widget_query_targets`` (same shape, same
    stepper-nesting walk) but for ``widget.ref`` instead of ``widget.query_id``
    — used by the "used by N" usage endpoints so a library widget's / board's
    reference count is computed the same way board exports already compute
    query usage. Operates on a plain dict (as returned by
    ``app.dashboards.collect.spec_from_board``), not a parsed
    :class:`~app.dashboards.spec.DashboardSpec`, so callers can scan many
    boards' stored specs without paying a full Pydantic parse per board.

    Parameters
    ----------
    spec:
        A raw dashboard spec dict (``{"widgets": [...], ...}``).
    only_ref_id:
        When given, only widgets whose ``ref`` equals this id are returned.
        ``None`` (default) returns every reference widget in the spec.

    Returns
    -------
    list[dict[str, str]]
        ``[{"widget_id": ..., "ref": ...}, ...]``, de-duplicated by
        ``(widget_id, ref)``.
    """
    widgets = spec.get("widgets")
    if not isinstance(widgets, list):
        return []

    targets: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _visit(w: Any) -> None:
        if not isinstance(w, dict):
            return
        rid = w.get("ref")
        if rid and not (only_ref_id and rid != only_ref_id):
            wid = str(w.get("id") or rid)
            key = (wid, str(rid))
            if key not in seen:
                seen.add(key)
                targets.append({"widget_id": wid, "ref": str(rid)})

        # Stepper children nest real widgets under props.steps[].widget — see
        # widget_query_targets in app.dashboards.collect for why this walk
        # matters (a nested widget's own ref must count too).
        steps = (w.get("props") or {}).get("steps")
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict):
                    _visit(step.get("widget"))

    for w in widgets:
        _visit(w)
    return targets


async def load_library_rows(org_id: str, repo: Any) -> dict[str, dict[str, Any]]:
    """Fetch the org's ``widgets`` library rows as ``{id: row}``.

    Thin async loader — the one function in this module that is NOT pure (it
    performs a DB read via *repo*). Kept separate from :func:`resolve_widget_refs`
    so the resolver itself stays a pure, trivially-testable function; callers
    typically do::

        library_rows = await load_library_rows(org_id, repo)
        resolved_spec, issues = resolve_widget_refs(spec, library_rows)

    ``repo.list`` is async DB I/O (asyncpg under ``PgRepo``), not a blocking
    call, so this needs no ``asyncio.to_thread`` wrapping.

    Parameters
    ----------
    org_id:
        The caller's resolved org id — rows are scoped to this org only, same
        as every other ``repo.list`` call in this codebase.
    repo:
        Active repository implementation (``app.repos.provider.Repo``).

    Returns
    -------
    dict[str, dict]
        ``{row_id: row}`` for every row in the org's ``widgets`` resource.
        Rows without a usable ``id`` are skipped rather than raising.
    """
    rows = await repo.list("widgets", org_id)
    return {
        str(row["id"]): row
        for row in rows
        if isinstance(row, dict) and row.get("id")
    }
