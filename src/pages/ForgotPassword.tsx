/**
 * ForgotPassword — ask for a password reset link.
 *
 * The confirmation is the same whether or not the address has an account, and
 * shows only after the server has accepted the request.
 */

import { useState, useRef } from 'react'
import { Link } from 'react-router-dom'
import { postForgotPassword } from '../lib/api.js'
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
  const [sent, setSent] = useState(false)
  const banner = useRef<HTMLDivElement>(null)

  const emailErr = touched ? check(email, validators.email) : null

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setTouched(true)
    if (check(email, validators.email)) { focusFirst(['forgot-email']); return }
    setPending(true)
    try {
      await postForgotPassword(email.trim())
      setSent(true)
    } catch (err: any) {
      // A 422 carries a list of field errors, not a message.
      setError(err?.status === 422 ? 'Enter a valid email address.' : err?.message ?? 'Could not request a reset link. Try again.')
      requestAnimationFrame(() => banner.current?.scrollIntoView({ block: 'nearest' }))
    } finally {
      setPending(false)
    }
  }

  return (
    <AuthLayout
      title="Reset your password"
      subtitle="Enter your email and we'll send you a link to choose a new one."
      eyebrow="RESET PASSWORD"
      artTagline="Your data, computed where it is read."
      footer={<>Remembered it?{' '}<Link to="/login" className="mk-link">Sign in</Link></>}
    >
      <MarketingStyles />
      <div className="mk-form">
        {sent ? (
          <>
            <Banner kind="ok">
              If that address can reset its password, a link is on its way. It expires in 30 minutes.
            </Banner>
            <p className="mk-fhint">
              No email? This Nubi server may not be set up to send mail, or your account may sign in
              with Google. Contact your administrator.
            </p>
          </>
        ) : (
          <>
            {error && <div ref={banner}><Banner>{error}</Banner></div>}
            <form onSubmit={handleSubmit} className="mk-form" noValidate>
              <TextField
                id="forgot-email" label="Email" type="email" autoComplete="email" inputMode="email"
                placeholder="name@company.com" value={email} onChange={setEmail} disabled={pending}
                error={emailErr} onBlurValue={() => setTouched(true)}
              />
              <button type="submit" disabled={pending} className="nubi-btn nubi-btn-primary nubi-btn-xl mk-submit" aria-busy={pending}>
                <SubmitLabel pending={pending} idle="Send reset link" busy="Sending…" />
              </button>
            </form>
          </>
        )}
      </div>
    </AuthLayout>
  )
}
