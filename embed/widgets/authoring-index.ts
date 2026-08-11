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
 */

import { NubiQueryEditor } from './nubi-query-editor.js'

export { NubiQueryEditor }

export function registerNubiAuthoringWidgets() {
  if (!customElements.get('nubi-query-editor')) {
    customElements.define('nubi-query-editor', NubiQueryEditor)
  }
}

// Auto-register
registerNubiAuthoringWidgets()
