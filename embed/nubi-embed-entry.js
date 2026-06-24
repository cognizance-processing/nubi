/**
 * nubi-embed-entry.js — Self-contained embed bundle entry point.
 *
 * Imports every custom element and ensures they are registered when this
 * module is executed.  Built by vite.embed.config.js into
 * embed/dist/nubi-embed.js — a browser-native ESM that needs no import map
 * and no node_modules; all dependencies (React, apache-arrow, ECharts) are
 * bundled in.
 *
 * Registered custom elements
 * --------------------------
 *   nubi-kpi             — big-number metric card (vanilla)
 *   nubi-table           — data table (vanilla)
 *   nubi-chart           — auto chart (ECharts, vanilla)
 *   nubi-kpi-react       — React KPI card
 *   nubi-explain         — root-cause contribution analysis
 *   nubi-query-editor    — SQL / metric authoring editor
 *   nubi-metric-explorer — governed metric query builder
 *   nubi-dashboard       — read-only dashboard embed
 *
 * Usage in a bare HTML page:
 *   <script type="module" src="nubi-embed.js"></script>
 */

// ── Vanilla widgets (nubi-kpi, nubi-table, nubi-chart) ───────────────────────
// index.js auto-registers on import via its module-level call to
// registerNubiWidgets(), so a bare import is enough.
import './widgets/index.js'

// ── React-based KPI widget (nubi-kpi-react) ───────────────────────────────────
// defineNubiElement() inside nubi-kpi-react.js calls customElements.define()
// at module evaluation time, so the import is sufficient.
import './widgets/nubi-kpi-react.js'

// ── Explain widget (nubi-explain) ─────────────────────────────────────────────
// nubi-explain.js has a guarded customElements.define() at module level.
import './widgets/nubi-explain.js'

// ── Authoring widgets (nubi-query-editor, nubi-metric-explorer) ──────────────
// authoring-index.js auto-registers on import.
import './widgets/authoring-index.js'

// ── Legacy dashboard (nubi-dashboard) ────────────────────────────────────────
// nubi-dashboard.js calls customElements.define() at module level.
import './nubi-dashboard.js'
