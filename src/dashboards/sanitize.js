/**
 * sanitize.js — DOMPurify-based HTML sanitizer for Nubi dashboard documents.
 *
 * SECURITY TRUST BOUNDARY
 * -----------------------
 * Dashboard HTML is authored by LLMs / AI agents and must NEVER be rendered
 * unsanitized. This module is the single choke-point: all HTML passes through
 * sanitizeDashboardHtml() before being set as innerHTML.
 *
 * Threat model:
 *   - Attacker controls the HTML string (LLM hallucination or supply-chain).
 *   - Goals: steal tokens, exfiltrate data, execute arbitrary JS.
 *   - Mitigations applied:
 *       • <script> and similar execution sinks are removed (FORBID_TAGS).
 *       • Inline event handlers (on*) stripped by DOMPurify default + explicit check.
 *       • javascript:/data: URLs in href/src/action are removed (FORCE_BODY +
 *         DOMPurify's built-in URL-scheme check which rejects non-http(s)/mailto).
 *       • <style> / <link> / <iframe> / <object> / <embed> / <base> / <form>
 *         are forbidden — style injection, network requests, and form phishing.
 *       • Only a known-safe allowlist of tags and attributes passes through.
 *       • Nubi custom elements (nubi-kpi, nubi-table, nubi-chart) are explicitly
 *         allowed so widget upgrade works after innerHTML is set.
 *
 * Usage:
 *   import { sanitizeDashboardHtml } from './sanitize.js'
 *   container.innerHTML = sanitizeDashboardHtml(untrustedHtml)
 */

import DOMPurify from 'dompurify'

// ---------------------------------------------------------------------------
// Allowed custom element tags — Nubi widgets
// ---------------------------------------------------------------------------

const NUBI_TAGS = ['nubi-kpi', 'nubi-table', 'nubi-chart']

// ---------------------------------------------------------------------------
// Tags that must be blocked even if DOMPurify would allow them
// ---------------------------------------------------------------------------

const FORBID_TAGS = [
  'script',
  'style',
  'iframe',
  'object',
  'embed',
  'link',
  'base',
  'form',
  'input',
  'button',
  'textarea',
  'select',
  'meta',
  'noscript',
  'template',
]

// ---------------------------------------------------------------------------
// Widget + layout attributes that must survive sanitization
// ---------------------------------------------------------------------------

const WIDGET_ATTRS = [
  // nubi-kpi
  'query-id',
  'value-col',
  'label',
  'format',
  // nubi-table
  'limit',
  'columns',
  // nubi-chart
  'type',
  'x',
  'y',
  'color',
  // shared widget attrs
  'backend',
  'token',
  'get-token',
  // layout / styling (safe — no execution semantics)
  'style',
  'class',
  // standard safe HTML attrs (already allowed by DOMPurify, listed for clarity)
  'id',
  'title',
  'alt',
  'src',
  'href',
  'rel',
  'target',
  'colspan',
  'rowspan',
  'width',
  'height',
]

// ---------------------------------------------------------------------------
// DOMPurify configuration (assembled once, reused per call)
// ---------------------------------------------------------------------------

/**
 * @type {import('dompurify').Config}
 */
const PURIFY_CONFIG = {
  // Allow the Nubi custom elements in addition to DOMPurify's safe defaults.
  ADD_TAGS: NUBI_TAGS,

  // Ensure widget-specific attributes are not stripped.
  ADD_ATTR: WIDGET_ATTRS,

  // Hard-block these tags regardless of attribute safety.
  FORBID_TAGS,

  // on* event-handler attributes are stripped by DOMPurify by default.
  // We add an explicit empty FORBID_ATTR list here; actual on* removal is
  // enforced by DOMPurify's built-in sanitizer + the SANITIZE_DOM hook below.
  FORBID_ATTR: [],

  // Wrap output in a <body> fragment — prevents mXSS via broken parser context.
  FORCE_BODY: true,

  // Return a string, not a DocumentFragment.
  RETURN_DOM: false,
  RETURN_DOM_FRAGMENT: false,

  // Block data: and javascript: URL schemes in all attribute positions.
  ALLOW_DATA_ATTR: false,
}

// ---------------------------------------------------------------------------
// Post-parse hook: belt-and-suspenders on* attr strip + URL-scheme guard
// ---------------------------------------------------------------------------

// Register once (DOMPurify hooks are global per window — guard double-register).
if (typeof window !== 'undefined' && !DOMPurify._nubiHookInstalled) {
  DOMPurify.addHook('uponSanitizeAttribute', (node, data) => {
    const name = data.attrName.toLowerCase()

    // Strip any on* attribute that slipped through.
    if (name.startsWith('on')) {
      data.keepAttr = false
      return
    }

    // Strip javascript: / data: / vbscript: URL values.
    const val = (data.attrValue ?? '').trim().toLowerCase()
    if (
      val.startsWith('javascript:') ||
      val.startsWith('data:') ||
      val.startsWith('vbscript:')
    ) {
      data.keepAttr = false
    }
  })

  DOMPurify._nubiHookInstalled = true
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Sanitize LLM-authored dashboard HTML and return a safe string.
 *
 * The returned string is safe to set as `element.innerHTML` in a browser
 * context. It preserves:
 *   - Standard layout/text tags: h1-h6, div, span, p, section, table,
 *     thead, tbody, tr, td, th, ul, ol, li, strong, em, br, hr, header,
 *     main, article, nav, aside, figure, figcaption, a (href safe).
 *   - Nubi widget custom elements: <nubi-kpi>, <nubi-table>, <nubi-chart>
 *     with all their declared attributes.
 *   - class / style / id attributes for layout.
 *
 * It strips:
 *   - <script>, <style>, <iframe>, <object>, <embed>, <link>, <base>,
 *     <form>, <input> and other execution / navigation sinks.
 *   - All on* event-handler attributes.
 *   - javascript: / data: / vbscript: URL values in any attribute.
 *
 * @param {string} html - Raw, potentially untrusted HTML from an LLM/agent.
 * @returns {string} Sanitized HTML string, safe for innerHTML assignment.
 */
export function sanitizeDashboardHtml(html) {
  if (typeof html !== 'string') return ''
  return DOMPurify.sanitize(html, PURIFY_CONFIG)
}
