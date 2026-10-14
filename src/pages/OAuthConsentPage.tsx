/**
 * OAuthConsentPage — /oauth/consent?request=…
 *
 * Where an AI app (claude.ai, ChatGPT, …) sends a user to approve a connection
 * to Nubi. The backend's /oauth/authorize validated the request and parked it;
 * this page shows who is asking, lets the user pick which of their orgs to
 * connect, and on approval hands the browser back to the app with a one-time
 * code (backend/app/routes/oauth.py).
 *
 * Dynamic client registration lets any app call itself anything, so the page
 * leads with where the user will be sent (the redirect host), and only names a
 * client as verified when that host is one we recognise.
 */

import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { AlertCircle, ShieldCheck, ShieldAlert, Check, Monitor } from 'lucide-react'
import AuthLayout from '../components/AuthLayout.jsx'
import { useAuth } from '../contexts/AuthContext.jsx'
import { decideConsentRequest, getConsentRequest, type ConsentDetails } from '../lib/oauthConnect.js'

const CAN_DO = [
  'See your connectors, schema, saved queries and dashboards',
  'Run queries with your access — row-level security still applies',
  'Create and edit dashboards and queries as you',
]

export default function OAuthConsentPage() {
  const [params] = useSearchParams()
  const requestId = params.get('request') ?? ''
  const { user } = useAuth()

  const [details, setDetails] = useState<ConsentDetails | null>(null)
  const [orgId, setOrgId] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  useEffect(() => {
    let alive = true
    if (!requestId) {
      setError('This link is missing its connection request. Start again from your AI app.')
      return
    }
    getConsentRequest(requestId)
      .then((d) => {
        if (!alive) return
        setDetails(d)
        setOrgId(d.orgs[0]?.id ?? '')
      })
      .catch((e) => alive && setError(e?.message ?? 'This connection request is no longer valid.'))
    return () => {
      alive = false
    }
  }, [requestId])

  async function decide(approve: boolean) {
    setPending(true)
    setError(null)
    try {
      const { redirect_to } = await decideConsentRequest(requestId, approve, approve ? orgId : undefined)
      window.location.assign(redirect_to)
    } catch (e) {
      setError((e as Error)?.message ?? 'Something went wrong.')
      setPending(false)
    }
  }

  // Never offer Approve inside a frame: a page that framed this one could
  // trick the click (clickjacking). Hosting also sends frame-ancestors 'none'
  // for /oauth/*; this covers hosts that don't.
  const framed = typeof window !== 'undefined' && window.top !== window.self

  const appName = details?.verified_as ?? details?.client_name ?? 'An app'

  return (
    <AuthLayout
      title={details ? `Connect ${appName} to Nubi` : 'Connect an app to Nubi'}
      subtitle={user?.email ? `Signed in as ${user.email}` : undefined}
      artTagline="Ask your data anything"
      footer={
        <>
          You can disconnect at any time in{' '}
          <Link to="/settings/connections" className="font-semibold text-primary hover:opacity-80">
            Settings → Connections
          </Link>
        </>
      }
    >
      {error && (
        <div role="alert" aria-live="assertive" className="nubi-auth-error">
          <AlertCircle size={15} className="shrink-0 mt-0.5" aria-hidden="true" />
          {error}
        </div>
      )}

      {framed && (
        <div role="alert" className="nubi-auth-error">
          <AlertCircle size={15} className="shrink-0 mt-0.5" aria-hidden="true" />
          For your security, open this page in its own browser tab to approve the connection.
        </div>
      )}

      {details && !framed && (
        <div className="flex flex-col gap-4">
          {details.verified_as ? (
            <div className="flex items-start gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2.5 text-xs text-fg">
              <ShieldCheck size={15} className="shrink-0 mt-0.5 text-emerald-600" aria-hidden="true" />
              <span>
                <strong>{details.verified_as}</strong> is asking to act on your behalf. You&apos;ll be sent
                back to <strong>{details.redirect_host}</strong>.
              </span>
            </div>
          ) : details.local_app ? (
            <div className="flex items-start gap-2 rounded-lg border border-border bg-surface-2 px-3 py-2.5 text-xs text-fg">
              <Monitor size={15} className="shrink-0 mt-0.5 text-primary" aria-hidden="true" />
              <span>
                <strong>“{details.client_name}”</strong> is an app on this computer, such as Claude Code.
                Approve only if you just started a sign-in from it.
              </span>
            </div>
          ) : (
            <div className="flex items-start gap-2 rounded-lg border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 px-3 py-2.5 text-xs text-amber-800 dark:text-amber-300">
              <ShieldAlert size={15} className="shrink-0 mt-0.5" aria-hidden="true" />
              <span>
                Nubi can&apos;t verify this app. It calls itself <strong>“{details.client_name}”</strong> and
                you&apos;ll be sent to <strong>{details.redirect_host}</strong>. Only continue if you started
                this from an app you trust.
              </span>
            </div>
          )}

          <div>
            <p className="nubi-label">It will be able to</p>
            <ul className="flex flex-col gap-1.5 text-sm text-fg">
              {CAN_DO.map((line) => (
                <li key={line} className="flex items-start gap-2">
                  <Check size={14} className="shrink-0 mt-1 text-primary" aria-hidden="true" />
                  {line}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-muted">
              Only in the organisation you choose, and never more than you can do yourself.
            </p>
          </div>

          {details.orgs.length === 0 ? (
            <p className="text-sm text-muted">You don&apos;t belong to any organisation yet.</p>
          ) : (
            <div className="nubi-field">
              <label htmlFor="consent-org" className="nubi-label">Organisation</label>
              <select
                id="consent-org"
                value={orgId}
                onChange={(e) => setOrgId(e.target.value)}
                className="nubi-input nubi-input-lg"
                disabled={pending}
              >
                {details.orgs.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name} ({o.role})
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => decide(false)}
              disabled={pending}
              className="nubi-btn nubi-btn-secondary flex-1 justify-center"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => decide(true)}
              disabled={pending || !orgId}
              className="nubi-btn nubi-btn-primary flex-1 justify-center"
            >
              {pending ? 'Connecting…' : 'Approve'}
            </button>
          </div>
        </div>
      )}
    </AuthLayout>
  )
}
