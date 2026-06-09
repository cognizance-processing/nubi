/**
 * flows.js — API client for the Flows orchestrator.
 *
 * All functions degrade gracefully: catch any transport/auth error and
 * return a safe empty value so the UI can still render.
 *
 * Endpoints mirror backend/app/routes/flows.py:
 *   POST   /flows                     createFlow
 *   GET    /flows                     listFlows
 *   GET    /flows/{id}                getFlow
 *   PUT    /flows/{id}                updateFlow
 *   DELETE /flows/{id}                deleteFlow
 *   POST   /flows/validate            validateFlow
 *   POST   /flows/{id}/run            runFlow
 *   GET    /flows/{id}/runs           listFlowRuns
 *   GET    /flows/runs/{run_id}       getFlowRun
 */

import { get, post, put, del } from './api.js'

const BASE = '/flows'

// ---------------------------------------------------------------------------
// Flows CRUD
// ---------------------------------------------------------------------------

/**
 * List all flows for the active org.
 * @returns {Promise<Array>}
 */
export async function listFlows() {
  try {
    const data = await get(BASE)
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.flows)) return data.flows
    return []
  } catch (err) {
    console.warn('[flows] listFlows failed:', err.message)
    return []
  }
}

/**
 * Get a single flow by id.
 * @param {string} id
 * @returns {Promise<object|null>}
 */
export async function getFlow(id) {
  try {
    return await get(`${BASE}/${id}`)
  } catch (err) {
    console.warn('[flows] getFlow failed:', err.message)
    return null
  }
}

/**
 * Create a new flow.
 * @param {string} name
 * @param {object} spec  — FlowSpec object (version, name, params, tasks)
 * @returns {Promise<object|null>}  the created flow row
 */
export async function createFlow(name, spec) {
  try {
    return await post(BASE, { name, spec })
  } catch (err) {
    console.warn('[flows] createFlow failed:', err.message)
    return null
  }
}

/**
 * Update an existing flow (name, spec, enabled, schedule — all optional).
 * @param {string} id
 * @param {object} fields
 * @returns {Promise<object|null>}
 */
export async function updateFlow(id, fields) {
  try {
    return await put(`${BASE}/${id}`, fields)
  } catch (err) {
    console.warn('[flows] updateFlow failed:', err.message)
    return null
  }
}

/**
 * Delete a flow.
 * @param {string} id
 * @returns {Promise<boolean>}
 */
export async function deleteFlow(id) {
  try {
    await del(`${BASE}/${id}`)
    return true
  } catch (err) {
    console.warn('[flows] deleteFlow failed:', err.message)
    return false
  }
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

/**
 * Validate a FlowSpec without persisting it.
 * @param {object} spec
 * @returns {Promise<{ valid: boolean, issues: string[] }>}
 */
export async function validateFlow(spec) {
  try {
    return await post(`${BASE}/validate`, { spec })
  } catch (err) {
    console.warn('[flows] validateFlow failed:', err.message)
    return { valid: false, issues: [err.message ?? 'Validation request failed'] }
  }
}

// ---------------------------------------------------------------------------
// Running
// ---------------------------------------------------------------------------

/**
 * Trigger a synchronous run of a flow.
 * @param {string} id
 * @param {object} [params]  — runtime param overrides
 * @param {string} [env]     — trigger-time environment override (dev/prod/custom).
 *                             Omitted ⇒ backend resolves from spec.env → 'prod'.
 * @returns {Promise<object|null>}  flow_run + { task_runs: [...] }
 */
export async function runFlow(id, params = {}, env) {
  try {
    const body = { params }
    if (env) body.env = env
    return await post(`${BASE}/${id}/run`, body)
  } catch (err) {
    console.warn('[flows] runFlow failed:', err.message)
    return null
  }
}

/**
 * List all runs for a flow (newest first).
 * @param {string} id  — flow id
 * @returns {Promise<Array>}
 */
export async function listFlowRuns(id) {
  try {
    const data = await get(`${BASE}/${id}/runs`)
    if (Array.isArray(data)) return data
    if (Array.isArray(data?.runs)) return data.runs
    return []
  } catch (err) {
    console.warn('[flows] listFlowRuns failed:', err.message)
    return []
  }
}

/**
 * Get a single flow run (including its task_runs) for live polling.
 * @param {string} runId
 * @returns {Promise<object|null>}  flow_run + { task_runs: [...] }
 */
export async function getFlowRun(runId) {
  try {
    return await get(`${BASE}/runs/${runId}`)
  } catch (err) {
    console.warn('[flows] getFlowRun failed:', err.message)
    return null
  }
}

// ---------------------------------------------------------------------------
// Codegen
// ---------------------------------------------------------------------------

/**
 * Generate nubi.flows SDK code from a saved flow (POST /flows/{id}/codegen).
 * @param {string} id
 * @returns {Promise<{ source: string }|null>}
 */
export async function codegenFlow(id) {
  try {
    return await post(`${BASE}/${id}/codegen`, {})
  } catch (err) {
    console.warn('[flows] codegenFlow failed:', err.message)
    return null
  }
}

/**
 * Generate nubi.flows SDK code from an inline spec (POST /flows/codegen).
 * @param {object} spec
 * @returns {Promise<{ source: string }|null>}
 */
export async function codegenSpec(spec) {
  try {
    return await post(`${BASE}/codegen`, { spec })
  } catch (err) {
    console.warn('[flows] codegenSpec failed:', err.message)
    return null
  }
}

/**
 * Compile nubi.flows Python SDK source to a FlowSpec (POST /flows/compile).
 * Returns { spec, issues } on success or null on failure.
 * On a 400 compile_error the returned object has { error: string } shape.
 */
export async function compileCode(code) {
  try {
    return await post(`${BASE}/compile`, { code })
  } catch (err) {
    // Surface compile errors to the caller rather than swallowing them.
    const msg = err?.body?.error?.message ?? err?.message ?? 'Compile failed.'
    return { error: msg }
  }
}
