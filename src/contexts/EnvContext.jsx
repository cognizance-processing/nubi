/**
 * EnvContext — the active project's environments + the globally selected env.
 *
 * Mirrors ProjectContext, one level deeper (org → project → environment):
 *   - Loads GET /projects/{id}/environments for the active project (the
 *     backend lazily seeds dev + prod) via lib/versions.js listEnvironments.
 *   - Re-loads whenever the active PROJECT changes (reads useProject()).
 *   - Persists the active env key in localStorage, keyed per project, so each
 *     project remembers its own last-selected environment.
 *   - Defaults to the project's is_default environment (prod).
 *
 * The selected env is global app state: the sidebar env selector and the
 * Flows toolbar EnvSelector both read/write it here, so they stay in sync.
 * Resolution endpoints (GET /boards|queries|flows/{id}?env=, POST
 * /flows/{id}/run {env}) consume the active key.
 *
 * Environment shape: { id, project_id, key, name, is_default, protected,
 * position, created_at, git_branch, last_synced_sha }
 *
 * Exposes (useEnv()):
 *   environments   {Array<Environment>|null} — null until loaded / when the
 *                  API is unavailable (callers may fall back, e.g. FlowsPage's
 *                  EnvSelector keeps its legacy localStorage custom-env list)
 *   activeEnv      {string} — the selected env key (default 'prod')
 *   setActiveEnv(key) — switch active env, persists per-project
 *   refresh()      — re-fetch the env list for the active project
 *   addEnv(key, opts) — create an environment, refresh (throws on failure);
 *                  opts: { git_branch?, from_branch? } (git workspace seeding)
 *   removeEnv(env) — delete an environment ({id,key}), refresh; resets the
 *                  selection to the default env when the active one was
 *                  removed (throws on failure, e.g. 409 default/protected)
 *   loading        {boolean}
 */

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
} from 'react'
import {
  listEnvironments,
  createEnvironment,
  deleteEnvironment,
} from '../lib/versions.js'
import { useProject } from './ProjectContext.jsx'
import {
  envDotClass as envDotClassImpl,
  defaultEnvKey,
  resolveActiveEnv,
} from '../shell/shellLogic.js'

// ---------------------------------------------------------------------------
// Shared UI helper — per-env accent dot
// ---------------------------------------------------------------------------

/** prod = emerald (live), dev = sky, anything else (custom) = violet. */
export const envDotClass = envDotClassImpl

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const EnvContext = createContext(null)

/** localStorage key for a given project's active env key. */
function activeEnvStorageKey(projectId) {
  return `nubi.activeEnv.${projectId ?? 'default'}`
}

function getSavedEnv(projectId) {
  try {
    return localStorage.getItem(activeEnvStorageKey(projectId)) || null
  } catch {
    return null
  }
}

function saveEnv(projectId, key) {
  try {
    localStorage.setItem(activeEnvStorageKey(projectId), key)
  } catch {
    // Ignore (private mode etc.)
  }
}

export function EnvProvider({ children }) {
  const { activeProject } = useProject()
  const projectId = activeProject?.id ?? null

  // null until loaded / when the API is unavailable (read helpers degrade).
  const [environments, setEnvironments] = useState(null)
  const [activeEnv, setActiveEnvState] = useState('prod')
  const [loading, setLoading] = useState(true)

  // Load (and reload) environments whenever the active project changes.
  useEffect(() => {
    let cancelled = false

    async function fetchEnvs() {
      setLoading(true)
      if (!projectId) {
        setEnvironments(null)
        setActiveEnvState('prod')
        setLoading(false)
        return
      }
      // listEnvironments() degrades to null on failure.
      const list = await listEnvironments(projectId)
      if (cancelled) return

      setEnvironments(list)

      // Restore the saved selection for this project; default to the
      // project's is_default env (prod). When the API list is unavailable we
      // trust the saved key so offline selection survives reloads.
      const saved = getSavedEnv(projectId)
      setActiveEnvState(resolveActiveEnv(saved, list))
      setLoading(false)
    }

    fetchEnvs()
    return () => { cancelled = true }
  }, [projectId])

  const setActiveEnv = useCallback(
    (key) => {
      if (!key || typeof key !== 'string') return
      setActiveEnvState(key)
      if (projectId) saveEnv(projectId, key)
    },
    [projectId],
  )

  const refresh = useCallback(async () => {
    if (!projectId) return null
    const list = await listEnvironments(projectId)
    setEnvironments(list)
    return list
  }, [projectId])

  const addEnv = useCallback(
    async (key, opts = {}) => {
      if (!projectId) throw new Error('No active project.')
      // opts: { git_branch?, from_branch? } — from_branch seeds the new env
      // from an existing branch in the project's git workspace repo (the
      // response may carry `imported` counts or a `warning`, see lib docs).
      const created = await createEnvironment(projectId, {
        key,
        name: key,
        git_branch: opts.git_branch,
        from_branch: opts.from_branch,
      })
      await refresh()
      return created
    },
    [projectId, refresh],
  )

  const removeEnv = useCallback(
    async (env) => {
      // Throws on failure (e.g. 409 deleting the default / a protected env)
      // so callers can surface the backend's message.
      await deleteEnvironment(env.id)
      const list = await refresh()
      // Removed the active env → fall back to the project default.
      if (env.key === activeEnv) setActiveEnv(defaultEnvKey(list))
    },
    [refresh, activeEnv, setActiveEnv],
  )

  return (
    <EnvContext.Provider
      value={{ environments, activeEnv, setActiveEnv, refresh, addEnv, removeEnv, loading }}
    >
      {children}
    </EnvContext.Provider>
  )
}

/**
 * @returns {{
 *   environments: Array<{id:string,key:string,name:string,is_default:boolean,protected:boolean,position:number}>|null,
 *   activeEnv: string,
 *   setActiveEnv: (key: string) => void,
 *   refresh: () => Promise<Array|null>,
 *   addEnv: (key: string) => Promise<Object>,
 *   removeEnv: (env: {id:string,key:string}) => Promise<void>,
 *   loading: boolean,
 * }}
 */
export function useEnv() {
  const ctx = useContext(EnvContext)
  if (!ctx) throw new Error('useEnv must be used inside <EnvProvider>')
  return ctx
}
