/**
 * boardThumbnail.js — fetch a board's rendered SVG thumbnail, politely.
 *
 * `GET /boards/:id/thumbnail.svg` renders the REAL board server-side (runs the
 * board's queries, then Node/ECharts SSR — see app/dashboards/svg_render.py).
 * That is genuinely expensive: ~7-10s cold for a big board, and it is cached
 * server-side by (spec hash, policy fingerprint) so it is ~25ms warm.
 *
 * Two consequences this module exists to handle:
 *
 *  1. A dashboards page with 13 cards must NOT fire 13 cold renders at once —
 *     each one is a Node subprocess plus that board's whole query set. So all
 *     requests funnel through a small concurrency gate and cards fill in
 *     progressively instead of stampeding the API.
 *  2. `<img src>` cannot send an Authorization header, and the endpoint is
 *     first-party authed. So we fetch through lib/api.js's `getBlob` (which
 *     attaches the bearer token + X-Org-Id/X-Project-Id) and hand the caller an
 *     object URL.
 *
 * 3. Coming back to the gallery should not mean watching every card fill in
 *    again. Pictures are cached in memory (SPA navigation) and in the Cache
 *    API (reloads), keyed by board, theme and the board's `updated_at`, and
 *    served instantly; a background check then refreshes the cached copy for
 *    next time (stale-while-revalidate).
 *
 * This module owns every object URL it hands out — callers must NOT revoke them.
 */

import { getBlob } from './api.js'

/**
 * Max cold renders in flight at once.
 *
 * 2 is deliberate, not tuned: the server's own Node-subprocess semaphore is the
 * real backstop, and the point here is to stop a scrolling gallery from queueing
 * dozens of board-wide query runs.
 */
const MAX_CONCURRENT = 2

/**
 * Max stored-picture lookups in flight. These are ~10ms reads from the server's
 * cache/object storage (`cached_only=1` never renders), so they must not wait
 * behind cold renders — 6 matches the browser's own per-host connection cap.
 */
const MAX_STORED_CONCURRENT = 6

interface ThumbnailJob {
  run: () => Promise<any>
  resolve: (value: any) => void
  reject: (reason?: any) => void
  signal?: AbortSignal
}

interface Gate {
  active: number
  queue: ThumbnailJob[]
  limit: number
}

const _coldGate: Gate = { active: 0, queue: [], limit: MAX_CONCURRENT }
const _storedGate: Gate = { active: 0, queue: [], limit: MAX_STORED_CONCURRENT }

function _pump(gate: Gate) {
  while (gate.active < gate.limit && gate.queue.length > 0) {
    const job = gate.queue.shift()
    if (!job) continue
    if (job.signal?.aborted) { job.resolve(null); continue }   // scrolled away before we got to it
    gate.active += 1
    job
      .run()
      .then(job.resolve, job.reject)
      .finally(() => {
        gate.active -= 1
        _pump(gate)
      })
  }
}

/** Run `fn` when a slot in `gate` frees up. Aborted jobs resolve null unrun. */
function _gate<T>(gate: Gate, fn: () => Promise<T>, signal?: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    gate.queue.push({ run: fn, resolve, reject, signal })
    _pump(gate)
  })
}

type Theme = 'light' | 'dark'

// ── Client cache ────────────────────────────────────────────────────────────

/** In-memory: key -> object URL. Survives SPA navigation, not reloads. */
const _memory = new Map<string, string>()
const MEMORY_MAX = 300

/** Keys already revalidated this session — one background check per picture. */
const _revalidated = new Set<string>()

const CACHE_NAME = 'nubi-board-thumbnails-v1'

function _key(boardId: string, theme: Theme, version?: string | null) {
  return `${boardId}|${theme}|${version ?? ''}`
}

/** Synthetic same-origin URL the Cache API stores a picture under. */
function _cacheUrl(boardId: string, theme: Theme, version?: string | null) {
  return `${location.origin}/__nubi-thumb/${encodeURIComponent(boardId)}/${theme}?v=${encodeURIComponent(version ?? '')}`
}

function _remember(key: string, blob: Blob): string {
  const prev = _memory.get(key)
  if (prev) { _memory.delete(key); URL.revokeObjectURL(prev) }
  if (_memory.size >= MEMORY_MAX) {
    const oldest = _memory.keys().next().value
    if (oldest !== undefined) {
      URL.revokeObjectURL(_memory.get(oldest)!)
      _memory.delete(oldest)
    }
  }
  const url = URL.createObjectURL(blob)
  _memory.set(key, url)
  return url
}

async function _openCache(): Promise<Cache | null> {
  try {
    return typeof caches === 'undefined' ? null : await caches.open(CACHE_NAME)
  } catch {
    return null   // private window / blocked storage: memory cache only
  }
}

async function _readPersisted(boardId: string, theme: Theme, version?: string | null): Promise<Blob | null> {
  const cache = await _openCache()
  if (!cache) return null
  try {
    const hit = await cache.match(_cacheUrl(boardId, theme, version))
    return hit ? await hit.blob() : null
  } catch {
    return null
  }
}

async function _writePersisted(boardId: string, theme: Theme, version: string | null | undefined, blob: Blob) {
  const cache = await _openCache()
  if (!cache) return
  const url = _cacheUrl(boardId, theme, version)
  try {
    // One entry per board+theme: drop the superseded versions first.
    for (const old of await cache.keys(url, { ignoreSearch: true })) await cache.delete(old)
    await cache.put(url, new Response(blob, { headers: { 'Content-Type': 'image/svg+xml' } }))
  } catch {
    /* quota or blocked storage — the memory cache still has it */
  }
}

/** Drop every cached picture (call on logout: the cache is per-browser, not per-user). */
export async function clearBoardThumbnailCache() {
  for (const url of _memory.values()) URL.revokeObjectURL(url)
  _memory.clear()
  _revalidated.clear()
  try {
    if (typeof caches !== 'undefined') await caches.delete(CACHE_NAME)
  } catch { /* nothing to clear */ }
}

/**
 * The cached picture for this board version, synchronously — lets a card
 * mount with its picture already in place instead of fading it in.
 */
export function peekBoardThumbnail(
  boardId: string,
  { theme = 'light', version }: { theme?: Theme; version?: string | null } = {},
): string | null {
  return _memory.get(_key(boardId, theme, version)) ?? null
}

// ── Network ─────────────────────────────────────────────────────────────────

/** null = 503 (no renderer) / 404 (board gone): keep the placeholder. */
async function _get(path: string): Promise<Blob | null> {
  try {
    return await getBlob(path)
  } catch (err) {
    if (err?.status === 503 || err?.status === 404) return null
    throw err
  }
}

function _path(boardId: string, theme: Theme, cachedOnly: boolean) {
  // Theme is part of the REQUEST, not a CSS afterthought: a board's widgets
  // are styled with theme tokens (var(--surface)…) that only the browser can
  // resolve, so the server has to render for a chosen theme. Ask for ours.
  const q = `theme=${encodeURIComponent(theme)}${cachedOnly ? '&cached_only=1' : ''}`
  return `/boards/${boardId}/thumbnail.svg?${q}`
}

/** The server's stored picture, or null when it has none yet (204 → empty blob). */
function _fetchStored(boardId: string, theme: Theme, signal?: AbortSignal): Promise<Blob | null> {
  return _gate(_storedGate, async () => {
    if (signal?.aborted) return null
    const blob = await _get(_path(boardId, theme, true))
    return blob && blob.size > 0 ? blob : null
  }, signal)
}

/** Refresh a cached picture in the background, for the next view. */
function _revalidate(boardId: string, theme: Theme, version: string | null | undefined, key: string) {
  if (_revalidated.has(key)) return
  _revalidated.add(key)
  _fetchStored(boardId, theme)
    .then((blob) => {
      // No stored picture (e.g. a renderer upgrade not yet re-warmed): keep
      // showing the cached one rather than dropping back to the placeholder.
      if (!blob) return
      _remember(key, blob)
      return _writePersisted(boardId, theme, version, blob)
    })
    .catch(() => { /* the cached picture stays */ })
}

/**
 * Fetch a board's rendered thumbnail as an object URL.
 *
 * Order: memory → Cache API → the server's stored picture (fast lane) → a cold
 * render (2-at-a-time lane). Cached hits are revalidated in the background.
 *
 * @param version the board's `updated_at` — a saved board is a new picture.
 * @returns an object URL owned by this module, or null when the server has no
 *   render to give (503 when Node is unavailable) — the caller should keep its
 *   placeholder rather than show a broken image.
 */
export async function fetchBoardThumbnail(
  boardId: string,
  { signal, theme = 'light', version }: { signal?: AbortSignal; theme?: Theme; version?: string | null } = {},
): Promise<string | null> {
  const key = _key(boardId, theme, version)

  const inMemory = _memory.get(key)
  if (inMemory) {
    _revalidate(boardId, theme, version, key)
    return inMemory
  }

  const persisted = await _readPersisted(boardId, theme, version)
  if (signal?.aborted) return null
  if (persisted) {
    const url = _remember(key, persisted)
    _revalidate(boardId, theme, version, key)
    return url
  }

  let blob = await _fetchStored(boardId, theme, signal)
  if (signal?.aborted) return null
  if (!blob) {
    blob = await _gate(_coldGate, async () => {
      if (signal?.aborted) return null
      return _get(_path(boardId, theme, false))
    }, signal)
    if (signal?.aborted || !blob) return null
  }
  // Fresh from the server: nothing to revalidate this session.
  _revalidated.add(key)
  void _writePersisted(boardId, theme, version, blob)
  return _remember(key, blob)
}

/** Test seam: how many renders are in flight / waiting. */
export function _thumbnailQueueState() {
  return {
    active: _coldGate.active,
    queued: _coldGate.queue.length,
    storedActive: _storedGate.active,
    storedQueued: _storedGate.queue.length,
  }
}
