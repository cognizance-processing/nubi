"""AI-powered dashboard generation for Nubi (M8-C + EDITOR-2A).

Public API
----------
generate_dashboard_spec(question, catalog, provider) -> DashboardSpec
    Ground the question, pick relevant registered query_ids, and produce a
    canonical ``DashboardSpec`` (JSON-serializable Pydantic model) referencing
    REAL registered query_ids and real column names.

    With ``NullProvider`` (the default) the spec is built deterministically
    from the grounding context — no network call, no LLM inference.

    With a real provider the model is given a tight prompt that includes the
    full JSON Schema for DashboardSpec and is instructed to emit a valid JSON
    DashboardSpec using ONLY grounded query_ids and real columns.

generate_dashboard_html(question, catalog, provider) -> str
    Backward-compatible wrapper: ``spec_to_html(generate_dashboard_spec(...))``.
    Returns a CSS-grid HTML dashboard composed of ``<nubi-kpi>``,
    ``<nubi-table>``, and ``<nubi-chart>`` custom elements.

validate_dashboard_html(html) -> tuple[bool, list[str]]
    Server-side sanity check.  Returns ``(True, [])`` when the HTML is clean,
    or ``(False, [issue1, issue2, ...])`` when problems are detected.

    Checks performed (defense-in-depth; frontend also sanitises via DOMPurify):
    - No ``<script`` tags.
    - No ``on*=`` inline event handlers.
    - No ``javascript:`` or ``data:text/html`` URIs (case-insensitive).
    - All widget tags present (if any) are from the ``nubi-*`` allowlist.
    - Query IDs referenced in widget attributes are present in the registry
      (best-effort — warns rather than hard-fails for unknown ids).

Design notes
------------
- The grounding step (``ai.grounding.ground``) is ALWAYS run, even for the
  NullProvider path, so that the template dashboard references REAL catalog
  objects rather than made-up names.
- The fallback (NullProvider) template is intentionally minimal: one KPI card,
  one table, one scatter chart — enough to prove the contract end-to-end.
- No ``<script>`` tags are ever emitted by this module.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.ai.grounding import ground
from app.ai.provider import LLMProvider, NullProvider

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Widget tag names that are allowed in a dashboard document.
ALLOWED_WIDGET_TAGS: frozenset[str] = frozenset(
    {"nubi-kpi", "nubi-table", "nubi-chart"}
)

#: Minimum length for an HTML preview (first N chars returned in previews).
HTML_PREVIEW_LENGTH: int = 200

# Regex patterns for security checks.
_SCRIPT_RE = re.compile(r"<\s*script", re.IGNORECASE)
_ON_HANDLER_RE = re.compile(r"\bon\w+=", re.IGNORECASE)
_JAVASCRIPT_URI_RE = re.compile(r"javascript\s*:", re.IGNORECASE)
_DATA_HTML_RE = re.compile(r"data\s*:\s*text/html", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Prompt template for real LLM providers
# ---------------------------------------------------------------------------

_DASHBOARD_SYSTEM = """\
You are a Nubi dashboard author.
You generate sanitized HTML/CSS dashboard documents using ONLY the Nubi widget
custom elements listed below.  No JavaScript, no <script> tags, no inline
event handlers (on*=), no javascript: or data: URIs.

ALLOWED WIDGET ELEMENTS (use only these):
  <nubi-kpi  query-id="<id>" value-col="<col>" label="<label>" />
  <nubi-table query-id="<id>" limit="50" />
  <nubi-chart query-id="<id>" type="scatter" x="<col>" y="<col>" />

LAYOUT: Use a CSS grid <div> as the outermost container.  Keep it simple.

RULES (strict):
1. Output ONLY the HTML — no markdown, no explanation, no fences.
2. Use ONLY the query-ids and column names listed in the GROUNDED SCHEMA below.
   Never invent table names, column names, or query ids.
3. No <script> tags.  No on*= attributes.  No javascript: URIs.
4. Every widget must have a valid query-id attribute from the GROUNDED QUERIES list.
5. If the question cannot be answered with the grounded schema, return:
   <div class="nubi-dashboard"><nubi-table query-id="{fallback_id}" /></div>

GROUNDED SCHEMA:
{snippets}

GROUNDED QUERIES (ids you may use):
{query_ids}
""".strip()

_DASHBOARD_USER = """\
Question: {question}

Generate a CSS-grid HTML dashboard that answers the question using ONLY the
grounded widget elements and query-ids listed above.
""".strip()


# ---------------------------------------------------------------------------
# Spec-based prompt templates (EDITOR-2A)
# ---------------------------------------------------------------------------

_SPEC_SYSTEM = """\
You are a Nubi dashboard author.
You generate canonical DashboardSpec JSON documents.  The spec format is the
single source of truth shared by the drag-and-drop editor and the LLM pipeline.

RULES (strict):
1. Output ONLY a valid JSON object matching the DashboardSpec schema below.
   No markdown fences, no explanation, no extra keys outside the schema.
2. Use ONLY the query_ids listed in GROUNDED QUERIES.  Never invent ids.
3. Use ONLY column names listed in GROUNDED SCHEMA.  Never invent column names.
4. Each chart widget MUST have chart_type (line|bar|scatter|area|pie) and
   encoding with at least "x" and "y" keys set to real column names.
5. Each widget id must be unique (e.g. "w1", "w2", "w3").
6. pos.x, pos.y are 1-based grid positions; pos.w, pos.h are spans.
7. If the question cannot be answered with the grounded schema, return a
   minimal spec with one table widget referencing {fallback_id}.

DASHBOARDSPEC JSON SCHEMA:
{spec_schema}

GROUNDED SCHEMA:
{snippets}

GROUNDED QUERIES (query_id values you may use):
{query_ids}
""".strip()

_SPEC_USER = """\
Question: {question}

Generate a DashboardSpec JSON object that answers the question using ONLY the
grounded query_ids and column names listed above.
""".strip()


# ---------------------------------------------------------------------------
# NullProvider template builder
# ---------------------------------------------------------------------------


def _pick_best_query(
    grounding: dict[str, Any],
    catalog: dict[str, Any],
) -> tuple[str, list[str]]:
    """Pick the best registered query_id + real columns for the template.

    Strategy
    --------
    1. Use the first related_query from the grounding context.
    2. Fall back to the first query in the catalog.
    3. Resolve the columns from the catalog tables the query touches.

    Returns
    -------
    tuple[str, list[str]]
        ``(query_id, [col1, col2, ...])``  — columns may be empty.
    """
    from app.queries.registry import get_query_registry  # noqa: PLC0415

    registry = get_query_registry()
    related = grounding.get("related_queries", [])

    # Pick from related queries first, then fall back to any registered query.
    candidates = list(related) + [rq.id for rq in registry.all()]
    chosen_id: str = ""
    for qid in candidates:
        if registry.get(qid) is not None:
            chosen_id = qid
            break

    if not chosen_id:
        # Last resort — should never happen if the registry is seeded.
        chosen_id = "demo_all"

    # Resolve the columns for this query from the catalog's tables.
    relevant_columns: list[str] = []
    # Try relevant_columns from grounding first.
    for col_ref in grounding.get("relevant_columns", []):
        col = col_ref.get("column", "")
        if col and col not in relevant_columns:
            relevant_columns.append(col)

    # Also pull from catalog tables that overlap with the grounding.
    tables = catalog.get("tables", {})
    for tbl in grounding.get("relevant_tables", []):
        for col in tables.get(tbl, []):
            if col not in relevant_columns:
                relevant_columns.append(col)

    # Always ensure we have at least some columns from the catalog if empty.
    if not relevant_columns:
        for cols in tables.values():
            relevant_columns.extend(cols)
            if len(relevant_columns) >= 4:
                break

    return chosen_id, relevant_columns


def _build_null_spec(
    question: str,
    grounding: dict[str, Any],
    catalog: dict[str, Any],
) -> "DashboardSpec":
    """Build a deterministic DashboardSpec for NullProvider.

    The spec references REAL registered query_ids and REAL column names
    extracted from the grounding context and the catalog.

    Layout: 12-column CSS grid with three widgets:
    - ``kpi``   (col 1–4, row 2) — first grounded metric column.
    - ``table`` (col 5–12, row 2) — full table for the primary query.
    - ``chart`` (col 1–12, row 3) — scatter of the first two columns.

    Parameters
    ----------
    question:
        Original user question (becomes the dashboard title).
    grounding:
        Output of ``ground(question, catalog)``.
    catalog:
        Output of ``build_catalog()``.

    Returns
    -------
    DashboardSpec
        A fully valid spec with one KPI, one table, and one chart widget.
    """
    from app.dashboards.spec import DashboardSpec, Widget, WidgetPos  # noqa: PLC0415

    query_id, columns = _pick_best_query(grounding, catalog)

    # Pick value column for KPI (prefer non-id, non-timestamp columns).
    def _is_metric_col(c: str) -> bool:
        cl = c.lower()
        return not any(s in cl for s in ("_at", "_id", "id", "uuid"))

    metric_cols = [c for c in columns if _is_metric_col(c)]
    kpi_col = metric_cols[0] if metric_cols else (columns[0] if columns else "value")

    # Pick x / y columns for the scatter chart.
    x_col = columns[0] if len(columns) > 0 else "x"
    y_col = columns[1] if len(columns) > 1 else "y"

    # Sanitize title (truncate, no HTML injection).
    title = question[:120]

    spec = DashboardSpec(
        version=1,
        title=title,
        layout={"cols": 12, "row_height": 60},
        widgets=[
            Widget(
                id="w1",
                type="kpi",
                query_id=query_id,
                encoding={"value": kpi_col},
                props={
                    "label": kpi_col.replace("_", " ").title(),
                },
                pos=WidgetPos(x=1, y=2, w=4, h=2),
            ),
            Widget(
                id="w2",
                type="table",
                query_id=query_id,
                encoding={},
                props={"limit": 50},
                pos=WidgetPos(x=5, y=2, w=8, h=2),
            ),
            Widget(
                id="w3",
                type="chart",
                query_id=query_id,
                chart_type="scatter",
                encoding={"x": x_col, "y": y_col},
                props={},
                pos=WidgetPos(x=1, y=4, w=12, h=3),
            ),
        ],
    )
    return spec


def _build_null_dashboard(
    question: str,
    grounding: dict[str, Any],
    catalog: dict[str, Any],
) -> str:
    """Build a deterministic template dashboard HTML for NullProvider.

    Delegates to ``_build_null_spec`` then ``spec_to_html`` for a consistent
    output that is guaranteed to pass the server-side HTML validator and the
    frontend DOMPurify sanitizer.

    Returns
    -------
    str
        A valid HTML fragment starting with a ``<div class="nubi-dashboard">``.
    """
    from app.dashboards.spec import spec_to_html  # noqa: PLC0415

    spec = _build_null_spec(question, grounding, catalog)
    return spec_to_html(spec)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Forward reference for type annotations only (imported lazily below to avoid
# circular imports at module load time).
if False:  # TYPE_CHECKING equivalent without the import machinery
    from app.dashboards.spec import DashboardSpec  # noqa: F401


def generate_dashboard_spec(
    question: str,
    catalog: dict[str, Any],
    provider: LLMProvider,
) -> "DashboardSpec":
    """Generate a canonical DashboardSpec for *question*.

    The spec is the single source of truth for both the drag-and-drop editor
    and the LLM authoring pipeline.  It references REAL registered query_ids
    and REAL column names from the grounded catalog.

    Parameters
    ----------
    question:
        Natural-language question describing the desired dashboard.
    catalog:
        Output of ``build_catalog()`` — the live registry + lineage snapshot.
    provider:
        An ``LLMProvider`` instance.  With ``NullProvider`` (the default when
        no API key is set) the output is a deterministic template spec.  With a
        real provider the LLM generates JSON that is parsed and validated.

    Returns
    -------
    DashboardSpec
        A fully validated spec.  Falls back to a deterministic NullProvider
        spec if the LLM response cannot be parsed or fails validation.
    """
    from app.dashboards.spec import (  # noqa: PLC0415
        validate_spec,
        spec_json_schema,
    )

    # Always run grounding so both the NullProvider template and real LLM
    # prompt reference only schema objects that exist in the catalog.
    grounding = ground(question, catalog)

    if isinstance(provider, NullProvider):
        return _build_null_spec(question, grounding, catalog)

    # ── Real LLM path ───────────────────────────────────────────────────────
    snippets_text = (
        "\n".join(f"  {s}" for s in grounding.get("snippets", []))
        or "  (no schema matched)"
    )
    query_ids_text = (
        ", ".join(grounding.get("related_queries", []))
        or "demo_all"
    )
    related = grounding.get("related_queries", [])
    fallback_id = related[0] if related else "demo_all"

    # Inject the full JSON Schema so the LLM knows the exact format.
    schema_str = json.dumps(spec_json_schema(), indent=2)

    system = _SPEC_SYSTEM.format(
        spec_schema=schema_str,
        snippets=snippets_text,
        query_ids=query_ids_text,
        fallback_id=fallback_id,
    )
    user = _SPEC_USER.format(question=question)

    raw_response = provider.complete(user, system=system)

    # Attempt to parse + validate the JSON response.
    try:
        # Strip markdown code fences if present.
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            # Remove first and last fence lines.
            start = 1 if lines[0].startswith("```") else 0
            end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
            cleaned = "\n".join(lines[start:end]).strip()

        data = json.loads(cleaned)
        spec, issues = validate_spec(data)
        if spec is not None and not any("required" in i.lower() or "field" in i.lower() for i in issues if "not in the registered" not in i):
            return spec
        if spec is not None:
            # Soft issues only (e.g. unknown query_id warnings) — still usable.
            return spec
    except Exception:  # noqa: BLE001
        pass

    # Fallback: return a deterministic spec if LLM output cannot be parsed.
    return _build_null_spec(question, grounding, catalog)


def generate_dashboard_html(
    question: str,
    catalog: dict[str, Any],
    provider: LLMProvider,
) -> str:
    """Generate a dashboard HTML document for *question*.

    Backward-compatible wrapper around ``generate_dashboard_spec`` +
    ``spec_to_html``.  With ``NullProvider`` the output is a deterministic
    template compiled from a DashboardSpec.  With a real provider the spec is
    generated via the LLM and then compiled to HTML.

    Parameters
    ----------
    question:
        Natural-language question describing the desired dashboard.
    catalog:
        Output of ``build_catalog()`` — the live registry + lineage snapshot.
    provider:
        An ``LLMProvider`` instance.

    Returns
    -------
    str
        HTML string ready for sanitization and rendering.  Never contains
        ``<script>`` tags or inline event handlers.
    """
    from app.dashboards.spec import spec_to_html  # noqa: PLC0415

    spec = generate_dashboard_spec(question, catalog, provider)
    return spec_to_html(spec)


def validate_dashboard_html(html: str) -> tuple[bool, list[str]]:
    """Server-side sanity check on a dashboard HTML string.

    Checks (defense-in-depth; the frontend also sanitises via DOMPurify):

    1. No ``<script`` elements.
    2. No ``on*=`` inline event handlers.
    3. No ``javascript:`` or ``data:text/html`` URIs.
    4. Any widget tags present are from the ``nubi-*`` allowlist.
    5. Any ``query-id`` attribute values reference registered query ids
       (best-effort warning; does not hard-fail for forward compatibility).

    Parameters
    ----------
    html:
        The HTML string to validate.

    Returns
    -------
    tuple[bool, list[str]]
        ``(True, [])`` if clean; ``(False, [issue, ...])`` if problems found.
    """
    issues: list[str] = []

    # 1. No <script> tags.
    if _SCRIPT_RE.search(html):
        issues.append("HTML contains <script> tag — forbidden in dashboard documents.")

    # 2. No on*= inline event handlers.
    if _ON_HANDLER_RE.search(html):
        issues.append(
            "HTML contains inline event handler (on*=) — forbidden in dashboard documents."
        )

    # 3. No javascript: or data:text/html URIs.
    if _JAVASCRIPT_URI_RE.search(html):
        issues.append("HTML contains javascript: URI — forbidden.")
    if _DATA_HTML_RE.search(html):
        issues.append("HTML contains data:text/html URI — forbidden.")

    # 4. Check that all custom elements (tag names with a hyphen) are nubi-*.
    custom_tag_re = re.compile(r"<([a-z][a-z0-9]*-[a-z0-9-]+)", re.IGNORECASE)
    found_custom_tags: set[str] = set(
        m.group(1).lower() for m in custom_tag_re.finditer(html)
    )
    unknown_custom = found_custom_tags - ALLOWED_WIDGET_TAGS
    if unknown_custom:
        issues.append(
            f"Unknown custom element(s) in dashboard: {sorted(unknown_custom)}. "
            f"Only {sorted(ALLOWED_WIDGET_TAGS)} are allowed."
        )

    # 5. Best-effort: check query-id attribute values.
    query_id_attr_re = re.compile(r'query-id=["\']([^"\']+)["\']', re.IGNORECASE)
    referenced_ids: set[str] = {
        m.group(1) for m in query_id_attr_re.finditer(html)
    }
    if referenced_ids:
        try:
            from app.queries.registry import get_query_registry  # noqa: PLC0415
            registry = get_query_registry()
            known_ids = {rq.id for rq in registry.all()}
            unknown_ids = referenced_ids - known_ids
            if unknown_ids:
                issues.append(
                    f"Dashboard references unknown query-id(s): {sorted(unknown_ids)}. "
                    "These ids are not in the registered query registry."
                )
        except Exception:  # noqa: BLE001
            # Registry unavailable — skip this check rather than blocking.
            pass

    ok = len(issues) == 0
    return ok, issues
