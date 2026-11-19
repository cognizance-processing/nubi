/**
 * queryResultCache.ts — short-lived, in-browser memory of dashboard query results.
 *
 * Why: a board only renders the widgets of the tab on screen, so every tab
 * switch unmounts the old tab's widgets and remounts the new tab's. Without a
 * client-side memory, going back to a tab you just looked at re-runs every one
 * of its queries (and every filter's option list) against the server, whose own
 * cache is per-instance and often misses. Remembering successful results for a
 * few minutes makes tab switches instant without changing what data is shown.
 *
 * Rules:
 *   - Opt-in per call (`runArrowQueryById(..., { cache: true })`). Board widgets
 *     opt in through useQueryCacheOpts(); the query editor never does, so "Run"
 *     always goes to the server.
 *   - Only successful results are kept. A failed query is forgotten at once, so
 *     Retry always re-runs it.
 *   - Identical requests that are already in flight share one server call.
 *   - Entries are scoped to the signed-in user (JWT `sub`) and the active
 *     workspace headers, and expire after CACHE_TTL_MS.
 *   - Bounded by entry count and by approximate Arrow byte size, oldest first.
 *   - clearQueryResultCache() drops everything (query saved, logout, refresh).
 */

export const CACHE_TTL_MS = 10 * 60 * 1000
export const CACHE_MAX_ENTRIES = 80
export const CACHE_MAX_BYTES = 150 * 1024 * 1024

type Entry<T> = {
  promise: Promise<T>
  settled: boolean
  storedAt: number
  bytes: number
}

// Map preserves insertion order; re-inserting on hit makes it an LRU.
const _entries: Map<string, Entry<any>> = new Map()
let _totalBytes = 0
let _now: () => number = () => Date.now()

/** Test hook: replace the clock. */
export function _setNowForTests(fn: (() => number) | null) {
  _now = fn ?? (() => Date.now())
}

/** Number of results currently remembered (tests / diagnostics). */
export function queryResultCacheSize(): number {
  return _entries.size
}

/** Forget every remembered result. */
export function clearQueryResultCache(): void {
  _entries.clear()
  _totalBytes = 0
}

/**
 * Who the cached data belongs to: the JWT subject of the in-memory access
 * token. Returns null when there is no token or it cannot be read — callers
 * then skip the cache entirely rather than risk sharing data across users.
 */
export function cacheIdentity(token: string | null | undefined): string | null {
  if (!token || typeof token !== 'string') return null
  const parts = token.split('.')
  if (parts.length < 2) return null
  try {
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4)
    const json = typeof atob === 'function'
      ? atob(padded)
      : Buffer.from(padded, 'base64').toString('binary')
    const claims = JSON.parse(json)
    const sub = claims?.sub
    return sub != null && sub !== '' ? String(sub) : null
  } catch {
    return null
  }
}

/** Stable JSON: object keys sorted so {a,b} and {b,a} give the same key. */
function stable(value: unknown): string {
  if (value === undefined) return 'null'
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`
  const keys = Object.keys(value as Record<string, unknown>).sort()
  return `{${keys.map(k => `${JSON.stringify(k)}:${stable((value as any)[k])}`).join(',')}}`
}

export function buildCacheKey(parts: {
  identity: string
  tenant: Record<string, string>
  queryId: string
  namedParams?: unknown
  filters?: unknown
  datastoreId?: unknown
  limit?: unknown
  epoch?: unknown
}): string {
  return stable([
    parts.identity,
    parts.tenant ?? {},
    parts.queryId,
    parts.namedParams ?? null,
    parts.filters ?? null,
    parts.datastoreId ?? null,
    parts.limit ?? null,
    parts.epoch ?? 0,
  ])
}

/** Approximate in-memory size of an Arrow table (0 when unknown). */
export function approxTableBytes(table: any): number {
  try {
    const data = table?.data
    if (!Array.isArray(data)) return 0
    let n = 0
    for (const d of data) n += Number(d?.byteLength) || 0
    return n
  } catch {
    return 0
  }
}

function evict() {
  while (_entries.size > CACHE_MAX_ENTRIES || _totalBytes > CACHE_MAX_BYTES) {
    const oldestKey = _entries.keys().next().value
    if (oldestKey === undefined) break
    const e = _entries.get(oldestKey)
    _entries.delete(oldestKey)
    _totalBytes -= e?.bytes ?? 0
  }
  if (_totalBytes < 0) _totalBytes = 0
}

/**
 * Return the remembered result for `key`, or run `run()` once and remember it.
 *
 * `isFailure(result)` marks results that must not be kept (query errors);
 * a thrown error is never kept either.
 */
export function cachedRun<T>(
  key: string,
  run: () => Promise<T>,
  opts: { isFailure?: (r: T) => boolean; sizeOf?: (r: T) => number } = {},
): Promise<T> {
  const hit = _entries.get(key)
  if (hit) {
    const fresh = !hit.settled || (_now() - hit.storedAt) < CACHE_TTL_MS
    if (fresh) {
      // Refresh LRU position.
      _entries.delete(key)
      _entries.set(key, hit)
      return hit.promise
    }
    _entries.delete(key)
    _totalBytes -= hit.bytes
  }

  const entry: Entry<T> = { promise: null as any, settled: false, storedAt: _now(), bytes: 0 }
  const forget = () => {
    if (_entries.get(key) === entry) {
      _entries.delete(key)
      _totalBytes -= entry.bytes
    }
  }
  entry.promise = run().then(
    (result) => {
      if (opts.isFailure?.(result)) {
        forget()
      } else if (_entries.get(key) === entry) {
        entry.settled = true
        entry.storedAt = _now()
        entry.bytes = opts.sizeOf ? opts.sizeOf(result) : 0
        _totalBytes += entry.bytes
        evict()
      }
      return result
    },
    (err) => {
      forget()
      throw err
    },
  )
  _entries.set(key, entry)
  evict()
  return entry.promise
}
