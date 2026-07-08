/**
 * src/editor/shared/index.js — Barrel for shared inspector panels.
 *
 * Import from here so the editor's components
 * reuse the exact same components without coupling to each other's internals.
 */

export * from './constants.js'
export * from './inspectorPrimitives.jsx'
export * from './useInspectorData.js'
export { QueryPicker } from './QueryPicker.jsx'
export { BackgroundEditor } from './BackgroundEditor.jsx'
export { ParamBindingSection } from './ParamBindingSection.jsx'
export { ChartConfig } from './ChartConfig.jsx'
export { KpiConfig } from './KpiConfig.jsx'
export { TableConfig, ColumnFormatsEditor, ConditionalRulesEditor } from './TableConfig.jsx'
export { FilterConfig, TextConfig, PlacementControl } from './FilterConfig.jsx'
export { applyPlacement, effectivePlacement } from './placementHelpers.js'
