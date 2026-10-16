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
 * On mount: calls refresh() then me() to silently restore an existing session,
 * unless this browser already learned there is none (see authSession.ts), so
 * anonymous visitors do not log a 401 on every page. A definitive 401/403 means
 * "logged out"; a network failure or 5xx means "unknown" and is retried, and
 * never records a logged-out hint. The app never crashes.
 */

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import * as api from '../lib/api.js'
import { announceSession, shouldProbeSession, writeSessionHint } from './authSession.js'

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

    // Network failures and 5xx are "unknown", not "logged out": retry with a
    // short backoff. 429 is a transient rate limit and gets the same treatment.
    const RETRY_DELAYS = [1500, 3000]
    const isUnknown = (err: any) =>
      typeof err?.status !== 'number' || err.status === 429 || err.status >= 500

    async function restoreSession() {
      if (!shouldProbeSession()) {
        // Last answer from the server was "no session": stay silent.
        api.setAccessToken(null)
        setLoading(false)
        return
      }
      try {
        let meData
        for (let attempt = 0; ; attempt++) {
          try {
            meData = await attemptRestore()
            break
          } catch (err: any) {
            if (cancelled) return
            if (!isUnknown(err) || attempt >= RETRY_DELAYS.length) throw err
            await new Promise((resolve) => setTimeout(resolve, RETRY_DELAYS[attempt]))
          }
        }
        if (!cancelled) {
          writeSessionHint('1')
          setUser(meData.user)
          announceSession()
        }
      } catch (err: any) {
        // Definitive "no session" is remembered; "unknown" is not, so the next
        // load probes again instead of locking the user out.
        api.setAccessToken(null)
        if (!isUnknown(err)) writeSessionHint('0')
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
    writeSessionHint('1')
    setUser(data.user)
    announceSession()
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
    writeSessionHint('1')
    setUser(data.user)
    announceSession()
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
      writeSessionHint('0')
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
