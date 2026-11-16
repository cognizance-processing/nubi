/**
 * authSession — a per-browser hint about whether a session is worth restoring.
 *
 * The refresh cookie is HttpOnly, so the app cannot see it. Probing for it with
 * POST /auth/refresh when nobody is signed in costs a 401 on every page view,
 * which devtools prints as a red error even though it is the expected answer.
 * So we remember the last definitive answer in localStorage:
 *
 *   '1'    a session existed (login, register or a successful restore)
 *   '0'    the server said there is none (a definitive 401/403)
 *   unset  never asked in this browser, so ask once
 *
 * The hint is only ever a reason to SKIP a probe, never proof of identity.
 * The server stays the authority. Storage can throw (private windows, blocked
 * site data), in which case every read answers "unset" and the app simply probes.
 */

const KEY = 'nubi_session'

export type SessionHint = '1' | '0' | null

export function readSessionHint(): SessionHint {
  try {
    const v = window.localStorage.getItem(KEY)
    return v === '1' || v === '0' ? v : null
  } catch {
    return null
  }
}

export function writeSessionHint(value: '1' | '0'): void {
  try {
    window.localStorage.setItem(KEY, value)
  } catch {
    /* storage unavailable: the app just probes next time */
  }
}

/**
 * Should the app ask the server whether a session exists?
 *
 * Skips only on a recorded "no session", and never when the page was reached
 * from another site or with an auth error attached. That is how a full-page
 * sign-in (Google) lands back on the app, and a stale '0' must not hide it.
 */
export function shouldProbeSession(): boolean {
  if (readSessionHint() !== '0') return true
  try {
    if (new URLSearchParams(window.location.search).has('auth_error')) return true
    const ref = document.referrer
    if (ref && new URL(ref).origin !== window.location.origin) return true
  } catch {
    return true
  }
  return false
}

/** Window event fired after a session is established (login, register or restore). */
export const SESSION_EVENT = 'nubi:session'

/**
 * Tell code outside React (feature flags, EE loaders) that an access token now exists, so
 * authenticated bootstrap requests can wait for it instead of firing anonymously and 401-ing.
 */
export function announceSession(): void {
  try { window.dispatchEvent(new Event(SESSION_EVENT)) } catch { /* non-browser */ }
}
