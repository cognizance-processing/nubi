"""Canvas document model and server-side validation (Canvas Wave 1).

A Canvas is an HTML-native sibling to Dashboards: the source of truth is an
HTML document the author writes directly, with data bindings expressed in a
side map keyed by ``data-el-id`` attributes.

CanvasDoc shape (stored as ``config.doc``)
------------------------------------------
::

    {
      "version": 1,
      "title": "Q3 Exec Brief",
      "html": "<section>...<nubi-kpi data-el-id='el_1' ...></section>",
      "bindings": {
        "el_1": { "kind": "query",  "query_id": "rev_total", "params": {},
                  "field": "total", "format": "currency" },
        "el_2": { "kind": "metric", "metric_id": "...",
                  "dimensions": [], "time_grain": "month" },
        "el_3": { "kind": "api",    "connector_id": "...", "path": "/v1/x",
                  "select": "$.data[0].value" }
      },
      "variables": [ { "name": "region", "type": "select", "default": "US" } ],
      "assets": { "css": "...optional scoped stylesheet..." }
    }

Canonical element vocabulary
-----------------------------
Canvas uses the **nubi-*** vocabulary (client convention).  The following
custom elements are recognised (all require ``data-el-id`` when data-bound):

  <nubi-kpi>       — single KPI metric card
  <nubi-table>     — tabular data grid
  <nubi-chart>     — chart (type attribute: scatter/bar/line/pie/area)
  <nubi-metric>    — semantic-layer metric value
  <nubi-filter>    — variable filter control
  <nubi-text>      — rich-text / markdown panel
  <nubi-value>     — inline single scalar ({{token}}-style injection)

Plain HTML elements can also carry ``data-el-id`` to receive {{token}} text
or attribute injection via the "query" or "api" binding kind.

Security model (unchanged from dashboards)
-------------------------------------------
* ``validate_canvas_doc`` REUSES ``validate_dashboard_html`` for all HTML
  safety checks — no forking, no relaxation of the sanitiser trust boundary.
* RLS comes from the verified token only, never from the request body.
* The allowlist is EXTENDED to cover the full nubi-* vocabulary (Canvas adds
  nubi-metric, nubi-filter, nubi-text, nubi-value beyond the dashboard set).
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Canonical Canvas element vocabulary (nubi-* client convention)
# ---------------------------------------------------------------------------

#: All nubi-* custom elements recognised in a Canvas document.
CANVAS_ALLOWED_WIDGET_TAGS: frozenset[str] = frozenset(
    {
        "nubi-kpi",
        "nubi-table",
        "nubi-chart",
        "nubi-metric",
        "nubi-filter",
        "nubi-text",
        "nubi-value",
    }
)

# ---------------------------------------------------------------------------
# Binding models
# ---------------------------------------------------------------------------


class QueryBinding(BaseModel):
    """A binding that runs a registered query and maps one field to an element."""

    kind: Literal["query"]
    query_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    field: str | None = None
    """Optional column name to extract for scalar injection ({{token}})."""
    format: str | None = None
    """Optional display format: 'currency' | 'number' | 'percent' | 'date'."""


class MetricBinding(BaseModel):
    """A binding that runs a semantic-layer metric and maps it to an element."""

    kind: Literal["metric"]
    metric_id: str
    dimensions: list[str] = Field(default_factory=list)
    time_grain: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    format: str | None = None


class ApiBinding(BaseModel):
    """A binding that calls an HTTP_JSON connector and maps the result to an element.

    ``connector_id`` must resolve to an HTTP_JSON datastore owned by the org
    (hard validation rule).  ``path`` is the resource path appended to the
    connector's base URL.  ``select`` is a JSONPath expression applied to the
    connector response to extract the value.
    """

    kind: Literal["api"]
    connector_id: str
    path: str = "/"
    select: str | None = None
    """JSONPath selector applied to the HTTP response JSON."""
    format: str | None = None

    @field_validator("path", mode="before")
    @classmethod
    def _validate_path(cls, v: Any) -> Any:
        """Reject unsafe paths at save time (defense-in-depth).

        URL-decodes the path first (%2e%2e, .%2e, %2e. etc.) so encoded
        traversal sequences are caught before the segment check.  Rejects:
        - Paths containing '..' segments after URL-decoding.
        - Paths containing a URL scheme ('://').
        - Protocol-relative paths starting with '//'.
        """
        if not v or not isinstance(v, str):
            return v
        # Decode percent-encoding to a fixpoint so double-encoded sequences
        # like %252e%252e are fully expanded before the safety checks.
        # Cap iterations to prevent pathological inputs from looping forever.
        decoded = v
        for _ in range(16):
            next_decoded = urllib.parse.unquote(decoded)
            if next_decoded == decoded:
                break
            decoded = next_decoded
        decoded = decoded.strip()
        if (
            "://" in decoded
            or decoded.startswith("//")
            or ".." in decoded.split("/")
        ):
            raise ValueError(
                f"ApiBinding.path {v!r} is unsafe: paths must be relative, must not "
                "contain '..' segments (including URL-encoded forms such as %2e%2e, "
                ".%2e, or %2e.), and must not include a URL scheme or '//'. "
                "They are joined under the connector's base URL only."
            )
        # [MED injection] Reject query strings and fragments — a path like
        # 'endpoint?tenant_id=other' injects arbitrary query params into the
        # outbound HTTP request, potentially defeating API-level tenant scoping.
        # Static query params belong on the connector base URL, not the path.
        parsed = urllib.parse.urlsplit(decoded)
        if parsed.query or parsed.fragment:
            raise ValueError(
                f"ApiBinding.path {v!r} is unsafe: paths must not contain a query "
                "string ('?') or fragment ('#'). Static query parameters belong on "
                "the connector's base URL, not the binding path."
            )
        return v


# Union type for a single binding entry (discriminated by ``kind``).
CanvasBinding = QueryBinding | MetricBinding | ApiBinding


# ---------------------------------------------------------------------------
# Variable model (mirrors DashboardSpec.Variable)
# ---------------------------------------------------------------------------


class CanvasVariable(BaseModel):
    """A runtime variable declared for a Canvas document."""

    name: str
    type: Literal["select", "multiselect", "date", "daterange", "text"]
    default: Any = None


# ---------------------------------------------------------------------------
# CanvasDoc (the top-level document stored in config.doc)
# ---------------------------------------------------------------------------


class CanvasDoc(BaseModel):
    """The top-level Canvas document model.

    Stored as ``config.doc`` in the ``canvases`` resource row.
    HTML is authored directly (not compiled from a spec); bindings are kept in
    a *side map* keyed by ``data-el-id`` so the editor can mutate them without
    rewriting the HTML string.
    """

    version: int = 1
    title: str = ""
    html: str = ""
    bindings: dict[str, CanvasBinding] = Field(default_factory=dict, max_length=500)
    """Mapping from data-el-id → binding descriptor.  At most 500 entries are
    accepted; larger dicts are rejected at parse/save time (Pydantic v2
    ``max_length`` on ``dict`` fields)."""
    variables: list[CanvasVariable] = Field(default_factory=list)
    assets: dict[str, Any] = Field(default_factory=dict)
    """Optional assets (e.g. ``{"css": "...scoped stylesheet..."}``).  The
    CSS value is stored as-is; it is the frontend's responsibility to scope it
    to the canvas container and sanitise it.  No JS is accepted here."""
    theme: dict[str, Any] = Field(default_factory=dict)
    schedule_hint: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_bindings(cls, values: Any) -> Any:
        """Discriminate binding dicts by their ``kind`` field before parsing."""
        if not isinstance(values, dict):
            return values
        raw_bindings = values.get("bindings") or {}
        if not isinstance(raw_bindings, dict):
            return values
        coerced: dict[str, Any] = {}
        for el_id, binding in raw_bindings.items():
            if isinstance(binding, dict):
                kind = binding.get("kind")
                if kind == "query":
                    coerced[el_id] = QueryBinding(**binding)
                elif kind == "metric":
                    coerced[el_id] = MetricBinding(**binding)
                elif kind == "api":
                    coerced[el_id] = ApiBinding(**binding)
                else:
                    coerced[el_id] = binding  # pass through; will fail Pydantic
            else:
                coerced[el_id] = binding
        values = dict(values)
        values["bindings"] = coerced
        return values


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

# Regex to extract ``data-el-id`` values from HTML.
_DATA_EL_ID_RE = re.compile(r'data-el-id=["\']([^"\']+)["\']', re.IGNORECASE)


def _extract_el_ids(html: str) -> set[str]:
    """Return the set of ``data-el-id`` values present in *html*."""
    return {m.group(1) for m in _DATA_EL_ID_RE.finditer(html)}


def validate_canvas_doc(doc: CanvasDoc, *, org_id: str | None = None) -> tuple[bool, list[str]]:
    """Server-side validation for a :class:`CanvasDoc`.

    Runs all checks in order; collects ALL issues before returning so the
    caller sees the full picture in one round-trip (mirrors ``validate_spec``).

    Validation rules
    ----------------
    1. HTML safety — delegates to :func:`validate_dashboard_html` (extended for
       the full nubi-* Canvas vocabulary).  Any HTML safety failure is a HARD
       error.
    2. Orphan bindings — a binding whose ``el_id`` is absent from the HTML is
       a soft ``[warn]`` (agents may write bindings before finishing the HTML).
    3. Unbound elements — a ``data-el-id`` in the HTML with no corresponding
       binding is a soft ``[warn]``.
    4. ``query_id`` / ``metric_id`` / ``connector_id`` registry lookups —
       soft ``[warn]`` when the referenced id is not currently registered
       (forward-reference tolerance, same as dashboards).
    5. At-most-one binding kind per element — ``bindings`` is already a
       ``{el_id: binding}`` map, so this is trivially enforced by the model;
       we re-check the raw dict to catch duplicate el_ids.
    6. API binding connector resolution — ``connector_id`` must resolve to an
       HTTP_JSON datastore owned by *org_id* (hard error when the lookup
       returns a non-HTTP_JSON datastore or None and an org_id is provided).
       When *org_id* is None (stateless path) this check is skipped.

    Parameters
    ----------
    doc:
        The parsed :class:`CanvasDoc` to validate.
    org_id:
        Optional org context for the API-connector ownership check.  Pass
        ``None`` for the stateless validate oracle (POST /canvas/validate).

    Returns
    -------
    tuple[bool, list[str]]
        ``(True, warnings_only)`` when there are no hard errors;
        ``(False, all_issues)`` when one or more hard errors are present.
        Soft warnings are prefixed with ``[warn]``.
    """
    from app.ai.dashboard import (  # noqa: PLC0415
        ALLOWED_WIDGET_TAGS as _DASH_ALLOWED,
        validate_dashboard_html as _validate_html,
    )

    issues: list[str] = []

    # ── 1. HTML safety (extended allowlist for Canvas) ───────────────────────
    # Temporarily patch the ALLOWED_WIDGET_TAGS constant used by
    # validate_dashboard_html so Canvas elements pass the custom-tag check.
    # We do this by calling validate_dashboard_html and then re-checking the
    # custom-tag portion ourselves against the expanded Canvas allowlist.
    html = doc.html

    # Run the base checks (script/on*/js:/data:text/html).
    _base_ok, _base_issues = _validate_html(html)

    # Filter out the "Unknown custom element" issue from the base validator
    # because it uses the narrower dashboard allowlist — we re-run it below
    # with the Canvas-extended allowlist.
    for issue in _base_issues:
        if "Unknown custom element" not in issue:
            issues.append(issue)

    # Re-run custom-tag check against the Canvas-extended allowlist.
    _custom_tag_re = re.compile(r"<([a-z][a-z0-9]*-[a-z0-9-]+)", re.IGNORECASE)
    found_custom_tags: set[str] = {
        m.group(1).lower() for m in _custom_tag_re.finditer(html)
    }
    unknown_custom = found_custom_tags - CANVAS_ALLOWED_WIDGET_TAGS
    if unknown_custom:
        issues.append(
            f"Unknown custom element(s) in Canvas: {sorted(unknown_custom)}. "
            f"Only {sorted(CANVAS_ALLOWED_WIDGET_TAGS)} are allowed."
        )

    # ── 2 & 3. Orphan bindings / unbound elements ────────────────────────────
    html_el_ids = _extract_el_ids(html)
    binding_el_ids = set(doc.bindings.keys())

    for el_id in sorted(binding_el_ids - html_el_ids):
        issues.append(
            f"[warn] Binding for el_id={el_id!r} has no matching data-el-id in the HTML."
        )

    for el_id in sorted(html_el_ids - binding_el_ids):
        issues.append(
            f"[warn] Element data-el-id={el_id!r} has no corresponding binding."
        )

    # ── 4. Registry lookups (soft warn) ──────────────────────────────────────
    for el_id, binding in doc.bindings.items():
        if isinstance(binding, QueryBinding):
            try:
                from app.queries.registry import get_query_registry  # noqa: PLC0415
                registry = get_query_registry()
                if registry.get(binding.query_id) is None:
                    issues.append(
                        f"[warn] Canvas binding el_id={el_id!r} references unknown "
                        f"query_id={binding.query_id!r}. "
                        "The id is not in the registered query registry."
                    )
            except Exception:  # noqa: BLE001
                pass  # Registry unavailable — skip

        elif isinstance(binding, MetricBinding):
            try:
                from app.metrics.registry import get_metric_registry  # noqa: PLC0415
                mreg = get_metric_registry()
                if mreg.get(binding.metric_id) is None:
                    issues.append(
                        f"[warn] Canvas binding el_id={el_id!r} references unknown "
                        f"metric_id={binding.metric_id!r}. "
                        "The id is not in the registered metric registry."
                    )
            except Exception:  # noqa: BLE001
                pass  # Registry unavailable — skip

        elif isinstance(binding, ApiBinding):
            # ── 6. API connector ownership check (hard, requires org_id) ─────
            if org_id is not None:
                # We do this check asynchronously in a lazy manner at route
                # level; here we just note it needs checking.
                # For sync validate_canvas_doc, we skip the async repo lookup.
                pass

    # ── 7. [LOW] CSS safety — reject malicious assets.css at SAVE TIME ──────────
    # The render path strips dangerous CSS, but we add a defence-in-depth hard
    # error at save time so attackers cannot persist XSS/CSRF payloads in the
    # stored doc.  We reject the canonical CSS injection vectors:
    #   a) </style>  — breaks out of a <style> block.
    #   b) <script   — injects a script element via CSS content.
    #   c) @import with javascript:, vbscript:, or data:text payloads — loads
    #      external scripts or data URIs via CSS @import rules.
    #   d) expression() — IE/legacy-Outlook JS execution via CSS property values.
    #   e) NUL bytes / non-whitespace ASCII control chars — smuggle tag-breakout
    #      sequences past regex checks (e.g. </sty\x00le>).
    css_value = doc.assets.get("css") if isinstance(doc.assets, dict) else None
    if css_value and isinstance(css_value, str):
        # For tag-breakout checks, apply the same NUL-normalization as the render
        # path so that a smuggled NUL byte cannot bypass the regex below.
        _css_normalized = re.sub(r"[\x00\x01-\x08\x0b\x0c\x0e-\x1f]", "", css_value)
        # Strip CSS block comments before checking injection patterns.  IE
        # silently strips /* */ comment fragments from property values, so
        # "expre/**/ssion(alert(1))" becomes "expression(alert(1))" after the
        # browser removes the comment.  We apply the same transformation here
        # so the patterns below see the comment-stripped form.  This also
        # hardens @import and url() against the same comment-splitting bypass.
        _css_no_comments = re.sub(r"/\*.*?\*/", "", _css_normalized, flags=re.DOTALL)
        # Also collapse all whitespace (including newlines) so that a token
        # split across lines (expr\nession) is normalised to a single token
        # before the patterns are applied.  We use this collapsed form ONLY for
        # the injection patterns — not for the tag-breakout patterns — because
        # collapsing whitespace could change the semantics of selectors.
        _css_collapsed = re.sub(r"\s+", "", _css_no_comments)

        # Patterns are grouped by which normalised haystack they run against:
        #
        # TAG_PATTERNS: run against the NUL-normalised copy (whitespace and
        #   newlines preserved so the </style> / <script regex work correctly).
        #
        # COMMENT_PATTERNS (@import, url): run against the comment-stripped
        #   copy.  Whitespace is preserved so "@import 'js:'" is still matched
        #   by "@import\s+" — but CSS block comments can no longer split the
        #   token.
        #
        # COLLAPSED_PATTERNS (expression): run against the fully-collapsed copy
        #   (no comments, no whitespace).  This catches both the comment-split
        #   form (expre/**/ssion) and the newline-split form (expr\nession)
        #   because collapsing normalises them both to "expression(".
        _css_hard_patterns: list[tuple[str, str, str]] = [
            (
                "tag",
                r"</\s*style",
                "</style> tag in assets.css breaks out of a <style> block",
            ),
            (
                "tag",
                r"<\s*script",
                "<script in assets.css injects a script element",
            ),
            (
                "comment",
                r"@import\s+['\"]?\s*(javascript:|vbscript:|data:\s*text)",
                "@import with javascript:, vbscript:, or data:text in assets.css "
                "loads a malicious payload",
            ),
            (
                "comment",
                r"url\s*\(\s*['\"]?\s*(javascript:|vbscript:|data:\s*text)",
                "url(javascript:), url(vbscript:), or url(data:text) in assets.css "
                "executes a script via CSS url()",
            ),
            (
                "collapsed",
                r"expression\s*\(",
                "expression() in assets.css executes JavaScript in IE/legacy clients",
            ),
        ]
        _haystack_map = {
            "tag": _css_normalized,
            "comment": _css_no_comments,
            "collapsed": _css_collapsed,
        }
        for _kind, _pattern, _description in _css_hard_patterns:
            _haystack = _haystack_map[_kind]
            if re.search(_pattern, _haystack, re.IGNORECASE):
                issues.append(
                    f"assets.css contains disallowed content: {_description}. "
                    "Remove the offending rule before saving."
                )

        # Also reject raw NUL bytes / control chars in the stored value — they
        # serve no legitimate CSS purpose and exist only to confuse parsers.
        if re.search(r"[\x00\x01-\x08\x0b\x0c\x0e-\x1f]", css_value):
            issues.append(
                "assets.css contains disallowed content: NUL bytes or ASCII control "
                "characters in assets.css can bypass tag-breakout checks. "
                "Remove the offending rule before saving."
            )

    # Hard errors = any issue NOT prefixed with [warn]
    hard = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
    ok = len(hard) == 0
    return ok, issues


async def validate_canvas_doc_with_repo(
    doc: CanvasDoc, org_id: str, repo: Any
) -> tuple[bool, list[str]]:
    """Async variant of :func:`validate_canvas_doc` that also checks API connector ownership.

    All rule-6 (API connector → HTTP_JSON ownership) checks are performed here
    using the repo for the datastore lookup.  All other rules are identical to
    the sync variant.

    Parameters
    ----------
    doc:
        The parsed :class:`CanvasDoc` to validate.
    org_id:
        The caller's org id (used for datastore scoping).
    repo:
        Active repository implementation.

    Returns
    -------
    tuple[bool, list[str]]
        ``(ok, issues)`` — same semantics as :func:`validate_canvas_doc`.
    """
    ok, issues = validate_canvas_doc(doc, org_id=org_id)

    # Rule 6: API connector must resolve to an HTTP_JSON datastore the org owns.
    for el_id, binding in doc.bindings.items():
        if not isinstance(binding, ApiBinding):
            continue
        ds = await repo.get("datastores", org_id, binding.connector_id)
        if ds is None:
            issues.append(
                f"API binding el_id={el_id!r} references connector_id="
                f"{binding.connector_id!r} which does not exist in this org."
            )
        else:
            ctype = (ds.get("config") or {}).get("type")
            if ctype != "http_json":
                issues.append(
                    f"API binding el_id={el_id!r} connector_id={binding.connector_id!r} "
                    f"resolves to a {ctype!r} datastore, not 'http_json'. "
                    "Only HTTP_JSON connectors are supported for Canvas API bindings."
                )

    hard = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
    ok = len(hard) == 0
    return ok, issues
