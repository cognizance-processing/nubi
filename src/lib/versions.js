/**
 * lib/versions.js — API client for project-scoped environments + resource
 * versioning (backend: app/routes/environments.py).
 *
 * Resources of kind 'flow' | 'board' | 'query' can be checkpointed into
 * immutable versions; per-project environments (dev/prod/custom) each hold a
 * pointer to a pinned version. Promote copies a pointer between environments.
 *
 * Error handling (house pattern, see lib/admin.js):
 *   - Read helpers (listEnvironments, listVersions, getVersion) are graceful:
 *     they log a warning and return null so callers can fall back / degrade.
 *   - Mutations (create/update/delete/checkpoint/restore/promote) THROW so the
 *     UI can surface the backend's error message (e.g. 409 on protected envs).
 *
 * X-Org-Id / X-Project-Id headers are attached automatically by lib/api.js.
 */

import { get, post, patch, del } from './api.js'

// ---------------------------------------------------------------------------
// Environments
// ---------------------------------------------------------------------------

/**
 * List a project's environments (the backend lazily seeds dev + prod).
 *
 * GET /projects/{projectId}/environments
 *
 * @param {string} projectId
 * @returns {Promise<Array<{
 *   id: string, project_id: string, key: string, name: string,
 *   is_default: boolean, protected: boolean, position: number, created_at: string
 * }> | null>} null on failure so callers can fall back (e.g. to localStorage).
 */
export async function listEnvironments(projectId) {
  try {
    const data = await get(`/projects/${projectId}/environments`)
    return Array.isArray(data) ? data : null
  } catch (cause) {
    console.warn('[versions] listEnvironments failed:', cause.message)
    return null
  }
}

/**
 * Create an environment in a project.
 *
 * POST /projects/{projectId}/environments { key, name, git_branch?, from_branch? }
 *
 * git_branch defaults server-side ('main' for key 'prod', else the key).
 * from_branch seeds the new env from an existing branch in the project's git
 * workspace repo (best-effort — the response carries `imported` counts, or a
 * `warning` string when the repo/branch is missing and the env stays empty).
 *
 * @param {string} projectId
 * @param {{ key: string, name: string, git_branch?: string, from_branch?: string }} body
 * @returns {Promise<Object>} the created environment row (+ imported?/warning?).
 *   Throws on failure.
 */
export function createEnvironment(projectId, { key, name, git_branch, from_branch }) {
  return post(`/projects/${projectId}/environments`, { key, name, git_branch, from_branch })
}

/**
 * Update an environment.
 *
 * PATCH /environments/{id} { name?, is_default?, protected? }
 *
 * @param {string} id
 * @param {{ name?: string, is_default?: boolean, protected?: boolean }} body
 * @returns {Promise<Object>} the updated environment row. Throws on failure.
 */
export function updateEnvironment(id, body) {
  return patch(`/environments/${id}`, body)
}

/**
 * Delete an environment (409 if it is the default or protected).
 *
 * DELETE /environments/{id}
 *
 * @param {string} id
 * @returns {Promise<null>} Throws on failure.
 */
export function deleteEnvironment(id) {
  return del(`/environments/${id}`)
}

// ---------------------------------------------------------------------------
// Versions
// ---------------------------------------------------------------------------

/**
 * List a resource's version history + environment pointers.
 *
 * GET /versions/{kind}/{resourceId}
 *
 * @param {'flow'|'board'|'query'} kind
 * @param {string} resourceId
 * @returns {Promise<{
 *   versions: Array<{ id: string, version: number, config_hash: string,
 *     message: string|null, created_by: string|null, created_at: string }>,
 *   pointers: Array<{ environment_id: string, env_key: string, version_id: string,
 *     version: number, promoted_at: string, promoted_by: string|null }>
 * } | null>} null on failure.
 */
export async function listVersions(kind, resourceId) {
  try {
    return await get(`/versions/${kind}/${resourceId}`)
  } catch (cause) {
    console.warn('[versions] listVersions failed:', cause.message)
    return null
  }
}

/**
 * Checkpoint the resource's current draft definition as a new version, then
 * point env_key's environment at it (protected envs only change via promote).
 * Dedupes: an identical draft returns the latest version with deduped=true.
 *
 * POST /versions/{kind}/{resourceId} { message?, env_key? }
 *
 * @param {'flow'|'board'|'query'} kind
 * @param {string} resourceId
 * @param {{ message?: string, env_key?: string }} [body]
 * @returns {Promise<{ id: string, version: number, config_hash: string,
 *   message: string|null, deduped?: boolean }>} Throws on failure.
 */
export function checkpoint(kind, resourceId, { message, env_key = 'dev' } = {}) {
  return post(`/versions/${kind}/${resourceId}`, { message, env_key })
}

/**
 * Fetch one full version (including its config snapshot).
 *
 * GET /versions/{kind}/{resourceId}/{version}
 *
 * @param {'flow'|'board'|'query'} kind
 * @param {string} resourceId
 * @param {number} version
 * @returns {Promise<Object | null>} null on failure.
 */
export async function getVersion(kind, resourceId, version) {
  try {
    return await get(`/versions/${kind}/${resourceId}/${version}`)
  } catch (cause) {
    console.warn('[versions] getVersion failed:', cause.message)
    return null
  }
}

/**
 * Restore a version's config back into the resource's draft definition.
 *
 * POST /versions/{kind}/{resourceId}/{version}/restore
 *
 * @param {'flow'|'board'|'query'} kind
 * @param {string} resourceId
 * @param {number} version
 * @returns {Promise<Object>} the updated draft row. Throws on failure.
 */
export function restoreVersion(kind, resourceId, version) {
  return post(`/versions/${kind}/${resourceId}/${version}/restore`)
}

/**
 * Promote: copy from_env's pinned version pointer to to_env (plus, for flows,
 * best-effort watermark copies; for boards, dependent queries' pointers too
 * when include_dependencies).
 *
 * POST /environments/promote
 *
 * @param {{
 *   kind: 'flow'|'board'|'query',
 *   resource_id: string,
 *   from_env: string,
 *   to_env: string,
 *   include_dependencies?: boolean,
 * }} body
 * @returns {Promise<{ promoted: Array<Object> }>} Throws on failure.
 */
export function promote({ kind, resource_id, from_env, to_env, include_dependencies = true }) {
  return post('/environments/promote', { kind, resource_id, from_env, to_env, include_dependencies })
}
