/**
 * lib/gitenv.js — API client for the environment ⇄ git-branch sync layer
 * (backend: app/routes/environments.py + app/git/env_sync.py).
 *
 * Every environment is bound to a branch (env.git_branch) in the project's
 * git workspace repo. These helpers drive the explicit sync operations:
 * push (env pins → branch), pull (branch → env pins, 409 on divergence) and
 * the per-project commit graph. Creating an env from a branch is handled by
 * createEnvironment in lib/versions.js (git_branch / from_branch fields).
 *
 * Error handling (house pattern, see lib/versions.js):
 *   - Read helpers (getGitGraph) are graceful: log a warning, return null.
 *   - Mutations (pushEnvironment, pullEnvironment) THROW so the UI can
 *     surface the backend's message. A diverged pull throws with
 *     err.status === 409 and err.payload === { diverged, files, env_sha,
 *     branch_sha } — re-call pullEnvironment with a strategy to resolve.
 *
 * The backend git layer is best-effort: with no workspace repo / remote the
 * endpoints still return 200 with `warning` / `warnings` fields, so callers
 * should surface those instead of assuming a commit happened.
 *
 * X-Org-Id / X-Project-Id headers are attached automatically by lib/api.js.
 */

import { get, post } from './api.js'

/**
 * Push: serialize ALL resources pinned in the environment to its git branch
 * as one commit, update last_synced_sha, and push to the project's remote
 * when one is bound.
 *
 * POST /environments/{envId}/git/push { message? }
 *
 * @param {string} envId
 * @param {{ message?: string }} [body]
 * @returns {Promise<{
 *   branch: string, sha: string|null, committed: boolean, files: number,
 *   pushed: boolean, last_synced_sha: string|null, warnings: string[]
 * }>} Throws on failure.
 */
export function pushEnvironment(envId, { message } = {}) {
  return post(`/environments/${envId}/git/push`, { message })
}

/**
 * Pull: sync the environment from its git branch.
 *
 * POST /environments/{envId}/git/pull { strategy? }
 *
 * Outcomes:
 *   - up to date            → { pulled: false, up_to_date: true, sha }
 *   - fast-forward          → { pulled: true, sha, updated: {kind: n} }
 *   - diverged (no strategy)→ THROWS with err.status === 409 and
 *     err.payload === { diverged: true, files, env_sha, branch_sha }
 *   - no repo / branch      → { pulled: false, warning }
 *
 * @param {string} envId
 * @param {{ strategy?: 'take_branch'|'take_env' }} [body]
 *   'take_branch' imports the branch state into the env; 'take_env'
 *   overwrites the branch from the env's pinned state (force-with-lease).
 * @returns {Promise<Object>} Throws on failure (incl. 409 divergence).
 */
export function pullEnvironment(envId, { strategy } = {}) {
  return post(`/environments/${envId}/git/pull`, strategy ? { strategy } : {})
}

/**
 * Extract the divergence payload from a pullEnvironment error, or null.
 *
 * A diverged pull rejects with err.status === 409 and
 * err.payload === { diverged: true, files, env_sha, branch_sha } — callers
 * re-run pullEnvironment with strategy 'take_branch' or 'take_env'.
 *
 * @param {Error & { status?: number, payload?: Object }} err
 * @returns {{ diverged: true, files: string[], env_sha: string|null,
 *   branch_sha: string }|null}
 */
export function divergedPayload(err) {
  return err?.status === 409 && err?.payload?.diverged ? err.payload : null
}

/**
 * Fetch the project's commit graph: one entry per env-bound branch.
 *
 * GET /projects/{projectId}/git/graph
 *
 * @param {string} projectId
 * @returns {Promise<{
 *   branches: Array<{
 *     branch: string, env_key: string, head_sha: string,
 *     commits: Array<{ sha: string, parents: string[], message: string,
 *       author: string, date: string }>
 *   }>
 * } | null>} null on failure so callers can degrade (graph is read-only sugar).
 */
export async function getGitGraph(projectId) {
  try {
    return await get(`/projects/${projectId}/git/graph`)
  } catch (cause) {
    console.warn('[gitenv] getGitGraph failed:', cause.message)
    return null
  }
}
