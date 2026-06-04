/**
 * index.js — Nubi Widget Kit entry point (M8-A).
 *
 * registerNubiWidgets() registers all three custom elements with the browser's
 * customElements registry. Guards against double-define so the function is safe
 * to call multiple times (e.g. if the bundle is loaded more than once on a page).
 *
 * Registered elements
 * -------------------
 *   <nubi-kpi>    — big-number metric card
 *   <nubi-table>  — HTML data table
 *   <nubi-chart>  — auto-WebGL/SVG chart (scatter/line/bar)
 *
 * Usage (ESM):
 *   import { registerNubiWidgets } from './nubi-widgets.es.js'
 *   registerNubiWidgets()
 *
 * Usage (UMD / plain <script>):
 *   <script src="nubi-widgets.js"></script>
 *   <!-- registerNubiWidgets() is called automatically on load -->
 *   <!-- also available at window.NubiWidgets.registerNubiWidgets() -->
 *
 * WEBGL_THRESHOLD (exported for host inspection):
 *   import { WEBGL_THRESHOLD } from './nubi-widgets.es.js'
 *   // 20000 — scatter plots with more rows than this use WebGL
 */

import { NubiKpi }    from './nubi-kpi.js'
import { NubiTable }  from './nubi-table.js'
import { NubiChart }  from './nubi-chart.js'

// WEBGL_THRESHOLD is no longer applicable — the chart now uses ECharts (canvas).
// Kept as a named export for backwards compatibility with any host code that
// imported it; the value is 0 (ECharts handles all dataset sizes).
export const WEBGL_THRESHOLD = 0

/**
 * Register all Nubi widget custom elements.
 * Safe to call multiple times — already-defined elements are silently skipped.
 */
export function registerNubiWidgets() {
  if (!customElements.get('nubi-kpi')) {
    customElements.define('nubi-kpi', NubiKpi)
  }
  if (!customElements.get('nubi-table')) {
    customElements.define('nubi-table', NubiTable)
  }
  if (!customElements.get('nubi-chart')) {
    customElements.define('nubi-chart', NubiChart)
  }
}

// Auto-register on import so the bundle works as a plain <script> drop-in.
// The guard above ensures this is harmless if called again by the host.
registerNubiWidgets()
