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

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import * as api from '../lib/api.js'

export interface AuthUser {
  id: string
  email: string
  name?: string
  [key: string]: any
}

export interface AuthContextValue {
  user: AuthUser | null
  loading: boolean
  login: (credentials: { email: string; password: string }) => Promise<void>
  register: (fields: {
    email: string
    password: string
    name: string
    org_name?: string
    project_name?: string
    demo_project?: boolean
  }) => Promise<void>
  logout: () => Promise<void>
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const AuthContext = createContext<AuthContextValue | null>(null)

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)

  // -- Session restore on mount ---------------------------------------------
  useEffect(() => {
    let cancelled = false

    async function attemptRestore() {
      // Exchange the HttpOnly refresh cookie for a new access token
      const refreshData = await api.refresh()
      api.setAccessToken(refreshData.access_token)

      // Fetch the user profile with the new token
      return api.me()
    }

    async function restoreSession() {
      try {
        let meData
        try {
          meData = await attemptRestore()
        } catch (err: any) {
          // A 429 is a transient rate limit, not proof the session is
          // invalid — retry once after a short backoff instead of
          // force-logging the user out (this used to fire on every ordinary
          // page reload once the auth bucket was exhausted).
          if (err?.status === 429) {
            await new Promise((resolve) => setTimeout(resolve, 1500))
            meData = await attemptRestore()
          } else {
            throw err
          }
        }
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

  /** Log in with email + password. */
  async function login({ email, password }: { email: string; password: string }) {
    const data = await api.login({ email, password })
    api.setAccessToken(data.access_token)
    setUser(data.user)
  }

  /**
   * Register a new account.
   * Optional workspace fields (org_name, project_name, demo_project) are
   * passed straight through to POST /auth/register so the backend creates
   * the user's first org/project (and, when demo_project is set, seeds the
   * demo bundle INTO that single project) atomically.
   */
  async function register({ email, password, name, org_name, project_name, demo_project }: {
    email: string
    password: string
    name: string
    org_name?: string
    project_name?: string
    demo_project?: boolean
  }) {
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

/** Access auth state and actions from any component inside <AuthProvider>. */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used inside <AuthProvider>')
  }
  return ctx
}
