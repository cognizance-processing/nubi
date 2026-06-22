"""AI-powered Canvas document generation and editing for Nubi (Canvas Wave 6).

Public API
----------
generate_canvas_doc(question, catalog, provider, model) -> CanvasDoc
    Ground the question, pick relevant registered query_ids/metrics, and produce
    a canonical CanvasDoc (JSON-serializable Pydantic model) with valid HTML
    using the nubi-* element vocabulary and a bindings{} side map.

    With NullProvider (the default) the doc is built deterministically from the
    grounding context — no network call, no LLM inference.

    With a real provider the model is given a tight system prompt that includes
    the full JSON Schema for CanvasDoc + the element/binding vocabulary and is
    instructed to emit a valid JSON CanvasDoc using ONLY grounded query_ids and
    real columns.  The output is run through a generate -> validate -> repair
    loop: hard validation errors are fed back to the model for up to
    MAX_CANVAS_REPAIR_ROUNDS attempts.  If the doc is STILL invalid after the
    final round the failure is surfaced LOUDLY (AppError with structured issues)
    rather than being silently swallowed by the NullProvider template.

edit_canvas_doc(doc, instruction, catalog, provider, model) -> CanvasDoc
    Diff-style edit of an existing CanvasDoc ("bind this KPI to revenue",
    "make the header red").  The model receives the current doc + the instruction
    and returns an updated CanvasDoc JSON.  Same validate/repair loop.

canvas_doc_json_schema() -> dict
    Return the JSON Schema for CanvasDoc (Pydantic v2 output).

Design notes
------------
- Mirrors app/ai/dashboard.py (generate_dashboard_spec + repair loop pattern).
- NullProvider template: one KPI card + one table using the first grounded query.
- Real LLM path uses the same FakeProvider-compatible complete() interface.
- No <script> tags are ever emitted by this module.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.ai.grounding import ground
from app.ai.provider import LLMProvider, NullProvider

logger = logging.getLogger("nubi.ai.canvas")

#: Maximum number of generation attempts for the real-provider path (the first
#: generation plus repair rounds).
MAX_CANVAS_REPAIR_ROUNDS: int = 3

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_CANVAS_SYSTEM = """\
You are a Nubi Canvas author.
You generate canonical CanvasDoc JSON documents.  A CanvasDoc is an HTML-native
document with a side bindings map that wires elements to live queries/metrics.

CANVAS ELEMENT VOCABULARY (nubi-* custom elements only):
  <nubi-kpi   data-el-id="<id>">...</nubi-kpi>       — single KPI metric card
  <nubi-table data-el-id="<id>">...</nubi-table>      — tabular data grid
  <nubi-chart data-el-id="<id>">...</nubi-chart>      — chart widget
  <nubi-metric data-el-id="<id>">...</nubi-metric>    — semantic-layer metric
  <nubi-filter data-el-id="<id>">...</nubi-filter>    — variable filter control
  <nubi-text  data-el-id="<id>">...</nubi-text>       — rich-text/markdown panel
  <nubi-value data-el-id="<id>">...</nubi-value>      — inline scalar value

Plain HTML elements (div, section, h1, p, span, ul, li, table, tr, td, etc.)
are ALLOWED in the HTML but MUST NOT carry on*= handlers, javascript: URIs,
<script>, <style>, <iframe>, <object>, <embed>, <link>, <base>, or <form> tags.

BINDING KINDS (side map, keyed by data-el-id):
  "query"   -> {{ "kind": "query",  "query_id": "<id>", "params": {{}},
                 "field": "<col>", "format": "currency|number|percent|date" }}
  "metric"  -> {{ "kind": "metric", "metric_id": "<id>",
                 "dimensions": [], "time_grain": "month", "filters": {{}} }}
  "api"     -> {{ "kind": "api",    "connector_id": "<id>", "path": "/v1/x",
                 "select": "$.data[0].value" }}

RULES (strict):
1. Output ONLY a valid JSON object matching the CanvasDoc schema below.
   No markdown fences, no explanation, no extra keys outside the schema.
2. Every data-bound nubi-* element MUST carry a unique data-el-id attribute.
3. Every data-el-id in the HTML MUST have a corresponding entry in bindings.
4. Use ONLY the query_ids listed in GROUNDED QUERIES.  Never invent ids.
5. Use ONLY column names listed in GROUNDED SCHEMA.  Never invent column names.
6. No <script> tags.  No on*= attributes.  No javascript: URIs.
7. If the question cannot be answered with the grounded schema, return a
   minimal doc with one nubi-table bound to {fallback_id}.

CANVASDOC JSON SCHEMA:
{doc_schema}

GROUNDED SCHEMA:
{snippets}

GROUNDED QUERIES (query_id values you may use):
{query_ids}
""".strip()

_CANVAS_USER = """\
Question: {question}

Generate a CanvasDoc JSON object that answers the question using ONLY the
grounded query_ids and column names listed above.
""".strip()

_CANVAS_REPAIR_USER = """\
Your previous CanvasDoc JSON had these validation errors:
{issues}

Return a corrected CanvasDoc JSON object that fixes ALL of the errors above.
Output ONLY the JSON object — no markdown fences, no explanation.
""".strip()

_CANVAS_EDIT_SYSTEM = """\
You are a Nubi Canvas editor.
You edit an existing CanvasDoc JSON document by applying a natural-language
instruction (e.g. "bind the KPI to revenue", "make the header red",
"add a table showing orders").

CANVAS ELEMENT VOCABULARY AND BINDING RULES are the same as when generating:
  - nubi-* custom elements with data-el-id attributes for data-bound elements
  - bindings side map keyed by data-el-id
  - ONLY query_ids and column names from the GROUNDED SCHEMA below
  - No <script>, no on*=, no javascript: URIs

RULES (strict):
1. Output ONLY a valid JSON object matching the CanvasDoc schema.
   No markdown fences, no explanation.
2. Return the COMPLETE updated document (not a diff) — include ALL existing
   elements and bindings, modifying only what the instruction asks.
3. Maintain all existing data-el-id values; assign new unique ones for any
   new elements you add.
4. Use ONLY query_ids and column names from GROUNDED QUERIES / GROUNDED SCHEMA.

CANVASDOC JSON SCHEMA:
{doc_schema}

GROUNDED SCHEMA:
{snippets}

GROUNDED QUERIES (query_id values you may use):
{query_ids}
""".strip()

_CANVAS_EDIT_USER = """\
Current CanvasDoc:
{current_doc}

Instruction: {instruction}

Return the updated CanvasDoc JSON object.
""".strip()

_CANVAS_EDIT_REPAIR_USER = """\
Your updated CanvasDoc JSON had these validation errors:
{issues}

Return a corrected CanvasDoc JSON object that fixes ALL of the errors above.
Output ONLY the JSON object — no markdown fences, no explanation.
""".strip()


# ---------------------------------------------------------------------------
# Hard/soft error classification (mirrors dashboard.py)
# ---------------------------------------------------------------------------


def _is_canvas_warning(issue: str) -> bool:
    """Return True when *issue* is a soft warning rather than a hard error.

    Mirrors ``_is_warning`` in ai/dashboard.py:
    - ``[warn]`` prefix = soft warning (documented convention).
    - Registry "not in the registered" messages = soft forward-ref warnings.
    """
    stripped = issue.lstrip()
    if stripped.lower().startswith("[warn]"):
        return True
    return "not in the registered" in issue


def _hard_canvas_errors(issues: list[str]) -> list[str]:
    """Filter *issues* down to the hard (blocking) errors only."""
    return [i for i in issues if not _is_canvas_warning(i)]


def _strip_fences(raw: str) -> str:
    """Strip leading/trailing markdown code fences from an LLM JSON response."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        cleaned = "\n".join(lines[start:end]).strip()
    return cleaned


# ---------------------------------------------------------------------------
# NullProvider template builder
# ---------------------------------------------------------------------------


def _build_null_canvas_doc(
    question: str,
    grounding: dict[str, Any],
    catalog: dict[str, Any],
) -> "CanvasDoc":
    """Build a deterministic CanvasDoc for NullProvider.

    Layout: a simple HTML section with a heading, one nubi-kpi, and one
    nubi-table, both bound to the first grounded (registered) query_id.

    Returns a fully valid CanvasDoc with bindings referencing REAL catalog objects.
    """
    from app.dashboards.canvas import CanvasDoc  # noqa: PLC0415

    # Pick the best query from grounding (mirrors dashboard.py _pick_best_query).
    from app.queries.registry import get_query_registry  # noqa: PLC0415

    registry = get_query_registry()
    related = grounding.get("related_queries", [])
    candidates = list(related) + [rq.id for rq in registry.all()]
    query_id = ""
    for qid in candidates:
        if registry.get(qid) is not None:
            query_id = qid
            break
    if not query_id:
        query_id = "demo_all"

    # Pick a good value column (prefer non-id, non-timestamp).
    columns: list[str] = []
    for col_ref in grounding.get("relevant_columns", []):
        col = col_ref.get("column", "")
        if col and col not in columns:
            columns.append(col)
    tables = catalog.get("tables", {})
    for tbl in grounding.get("relevant_tables", []):
        for col in tables.get(tbl, []):
            if col not in columns:
                columns.append(col)
    if not columns:
        for cols in tables.values():
            columns.extend(cols)
            if len(columns) >= 4:
                break

    def _is_metric_col(c: str) -> bool:
        cl = c.lower()
        return not any(s in cl for s in ("_at", "_id", "id", "uuid"))

    metric_cols = [c for c in columns if _is_metric_col(c)]
    kpi_col = metric_cols[0] if metric_cols else (columns[0] if columns else "value")

    title = question[:120]

    html = (
        f'<section class="nubi-canvas">'
        f'<h1>{title}</h1>'
        f'<nubi-kpi data-el-id="el_kpi_1"></nubi-kpi>'
        f'<nubi-table data-el-id="el_table_1"></nubi-table>'
        f"</section>"
    )

    bindings: dict[str, Any] = {
        "el_kpi_1": {
            "kind": "query",
            "query_id": query_id,
            "params": {},
            "field": kpi_col,
            "format": "number",
        },
        "el_table_1": {
            "kind": "query",
            "query_id": query_id,
            "params": {},
            "field": None,
            "format": None,
        },
    }

    return CanvasDoc(
        version=1,
        title=title,
        html=html,
        bindings=bindings,  # type: ignore[arg-type]
        variables=[],
        assets={},
    )


# ---------------------------------------------------------------------------
# JSON Schema helper
# ---------------------------------------------------------------------------


def canvas_doc_json_schema() -> dict[str, Any]:
    """Return the JSON Schema for CanvasDoc (Pydantic v2 model_json_schema)."""
    from app.dashboards.canvas import CanvasDoc  # noqa: PLC0415

    return CanvasDoc.model_json_schema()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Forward reference (imported lazily to avoid circular imports).
if False:  # TYPE_CHECKING equivalent
    from app.dashboards.canvas import CanvasDoc  # noqa: F401


def generate_canvas_doc(
    question: str,
    catalog: dict[str, Any],
    provider: LLMProvider,
    model: str | None = None,
) -> "CanvasDoc":
    """Generate a canonical CanvasDoc for *question*.

    Parameters
    ----------
    question:
        Natural-language question describing the desired canvas.
    catalog:
        Output of ``build_catalog()`` — the live registry + lineage snapshot.
    provider:
        An ``LLMProvider`` instance.  With ``NullProvider`` the output is a
        deterministic template.  With a real provider the LLM generates JSON
        that is parsed and validated via the repair loop.
    model:
        Optional per-request model id.  ``None`` uses the provider's default.

    Returns
    -------
    CanvasDoc
        A validated CanvasDoc.  The returned instance carries introspectable
        metadata (NOT part of ``model_dump()``):

        - ``generated_by``  — ``"null_template"`` or ``"llm"``
        - ``valid``         — ``True`` when no hard errors remain
        - ``repair_rounds`` — number of follow-up repair attempts
        - ``issues``        — remaining validation issues

    Raises
    ------
    AppError("canvas_generation_failed", 422)
        Real-provider path ONLY: when the LLM output still fails validation
        after the maximum number of repair rounds.
    """
    from app.dashboards.canvas import CanvasDoc, validate_canvas_doc  # noqa: PLC0415

    grounding = ground(question, catalog)

    if isinstance(provider, NullProvider):
        doc = _build_null_canvas_doc(question, grounding, catalog)
        return _attach_canvas_meta(
            doc,
            generated_by="null_template",
            valid=True,
            repair_rounds=0,
            issues=[],
        )

    # ── Real LLM path ──────────────────────────────────────────────────────
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

    schema_str = json.dumps(canvas_doc_json_schema(), indent=2)

    system = _CANVAS_SYSTEM.format(
        doc_schema=schema_str,
        snippets=snippets_text,
        query_ids=query_ids_text,
        fallback_id=fallback_id,
    )

    user = _CANVAS_USER.format(question=question)
    last_doc: "CanvasDoc | None" = None
    last_issues: list[str] = []

    for attempt in range(MAX_CANVAS_REPAIR_ROUNDS):
        raw_response = (
            provider.complete(user, system=system, model=model)
            if model
            else provider.complete(user, system=system)
        )

        doc: "CanvasDoc | None"
        try:
            data = json.loads(_strip_fences(raw_response))
            doc = CanvasDoc(**data)
            ok, issues = validate_canvas_doc(doc)
        except Exception as exc:  # noqa: BLE001 — malformed/non-JSON output
            doc, issues = None, [f"Response was not valid JSON or CanvasDoc: {exc}"]

        hard = _hard_canvas_errors(issues) if doc is not None else issues

        if doc is not None and not hard:
            return _attach_canvas_meta(
                doc,
                generated_by="llm",
                valid=True,
                repair_rounds=attempt,
                issues=issues,
            )

        last_doc, last_issues = doc, issues

        next_round = attempt + 1
        if next_round < MAX_CANVAS_REPAIR_ROUNDS:
            logger.info(
                "canvas repair round %d/%d — feeding back %d hard error(s): %s",
                next_round,
                MAX_CANVAS_REPAIR_ROUNDS - 1,
                len(hard),
                hard,
            )
            user = _CANVAS_REPAIR_USER.format(
                issues="\n".join(f"- {i}" for i in hard)
                or "- (response was not valid JSON or CanvasDoc)",
            )

    # ── Loud failure ───────────────────────────────────────────────────────
    repair_rounds = MAX_CANVAS_REPAIR_ROUNDS - 1
    final_hard = _hard_canvas_errors(last_issues) if last_doc is not None else last_issues
    logger.warning(
        "canvas generation FAILED after %d repair round(s); %d hard error(s) remain: %s",
        repair_rounds,
        len(final_hard),
        final_hard,
    )

    from app.errors import AppError  # noqa: PLC0415

    raise AppError(
        "canvas_generation_failed",
        "LLM canvas generation failed validation after "
        f"{repair_rounds} repair round(s). Issues: {final_hard}",
        422,
    )


def edit_canvas_doc(
    doc: "CanvasDoc",
    instruction: str,
    catalog: dict[str, Any],
    provider: LLMProvider,
    model: str | None = None,
) -> "CanvasDoc":
    """Apply a natural-language edit instruction to an existing CanvasDoc.

    The model receives the full current doc + the instruction and returns an
    updated CanvasDoc JSON.  Same validate/repair loop as ``generate_canvas_doc``.

    Parameters
    ----------
    doc:
        The existing CanvasDoc to edit.
    instruction:
        Natural-language edit instruction (e.g. "bind the KPI to revenue",
        "make the header red", "add a chart showing monthly sales").
    catalog:
        Output of ``build_catalog()``.
    provider:
        An ``LLMProvider`` instance.  With ``NullProvider`` the doc is returned
        unchanged (no-op template path).
    model:
        Optional per-request model id.

    Returns
    -------
    CanvasDoc
        The updated (and re-validated) CanvasDoc.

    Raises
    ------
    AppError("canvas_edit_failed", 422)
        Real-provider path ONLY: when the LLM output still fails validation
        after the maximum number of repair rounds.
    """
    from app.dashboards.canvas import CanvasDoc, validate_canvas_doc  # noqa: PLC0415

    if isinstance(provider, NullProvider):
        # NullProvider: return the doc unchanged (no-op; deterministic path).
        return _attach_canvas_meta(
            doc,
            generated_by="null_template",
            valid=True,
            repair_rounds=0,
            issues=[],
        )

    grounding = ground(instruction, catalog)

    snippets_text = (
        "\n".join(f"  {s}" for s in grounding.get("snippets", []))
        or "  (no schema matched)"
    )
    query_ids_text = (
        ", ".join(grounding.get("related_queries", []))
        or "demo_all"
    )

    schema_str = json.dumps(canvas_doc_json_schema(), indent=2)

    system = _CANVAS_EDIT_SYSTEM.format(
        doc_schema=schema_str,
        snippets=snippets_text,
        query_ids=query_ids_text,
    )

    current_doc_json = json.dumps(doc.model_dump(), indent=2)
    user = _CANVAS_EDIT_USER.format(
        current_doc=current_doc_json,
        instruction=instruction,
    )

    last_doc: "CanvasDoc | None" = None
    last_issues: list[str] = []

    for attempt in range(MAX_CANVAS_REPAIR_ROUNDS):
        raw_response = (
            provider.complete(user, system=system, model=model)
            if model
            else provider.complete(user, system=system)
        )

        updated_doc: "CanvasDoc | None"
        try:
            data = json.loads(_strip_fences(raw_response))
            updated_doc = CanvasDoc(**data)
            ok, issues = validate_canvas_doc(updated_doc)
        except Exception as exc:  # noqa: BLE001
            updated_doc, issues = None, [f"Response was not valid JSON or CanvasDoc: {exc}"]

        hard = _hard_canvas_errors(issues) if updated_doc is not None else issues

        if updated_doc is not None and not hard:
            return _attach_canvas_meta(
                updated_doc,
                generated_by="llm",
                valid=True,
                repair_rounds=attempt,
                issues=issues,
            )

        last_doc, last_issues = updated_doc, issues

        next_round = attempt + 1
        if next_round < MAX_CANVAS_REPAIR_ROUNDS:
            logger.info(
                "canvas edit repair round %d/%d — feeding back %d hard error(s): %s",
                next_round,
                MAX_CANVAS_REPAIR_ROUNDS - 1,
                len(hard),
                hard,
            )
            user = _CANVAS_EDIT_REPAIR_USER.format(
                issues="\n".join(f"- {i}" for i in hard)
                or "- (response was not valid JSON or CanvasDoc)",
            )

    # ── Loud failure ───────────────────────────────────────────────────────
    repair_rounds = MAX_CANVAS_REPAIR_ROUNDS - 1
    final_hard = _hard_canvas_errors(last_issues) if last_doc is not None else last_issues
    logger.warning(
        "canvas edit FAILED after %d repair round(s); %d hard error(s) remain: %s",
        repair_rounds,
        len(final_hard),
        final_hard,
    )

    from app.errors import AppError  # noqa: PLC0415

    raise AppError(
        "canvas_edit_failed",
        "LLM canvas edit failed validation after "
        f"{repair_rounds} repair round(s). Issues: {final_hard}",
        422,
    )


# ---------------------------------------------------------------------------
# Metadata helper (mirrors dashboard._attach_meta)
# ---------------------------------------------------------------------------


def _attach_canvas_meta(
    doc: "CanvasDoc",
    *,
    generated_by: str,
    valid: bool,
    repair_rounds: int,
    issues: list[str],
) -> "CanvasDoc":
    """Attach repair/provenance metadata to *doc* without polluting model_dump."""
    object.__setattr__(doc, "generated_by", generated_by)
    object.__setattr__(doc, "valid", valid)
    object.__setattr__(doc, "repair_rounds", repair_rounds)
    object.__setattr__(doc, "issues", list(issues))
    return doc
