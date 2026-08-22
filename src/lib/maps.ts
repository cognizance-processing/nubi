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
const lazyLoaders = new Map() // name -> () => Promise<geoJson>
const labels = new Map() // name -> human-readable label (for pickers)
const categories = new Map() // name -> category ('world' | 'continent' | 'country' | 'other', for grouping pickers)
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
 * @param {string} [label]   human-readable label for pickers (defaults to name)
 * @param {string} [category] group for pickers ('world' | 'continent' | 'country' | 'other')
 */
export function registerMapGeoJson(name, geoJson, label = undefined, category = undefined) {
  if (!name || !geoJson) return
  registry.set(name, geoJson)
  if (label || !labels.has(name)) labels.set(name, label || name)
  if (category || !categories.has(name)) categories.set(name, category || 'other')
  if (echartsRef) echartsRef.registerMap(name, geoJson)
}

/** True if `name` has geometry registered (already loaded, not just lazily available). */
export function hasMap(name) {
  return registry.has(name)
}

/**
 * Register a map whose GeoJSON is fetched on demand rather than held in
 * memory / the initial bundle up front — used for the large built-in country
 * catalog (see ./maps/index.js), where eagerly registering every entry would
 * mean shipping several MB of geometry nobody asked for on every page load.
 *
 * @param {string}   name    map name
 * @param {()=>Promise<object>} loader   resolves to a GeoJSON FeatureCollection
 * @param {string}  [label]  human-readable label for pickers (defaults to name)
 * @param {string}  [category] group for pickers ('world' | 'continent' | 'country' | 'other')
 */
export function registerLazyMap(name, loader, label, category = undefined) {
  if (!name || typeof loader !== 'function') return
  lazyLoaders.set(name, loader)
  labels.set(name, label || name)
  categories.set(name, category || 'other')
}

/**
 * Resolve `name` to registered geometry, loading it lazily if needed.
 * No-op if already registered or if `name` isn't known to the registry.
 * Safe to call repeatedly / concurrently for the same name.
 */
export async function ensureMapRegistered(name) {
  if (!name || registry.has(name)) return
  const loader = lazyLoaders.get(name)
  if (!loader) return
  const geoJson = await loader()
  registerMapGeoJson(name, geoJson)
}

/** Human-readable label for a registered/lazy map name (falls back to the name itself). */
export function mapLabel(name) {
  return labels.get(name) || name
}

/** Picker group for a registered/lazy map name ('world' | 'continent' | 'country' | 'other'). */
export function mapCategory(name) {
  return categories.get(name) || 'other'
}

/** Names of every registered or lazily-available map (for pickers / diagnostics). */
export function registeredMaps() {
  return Array.from(new Set([...registry.keys(), ...lazyLoaders.keys()]))
}
