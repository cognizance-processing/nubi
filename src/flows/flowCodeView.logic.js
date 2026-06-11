/**
 * flowCodeView.logic.js — pure, side-effect-free logic for FlowCodeView.
 *
 * Extracted so it can be unit-tested with `node --test` (no jsdom / Monaco
 * needed). FlowCodeView.jsx imports these helpers and only owns React state +
 * rendering. Every function here is deterministic given its inputs.
 *
 * Covers:
 *   • fileMetaForTask / buildCellFiles — the cell → virtual-file projection.
 *   • deriveLoadKey                    — when to (re)fetch flow.py codegen.
 *   • classifyCodegenError             — map a thrown API error to a render
 *                                        state (unavailable | invalidSpec | error).
 *   • selectActiveId                   — resolve the effective selection when a
 *                                        cell file disappears (task deleted).
 *   • activeSourceFor                  — the text shown for the active file.
 */

import {
  Database,
  FileCode2,
  FileText,
  FileJson,
} from 'lucide-react'

export const FLOW_PY_ID = '__flow_py__'

// ---------------------------------------------------------------------------
// Cell → virtual-file mapping
// ---------------------------------------------------------------------------

/**
 * Resolve the editable-source projection for a task. Returns the config key
 * that holds the source, the file extension, the Monaco language, and an icon.
 * `key === null` ⇒ no single source string ⇒ render the config as read-only JSON.
 */
export function fileMetaForTask(task) {
  const ct = task?.cell_type
  const kind = task?.kind
  if (ct === 'sql' || kind === 'query') {
    return { ext: 'sql', lang: 'sql', key: 'sql', Icon: Database }
  }
  if (ct === 'python' || kind === 'python') {
    return { ext: 'py', lang: 'python', key: 'code', Icon: FileCode2 }
  }
  if (ct === 'markdown' || kind === 'note' || kind === 'noop') {
    return { ext: 'md', lang: 'markdown', key: 'markdown', Icon: FileText }
  }
  // agent / materialize / branch / map / bucket_load / … — no single
  // source string; expose the raw config read-only as JSON.
  return { ext: 'json', lang: 'json', key: null, Icon: FileJson }
}

/** Build the virtual file list (cells/…) from the spec's tasks. */
export function buildCellFiles(spec) {
  const tasks = Array.isArray(spec?.tasks) ? spec.tasks : []
  return tasks.map((task, index) => {
    const meta = fileMetaForTask(task)
    const safeKey = String(task?.key ?? `task_${index + 1}`).replace(/[^\w.-]+/g, '_')
    const num = String(index + 1).padStart(2, '0')
    return {
      id: `cell:${index}`,
      index,
      task,
      name: `${num}_${safeKey}.${meta.ext}`,
      ...meta,
    }
  })
}

// ---------------------------------------------------------------------------
// Codegen fetch key
// ---------------------------------------------------------------------------

/**
 * Derive the key that decides when flow.py codegen must be re-fetched.
 *
 * IMPORTANT: when a flow is SAVED (`flowId` set) we still key on the spec
 * CONTENT, not just the id. The previous (buggy) version keyed on `id:<flowId>`
 * alone, so after an in-memory edit (Apply, canvas/notebook change) the code
 * view re-generated from the PERSISTED DB row and silently discarded the live
 * spec. Keying on content keeps flow.py in sync with whatever the user is
 * actually editing, saved or not.
 *
 * @param {string|null} flowId
 * @param {object|null} spec
 * @returns {string|null} stable key, or null when there is nothing to generate.
 */
export function deriveLoadKey(flowId, spec) {
  let specHash = null
  if (spec) {
    try {
      specHash = JSON.stringify(spec)
    } catch {
      specHash = null
    }
  }
  if (flowId) return `id:${flowId}|spec:${specHash ?? ''}`
  if (specHash !== null) return `spec:${specHash}`
  return null
}

// ---------------------------------------------------------------------------
// Codegen error classification
// ---------------------------------------------------------------------------

/**
 * Map a thrown API error from the codegen request to a render state.
 *
 *  - 404                       → `unavailable`  (endpoint not deployed)
 *  - 400 / 422 (bad_flow_spec) → `invalidSpec`  (the spec can't be generated
 *                                yet — empty SQL cell, missing config, etc.).
 *                                This is a NORMAL transient state for a
 *                                half-built flow, NOT a hard failure, so the
 *                                UI shows a gentle hint instead of a red error.
 *  - anything else             → `error`        (network / server fault).
 *
 * @param {{ status?: number, message?: string }} err
 * @returns {{ kind: 'unavailable'|'invalidSpec'|'error', message: string|null }}
 */
export function classifyCodegenError(err) {
  const status = err?.status
  const message = err?.message ?? 'Codegen request failed.'
  if (status === 404 || (message && message.includes('404'))) {
    return { kind: 'unavailable', message: null }
  }
  if (status === 400 || status === 422) {
    return { kind: 'invalidSpec', message }
  }
  return { kind: 'error', message }
}

// ---------------------------------------------------------------------------
// Active-file selection
// ---------------------------------------------------------------------------

/**
 * Resolve the effective selection. If the selected cell file disappeared
 * (its task was deleted), fall back to flow.py. Pure derivation — no setState.
 *
 * @param {string} selectedId
 * @param {Array<{id: string}>} cellFiles
 * @returns {string}
 */
export function selectActiveId(selectedId, cellFiles) {
  if (selectedId === FLOW_PY_ID) return FLOW_PY_ID
  return (cellFiles ?? []).some(f => f.id === selectedId) ? selectedId : FLOW_PY_ID
}

/**
 * Text shown for the active file.
 *  - flow.py        → the live editor value (or generated source).
 *  - source cell    → config[key] as a string.
 *  - read-only cell → pretty-printed config JSON.
 *
 * @param {object} args
 * @returns {string}
 */
export function activeSourceFor({ activeId, pyValue, pySource, selectedCell }) {
  if (activeId === FLOW_PY_ID) return pyValue ?? pySource ?? ''
  if (!selectedCell) return ''
  if (selectedCell.key !== null) {
    return String(selectedCell.task?.config?.[selectedCell.key] ?? '')
  }
  return JSON.stringify(selectedCell.task?.config ?? {}, null, 2)
}
