/**
 * InviteAcceptPage — accept an org invitation via its token (/invite/:token).
 *
 * Rendered inside the authenticated app shell, so the user is guaranteed logged
 * in (ProtectedRoute redirects to /login?from=/invite/:token otherwise, and
 * Login returns here after auth). Previews the org + role, then on accept joins
 * the org and switches to it (persist active-org id + reload /home so the org
 * list refetches and selects the new org).
 */

import { useEffect, useState, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { Loader2, Building2, CheckCircle, AlertTriangle } from 'lucide-react'
import { getInvite, acceptInvite } from '../../lib/members.js'

const ACTIVE_ORG_KEY = 'nubi-active-org-id'

export default function InviteAcceptPage() {
  const { token } = useParams()
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [accepting, setAccepting] = useState(false)

  useEffect(() => {
    let cancelled = false
    getInvite(token)
      .then((p) => { if (!cancelled) setPreview(p) })
      .catch((e) => { if (!cancelled) setError(e?.message ?? 'This invite is invalid or has expired.') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [token])

  const accept = useCallback(async () => {
    setAccepting(true)
    setError(null)
    try {
      const org = await acceptInvite(token)
      try { localStorage.setItem(ACTIVE_ORG_KEY, org.id) } catch { /* ignore */ }
      // Hard navigation so OrgProvider refetches /orgs and selects the new org.
      window.location.assign('/home')
    } catch (e) {
      setError(e?.message ?? 'Could not accept this invite.')
      setAccepting(false)
    }
  }, [token])

  const pending = preview?.status === 'pending'

  return (
    <div className="flex items-center justify-center min-h-full p-6">
      <div className="w-full max-w-md rounded-2xl border border-border bg-surface shadow-sm p-8 text-center">
        <div
          className="mx-auto flex items-center justify-center w-14 h-14 rounded-2xl mb-4"
          style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
        >
          <Building2 size={26} className="text-white" />
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 text-sm text-muted py-6">
            <Loader2 size={16} className="animate-spin" /> Loading invitation…
          </div>
        ) : error && !preview ? (
          <>
            <h1 className="font-display font-semibold text-xl text-fg mb-1">Invitation unavailable</h1>
            <p className="text-sm text-muted mb-6">{error}</p>
            <Link to="/home" className="text-sm font-medium text-primary hover:underline">Go to home</Link>
          </>
        ) : !pending ? (
          <>
            <h1 className="font-display font-semibold text-xl text-fg mb-1">This invite is {preview?.status}</h1>
            <p className="text-sm text-muted mb-6">It can no longer be used. Ask an org admin for a new invite.</p>
            <Link to="/home" className="text-sm font-medium text-primary hover:underline">Go to home</Link>
          </>
        ) : (
          <>
            <h1 className="font-display font-semibold text-xl text-fg mb-1">Join {preview.org_name}</h1>
            <p className="text-sm text-muted mb-6">
              You&apos;ve been invited to join <span className="font-medium text-fg">{preview.org_name}</span> as{' '}
              <span className="font-medium text-fg">{preview.role}</span>.
            </p>
            {error && (
              <p className="flex items-center justify-center gap-1.5 text-xs text-red-600 dark:text-red-400 mb-4">
                <AlertTriangle size={13} /> {error}
              </p>
            )}
            <button
              type="button"
              onClick={accept}
              disabled={accepting}
              className="inline-flex items-center justify-center gap-2 w-full px-4 py-2.5 rounded-xl text-sm font-medium text-white disabled:opacity-50"
              style={{ background: 'linear-gradient(135deg, #2456a6, #17b3a3)' }}
            >
              {accepting ? <Loader2 size={15} className="animate-spin" /> : <CheckCircle size={15} />}
              Accept invitation
            </button>
            <Link to="/home" className="block mt-3 text-xs text-muted hover:text-fg">Not now</Link>
          </>
        )}
      </div>
    </div>
  )
}
