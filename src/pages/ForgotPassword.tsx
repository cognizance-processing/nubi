/**
 * ForgotPassword — ask for a password-reset link by email.
 *
 * The server answers the same way whether or not the address has an account,
 * so the confirmation does too.
 */

import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { forgotPassword, fetchAuthConfig } from '../lib/api.js'
import AuthLayout from '../components/AuthLayout.jsx'
import MarketingStyles from '../components/marketing/MarketingStyles.jsx'
import {
  TextField, Banner, SubmitLabel, check, validators, focusFirst,
} from '../components/marketing/AuthFields.jsx'

export default function ForgotPassword() {
  const [email, setEmail] = useState('')
  const [touched, setTouched] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [sentTo, setSentTo] = useState<string | null>(null)
  const [googleEnabled, setGoogleEnabled] = useState(false)
  const banner = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let alive = true
    fetchAuthConfig().then((c: any) => { if (alive) setGoogleEnabled(!!c.google_oauth) })
    return () => { alive = false }
  }, [])

  const emailErr = touched ? check(email, validators.email) : null

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSentTo(null)
    setTouched(true)
    if (check(email, validators.email)) { focusFirst(['forgot-email']); return }
    setPending(true)
    try {
      const address = email.trim()
      await forgotPassword({ email: address })
      setSentTo(address)
    } catch (err: any) {
      setError(err?.message ?? 'Could not request a reset link. Try again.')
    } finally {
      setPending(false)
      requestAnimationFrame(() => banner.current?.scrollIntoView({ block: 'nearest' }))
    }
  }

  return (
    <AuthLayout
      title="Reset your password"
      subtitle="Enter your email address and we will send you a link to choose a new password."
      eyebrow="RESET PASSWORD"
      artTagline="Your data, computed where it is read."
      footer={<>Remembered it?{' '}<Link to="/login" className="mk-link">Sign in</Link></>}
    >
      <MarketingStyles />
      <div className="mk-form">
        {error && <div ref={banner}><Banner>{error}</Banner></div>}
        {sentTo && (
          <div ref={banner}>
            <Banner kind="ok">
              If {sentTo} belongs to an account with a password, a reset link is on its way. It works once and
              expires in 30 minutes.
              {googleEnabled && ' If you signed up with Google, use Continue with Google to sign in.'}
            </Banner>
          </div>
        )}

        <form onSubmit={handleSubmit} className="mk-form" noValidate>
          <TextField
            id="forgot-email" label="Email" type="email" autoComplete="email" inputMode="email"
            placeholder="name@company.com" value={email} onChange={setEmail} disabled={pending}
            error={emailErr} onBlurValue={() => setTouched(true)}
          />
          <button type="submit" disabled={pending} className="nubi-btn nubi-btn-primary nubi-btn-xl mk-submit" aria-busy={pending}>
            <SubmitLabel pending={pending} idle={sentTo ? 'Send again' : 'Send reset link'} busy="Sending…" />
          </button>
        </form>
      </div>
    </AuthLayout>
  )
}
