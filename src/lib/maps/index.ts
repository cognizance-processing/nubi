/**
 * Map geometry registration.
 *
 * Imported once at app start (src/main.jsx). Add a `registerMapGeoJson(...)`
 * line here for each GeoJSON a deployment needs — see ./README.md.
 */

import { registerMapGeoJson } from '../maps.js'
import southAfricaProvinces from './south-africa-provinces.geo.json'

// South Africa's 9 provinces, derived from the public-domain click_that_hood
// dataset (which ships district municipalities, so districts are grouped into
// their province) and simplified from 23 MB to ~140 KB. Feature names:
// Eastern Cape, Free State, Gauteng, KwaZulu-Natal, Limpopo, Mpumalanga,
// North West, Northern Cape, Western Cape.
registerMapGeoJson('south-africa', southAfricaProvinces)
