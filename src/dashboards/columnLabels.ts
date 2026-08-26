/**
 * columnLabels.js — re-export of the shared column-header logic.
 *
 * The implementation lives in embed/widgets/column-labels.js so the chart
 * builder can use it too: chart series names come from column names exactly as
 * table headers do, and a legend reading `kitchen_seconds` is the same defect
 * as a header reading it. Same direction as chartOptionShared.js — the SPA
 * imports from embed, never the reverse, so the widget kit stays standalone.
 */
export { splitWords, humaniseColumn, columnLabel } from '../../embed/widgets/column-labels.js'
