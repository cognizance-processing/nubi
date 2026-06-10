"""Repair-grade structured dashboard-spec issues for external AI agents.

This is a **pure** module (no FastAPI, no I/O beyond a best-effort read of the
query registry) that turns the plain-string issues produced by
``app.dashboards.spec.validate_spec`` into *structured, repair-oriented* issues.

Why
---
``validate_spec`` returns a ``list[str]`` of human-readable issues such as::

    "Field 'widgets.0.chart_type': Field required"
    "Widget 'w1' (chart): encoding must include 'x' column."
    "Widget 'w2': query_id 'nope' is not in the registered query registry ..."

A human can read those, but an LLM agent fixing a spec in **one round-trip**
needs more: a stable machine ``code``, a normalised JSON ``path`` it can address
(``widgets[2].encoding.x``), a ``severity``, a one-line ``suggestion``, and —
crucially — ``valid_options`` (the real column names of the bound query, or the
list of known query ids) so it can pick a valid value without a second call.

``to_structured_issues(spec_data, raw_issues)`` is the single entry point.  It
is intentionally robust: if a raw string does not match any known shape it falls
back to a generic ``StructuredIssue`` that simply carries the raw message — it
never raises on unexpected input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# StructuredIssue
# ---------------------------------------------------------------------------


@dataclass
class StructuredIssue:
    """One repair-oriented validation issue.

    Attributes
    ----------
    path:
        JSON path to the offending value, e.g. ``"widgets[2].encoding.x"`` or
        ``"title"``.  ``""`` when the issue is not bound to a specific path.
    code:
        Stable machine code (e.g. ``missing_encoding_x``, ``unknown_query_id``,
        ``type_error``, ``missing_field``, ``duplicate_widget_id``,
        ``undeclared_var_ref``, ``unknown_tab_id``, ``generic``).  Agents should
        branch on this — it is part of the contract and changes deliberately.
    message:
        The human-readable message (the original raw string, lightly cleaned).
    severity:
        ``"error"`` (hard — spec is invalid) or ``"warning"`` (soft — spec
        still usable, e.g. an unknown query_id that may be a forward reference).
    suggestion:
        One-line fix hint, or ``None`` when there is nothing useful to add.
    valid_options:
        Concrete values the agent may choose from to fix the issue (e.g. the
        output columns of the bound query, or the known query ids), or ``None``.
    """

    path: str
    code: str
    message: str
    severity: str  # "error" | "warning"
    suggestion: str | None = None
    valid_options: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain JSON-able dict (for the API response body)."""
        return {
            "path": self.path,
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "suggestion": self.suggestion,
            "valid_options": self.valid_options,
        }


# ---------------------------------------------------------------------------
# Raw-string parsers
# ---------------------------------------------------------------------------
#
# validate_spec emits a small, fixed family of string shapes.  We pattern-match
# each one to recover (path, code) and enrich where cheap.  Everything is
# best-effort: an unmatched string becomes a generic issue carrying the raw text.

# Pydantic parse errors:  "Field 'widgets.0.chart_type': Field required"
_RE_FIELD = re.compile(r"^Field '([^']*)':\s*(.*)$")

# "Widget 'w1' (chart): encoding must include 'x' column."
_RE_WIDGET_TYPED = re.compile(r"^Widget '([^']*)' \((\w+)\):\s*(.*)$")

# "Widget 'w1' param 'p': ref 'v' is not declared in spec 'variables'. ..."
_RE_WIDGET_PARAM = re.compile(
    r"^Widget '([^']*)' param '([^']*)':\s*ref '([^']*)' is not declared.*$"
)

# "Widget 'w1': query_id 'nope' is not in the registered query registry ..."
# "Widget 'w1': options_query_id 'nope' is not in ..."
# "Widget 'w1': tab_id 't9' is not declared in spec 'tabs'. ..."
_RE_WIDGET_FIELD = re.compile(
    r"^Widget '([^']*)':\s*(query_id|options_query_id|tab_id) '([^']*)' (.*)$"
)

# "Duplicate widget id 'w1' — widget ids must be unique."
_RE_DUP_WIDGET = re.compile(r"^Duplicate widget id '([^']*)'")

# "Duplicate tab id 't1' — tab ids must be unique."
_RE_DUP_TAB = re.compile(r"^Duplicate tab id '([^']*)'")


def _json_path_from_pydantic_loc(loc: str) -> str:
    """Convert a dot-joined pydantic loc into a JSON path with [] indices.

    ``"widgets.0.chart_type"`` -> ``"widgets[2].chart_type"``-style output, i.e.
    numeric segments become ``[n]`` suffixes on the preceding key.  ``"title"``
    stays ``"title"``.
    """
    parts = loc.split(".")
    out = ""
    for part in parts:
        if part.isdigit():
            out += f"[{part}]"
        elif out:
            out += f".{part}"
        else:
            out = part
    return out


def _widget_index(spec_data: dict[str, Any], widget_id: str) -> int | None:
    """Return the index of the widget with *widget_id* in ``spec_data.widgets``."""
    widgets = spec_data.get("widgets")
    if not isinstance(widgets, list):
        return None
    for i, w in enumerate(widgets):
        if isinstance(w, dict) and w.get("id") == widget_id:
            return i
    return None


def _widget_by_id(spec_data: dict[str, Any], widget_id: str) -> dict[str, Any] | None:
    """Return the raw widget dict with *widget_id*, or ``None``."""
    widgets = spec_data.get("widgets")
    if not isinstance(widgets, list):
        return None
    for w in widgets:
        if isinstance(w, dict) and w.get("id") == widget_id:
            return w
    return None


def _widget_path(spec_data: dict[str, Any], widget_id: str) -> str:
    """JSON path to a widget by id — ``widgets[<idx>]`` or ``widgets[id=...]``.

    Falls back to an id-keyed path when the widget cannot be located by index
    (e.g. it failed to parse), so the path is always meaningful to the agent.
    """
    idx = _widget_index(spec_data, widget_id)
    if idx is not None:
        return f"widgets[{idx}]"
    return f"widgets[id={widget_id!r}]"


# ---------------------------------------------------------------------------
# Registry enrichment (best-effort — never raises, never required)
# ---------------------------------------------------------------------------


def _known_query_ids() -> list[str]:
    """Return all registered query ids (sorted), or ``[]`` if unavailable."""
    try:
        from app.queries.registry import get_query_registry  # noqa: PLC0415

        return sorted(rq.id for rq in get_query_registry().all())
    except Exception:  # noqa: BLE001 — registry optional; enrichment is best-effort
        return []


def _query_output_columns(query_id: str) -> list[str] | None:
    """Return the declared output-column names for *query_id*, or ``None``.

    Looks the query up in the live registry and reads its ``output_schema``
    (a tuple of ``OutputColumn``).  Returns ``None`` when the query is unknown
    or declares no output schema (so the agent gets columns only when they are
    actually known — never a misleading empty list).
    """
    try:
        from app.queries.registry import get_query_registry  # noqa: PLC0415

        rq = get_query_registry().get(query_id)
        if rq is None or not rq.output_schema:
            return None
        return [col.name for col in rq.output_schema]
    except Exception:  # noqa: BLE001 — best-effort enrichment
        return None


def _bound_query_id(widget: dict[str, Any] | None) -> str | None:
    """Return the (non-empty) query_id a widget is bound to, or ``None``."""
    if not isinstance(widget, dict):
        return None
    qid = widget.get("query_id")
    if isinstance(qid, str) and qid:
        return qid
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def to_structured_issues(
    spec_data: dict[str, Any], raw_issues: list[str]
) -> list[StructuredIssue]:
    """Convert ``validate_spec`` string issues into structured, repair issues.

    Parameters
    ----------
    spec_data:
        The raw spec dict that was validated.  Used to resolve widget indices
        (for stable JSON paths) and the query a widget is bound to (for column
        enrichment).  May be any dict — never trusted to be well-formed.
    raw_issues:
        The ``list[str]`` returned by ``validate_spec``.

    Returns
    -------
    list[StructuredIssue]
        One structured issue per raw issue, in the same order.
    """
    if not isinstance(spec_data, dict):
        spec_data = {}

    out: list[StructuredIssue] = []
    for raw in raw_issues:
        try:
            out.append(_structure_one(spec_data, raw))
        except Exception:  # noqa: BLE001 — never let one bad parse break the batch
            out.append(
                StructuredIssue(
                    path="",
                    code="generic",
                    message=str(raw),
                    severity="error",
                )
            )
    return out


def _structure_one(spec_data: dict[str, Any], raw: str) -> StructuredIssue:
    """Structure a single raw issue string (the parsing core)."""
    text = str(raw)

    # Some callers may prefix soft issues with "[warn]" — honour it as an
    # explicit severity override and strip it before pattern matching.
    forced_warning = False
    if text.lower().startswith("[warn]"):
        forced_warning = True
        text = text[len("[warn]"):].strip()

    issue = _classify(spec_data, text)
    if forced_warning:
        issue.severity = "warning"
    return issue


def _classify(spec_data: dict[str, Any], text: str) -> StructuredIssue:
    """Best-effort classification of a (warn-prefix-stripped) raw issue string."""

    # ── Pydantic field error: "Field 'loc': msg" ──────────────────────────
    m = _RE_FIELD.match(text)
    if m:
        loc, msg = m.group(1), m.group(2)
        path = _json_path_from_pydantic_loc(loc)
        lower = msg.lower()
        if "required" in lower:
            code = "missing_field"
            suggestion = f"Add the required '{loc.split('.')[-1]}' field."
        elif "input should be" in lower or "valid" in lower or "type" in lower:
            code = "type_error"
            suggestion = "Fix the value's type/allowed values; see message."
        else:
            code = "field_error"
            suggestion = None
        return StructuredIssue(
            path=path,
            code=code,
            message=text,
            severity="error",
            suggestion=suggestion,
        )

    # ── Widget param ref: undeclared {ref} variable ───────────────────────
    m = _RE_WIDGET_PARAM.match(text)
    if m:
        widget_id, param_name, _ref = m.group(1), m.group(2), m.group(3)
        declared = _declared_variable_names(spec_data)
        return StructuredIssue(
            path=f"{_widget_path(spec_data, widget_id)}.params.{param_name}",
            code="undeclared_var_ref",
            message=text,
            severity="error",
            suggestion=(
                "Reference a declared spec variable, or add it to spec.variables."
            ),
            valid_options=declared or None,
        )

    # ── Widget self-reference field: query_id / options_query_id / tab_id ──
    m = _RE_WIDGET_FIELD.match(text)
    if m:
        widget_id, field_name, _bad_val = m.group(1), m.group(2), m.group(3)
        base = _widget_path(spec_data, widget_id)
        if field_name in ("query_id", "options_query_id"):
            return StructuredIssue(
                path=f"{base}.{field_name}",
                # query_id and options_query_id are SOFT warnings in validate_spec
                # (forward-compat) — keep that severity here.
                code="unknown_query_id",
                message=text,
                severity="warning",
                suggestion="Use one of the registered query ids in valid_options.",
                valid_options=_known_query_ids() or None,
            )
        # tab_id is a HARD error in validate_spec.
        return StructuredIssue(
            path=f"{base}.tab_id",
            code="unknown_tab_id",
            message=text,
            severity="error",
            suggestion="Reference a tab id declared in spec.tabs.",
            valid_options=_declared_tab_ids(spec_data) or None,
        )

    # ── Typed widget requirement: "Widget 'w1' (chart): ..." ──────────────
    m = _RE_WIDGET_TYPED.match(text)
    if m:
        widget_id, _wtype, detail = m.group(1), m.group(2), m.group(3)
        return _classify_widget_requirement(spec_data, widget_id, detail, text)

    # ── Duplicate widget id ───────────────────────────────────────────────
    m = _RE_DUP_WIDGET.match(text)
    if m:
        widget_id = m.group(1)
        return StructuredIssue(
            path=_widget_path(spec_data, widget_id),
            code="duplicate_widget_id",
            message=text,
            # Duplicate ids are a soft warning in validate_spec.
            severity="warning",
            suggestion="Give each widget a unique 'id'.",
        )

    # ── Duplicate tab id ──────────────────────────────────────────────────
    m = _RE_DUP_TAB.match(text)
    if m:
        return StructuredIssue(
            path="tabs",
            code="duplicate_tab_id",
            message=text,
            severity="error",
            suggestion="Give each tab a unique 'id'.",
        )

    # ── Unmatched — generic fallback (never lose the message) ─────────────
    return StructuredIssue(
        path="",
        code="generic",
        message=text,
        severity="error",
    )


def _classify_widget_requirement(
    spec_data: dict[str, Any], widget_id: str, detail: str, full_text: str
) -> StructuredIssue:
    """Classify a 'Widget 'wX' (type): <detail>' requirement issue."""
    base = _widget_path(spec_data, widget_id)
    widget = _widget_by_id(spec_data, widget_id)
    lower = detail.lower()

    # Chart encoding: must include 'x' / 'y' column.  THIS is the high-value
    # case: enrich with the bound query's real columns so the agent can pick a
    # valid encoding in one round-trip.
    if "encoding must include 'x'" in lower:
        return _encoding_issue(base, widget, "x", full_text)
    if "encoding must include 'y'" in lower:
        return _encoding_issue(base, widget, "y", full_text)

    # Chart needs a chart_type.
    if "chart_type" in lower:
        return StructuredIssue(
            path=f"{base}.chart_type",
            code="missing_chart_type",
            message=full_text,
            severity="error",
            suggestion="Set 'chart_type' (e.g. 'line', 'bar', 'scatter').",
            valid_options=[
                "line", "bar", "hbar", "scatter",
                "area", "pie", "donut", "heatmap", "gauge",
            ],
        )

    # Filter needs subtype / target_var.
    if "subtype" in lower:
        return StructuredIssue(
            path=f"{base}.subtype",
            code="missing_filter_subtype",
            message=full_text,
            severity="error",
            suggestion="Set 'subtype' for the filter widget.",
            valid_options=["select", "multiselect", "daterange", "text"],
        )
    if "target_var" in lower:
        return StructuredIssue(
            path=f"{base}.target_var",
            code="missing_target_var",
            message=full_text,
            severity="error",
            suggestion="Set 'target_var' to the variable this filter writes to.",
            valid_options=_declared_variable_names(spec_data) or None,
        )

    # Text needs content.
    if "content" in lower:
        return StructuredIssue(
            path=f"{base}.content",
            code="missing_content",
            message=full_text,
            severity="error",
            suggestion="Set 'content' (markdown) for the text widget.",
        )

    # Unknown typed-widget detail — keep it but tag it.
    return StructuredIssue(
        path=base,
        code="widget_requirement",
        message=full_text,
        severity="error",
    )


def _encoding_issue(
    base_path: str, widget: dict[str, Any] | None, axis: str, full_text: str
) -> StructuredIssue:
    """Build a structured issue for a missing chart encoding axis ('x'/'y').

    Populates ``valid_options`` with the bound query's real output columns when
    available — the single most useful enrichment for one-round-trip repair.
    """
    qid = _bound_query_id(widget)
    cols = _query_output_columns(qid) if qid else None
    if cols:
        suggestion = (
            f"Set encoding.{axis} to one of the bound query's columns "
            f"(see valid_options)."
        )
    else:
        suggestion = f"Set encoding.{axis} to a column name returned by the query."
    return StructuredIssue(
        path=f"{base_path}.encoding.{axis}",
        code=f"missing_encoding_{axis}",
        message=full_text,
        severity="error",
        suggestion=suggestion,
        valid_options=cols,
    )


# ---------------------------------------------------------------------------
# Small spec-introspection helpers (raw-dict, never trusted to be well-formed)
# ---------------------------------------------------------------------------


def _declared_variable_names(spec_data: dict[str, Any]) -> list[str]:
    """Return declared spec.variables names (sorted), best-effort."""
    out: list[str] = []
    for v in spec_data.get("variables", []) or []:
        if isinstance(v, dict) and isinstance(v.get("name"), str):
            out.append(v["name"])
    return sorted(out)


def _declared_tab_ids(spec_data: dict[str, Any]) -> list[str]:
    """Return declared spec.tabs ids (sorted), best-effort."""
    out: list[str] = []
    for t in spec_data.get("tabs", []) or []:
        if isinstance(t, dict) and isinstance(t.get("id"), str):
            out.append(t["id"])
    return sorted(out)
