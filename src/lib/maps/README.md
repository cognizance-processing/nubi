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

## Supplying real geography

Nubi deliberately ships **no** real-world geography — boundary data carries
licensing and accuracy obligations, and every deployment needs different
regions (countries, provinces, sales territories, store catchments).

Drop a GeoJSON `FeatureCollection` in this directory and register it as above.
Each feature needs a `properties.name` matching the values your query returns.
Sources such as Natural Earth (public domain) and geoBoundaries (CC-BY) publish
suitable files; simplify them (e.g. with mapshaper) before bundling — full
resolution boundaries are megabytes and a dashboard tile cannot show the detail.

`south-africa-provinces.geo.json` is a worked example: a public-domain source
grouped to the level the data actually uses and simplified for tile rendering.
