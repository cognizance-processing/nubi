"""Server-side Canvas HTML renderer for scheduled sending (Canvas Wave 5).

Produces a **self-contained, inlined-CSS HTML document** from a
:class:`~app.dashboards.canvas.CanvasDoc` and pre-fetched binding data.

Design contract
---------------
* No live JavaScript survives in the output — dynamic bindings are *baked in*
  at render time (values resolved from *element_data* are HTML-escaped and
  injected into the document before delivery).
* All HTML passes through the same sanitiser used by the validation pipeline
  (:func:`~app.dashboards.canvas.validate_canvas_doc` →
  :func:`~app.ai.dashboard.validate_dashboard_html`).
* Inline styles only — no external stylesheet URLs.
* ``{{token}}`` placeholders and ``<nubi-*>`` custom elements are each replaced
  by a static representation whose data comes from *element_data*, never from a
  live query at delivery time.

Public API
----------
``render_canvas_html(doc, element_data) -> str``
    Render a :class:`CanvasDoc` to a self-contained HTML email / web document.

``render_canvas_pdf(doc, element_data) -> bytes``
    Thin wrapper: render to HTML then convert with the stdlib PDF path so
    reports can be sent as PDF attachments without an SVG intermediate.
"""

from __future__ import annotations

import html as _html_module
import logging
import re
from typing import Any

logger = logging.getLogger("nubi.dashboards.render_canvas")

# ---------------------------------------------------------------------------
# Token placeholder regex — matches {{identifier}} in text/attributes.
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")

# ---------------------------------------------------------------------------
# URL attributes whose values are treated as URLs — tokens interpolated into
# these attributes must have their scheme validated.
# ---------------------------------------------------------------------------

# Matches an opening URL attribute (href/src/action/formaction/data/xlink:href)
# followed immediately by a quote and then a {{token}} placeholder.
# Group 1: the quote character (' or ")
# Group 2: the token name
_URL_ATTR_TOKEN_RE = re.compile(
    r"""(?:href|src|action|formaction|data|xlink:href)\s*=\s*(?P<q>['"])"""
    r"""\{\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}\}(?P=q)""",
    re.IGNORECASE,
)

# Schemes permitted in href/src/etc. token values:
# - empty string / pure relative path (no scheme)
# - http:// or https://
# - mailto:
# Everything else (javascript:, data:, vbscript:, file:, ...) is blocked.
_SAFE_URL_SCHEME_RE = re.compile(
    r"^(?:https?://|mailto:|[^:/?#]*[/?#]|[^:/?#]*$)",
    re.IGNORECASE,
)


def _sanitize_token_url(value: str) -> str:
    """Return *value* if its scheme is safe for a URL attribute, else ``#``.

    Permitted:
    - Empty string.
    - Relative paths (no scheme, no ``//`` prefix).
    - ``http://`` and ``https://`` absolute URLs.
    - ``mailto:`` URIs.

    Blocked (replaced with ``#``):
    - ``javascript:`` — code execution.
    - ``data:``        — data URIs (can carry HTML/JS payloads).
    - ``vbscript:``    — legacy IE code execution.
    - ``//...``        — protocol-relative URLs (scheme inherited from page).
    - Any other scheme not explicitly allowed.
    """
    v = value.strip()
    if not v:
        return v
    vl = v.lower().lstrip()
    # Block protocol-relative URLs.
    if vl.startswith("//"):
        return "#"
    # Block any scheme that is not http/https/mailto and is not a relative path.
    if _SAFE_URL_SCHEME_RE.match(v):
        return v
    # Anything that has a "word:" prefix that didn't match above is blocked.
    return "#"

# ---------------------------------------------------------------------------
# nubi-* custom element tags recognised by the renderer.
# The renderer replaces these with static HTML equivalents.
# ---------------------------------------------------------------------------

_NUBI_TAG_RE = re.compile(
    r"<(nubi-kpi|nubi-table|nubi-chart|nubi-metric|nubi-filter|nubi-text|nubi-value)"
    r"([^>]*)>(.*?)</\1>",
    re.IGNORECASE | re.DOTALL,
)

_NUBI_VOID_TAG_RE = re.compile(
    r"<(nubi-kpi|nubi-table|nubi-chart|nubi-metric|nubi-filter|nubi-text|nubi-value)"
    r"([^>]*)/?>",
    re.IGNORECASE,
)

# Regex to extract data-el-id from an attribute string.
_EL_ID_ATTR_RE = re.compile(r'data-el-id=["\']([^"\']+)["\']', re.IGNORECASE)

# ---------------------------------------------------------------------------
# Inline brand CSS embedded into the rendered document.
# ---------------------------------------------------------------------------

_INLINE_CSS = """\
body{font-family:system-ui,-apple-system,sans-serif;margin:0;padding:24px;
  background:#f8f9fa;color:#1a1a2e;}
.nubi-canvas-doc{max-width:960px;margin:0 auto;}
.nubi-kpi{display:inline-block;background:#fff;border:1px solid #e5e7eb;
  border-radius:8px;padding:16px 24px;margin:8px;min-width:140px;
  box-shadow:0 1px 3px rgba(0,0,0,.08);}
.nubi-kpi .label{font-size:12px;color:#6b7280;text-transform:uppercase;
  letter-spacing:.04em;}
.nubi-kpi .value{font-size:28px;font-weight:700;color:#0d1527;margin-top:4px;}
.nubi-table-wrap{overflow:auto;background:#fff;border:1px solid #e5e7eb;
  border-radius:8px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,.08);}
table.nubi-data{border-collapse:collapse;width:100%;font-size:13px;}
table.nubi-data th{background:#0d1527;color:#fff;padding:8px 12px;
  text-align:left;font-weight:600;}
table.nubi-data td{padding:6px 12px;border-bottom:1px solid #f0f0f0;}
table.nubi-data tr:last-child td{border-bottom:none;}
table.nubi-data tr:nth-child(even) td{background:#f9fafb;}
.nubi-value-inline{font-weight:600;color:#0d1527;}
.nubi-error{color:#dc2626;font-size:12px;font-style:italic;}
.nubi-no-data{color:#9ca3af;font-size:12px;font-style:italic;}
"""

_MAX_TABLE_ROWS = 50  # cap rows in email/PDF tables to keep size manageable


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _attr_val(attrs_str: str, attr_name: str) -> str | None:
    """Extract an attribute value from an element attribute string."""
    pat = re.compile(rf'{re.escape(attr_name)}=["\']([^"\']*)["\']', re.IGNORECASE)
    m = pat.search(attrs_str)
    return m.group(1) if m else None


def _escape(value: Any) -> str:
    """HTML-escape a value for safe injection into HTML content."""
    if value is None:
        return ""
    return _html_module.escape(str(value), quote=True)


def _format_value(value: Any, fmt: str | None) -> str:
    """Apply optional display formatting to a scalar value."""
    if value is None:
        return ""
    raw = str(value)
    if fmt == "currency":
        try:
            num = float(raw.replace(",", ""))
            return f"R{num:,.2f}" if abs(num) < 1e15 else raw
        except (ValueError, TypeError):
            pass
    elif fmt == "number":
        try:
            num = float(raw.replace(",", ""))
            return f"{num:,.0f}" if num == int(num) else f"{num:,.2f}"
        except (ValueError, TypeError):
            pass
    elif fmt == "percent":
        try:
            num = float(raw.replace(",", "").rstrip("%"))
            return f"{num:.1f}%"
        except (ValueError, TypeError):
            pass
    return raw


def _render_kpi_html(el_id: str, attrs_str: str, element_data: dict[str, Any]) -> str:
    """Render a <nubi-kpi> element to static HTML."""
    label = _attr_val(attrs_str, "label") or el_id
    data = element_data.get(el_id, {})
    if data.get("error"):
        return (
            f'<div class="nubi-kpi" data-el-id="{_escape(el_id)}">'
            f'<div class="label">{_escape(label)}</div>'
            f'<div class="value nubi-error">–</div></div>'
        )
    columns: list[str] = data.get("columns") or []
    rows: list[list] = data.get("rows") or []
    if not rows or not columns:
        return (
            f'<div class="nubi-kpi" data-el-id="{_escape(el_id)}">'
            f'<div class="label">{_escape(label)}</div>'
            f'<div class="value nubi-no-data">–</div></div>'
        )
    # Field preference: use first column unless a binding field is specified.
    field_attr = _attr_val(attrs_str, "field")
    fmt_attr = _attr_val(attrs_str, "format")
    if field_attr and field_attr in columns:
        col_idx = columns.index(field_attr)
    else:
        col_idx = 0
    raw_value = rows[0][col_idx] if rows[0] else None
    display_value = _format_value(raw_value, fmt_attr)
    return (
        f'<div class="nubi-kpi" data-el-id="{_escape(el_id)}">'
        f'<div class="label">{_escape(label)}</div>'
        f'<div class="value">{_escape(display_value)}</div></div>'
    )


def _render_table_html(el_id: str, element_data: dict[str, Any]) -> str:
    """Render a <nubi-table> element to a static HTML table."""
    data = element_data.get(el_id, {})
    if data.get("error"):
        return (
            f'<div class="nubi-table-wrap" data-el-id="{_escape(el_id)}">'
            f'<span class="nubi-error">Data unavailable</span></div>'
        )
    columns: list[str] = data.get("columns") or []
    rows: list[list] = data.get("rows") or []
    if not rows or not columns:
        return (
            f'<div class="nubi-table-wrap" data-el-id="{_escape(el_id)}">'
            f'<span class="nubi-no-data">No data</span></div>'
        )
    header = "".join(f"<th>{_escape(c)}</th>" for c in columns)
    body_rows: list[str] = []
    display_rows = rows[:_MAX_TABLE_ROWS]
    for row in display_rows:
        cells = "".join(
            f"<td>{_escape(row[i] if i < len(row) else '')}</td>"
            for i in range(len(columns))
        )
        body_rows.append(f"<tr>{cells}</tr>")
    truncation = ""
    if len(rows) > _MAX_TABLE_ROWS:
        truncation = (
            f'<tr><td colspan="{len(columns)}" style="color:#9ca3af;font-size:11px;">'
            f"… {len(rows) - _MAX_TABLE_ROWS} more rows</td></tr>"
        )
    return (
        f'<div class="nubi-table-wrap" data-el-id="{_escape(el_id)}">'
        f'<table class="nubi-data"><thead><tr>{header}</tr></thead>'
        f'<tbody>{"".join(body_rows)}{truncation}</tbody></table></div>'
    )


def _render_value_html(el_id: str, attrs_str: str, element_data: dict[str, Any]) -> str:
    """Render a <nubi-value> element to an inline scalar."""
    data = element_data.get(el_id, {})
    if data.get("error"):
        return f'<span class="nubi-error nubi-value-inline" data-el-id="{_escape(el_id)}">–</span>'
    columns: list[str] = data.get("columns") or []
    rows: list[list] = data.get("rows") or []
    if not rows or not columns:
        return f'<span class="nubi-no-data nubi-value-inline" data-el-id="{_escape(el_id)}">–</span>'
    field_attr = _attr_val(attrs_str, "field")
    fmt_attr = _attr_val(attrs_str, "format")
    if field_attr and field_attr in columns:
        col_idx = columns.index(field_attr)
    else:
        col_idx = 0
    raw_value = rows[0][col_idx] if rows[0] else None
    display = _format_value(raw_value, fmt_attr)
    return f'<span class="nubi-value-inline" data-el-id="{_escape(el_id)}">{_escape(display)}</span>'


def _replace_nubi_element(
    tag: str,
    attrs_str: str,
    inner: str,
    element_data: dict[str, Any],
) -> str:
    """Replace a single <nubi-*> element with its static HTML equivalent."""
    el_id_m = _EL_ID_ATTR_RE.search(attrs_str)
    el_id = el_id_m.group(1) if el_id_m else ""

    tag_lower = tag.lower()
    if tag_lower in ("nubi-kpi", "nubi-metric"):
        return _render_kpi_html(el_id, attrs_str, element_data)
    if tag_lower in ("nubi-table", "nubi-chart"):
        # Charts rendered as tables in email/PDF — data is still accessible.
        return _render_table_html(el_id, element_data)
    if tag_lower == "nubi-value":
        return _render_value_html(el_id, attrs_str, element_data)
    if tag_lower in ("nubi-filter", "nubi-text"):
        # Filters are not interactive in static renders.
        # Strip ALL HTML tags from the inner content and HTML-escape what remains
        # so that attacker-controlled markup (iframe, form, object, etc.) stored
        # inside a nubi-text/nubi-filter element cannot survive into the
        # server-rendered email output.
        raw_inner = inner or ""
        plain_text = re.sub(r"<[^>]+>", "", raw_inner)
        return _html_module.escape(plain_text, quote=False)
    # Unknown nubi-* tag: drop silently (already validated upstream).
    return ""


def _bake_tokens(html: str, element_data: dict[str, Any]) -> str:
    """Replace ``{{token}}`` placeholders with HTML-escaped data values.

    The token name is matched first as a column name in the first row of
    any binding whose ``field`` matches, then falls back to a literal lookup
    of the token name across all binding columns.

    Values are HTML-escaped before injection — never raw.

    URL attribute safety
    --------------------
    When a placeholder appears as the sole value of a URL attribute
    (``href``, ``src``, ``action``, ``formaction``, ``data``,
    ``xlink:href``), the resolved value is first passed through
    :func:`_sanitize_token_url` to reject dangerous schemes such as
    ``javascript:``, ``data:``, and ``vbscript:`` before HTML-escaping.
    This prevents a token value like ``javascript:alert(1)`` from being
    baked into an ``href`` and executing in the rendered email/page.
    """
    # Collect the names of tokens that appear as the sole value of a URL
    # attribute in this HTML.  These must have their scheme validated.
    url_attr_token_names: set[str] = {
        m.group("name") for m in _URL_ATTR_TOKEN_RE.finditer(html)
    }

    def _resolve_token(name: str) -> str | None:
        """Return the raw (un-escaped) resolved value for *name*, or None."""
        for _el_id, data in element_data.items():
            if data.get("error"):
                continue
            columns: list[str] = data.get("columns") or []
            rows: list[list] = data.get("rows") or []
            if name in columns and rows:
                idx = columns.index(name)
                val = rows[0][idx] if rows[0] and idx < len(rows[0]) else None
                return str(val) if val is not None else ""
        return None

    def _replacer(m: re.Match) -> str:
        name = m.group(1)
        raw = _resolve_token(name)
        if raw is None:
            # No match — leave placeholder visible so the author can debug.
            return _escape(f"{{{{{name}}}}}")
        if name in url_attr_token_names:
            # Token is used in a URL attribute: validate scheme before escaping.
            raw = _sanitize_token_url(raw)
        return _escape(raw)

    return _TOKEN_RE.sub(_replacer, html)


def _replace_nubi_elements(html: str, element_data: dict[str, Any]) -> str:
    """Replace all <nubi-*> custom elements with static HTML equivalents.

    Two passes:
    1. Paired tags (opening + content + closing) via ``_NUBI_TAG_RE``.
    2. Void / self-closing tags that were not matched in pass 1.
    """

    def _paired_replacer(m: re.Match) -> str:
        return _replace_nubi_element(m.group(1), m.group(2), m.group(3), element_data)

    html = _NUBI_TAG_RE.sub(_paired_replacer, html)

    def _void_replacer(m: re.Match) -> str:
        return _replace_nubi_element(m.group(1), m.group(2), "", element_data)

    html = _NUBI_VOID_TAG_RE.sub(_void_replacer, html)
    return html


def _sanitize_for_render(html: str) -> str:
    """Apply server-side HTML safety checks to *html*.

    Reuses :func:`~app.ai.dashboard.validate_dashboard_html` and raises a
    :class:`ValueError` when any hard error is found.  This is a
    defense-in-depth step — the canvas doc should already have passed
    :func:`~app.dashboards.canvas.validate_canvas_doc` before reaching here.

    Parameters
    ----------
    html:
        Raw HTML from the CanvasDoc.

    Returns
    -------
    str
        The same *html* (unmodified — we only validate, not sanitise server-side;
        sanitisation happens on the client via DOMPurify or at save-time).

    Raises
    ------
    ValueError
        When a hard HTML safety error is found (script tag, on*= handler, etc.).
    """
    from app.dashboards.canvas import (  # noqa: PLC0415
        CanvasDoc,
        validate_canvas_doc,
    )

    # Build a minimal CanvasDoc wrapper for validation.
    try:
        doc_stub = CanvasDoc(html=html)
        ok, issues = validate_canvas_doc(doc_stub, org_id=None)
    except Exception:  # noqa: BLE001
        # If CanvasDoc parse fails, fall back to the raw validate_dashboard_html.
        try:
            from app.ai.dashboard import validate_dashboard_html  # noqa: PLC0415
            ok, issues = validate_dashboard_html(html)
        except Exception as _inner_exc:  # noqa: BLE001
            # Both validation paths failed — fail CLOSED: re-raise rather than
            # letting unchecked HTML through.
            raise ValueError(
                "Canvas HTML safety validation unavailable; refusing to render."
            ) from _inner_exc

    hard_errors = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
    if hard_errors:
        raise ValueError(
            f"Canvas HTML failed safety validation: {'; '.join(hard_errors)}"
        )
    return html


# ---------------------------------------------------------------------------
# CSS injection sanitizer
# ---------------------------------------------------------------------------

# Matches a CSS @import rule in any of its forms:
#   @import "url"; / @import url("..."); / @import url('...'); / @import url(...);
_CSS_IMPORT_RE = re.compile(
    r"@import\b[^;]*;?",
    re.IGNORECASE,
)

# Matches url(...) values in CSS, capturing the inner content.
# Three alternatives in one regex to handle single-quoted, double-quoted, and
# unquoted url() forms.  Named groups: sq (single-quoted), dq (double-quoted),
# uq (unquoted).  The unquoted form stops at the first ')' character, which is
# safe for typical CSS values without parentheses inside.
_CSS_URL_RE = re.compile(
    r"""url\(\s*(?:'(?P<sq>[^']*)'|"(?P<dq>[^"]*)"|\s*(?P<uq>[^)'"]*?))\s*\)""",
    re.IGNORECASE,
)

# Safe data: sub-schemes allowed inside url() — only safe raster image types.
# svg+xml is intentionally excluded: inline SVG can carry <script> payloads,
# matching the HTML validator's _is_safe_data_uri logic which also blocks svg.
_SAFE_DATA_MIME_RE = re.compile(
    r"^data:image/(png|jpeg|gif|webp)\s*;",
    re.IGNORECASE,
)


def _is_safe_css_url(target: str) -> bool:
    """Return True if a CSS url() target is safe to keep in assets.css.

    Permitted:
    - Empty string (harmless).
    - data:image/(png|jpeg|gif|webp|svg+xml);... — inline images only.
    - Relative paths with no scheme (no ``://``, no ``//``, no scheme prefix).

    Rejected (neutralized → replaced with ``none``):
    - javascript: / vbscript: — code execution.
    - data: with any MIME type that is not a safe image type.
    - Protocol-relative URLs (starting with ``//``).
    - Absolute URLs with any scheme (http, https, ftp, file, ...).
    - Any other string that contains a URL scheme (``word:``).
    """
    t = target.strip()
    if not t:
        return True

    tl = t.lower()

    # Block: javascript: / vbscript:
    if tl.startswith(("javascript:", "vbscript:")):
        return False

    # Block: protocol-relative URLs (//evil.example/...)
    if t.startswith("//"):
        return False

    # Block: data: URIs — only safe image MIME types are allowed.
    if tl.startswith("data:"):
        return bool(_SAFE_DATA_MIME_RE.match(t))

    # Block: any other absolute URL with a scheme (http, https, ftp, file, etc.)
    # Matches the pattern: one or more ASCII letters/digits/+/-/. followed by ://
    if re.match(r"[a-zA-Z][a-zA-Z0-9+\-.]*://", t):
        return False

    # Block: any bare scheme reference without // (e.g. "javascript:" already caught
    # above, but catch anything else: "vbscript:", "mailto:", etc.)
    # Pattern: word characters followed immediately by ':'
    if re.match(r"[a-zA-Z][a-zA-Z0-9+\-.]*:", t):
        return False

    # Everything else is a relative path — safe.
    return True


def _neutralize_css_url(m: re.Match) -> str:
    """Replace a dangerous url() value with url(none), keep safe ones unchanged."""
    # Extract the target from whichever named group matched.
    target = m.group("sq") or m.group("dq") or m.group("uq") or ""
    if _is_safe_css_url(target):
        return m.group(0)  # original unchanged
    return "url(none)"


def _sanitize_assets_css(css: str) -> str:
    """Sanitize user-authored CSS before inlining into the emailed HTML document.

    Defenses applied (in order):

    1. Strip HTML tag breakout attempts — remove any ``</style`` and ``<script``
       sequences so the CSS cannot escape the enclosing ``<style>`` block.
    2. Remove all CSS ``@import`` rules entirely — these can pull in arbitrary
       external stylesheets.
    3. Neutralize dangerous ``url()`` values — replace ``url()`` whose target is a
       dangerous scheme (javascript:, vbscript:, data: non-image, protocol-relative
       ``//``, absolute http/https) with ``url(none)``.

    Benign CSS (inline styles, relative paths, safe data:image/... URLs, normal
    class/property declarations) is left unchanged.
    """
    # 1. Strip HTML tag breakout sequences.
    sanitized = re.sub(r"</\s*style", "", css, flags=re.IGNORECASE)
    sanitized = re.sub(r"<\s*script", "", sanitized, flags=re.IGNORECASE)

    # 2. Remove all @import rules.
    sanitized = _CSS_IMPORT_RE.sub("", sanitized)

    # 3. Neutralize dangerous url() values.
    sanitized = _CSS_URL_RE.sub(_neutralize_css_url, sanitized)

    return sanitized


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_canvas_html(
    doc: "CanvasDoc",  # type: ignore[name-defined]  # noqa: F821
    element_data: dict[str, Any],
    *,
    title: str | None = None,
    include_assets_css: bool = True,
) -> str:
    """Render a :class:`~app.dashboards.canvas.CanvasDoc` to a self-contained HTML document.

    The returned document is suitable for use as an HTML email body or for
    conversion to PDF.  All data values are **baked in server-side**; no live
    JavaScript remains in the output.

    Processing pipeline
    -------------------
    1. Validate the canvas HTML for safety (script/on*/js: checks).
    2. Replace ``{{token}}`` placeholders with HTML-escaped resolved values.
    3. Replace ``<nubi-*>`` custom elements with static HTML equivalents.
    4. Wrap in a ``<!DOCTYPE html>`` document with inlined brand CSS.

    Parameters
    ----------
    doc:
        The parsed :class:`~app.dashboards.canvas.CanvasDoc`.
    element_data:
        Mapping from ``el_id`` → ``{columns, rows}`` or ``{error}`` as
        returned by :func:`~app.dashboards.collect.collect_canvas_data`.
    title:
        Optional document title (``<title>`` tag).  Falls back to ``doc.title``.
    include_assets_css:
        When ``True`` (default), inlines ``doc.assets.get("css")`` into the
        document's ``<style>`` block after the brand CSS.  Set to ``False`` in
        unit tests to keep output deterministic.

    Returns
    -------
    str
        A complete ``<!DOCTYPE html>`` document string.

    Raises
    ------
    ValueError
        When the canvas HTML fails safety validation (hard errors only).
    """
    raw_html: str = doc.html or ""
    doc_title: str = title or doc.title or "Canvas Report"

    # ── 1. Safety validation ─────────────────────────────────────────────────
    _sanitize_for_render(raw_html)

    # ── 2. Token substitution ({{name}} → HTML-escaped value) ────────────────
    rendered_html = _bake_tokens(raw_html, element_data)

    # ── 3. Replace nubi-* elements with static HTML ───────────────────────────
    rendered_html = _replace_nubi_elements(rendered_html, element_data)

    # ── 4. Assemble the full document ─────────────────────────────────────────
    assets_css = ""
    if include_assets_css:
        assets_raw = (doc.assets or {}).get("css") or ""
        if assets_raw:
            assets_css = "\n" + _sanitize_assets_css(assets_raw)

    doc_html = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>{_escape(doc_title)}</title>\n"
        "<style>\n"
        f"{_INLINE_CSS}"
        f"{assets_css}"
        "\n</style>\n"
        "</head>\n"
        '<body>\n'
        '<div class="nubi-canvas-doc">\n'
        f"{rendered_html}\n"
        "</div>\n"
        "</body>\n"
        "</html>"
    )

    return doc_html


def render_canvas_pdf(
    doc: "CanvasDoc",  # type: ignore[name-defined]  # noqa: F821
    element_data: dict[str, Any],
    *,
    title: str | None = None,
) -> bytes:
    """Render a :class:`~app.dashboards.canvas.CanvasDoc` to a PDF.

    Uses the stdlib PDF path (``app.jobs.report._render_pdf``) via a thin
    HTML → PDF conversion.  This intentionally reuses the same ``app.pdf``
    machinery used by the board report PDF path so we have a single PDF
    implementation.

    For a canvas the rendered HTML is the source of truth — we render the
    HTML to a PDF using the stdlib ``app.pdf`` path (which produces a
    dependency-free ``%PDF-1.4`` document).

    Returns
    -------
    bytes
        A valid ``%PDF`` byte string.
    """
    html_str = render_canvas_html(doc, element_data, title=title)

    # Use the stdlib PDF builder with an HTML-text adapter.
    # The canvas HTML is treated as pre-rendered rich text for the PDF.
    return _html_to_pdf_stdlib(html_str, title=title or doc.title)


def _html_to_pdf_stdlib(html_str: str, *, title: str | None = None) -> bytes:
    """Convert canvas HTML to a ``%PDF`` document via the stdlib ``app.pdf`` path.

    Strips HTML tags to plain text for embedding into the branded PDF layout.
    This is a pragmatic approach that keeps the PDF generation dependency-free
    while still producing a valid, deliverable document.

    For richer fidelity (e.g. weasyprint / headless browser), swap this
    function out without touching the public API.
    """
    from app.pdf import (  # noqa: PLC0415
        PAGE_H, PAGE_W, MARGIN,
        NAVY, INK,
        Pdf,
    )
    from datetime import datetime, timezone  # noqa: PLC0415

    board_title = title or "Canvas Report"
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Strip HTML tags to plain text for embedding.
    plain = re.sub(r"<[^>]+>", " ", html_str)
    plain = re.sub(r"\s{2,}", " ", plain).strip()
    # Limit to a reasonable length for the PDF.
    if len(plain) > 5000:
        plain = plain[:5000] + " …"

    pdf = Pdf()

    # Header band.
    pdf.rect_fill(0, PAGE_H - 92, PAGE_W, 92, NAVY)
    pdf.text(MARGIN, PAGE_H - 50, 20, board_title, bold=True, color=(1, 1, 1))
    pdf.text(
        MARGIN, PAGE_H - 72, 10.5,
        f"Canvas report  ·  generated {generated}",
        color=(0.78, 0.82, 0.88),
    )

    y = PAGE_H - 122
    line_h = 14.0
    chars_per_line = int((PAGE_W - 2 * MARGIN) / 6.5)

    # Simple word-wrap.
    words = plain.split()
    current_line: list[str] = []
    for word in words:
        test = " ".join(current_line + [word])
        if len(test) > chars_per_line and current_line:
            line_text = " ".join(current_line)
            if y - line_h < MARGIN:
                pdf.new_page()
                y = PAGE_H - MARGIN
            pdf.text(MARGIN, y, 10, line_text, color=INK)
            y -= line_h
            current_line = [word]
        else:
            current_line.append(word)

    if current_line:
        if y - line_h < MARGIN:
            pdf.new_page()
            y = PAGE_H - MARGIN
        pdf.text(MARGIN, y, 10, " ".join(current_line), color=INK)

    return pdf.to_bytes()
