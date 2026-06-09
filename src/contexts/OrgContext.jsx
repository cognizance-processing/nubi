/**
 * OrgContext — manages the authenticated user's organisations.
 *
 * Fetches GET /api/v1/orgs on mount (requires a valid access token in the
 * api client). Shape: { orgs: [{ id, name, role }] }
 *
 * Tolerates 404 / network errors gracefully — falls back to a single
 * default "Personal" org so the rest of the shell always has something to show.
 *
 * Exposes:
 *   orgs        {Array<{id, name, role}>}
 *   activeOrg   {Object|null}
 *   setActiveOrg(id) — switches active org, persists to localStorage
 *   loading     {boolean}
 *
 * The active org id is persisted under 'nubi-active-org-id'.
 *
 * When activeOrg changes we:
 *   1. Store it on the module-level ``currentActiveOrg`` export (readable by
 *      any module without a React dependency).
 *   2. Call ``setActiveOrgId`` from the api client so that subsequent fetch
 *      calls include the correct ``X-Org-Id`` header.
 */

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
} from 'react'
import * as api from '../lib/api.js'

// ---------------------------------------------------------------------------
// Module-level active org ref — readable by other modules without React
// ---------------------------------------------------------------------------

/** @type {{ id: string, name: string, role: string } | null} */
export let currentActiveOrg = null

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const OrgContext = createContext(null)

const ACTIVE_ORG_KEY = 'nubi-active-org-id'

const DEFAULT_ORG = { id: 'personal', name: 'Personal', role: 'owner' }

function getSavedOrgId() {
  try {
    return localStorage.getItem(ACTIVE_ORG_KEY) ?? null
  } catch {
    return null
  }
}

function saveOrgId(id) {
  try {
    localStorage.setItem(ACTIVE_ORG_KEY, id)
  } catch {
    // Ignore
  }
}

/**
 * Update both the module-level ref AND the api client's active org id.
 * Called whenever the active org changes.
 * @param {{ id: string, name: string, role: string } | null} org
 */
function _applyActiveOrg(org) {
  currentActiveOrg = org
  api.setActiveOrgId(org ? org.id : null)
}

export function OrgProvider({ children }) {
  const [orgs, setOrgs] = useState([])
  const [activeOrg, setActiveOrgState] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    async function fetchOrgs() {
      try {
        const data = await api.get('/orgs')
        const list = Array.isArray(data?.orgs)
          ? data.orgs
          : Array.isArray(data)
          ? data
          : []
        const resolved = list.length > 0 ? list : [DEFAULT_ORG]

        if (cancelled) return

        setOrgs(resolved)

        // Restore saved selection, defaulting to first org
        const savedId = getSavedOrgId()
        const saved = resolved.find(o => o.id === savedId) ?? resolved[0]
        setActiveOrgState(saved)
        _applyActiveOrg(saved)
      } catch {
        // API unavailable or 404 — degrade gracefully
        if (!cancelled) {
          setOrgs([DEFAULT_ORG])
          setActiveOrgState(DEFAULT_ORG)
          _applyActiveOrg(DEFAULT_ORG)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchOrgs()
    return () => { cancelled = true }
  }, [])

  const setActiveOrg = useCallback(
    (id) => {
      const org = orgs.find(o => o.id === id)
      if (!org) return
      setActiveOrgState(org)
      _applyActiveOrg(org)
      saveOrgId(id)
    },
    [orgs],
  )

  return (
    <OrgContext.Provider value={{ orgs, activeOrg, setActiveOrg, loading }}>
      {children}
    </OrgContext.Provider>
  )
}

/**
 * @returns {{ orgs: Array<{id:string,name:string,role:string}>, activeOrg: Object|null, setActiveOrg: Function, loading: boolean }}
 */
export function useOrg() {
  const ctx = useContext(OrgContext)
  if (!ctx) throw new Error('useOrg must be used inside <OrgProvider>')
  return ctx
}

/**
 * Whether the current user can write (create/edit/delete/run) in the active org.
 * `viewer` is read-only; every other role (owner/admin/member) can write.
 * Used to hide/disable mutating actions in the UI — the backend enforces the
 * same rule authoritatively (see app/auth/roles.py).
 *
 * @returns {boolean}
 */
export function useCanWrite() {
  const { activeOrg } = useOrg()
  return activeOrg?.role !== 'viewer'
}
