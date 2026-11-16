/**
 * liveState.js — draft vs live resolution for a dashboard.
 *
 * The model, in the user's words: "you can push to live from edit mode".
 *
 *   Draft  — the board row itself (`boards.config.spec`). Every save writes here.
 *            Only authors see it, via /d/:id/edit.
 *   Live   — the version pinned to the project's default (protected) environment.
 *            This is what /d/:id serves to viewers.
 *   Push   — checkpoint the draft as a new version, then promote that version's
 *            pointer from the draft env into the live env.
 *
 * This deliberately rides Nubi's EXISTING version/environment machinery
 * (lib/versions.js `checkpoint` + `promote`, and the `?env=` resolution that
 * `GET /boards/:id` already implements) rather than inventing a parallel
 * `published_spec` blob on `board.config`. A second publishing system would
 * fight checkpoint dedupe (which hashes the whole config), double every board
 * payload, and collide with the Promote/History/pinned-env features that already
 * ship. The cost of riding the real thing is that "live" is env vocabulary
 * underneath; the UI says Draft/Live and never says dev/prod.
 *
 * Back-compat: a board that has never been pushed has no pointer in the live
 * env, so `?env=` resolution returns the draft with `resolved_version: null`.
 * That means every existing board keeps rendering exactly as it does today, and
 * a board only starts diverging once its author pushes it for the first time.
 *
 * This module is PURE on purpose: no JSX and, critically, no import of
 * lib/api.js (which reads `import.meta.env` and only exists under Vite), so
 * `node --test` can import it — see liveState.test.mjs. The impure half, the
 * `pushToLive` call sequence, lives in lib/liveBoard.js and re-exports these.
 */

/** Fallback env keys — what the backend seeds per project (environments/store.py). */
export const FALLBACK_LIVE_ENV = 'prod'
export const FALLBACK_DRAFT_ENV = 'dev'

/**
 * The env a board is "live" in: the project's default environment.
 *
 * Derived from the env list rather than hardcoded to 'prod' so a project with
 * custom environments still resolves correctly; falls back to the seeded key
 * when the list is unavailable (listEnvironments degrades to null offline).
 *
 * @param {Array<{key:string,is_default?:boolean,protected?:boolean,position?:number}>|null} environments
 * @returns {string}
 */
export function resolveLiveEnvKey(environments) {
  if (!Array.isArray(environments) || environments.length === 0) return FALLBACK_LIVE_ENV
  const dflt = environments.find(e => e?.is_default)
  if (dflt?.key) return dflt.key
  // No explicit default: the last protected env by position is the best guess at
  // "furthest right in the pipeline", which is what live means.
  const protectedEnvs = environments.filter(e => e?.protected && e?.key)
  if (protectedEnvs.length > 0) {
    return [...protectedEnvs].sort((a, b) => (a.position ?? 0) - (b.position ?? 0)).at(-1).key
  }
  return FALLBACK_LIVE_ENV
}

/**
 * The env a checkpoint lands in before promotion: the earliest unprotected env.
 *
 * Checkpointing straight into a protected env is refused server-side (protected
 * envs only change via promote), which is exactly why the push is two steps.
 *
 * @param {Array<{key:string,protected?:boolean,position?:number}>|null} environments
 * @returns {string}
 */
export function resolveDraftEnvKey(environments) {
  if (!Array.isArray(environments) || environments.length === 0) return FALLBACK_DRAFT_ENV
  const open = environments.filter(e => e?.key && !e?.protected)
  if (open.length === 0) return FALLBACK_DRAFT_ENV
  return [...open].sort((a, b) => (a.position ?? 0) - (b.position ?? 0))[0].key
}

/**
 * Interpret a `GET /boards/:id?env=<live>` response.
 *
 * @param {object|null} board the response row (config already env-resolved)
 * @returns {{
 *   spec: object|null,
 *   html: string|null,
 *   isLive: boolean,      true when a pushed version is being served
 *   version: number|null, the live version number, when pushed
 * }}
 */
export function resolveLiveRender(board) {
  const resolved = board?.resolved_version ?? null
  return {
    spec: board?.config?.spec ?? null,
    html: board?.config?.html ?? null,
    // `resolved_version` is null both when the board was never pushed AND when
    // env resolution couldn't run at all — either way we're looking at the
    // draft, which is the honest thing to report.
    isLive: Boolean(resolved),
    version: typeof resolved?.version === 'number' ? resolved.version : null,
  }
}

/**
 * Whether the Push-to-live button should offer anything.
 *
 * A never-pushed board is always pushable (that's the first publish). A pushed
 * board is pushable when the draft has moved on — but we can't compare specs
 * cheaply here (the live config lives behind a separate fetch), so this only
 * gates on the states we KNOW: an unsaved draft, or a board with no live version
 * yet. A no-op push is harmless anyway: checkpoint dedupes an identical config.
 *
 * @param {{ isLive: boolean, dirty: boolean }} state
 * @returns {boolean}
 */
export function canPushToLive({ isLive, dirty }) {
  return Boolean(dirty) || !isLive
}

/**
 * Shape a push result for the UI, from the raw checkpoint + promote responses.
 *
 * Split out from the call sequence so the interpretation — which is where the
 * off-by-one "did anything actually publish?" bugs live — stays testable.
 *
 * @param {{version?:number, deduped?:boolean}|null} version checkpoint response
 * @param {{promoted?:Array}|null} promoteResult promote response
 * @returns {{ version: number|null, deduped: boolean, promoted: number }}
 */
export function summarisePush(version, promoteResult) {
  return {
    version: typeof version?.version === 'number' ? version.version : null,
    deduped: Boolean(version?.deduped),
    promoted: Array.isArray(promoteResult?.promoted) ? promoteResult.promoted.length : 0,
  }
}
