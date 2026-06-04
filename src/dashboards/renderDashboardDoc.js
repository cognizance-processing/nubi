/**
 * renderDashboardDoc.js — Mount a sanitized dashboard HTML document into a DOM container.
 *
 * This module is the runtime half of the M8-B dashboard renderer.  It:
 *   1. Ensures Nubi custom elements are registered (idempotent).
 *   2. Sanitizes the raw HTML through the security trust boundary (sanitize.js).
 *   3. Sets container.innerHTML to the clean HTML so custom elements upgrade.
 *   4. Propagates backend URL + auth token to any <nubi-*> widgets that don't
 *      already have their own `backend` / `token` attributes.
 *   5. Returns a cleanup function that clears the container on unmount.
 *
 * SECURITY NOTE:
 *   innerHTML is set ONLY on the sanitized string — never on raw html.
 *   This module never calls eval, Function(), or any other dynamic execution.
 */

import { registerNubiWidgets } from '../../embed/widgets/index.js'
import { sanitizeDashboardHtml } from './sanitize.js'

// ---------------------------------------------------------------------------
// Widget tag names we propagate credentials to
// ---------------------------------------------------------------------------

const NUBI_WIDGET_TAGS = ['nubi-kpi', 'nubi-table', 'nubi-chart']

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Render a sanitized dashboard HTML document into `container`.
 *
 * @param {HTMLElement} container
 *   The DOM element whose innerHTML will be replaced with the sanitized dashboard.
 *
 * @param {string} html
 *   Raw (untrusted) dashboard HTML — typically LLM-authored / from a boards resource.
 *   This is sanitized before use; callers do NOT need to pre-sanitize.
 *
 * @param {object} opts
 * @param {string}            [opts.backend]   Base URL of the Nubi API backend.
 *                                             Defaults to '' (same-origin).
 * @param {() => string|null} [opts.getToken]  Synchronous function returning the
 *                                             current JWT access token or null.
 *
 * @returns {() => void} Cleanup function — call on component unmount to clear
 *                       the container and avoid stale widget subscriptions.
 */
export function renderDashboardDoc(container, html, { backend = '', getToken = () => null } = {}) {
  if (!container) return () => {}

  // 1. Ensure custom elements are registered (safe to call multiple times).
  registerNubiWidgets()

  // 2. Sanitize — this is the security trust boundary.
  const clean = sanitizeDashboardHtml(html)

  // 3. Mount into the DOM; custom elements upgrade immediately.
  container.innerHTML = clean

  // 4. Propagate backend + token to widgets that lack their own credentials.
  //    We resolve the token once here so all widgets in this doc share it.
  const token = getToken()

  for (const tag of NUBI_WIDGET_TAGS) {
    const widgets = container.querySelectorAll(tag)
    widgets.forEach(widget => {
      // Only set if the attribute is absent — don't override per-widget creds.
      if (backend && !widget.hasAttribute('backend')) {
        widget.setAttribute('backend', backend)
      }
      if (token && !widget.hasAttribute('token') && !widget.hasAttribute('get-token')) {
        widget.setAttribute('token', token)
      }
    })
  }

  // 5. Return cleanup: clear the container so widgets can disconnect cleanly.
  return () => {
    container.innerHTML = ''
  }
}
