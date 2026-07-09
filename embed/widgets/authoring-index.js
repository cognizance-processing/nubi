/**
 * authoring-index.js — Register the Wave-2 authoring custom elements.
 *
 * Separate from widgets/index.js so the vanilla read-only widget kit
 * remains React-free.  Hosts that only need KPI/table/chart can omit this.
 *
 * Auto-registers on import (UMD-style drop-in).
 *
 * Exports
 * -------
 *  registerNubiAuthoringWidgets() — idempotent registration
 *  NubiQueryEditor
 *  NubiMetricExplorer
 */

import { NubiQueryEditor }    from './nubi-query-editor.js'
import { NubiMetricExplorer } from './nubi-metric-explorer.js'

export { NubiQueryEditor, NubiMetricExplorer }

export function registerNubiAuthoringWidgets() {
  if (!customElements.get('nubi-query-editor')) {
    customElements.define('nubi-query-editor', NubiQueryEditor)
  }
  if (!customElements.get('nubi-metric-explorer')) {
    customElements.define('nubi-metric-explorer', NubiMetricExplorer)
  }
}

// Auto-register
registerNubiAuthoringWidgets()
