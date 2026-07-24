/**
 * maps.js — GeoJSON registry for `chart_type: 'map'` (choropleth) widgets.
 *
 * ECharts 5 does not bundle any map geometry, so a choropleth needs its
 * GeoJSON registered by name before a chart referencing it renders. This module
 * is the single place that happens.
 *
 * A board asks for a map by name:
 *
 *   { type: 'chart', chart_type: 'map',
 *     encoding: { name: 'region', value: 'calls' },
 *     config: { map: 'south-africa' } }
 *
 * and the region values in the `name` column must match the GeoJSON feature
 * names. Rows that don't match are ignored by ECharts rather than erroring, so
 * a partial match degrades gracefully.
 *
 * Deliberately generic: nubi ships no geography of its own. Register whatever
 * GeoJSON a deployment needs — countries, provinces, sales territories, store
 * catchments — under any name. Nothing here is specific to one customer or
 * region.
 */

const registry = new Map()
let echartsRef = null

/**
 * Give the registry the ECharts module to register against.
 *
 * Called once by the chart widget before it renders. Any GeoJSON registered
 * before ECharts was available is flushed through now, so registration order
 * doesn't matter to callers.
 */
export function attachECharts(echarts) {
  if (!echarts || echartsRef === echarts) return
  echartsRef = echarts
  for (const [name, geoJson] of registry) {
    echarts.registerMap(name, geoJson)
  }
}

/**
 * Register a GeoJSON FeatureCollection under `name`.
 *
 * Safe to call before ECharts is attached (queued) and safe to call repeatedly
 * with the same name (last registration wins).
 *
 * @param {string} name      map name a widget's `config.map` refers to
 * @param {object} geoJson   a GeoJSON FeatureCollection
 */
export function registerMapGeoJson(name, geoJson) {
  if (!name || !geoJson) return
  registry.set(name, geoJson)
  if (echartsRef) echartsRef.registerMap(name, geoJson)
}

/** True if `name` has geometry registered. */
export function hasMap(name) {
  return registry.has(name)
}

/** Names of every registered map (for pickers / diagnostics). */
export function registeredMaps() {
  return Array.from(registry.keys())
}
