# Choropleth map geometry

`chart_type: 'map'` widgets colour named regions of a GeoJSON map. ECharts 5
bundles no geometry, so each map must be registered by name before a chart
referencing it renders.

## Registering a map

```js
import { registerMapGeoJson } from '../maps.js'
import myRegions from './my-regions.geo.json'

registerMapGeoJson('my-regions', myRegions)
```

Then a widget refers to it by name:

```json
{
  "type": "chart",
  "chart_type": "map",
  "encoding": { "name": "region", "value": "calls" },
  "config": { "map": "my-regions" }
}
```

The values in the `name` column must match the GeoJSON feature `properties.name`
values. Rows that don't match are ignored by ECharts rather than erroring, so a
partial match degrades gracefully.

## Built-in geography

Nubi ships a `world` map (all 177 UN/sovereign countries) plus a per-country
map for every one of them at admin-1 (state/province) resolution — e.g.
`france`, `united-states-of-america`, `japan` — registered under the slug of
the country's name (see `./countries/manifest.json` for the full slug→label
list, or the "Geography" picker in the chart config panel). All of it is
derived from Natural Earth 1:10m (public domain, no attribution required) and
simplified for tile rendering — coordinates rounded and polygons
Douglas-Peucker-simplified at ε≈0.02°, ~5 MB total across 252 country files.

These are registered **lazily**: `src/lib/maps/index.js` hands each country's
loader to `registerLazyMap()` rather than importing the GeoJSON eagerly, so
Vite code-splits every file into its own chunk and only the map a widget
actually selects gets fetched — the other 251 never leave the server.
`south-africa` is the exception, registered eagerly (it predates the lazy
mechanism and is small enough not to matter).

## Supplying your own geography

For anything the built-in set doesn't cover — sales territories, store
catchments, a finer subdivision than admin-1 — drop a GeoJSON
`FeatureCollection` in this directory and register it:

```js
import { registerLazyMap } from '../maps.js'

registerLazyMap('my-regions', async () => (await import('./my-regions.geo.json')).default, 'My Regions')
```

Each feature needs a `properties.name` matching the values your query returns.
Simplify large sources before bundling (e.g. with mapshaper) — full-resolution
boundaries are megabytes and a dashboard tile cannot show the detail.
`south-africa-provinces.geo.json` and `./countries/*.geo.json` are worked
examples of the same public-domain-source-and-simplify pattern.
