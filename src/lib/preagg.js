/**
 * preagg.js — API client for the auto pre-aggregation engine.
 *
 * Pre-aggregations are materialized rollup tables Nubi mines from the query
 * log: hot `GROUP BY` shapes are ranked by frequency × scanned-bytes,
 * materialized, content-hashed, and transparently routed to. This client wires
 * the query-section UI to those endpoints.
 *
 * Endpoints mirror backend/app/routes/preagg.py (mounted under /api/v1):
 *   GET  /preagg/suggestions   fetchPreaggSuggestions  — ranked mined candidates
 *   GET  /preagg               fetchPreaggs            — built rollups + HIT counts
 *   POST /preagg/build         buildPreagg             — materialize a rollup (writer-only)
 *
 * Read functions degrade gracefully: any transport/auth error is caught and a
 * safe empty value returned so the panel can still render its empty state.
 * buildPreagg surfaces its error to the caller so a writer can see why a build
 * failed (e.g. 403 not-a-writer, 404 unknown cluster_key, 400 invalid request).
 */

import { get, post } from './api.js'

const BASE = '/preagg'

/**
 * @typedef {Object} PreaggSuggestion  — a ranked mined rollup candidate.
 * @property {string}   table         Base fact table the rollup would aggregate.
 * @property {string[]} dimensions    GROUP BY columns (superset across the cluster).
 * @property {string[]} measures      `func(col)` measure strings, e.g. "sum(amount)".
 * @property {string[]} filters       Columns seen in WHERE clauses of clustered queries.
 * @property {number}   score         Rank key = sample_count × est_bytes.
 * @property {number}   sample_count  How many logged queries matched this cluster.
 * @property {number}   est_bytes     Estimated bytes scanned by the cluster.
 * @property {string}   cluster_key   Stable id — pass to buildPreagg to build it.
 */

/**
 * @typedef {Object} BuiltPreagg  — a materialized + registered rollup.
 * @property {string}        rollup_id     Stable id (also the registered query_id).
 * @property {string}        table         Rollup table name inside its DuckDB file.
 * @property {string}        source_table  Base fact table the rollup was built from.
 * @property {string[]}      dimensions    GROUP BY columns the rollup is grouped on.
 * @property {string[]}      measures      `func(col)` measure strings materialized.
 * @property {string[]}      rls_keys      Preserved RLS-key columns.
 * @property {string|null}   database      DuckDB file the rollup lives in.
 * @property {string|null}   datastore_id  Datastore the rollup is served through.
 * @property {string|null}   query_id      Registered query id for the read path.
 * @property {string}        rewrite_sig   Legacy exact groupby_sig (M2-C path).
 * @property {number}        hits          Incoming queries routed to this rollup.
 */

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

/**
 * Fetch ranked pre-aggregation candidates mined from the query log.
 *
 * GET /preagg/suggestions?min_hits=<n>
 *
 * @param {{ minHits?: number }} [opts] minimum sample_count to surface (default 3)
 * @returns {Promise<PreaggSuggestion[]>}  [] on any failure.
 */
export async function fetchPreaggSuggestions({ minHits } = {}) {
  try {
    const qs = minHits != null ? `?min_hits=${encodeURIComponent(minHits)}` : ''
    const data = await get(`${BASE}/suggestions${qs}`)
    return Array.isArray(data) ? data : []
  } catch (err) {
    console.warn('[preagg] fetchPreaggSuggestions failed:', err.message)
    return []
  }
}

/**
 * Fetch the rollups that have been built, each with its routed-query HIT count.
 *
 * GET /preagg
 *
 * @returns {Promise<BuiltPreagg[]>}  [] on any failure.
 */
export async function fetchPreaggs() {
  try {
    const data = await get(BASE)
    return Array.isArray(data) ? data : []
  } catch (err) {
    console.warn('[preagg] fetchPreaggs failed:', err.message)
    return []
  }
}

// ---------------------------------------------------------------------------
// Build (writer-only)
// ---------------------------------------------------------------------------

/**
 * Materialize and register a rollup for a chosen shape (writer-only).
 *
 * POST /preagg/build → 201 with the built-rollup manifest.
 *
 * Supply a mined candidate by `cluster_key`, or specify the shape explicitly
 * (`table` + `measures`, optional `dimensions`). Unlike the read helpers this
 * re-throws on failure so the caller can surface why a build was rejected
 * (403 not-a-writer, 404 unknown cluster_key, 400 invalid request).
 *
 * @param {{
 *   cluster_key?: string,
 *   table?: string,
 *   dimensions?: string[],
 *   measures?: string[],
 *   rls_keys?: string[],
 *   source_database?: string,
 *   datastore_id?: string,
 * }} body
 * @returns {Promise<BuiltPreagg>}
 */
export function buildPreagg(body) {
  return post(`${BASE}/build`, body)
}
