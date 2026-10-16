/**
 * The voxel kit (storyboard §3): pure-TypeScript builders for the stage's recognisable objects.
 * Scenes receive it as the first argument of Beat.layout(kit, state); it can also be imported directly
 * for module-level work (surface-text positions, tests).
 */
import * as B from './builders'
import { FONT, GLYPHS, measureVoxelText } from './font'
import { hashStr, normalisedOrder, orderBy, orderKeyFn, orderRange, rng } from './order'
import { TONE } from '../types'

export const kit = {
  TONE,
  // builders
  box: B.box, voxelText: B.voxelText, browserWindow: B.browserWindow, kpiTile: B.kpiTile,
  barChart: B.barChart, lineChart: B.lineChart, hbarChart: B.hbarChart, table: B.table, columns: B.columns,
  cylinder: B.cylinder, rack: B.rack, person: B.person, badge: B.badge, mcpPort: B.mcpPort, engineChip: B.engineChip,
  chatPanel: B.chatPanel, pane: B.pane, placeholder: B.placeholder,
  // composition
  merge: B.merge, offset: B.offset, retone: B.retone, surface: B.surface,
  // helpers
  rng, hashStr, orderBy, orderRange, orderKeyFn, normalisedOrder, measureVoxelText, FONT, GLYPHS,
}
export type Kit = typeof kit
export type { Part, TextSeed, WindowPart, ChatMessage } from './builders'
export default kit
