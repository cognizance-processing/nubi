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
 * The caller owns revoking the object URL — see BoardThumbnail.jsx.
 */

import { getBlob } from './api.js'

/**
 * Max cold renders in flight at once.
 *
 * 2 is deliberate, not tuned: the server's own Node-subprocess semaphore is the
 * real backstop, and the point here is to stop a scrolling gallery from queueing
 * dozens of board-wide query runs. Cached boards resolve so fast that this gate
 * is invisible for them.
 */
const MAX_CONCURRENT = 2

let _active = 0
const _queue = []

function _pump() {
  while (_active < MAX_CONCURRENT && _queue.length > 0) {
    const job = _queue.shift()
    if (job.signal?.aborted) continue   // scrolled away before we got to it
    _active += 1
    job
      .run()
      .then(job.resolve, job.reject)
      .finally(() => {
        _active -= 1
        _pump()
      })
  }
}

/** Run `fn` when a slot frees up. Aborted jobs are dropped from the queue. */
function _gate(fn, signal) {
  return new Promise((resolve, reject) => {
    _queue.push({ run: fn, resolve, reject, signal })
    _pump()
  })
}

/**
 * Fetch a board's rendered thumbnail as an object URL.
 *
 * @param {string} boardId
 * @param {{ signal?: AbortSignal, theme?: 'light'|'dark' }} [opts]
 * @returns {Promise<string|null>} an object URL, or null when the server has no
 *   render to give (503 when Node is unavailable) — the caller should keep its
 *   placeholder rather than show a broken image.
 */
export async function fetchBoardThumbnail(boardId, { signal, theme = 'light' } = {}) {
  return _gate(async () => {
    if (signal?.aborted) return null
    try {
      // Theme is part of the REQUEST, not a CSS afterthought: a board's widgets
      // are styled with theme tokens (var(--surface)…) that only the browser can
      // resolve, so the server has to render for a chosen theme. Ask for ours.
      const blob = await getBlob(`/boards/${boardId}/thumbnail.svg?theme=${encodeURIComponent(theme)}`)
      if (signal?.aborted) return null
      return URL.createObjectURL(blob)
    } catch (err) {
      // 503 = renderer unavailable (no Node on the server); 404 = board gone.
      // Neither is worth surfacing on a card — fall back to the placeholder.
      if (err?.status === 503 || err?.status === 404) return null
      throw err
    }
  }, signal)
}

/** Test seam: how many renders are in flight / waiting. */
export function _thumbnailQueueState() {
  return { active: _active, queued: _queue.length }
}
