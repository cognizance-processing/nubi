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
import { Check } from 'lucide-react'
import { getInvite, acceptInvite } from '../../lib/members.js'
import Button from '../../components/ui/Button.jsx'
import Tag from '../../components/ui/Tag.jsx'
import Skeleton from '../../components/ui/Skeleton.jsx'

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
    <div className="flex items-start justify-center min-h-full px-4 py-12 sm:py-20">
      <section
        aria-labelledby="invite-title"
        className="w-full max-w-md bg-surface border border-border border-t-2 border-t-ink rounded-[var(--radius-sheet)] p-6 sm:p-8"
      >
        <p className="font-mono text-[11px] font-medium uppercase tracking-[0.06em] text-subtle mb-3">Invitation</p>

        {loading ? (
          <div role="status" aria-label="Loading invitation" className="flex flex-col gap-3">
            <Skeleton className="h-7 w-3/4" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-10 w-full mt-4" />
          </div>
        ) : error && !preview ? (
          <>
            <h1 id="invite-title" className="text-2xl font-semibold tracking-[-0.015em] text-fg mb-2">Invitation unavailable</h1>
            <p role="alert" className="text-sm text-muted mb-6 leading-relaxed border-l-2 border-danger pl-3">
              {error || 'This invite is invalid or has expired.'} Ask an org admin to send a new one.
            </p>
            <div className="flex items-center gap-2">
              <Button variant="secondary" size="sm" onClick={retryInvite}>Try again</Button>
              <Link to="/home" className="nubi-btn nubi-btn-sm nubi-btn-ghost">Go to Home</Link>
            </div>
          </>
        ) : !pending ? (
          <>
            <h1 id="invite-title" className="text-2xl font-semibold tracking-[-0.015em] text-fg mb-2 flex items-center gap-3 flex-wrap">
              This invite is
              <Tag variant={preview?.status === 'accepted' ? 'success' : 'neutral'} dot size="md">
                {preview?.status}
              </Tag>
            </h1>
            <p className="text-sm text-muted mb-6 leading-relaxed">
              It can no longer be used. Ask an org admin for a new invite.
            </p>
            <Link to="/home" className="nubi-btn nubi-btn-sm nubi-btn-secondary">Go to Home</Link>
          </>
        ) : (
          <>
            <h1 id="invite-title" className="text-2xl font-semibold tracking-[-0.015em] text-fg mb-2">Join {preview.org_name}</h1>
            <p className="text-sm text-muted mb-6 leading-relaxed">
              You have been invited to join <span className="font-medium text-fg">{preview.org_name}</span> with the role{' '}
              <Tag variant="accent" caps={false}>{preview.role}</Tag>.
            </p>
            {error && (
              <p role="alert" className="text-sm text-fg mb-4 border-l-2 border-danger bg-danger-bg py-2 px-3">
                {error}
              </p>
            )}
            <Button className="w-full" size="lg" onClick={accept} loading={accepting} aria-busy={accepting}>
              {!accepting && <Check size={15} aria-hidden="true" />}
              {accepting ? 'Joining…' : 'Accept invitation'}
            </Button>
            <Link
              to="/home"
              className="inline-block mt-4 text-sm text-muted hover:text-fg underline-offset-2 hover:underline nubi-focus-ring rounded-[2px]"
            >
              Not now
            </Link>
          </>
        )}
      </section>
    </div>
  )
}
