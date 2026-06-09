/**
 * AuthContext — provides auth state and actions throughout the app.
 *
 * State:
 *   user    — the authenticated User object, or null
 *   loading — true while the initial session restore is in flight
 *
 * Actions:
 *   login({ email, password })         — POST /auth/login, stores access token
 *   register({ email, password, name }) — POST /auth/register, stores access token
 *   logout()                            — POST /auth/logout, clears token + user
 *
 * On mount: calls refresh() then me() to silently restore an existing session.
 * On failure the user is left logged-out; the app never crashes.
 */

import { createContext, useContext, useEffect, useState } from 'react'
import * as api from '../lib/api.js'

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const AuthContext = createContext(null)

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  // -- Session restore on mount ---------------------------------------------
  useEffect(() => {
    let cancelled = false

    async function restoreSession() {
      try {
        // Exchange the HttpOnly refresh cookie for a new access token
        const refreshData = await api.refresh()
        api.setAccessToken(refreshData.access_token)

        // Fetch the user profile with the new token
        const meData = await api.me()
        if (!cancelled) {
          setUser(meData.user)
        }
      } catch {
        // No valid session — stay logged out; never crash
        api.setAccessToken(null)
        if (!cancelled) {
          setUser(null)
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    restoreSession()
    return () => { cancelled = true }
  }, [])

  // -- Actions --------------------------------------------------------------

  /**
   * Log in with email + password.
   * @param {{ email: string, password: string }} credentials
   */
  async function login({ email, password }) {
    const data = await api.login({ email, password })
    api.setAccessToken(data.access_token)
    setUser(data.user)
  }

  /**
   * Register a new account.
   * Optional workspace fields (org_name, project_name, demo_project) are
   * passed straight through to POST /auth/register so the backend creates
   * the user's first org/project (and the seeded Demo project) atomically.
   * @param {{ email: string, password: string, name: string, org_name?: string, project_name?: string, demo_project?: boolean }} fields
   */
  async function register({ email, password, name, org_name, project_name, demo_project }) {
    const data = await api.register({ email, password, name, org_name, project_name, demo_project })
    api.setAccessToken(data.access_token)
    setUser(data.user)
  }

  /**
   * Log out — revokes the session family, clears token and user state.
   */
  async function logout() {
    try {
      await api.logout()
    } catch {
      // Best-effort: clear client state even if the server call fails
    } finally {
      api.setAccessToken(null)
      setUser(null)
    }
  }

  // -- Context value --------------------------------------------------------

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

/**
 * Access auth state and actions from any component inside <AuthProvider>.
 * @returns {{ user: import('../lib/api.js').User | null, loading: boolean, login: Function, register: Function, logout: Function }}
 */
export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used inside <AuthProvider>')
  }
  return ctx
}
