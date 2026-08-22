/**
 * Map geometry registration.
 *
 * Imported once at app start (src/main.jsx). Add a `registerMapGeoJson(...)`
 * line here for each GeoJSON a deployment needs — see ./README.md.
 */

import { registerMapGeoJson, registerLazyMap } from '../maps.js'
import southAfricaProvinces from './south-africa-provinces.geo.json'
import countryManifest from './countries/manifest.json'
import continentManifest from './continents/manifest.json'

// South Africa's 9 provinces, derived from the public-domain click_that_hood
// dataset (which ships district municipalities, so districts are grouped into
// their province) and simplified from 23 MB to ~140 KB. Feature names:
// Eastern Cape, Free State, Gauteng, KwaZulu-Natal, Limpopo, Mpumalanga,
// North West, Northern Cape, Western Cape.
registerMapGeoJson('south-africa', southAfricaProvinces, 'South Africa', 'country')

// World countries, continents (country-level, one map per continent), and
// per-country admin-1 (state/province) boundaries — derived from Natural
// Earth (public domain, no attribution required) and simplified for tile
// rendering. Registered lazily — `import.meta.glob` without `eager` gives
// Vite a dynamic import per file, so the several MB of geometry is split
// into on-demand chunks and only the one map a widget actually selects gets
// fetched, instead of bundling every entry into every page load.
// Regenerate via a fresh Natural Earth pull if this ever needs updating.
registerLazyMap('world', async () => (await import('./world.geo.json')).default, 'World', 'world')

const continentModules = import.meta.glob<{ default: object }>('./continents/*.geo.json')
for (const { slug, label } of continentManifest) {
  const path = `./continents/${slug}.geo.json`
  const load = continentModules[path]
  if (load) registerLazyMap(slug, async () => (await load()).default, label, 'continent')
}

const countryModules = import.meta.glob<{ default: object }>('./countries/*.geo.json')
for (const { slug, label } of countryManifest) {
  const path = `./countries/${slug}.geo.json`
  const load = countryModules[path]
  if (load) registerLazyMap(slug, async () => (await load()).default, label, 'country')
}
