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
 * leads with where the user will be sent (the redirect host). Only an app whose
 * host we recognise is named as verified and shown with its real logo; every
 * other client is labelled unverified and gets no brand.
 */

import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Check } from 'lucide-react'
import AuthLayout from '../components/AuthLayout.jsx'
import { useAuth } from '../contexts/AuthContext.jsx'
import { decideConsentRequest, getConsentRequest, type ConsentDetails } from '../lib/oauthConnect.js'
import MarketingStyles from '../components/marketing/MarketingStyles.jsx'
import { Banner, Field, SubmitLabel } from '../components/marketing/AuthFields.jsx'
import { Tag } from '../components/marketing/kit.jsx'
import Brand from '../components/marketing/Brand.jsx'
import Figure from '../components/marketing/Figure.jsx'

// What a connected app can do, in plain language.
const CAN_DO = [
  'See your connectors, schema, saved queries and dashboards',
  'Run queries as you, with row-level security still applied',
  'Create and edit dashboards and queries as you',
]

// Only recognised apps get a mark. The name comes from the backend's host check.
const LOGO_FOR: Record<string, { slug: string; name: string }> = {
  Claude: { slug: 'claude', name: 'Claude' },
  ChatGPT: { slug: 'openai', name: 'ChatGPT' },
}

const CSS = `
.oc-id { display: flex; align-items: center; gap: 14px; padding: 14px 0; border-top: 1px solid var(--ink); border-bottom: 1px solid var(--border); }
.oc-tile { width: 48px; height: 48px; flex: none; display: inline-flex; align-items: center; justify-content: center; border: 1px solid var(--border); border-radius: 2px; background: var(--surface); color: var(--text); font: 600 18px var(--font-ui); }
.oc-id b { display: block; font-size: 16px; font-weight: 600; letter-spacing: -.01em; line-height: 1.25; }
.oc-id small { display: block; margin-top: 2px; font: 12px var(--font-mono); color: var(--text-muted); overflow-wrap: anywhere; }
.oc-scope { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
.oc-scope li { display: flex; gap: 10px; align-items: flex-start; font-size: 15px; line-height: 1.45; }
.oc-scope svg { flex: none; margin-top: 3px; }
.oc-note { border-left: 2px solid var(--warning); padding: 2px 0 2px 14px; font-size: 14px; line-height: 1.55; color: var(--text); }
.oc-act { display: grid; gap: 10px; grid-template-columns: 1fr 1fr; }
.oc-fig { display: block; }
@media (min-width: 1024px) and (max-height: 940px) { .oc-fig { display: none; } }
.oc-foot { font-size: 13px; line-height: 1.5; color: var(--text-muted); }
`

export default function OAuthConsentPage() {
  const [params] = useSearchParams()
  const requestId = params.get('request') ?? ''
  const { user } = useAuth()

  const [details, setDetails] = useState<ConsentDetails | null>(null)
  const [orgId, setOrgId] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState<'allow' | 'deny' | null>(null)

  useEffect(() => {
    let alive = true
    if (!requestId) {
      setError('This link is missing its connection request. Start again from your AI app.')
      return
    }
    getConsentRequest(requestId)
      .then(d => {
        if (!alive) return
        setDetails(d)
        setOrgId(d.orgs[0]?.id ?? '')
      })
      .catch(e => alive && setError(e?.message ?? 'This connection request is no longer valid.'))
    return () => { alive = false }
  }, [requestId])

  async function decide(approve: boolean) {
    setPending(approve ? 'allow' : 'deny')
    setError(null)
    try {
      const { redirect_to } = await decideConsentRequest(requestId, approve, approve ? orgId : undefined)
      window.location.assign(redirect_to)
    } catch (e) {
      setError((e as Error)?.message ?? 'Something went wrong. Try again.')
      setPending(null)
    }
  }

  // Never offer Allow inside a frame: a page that framed this one could trick
  // the click (clickjacking). Hosting also sends frame-ancestors 'none' for
  // /oauth/*; this covers hosts that don't.
  const framed = typeof window !== 'undefined' && window.top !== window.self

  const appName = details?.verified_as ?? details?.client_name ?? 'An app'
  const mark = details?.verified_as ? LOGO_FOR[details.verified_as] : undefined
  const busy = pending !== null

  return (
    <AuthLayout
      title={details ? `Connect ${appName} to Nubi` : 'Connect an app to Nubi'}
      subtitle={user?.email ? `Signed in as ${user.email}` : undefined}
      eyebrow="CONNECT AN APP"
      artTagline="Ask your data anything."
      footer={
        <p className="oc-foot">
          Revoke access any time in{' '}
          <Link to="/settings/connections" className="mk-link">Settings › Connections</Link>.
        </p>
      }
    >
      <MarketingStyles />
      <style href="nubi-consent" precedence="default">{CSS}</style>

      <div className="mk-form">
        {error && <Banner>{error}</Banner>}
        {framed && <Banner>For your security, open this page in its own browser tab to approve the connection.</Banner>}

        {details && !framed && (
          <>
            <div className="oc-id">
              <span className="oc-tile" aria-hidden={mark ? undefined : 'true'}>
                {mark
                  ? <Brand slug={mark.slug} name={mark.name} mode="mono" size={26} decorative={false} />
                  : (details.client_name || '?').trim().charAt(0).toUpperCase()}
              </span>
              <div style={{ minWidth: 0, flex: 1 }}>
                <b>{appName}</b>
                <small>{details.local_app ? 'App on this computer' : `Sends you back to ${details.redirect_host}`}</small>
              </div>
              {details.verified_as
                ? <Tag tone="success" dot>Verified</Tag>
                : details.local_app
                  ? <Tag>Local app</Tag>
                  : <Tag tone="warning" dot>Unverified</Tag>}
            </div>

            {details.verified_as ? null : details.local_app ? (
              <p className="oc-note">
                <b>&ldquo;{details.client_name}&rdquo;</b> runs on this computer, like Claude Code. Allow it only if you just started a sign-in from it.
              </p>
            ) : (
              <p className="oc-note">
                Nubi can&rsquo;t verify this app. It calls itself <b>&ldquo;{details.client_name}&rdquo;</b> and will send you to <b>{details.redirect_host}</b>.
                Continue only if you started this from an app you trust.
              </p>
            )}

            <div className="oc-fig">
              <Figure name="OAuthConsentFlow" compact title={`${appName} asks Nubi for access to your data, through a scopes gate you control`} className="w-full h-auto" />
            </div>

            <div>
              <p className="mk-label" style={{ marginBottom: 10 }}>{appName} will be able to</p>
              <ul className="oc-scope">
                {CAN_DO.map(line => (
                  <li key={line}><Check size={15} strokeWidth={2} aria-hidden="true" /><span>{line}</span></li>
                ))}
              </ul>
              <p className="mk-small" style={{ marginTop: 12 }}>Only in the organization you choose, and never more than you can do yourself.</p>
            </div>

            {details.orgs.length === 0 ? (
              <Banner>You don&rsquo;t belong to any organization yet. Create one in Nubi, then try again.</Banner>
            ) : (
              <Field id="consent-org" label="Organization">
                <select id="consent-org" value={orgId} onChange={e => setOrgId(e.target.value)} className="mk-input" disabled={busy}>
                  {details.orgs.map(o => <option key={o.id} value={o.id}>{o.name} ({o.role})</option>)}
                </select>
              </Field>
            )}

            <div className="oc-act">
              <button type="button" onClick={() => decide(false)} disabled={busy} className="nubi-btn nubi-btn-ghost nubi-btn-xl" style={{ border: '1px solid var(--frame-line)' }}>
                <SubmitLabel pending={pending === 'deny'} idle="Deny" busy="Denying…" />
              </button>
              <button type="button" onClick={() => decide(true)} disabled={busy || !orgId} className="nubi-btn nubi-btn-primary nubi-btn-xl" aria-busy={pending === 'allow'}>
                <SubmitLabel pending={pending === 'allow'} idle="Allow" busy="Connecting…" />
              </button>
            </div>
          </>
        )}
      </div>
    </AuthLayout>
  )
}
