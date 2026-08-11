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
import { Loader2, Building2, CheckCircle, AlertTriangle, XCircle } from 'lucide-react'
import { getInvite, acceptInvite } from '../../lib/members.js'
import Button from '../../components/ui/Button.jsx'
import Badge from '../../components/ui/Badge.jsx'

const ACTIVE_ORG_KEY = 'nubi-active-org-id'

export default function InviteAcceptPage() {
  const { token } = useParams()
  const [preview, setPreview] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [accepting, setAccepting] = useState(false)

  const fetchInvite = useCallback(() => {
    let cancelled = false
    getInvite(token)
      .then((p) => { if (!cancelled) setPreview(p) })
      .catch((e) => { if (!cancelled) setError(e?.message ?? 'This invite is invalid or has expired.') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [token])

  useEffect(() => fetchInvite(), [fetchInvite])

  const retryInvite = useCallback(() => {
    setLoading(true)
    setError(null)
    fetchInvite()
  }, [fetchInvite])

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
      <div className="w-full max-w-md rounded-2xl border border-border bg-surface shadow-nubi-xl p-8 text-center nubi-animate-slide-up">
        <div
          className="mx-auto flex items-center justify-center w-14 h-14 rounded-2xl mb-5 shadow-nubi-md"
          style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
        >
          <Building2 size={26} className="text-white" aria-hidden="true" />
        </div>

        {loading ? (
          <div className="flex flex-col items-center gap-3 py-6">
            <div className="flex items-center gap-2 text-sm text-muted">
              <Loader2 size={16} className="animate-spin" aria-hidden="true" /> Loading invitation…
            </div>
            <div className="w-full space-y-2 mt-2" aria-hidden="true">
              <div className="h-3.5 w-3/4 mx-auto rounded nubi-shimmer" />
              <div className="h-3.5 w-1/2 mx-auto rounded nubi-shimmer" />
            </div>
          </div>
        ) : error && !preview ? (
          <>
            <div className="mx-auto flex items-center justify-center w-11 h-11 rounded-xl bg-danger-bg mb-4">
              <XCircle size={20} className="text-danger" aria-hidden="true" />
            </div>
            <h1 className="font-display font-semibold text-xl text-fg mb-1.5">Invitation unavailable</h1>
            <p className="text-sm text-muted mb-6 leading-relaxed">
              {error || 'This invite is invalid or has expired.'}
            </p>
            <div className="flex items-center justify-center gap-3">
              <Button variant="outline" size="sm" onClick={retryInvite}>Try again</Button>
              <Link to="/home" className="nubi-btn nubi-btn-sm nubi-btn-ghost">Go to home</Link>
            </div>
          </>
        ) : !pending ? (
          <>
            <div className="mx-auto flex items-center justify-center w-11 h-11 rounded-xl bg-surface-2 border border-border mb-4">
              <AlertTriangle size={20} className="text-muted" aria-hidden="true" />
            </div>
            <h1 className="font-display font-semibold text-xl text-fg mb-2 flex items-center justify-center gap-2 flex-wrap">
              This invite is
              <Badge variant={preview?.status === 'accepted' ? 'success' : 'default'} size="md">
                {preview?.status}
              </Badge>
            </h1>
            <p className="text-sm text-muted mb-6 leading-relaxed">
              It can no longer be used. Ask an org admin for a new invite.
            </p>
            <Link to="/home" className="nubi-btn nubi-btn-sm nubi-btn-secondary">Go to home</Link>
          </>
        ) : (
          <>
            <h1 className="font-display font-semibold text-xl text-fg mb-1.5">Join {preview.org_name}</h1>
            <p className="text-sm text-muted mb-6 leading-relaxed">
              You&apos;ve been invited to join <span className="font-medium text-fg">{preview.org_name}</span> as{' '}
              <Badge variant="primary" size="sm">{preview.role}</Badge>.
            </p>
            {error && (
              <p
                role="alert"
                className="flex items-center justify-center gap-1.5 text-xs text-danger mb-4 bg-danger-bg rounded-lg py-2 px-3"
              >
                <AlertTriangle size={13} className="shrink-0" aria-hidden="true" /> {error}
              </p>
            )}
            <button
              type="button"
              onClick={accept}
              disabled={accepting}
              aria-busy={accepting}
              className="inline-flex items-center justify-center gap-2 w-full h-11 px-4 rounded-xl text-sm font-semibold text-white disabled:opacity-50 transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              style={{ background: 'linear-gradient(135deg, #2456a6, #17b3a3)' }}
            >
              {accepting ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <CheckCircle size={15} aria-hidden="true" />}
              {accepting ? 'Joining…' : 'Accept invitation'}
            </button>
            <Link
              to="/home"
              className="inline-block mt-4 text-xs text-muted hover:text-fg transition-colors nubi-focus-ring rounded"
            >
              Not now
            </Link>
          </>
        )}
      </div>
    </div>
  )
}
