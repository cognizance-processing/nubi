/**
 * src/lib/chartOptionShared.js — Single-source-of-truth re-export of the
 * comprehensive chart-option builder that lives in embed/widgets/chart-options.js.
 *
 * Both the SPA dashboard widgets AND the embedded <nubi-chart> web component
 * consume buildChartOption from this one module so chart rendering logic is
 * never forked or duplicated.
 *
 * Usage (SPA):
 *   import { buildChartOption, SUPPORTED_TYPES, tableView } from '../lib/chartOptionShared.js'
 *
 * Usage (embed — imports directly from the source):
 *   import { buildChartOption } from '../../embed/widgets/chart-options.js'
 *
 * The embed build imports the source directly to avoid introducing a circular
 * path through the SPA tree, but both resolve to the same file on disk.
 */

export {
  buildChartOption,
  SUPPORTED_TYPES,
  DEFAULT_PALETTE,
  tableView,
  deepMerge,
  makeFormatter,
} from '../../embed/widgets/chart-options.js'
