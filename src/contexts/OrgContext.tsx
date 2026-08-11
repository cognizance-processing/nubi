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
  type ReactNode,
} from 'react'
import * as api from '../lib/api.js'

export interface Org {
  id: string
  name: string
  role: string
  [key: string]: any
}

export interface OrgContextValue {
  orgs: Org[]
  activeOrg: Org | null
  setActiveOrg: (id: string) => void
  createOrg: (name: string) => Promise<Org>
  loading: boolean
  hasNoOrgs: boolean
}

// ---------------------------------------------------------------------------
// Module-level active org ref — readable by other modules without React
// ---------------------------------------------------------------------------

export let currentActiveOrg: Org | null = null

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const OrgContext = createContext<OrgContextValue | null>(null)

const ACTIVE_ORG_KEY = 'nubi-active-org-id'

const DEFAULT_ORG: Org = { id: 'personal', name: 'Personal', role: 'owner' }

function getSavedOrgId(): string | null {
  try {
    return localStorage.getItem(ACTIVE_ORG_KEY) ?? null
  } catch {
    return null
  }
}

function saveOrgId(id: string) {
  try {
    localStorage.setItem(ACTIVE_ORG_KEY, id)
  } catch {
    // Ignore
  }
}

/**
 * Update both the module-level ref AND the api client's active org id.
 * Called whenever the active org changes.
 */
function _applyActiveOrg(org: Org | null) {
  currentActiveOrg = org
  api.setActiveOrgId(org ? org.id : null)
}

export function OrgProvider({ children }: { children: ReactNode }) {
  const [orgs, setOrgs] = useState<Org[]>([])
  const [activeOrg, setActiveOrgState] = useState<Org | null>(null)
  const [loading, setLoading] = useState(true)
  // True when GET /orgs SUCCEEDED but the user belongs to zero orgs
  // (e.g. a brand-new Google OAuth user). The shell guard redirects such
  // users to /onboarding. Transport errors do NOT set this — they fall back
  // to DEFAULT_ORG so offline/dev still works.
  const [hasNoOrgs, setHasNoOrgs] = useState(false)

  useEffect(() => {
    let cancelled = false

    async function fetchOrgs() {
      try {
        const data = await api.get('/orgs')
        const list: Org[] = Array.isArray(data?.orgs)
          ? data.orgs
          : Array.isArray(data)
          ? data
          : []

        if (cancelled) return

        if (list.length === 0) {
          // Successful response, zero memberships → forced onboarding.
          setOrgs([])
          setActiveOrgState(null)
          _applyActiveOrg(null)
          setHasNoOrgs(true)
          return
        }

        setOrgs(list)
        setHasNoOrgs(false)

        // Restore saved selection, defaulting to first org
        const savedId = getSavedOrgId()
        const saved = list.find(o => o.id === savedId) ?? list[0]
        setActiveOrgState(saved)
        _applyActiveOrg(saved)
      } catch {
        // API unavailable or 404 — degrade gracefully
        if (!cancelled) {
          setOrgs([DEFAULT_ORG])
          setActiveOrgState(DEFAULT_ORG)
          _applyActiveOrg(DEFAULT_ORG)
          setHasNoOrgs(false)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchOrgs()
    return () => { cancelled = true }
  }, [])

  const setActiveOrg = useCallback(
    (id: string) => {
      const org = orgs.find(o => o.id === id)
      if (!org) return
      setActiveOrgState(org)
      _applyActiveOrg(org)
      saveOrgId(id)
    },
    [orgs],
  )

  /**
   * Create a new org (current user becomes owner) and switch to it.
   * POST /orgs {name} → {id, name, role}
   */
  const createOrg = useCallback(async (name: string) => {
    const org = await api.post('/orgs', { name })
    setOrgs(prev => [...prev, org])
    setActiveOrgState(org)
    _applyActiveOrg(org)
    saveOrgId(org.id)
    setHasNoOrgs(false)
    return org
  }, [])

  return (
    <OrgContext.Provider value={{ orgs, activeOrg, setActiveOrg, createOrg, loading, hasNoOrgs }}>
      {children}
    </OrgContext.Provider>
  )
}

export function useOrg(): OrgContextValue {
  const ctx = useContext(OrgContext)
  if (!ctx) throw new Error('useOrg must be used inside <OrgProvider>')
  return ctx
}

/**
 * Whether the current user can write (create/edit/delete/run) in the active org.
 * `viewer` is read-only; every other role (owner/admin/member) can write.
 * Used to hide/disable mutating actions in the UI — the backend enforces the
 * same rule authoritatively (see app/auth/roles.py).
 */
export function useCanWrite(): boolean {
  const { activeOrg } = useOrg()
  return activeOrg?.role !== 'viewer'
}
