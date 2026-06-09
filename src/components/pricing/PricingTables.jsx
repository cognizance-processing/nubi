/**
 * PricingTables.jsx — core pricing support components (src/components/pricing/PricingTables.jsx)
 *
 * Exports four reusable, OSS-safe components used by both the EE PricingPage
 * and any public-facing landing/docs pages:
 *
 *   BillingToggle          — monthly / annual switch with "2 months free" badge
 *   WalletBillingExplainer — "How billing works: flat plan + usage wallet" explainer block
 *   OverageTable           — collapsible overage rate schedule table
 *   FxDisclosure           — ZAR/USD FX disclosure block (full or compact mode)
 *
 * No EE imports.  No auth.  No checkout logic.
 *
 * FxDisclosure replaces the EE FxNotice.jsx for core use.  The EE FxNotice
 * can keep its existing API or re-export from this module.
 */

import { ChevronDown, ChevronUp, Wallet, Zap, RotateCcw, ShieldOff, CreditCard } from 'lucide-react'
import { useState } from 'react'

// ---------------------------------------------------------------------------
// BillingToggle
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   value: 'monthly' | 'annual',
 *   onChange: (v: 'monthly' | 'annual') => void,
 * }} props
 */
export function BillingToggle({ value, onChange }) {
  return (
    <div className="flex items-center gap-3 justify-center">
      <span className={`text-sm font-medium ${value === 'monthly' ? 'text-fg' : 'text-muted'}`}>
        Monthly
      </span>
      <button
        role="switch"
        aria-checked={value === 'annual'}
        onClick={() => onChange(value === 'monthly' ? 'annual' : 'monthly')}
        className={[
          'relative inline-flex h-6 w-11 shrink-0 rounded-full border-2 border-transparent transition-colors duration-200',
          'focus:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2',
          value === 'annual' ? 'bg-accent' : 'bg-border',
        ].join(' ')}
      >
        <span
          className={[
            'pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow ring-0 transition-transform duration-200',
            value === 'annual' ? 'translate-x-5' : 'translate-x-0',
          ].join(' ')}
        />
      </button>
      <span className={`text-sm font-medium ${value === 'annual' ? 'text-fg' : 'text-muted'}`}>
        Annual
        <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded-full bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300 text-[10px] font-bold uppercase tracking-wide">
          2 months free
        </span>
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// WalletBillingExplainer
// ---------------------------------------------------------------------------

const WALLET_STEPS = [
  {
    icon: CreditCard,
    color: 'text-accent',
    bg: 'bg-accent/10',
    title: 'Flat plan subscription',
    body: 'Pay a fixed monthly or annual fee for your tier. Your included quota — storage, compute, AI calls, embedded sessions — is consumed first at no extra charge.',
  },
  {
    icon: Wallet,
    color: 'text-teal-600 dark:text-teal-400',
    bg: 'bg-teal-50 dark:bg-teal-900/20',
    title: 'Usage wallet (prepaid credits)',
    body: 'Each paid org has a prepaid credit balance (in USD, displayed in ZAR). Metered usage beyond your plan\'s included quota is drawn down from the wallet — only on successful operations.',
  },
  {
    icon: Zap,
    color: 'text-amber-600 dark:text-amber-400',
    bg: 'bg-amber-50 dark:bg-amber-900/20',
    title: 'Auto-topup when balance is low',
    body: 'Set a threshold and a topup amount. When your wallet balance dips below the threshold, your saved card is charged automatically — like Anthropic\'s auto-reload. A monthly spend cap prevents surprise charges.',
  },
  {
    icon: RotateCcw,
    color: 'text-violet-600 dark:text-violet-400',
    bg: 'bg-violet-50 dark:bg-violet-900/20',
    title: 'Manual top-up & spend controls',
    body: 'Top up on demand at any time. Set a hard monthly spend cap so usage stops (not auto-charged) if you hit your ceiling. Full ledger history — every credit and debit is logged.',
  },
  {
    icon: ShieldOff,
    color: 'text-red-500 dark:text-red-400',
    bg: 'bg-red-50 dark:bg-red-900/20',
    title: 'Zero-balance hard stop',
    body: 'If wallet balance hits zero and auto-topup is off (or capped), metered usage beyond your plan quota stops immediately. Flat-plan features inside your quota always keep working.',
  },
]

/**
 * Explains the flat plan + usage wallet billing model to users.
 *
 * @param {{ compact?: boolean }} props
 */
export function WalletBillingExplainer({ compact = false }) {
  const [open, setOpen] = useState(!compact)

  if (compact) {
    return (
      <section>
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-2 text-sm font-medium text-muted hover:text-fg transition-colors"
          aria-expanded={open}
        >
          {open ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
          <Wallet size={14} />
          How billing works: flat plan + usage wallet
        </button>

        {open && <WalletExplainerBody />}
      </section>
    )
  }

  return (
    <section aria-label="How billing works">
      <div className="flex items-center gap-2 mb-4">
        <Wallet size={16} className="text-accent" />
        <h3 className="font-display font-semibold text-base text-fg">
          How billing works: flat plan + usage wallet
        </h3>
      </div>
      <WalletExplainerBody />
    </section>
  )
}

function WalletExplainerBody() {
  return (
    <div className="mt-4 rounded-2xl border border-border bg-surface overflow-hidden">
      {/* Summary row */}
      <div className="px-5 py-4 border-b border-border bg-surface-2">
        <p className="text-sm text-fg leading-relaxed">
          Nubi uses a <strong>two-layer billing model</strong> inspired by Anthropic and OpenAI:{' '}
          a <em>flat plan subscription</em> covers your monthly base quota, and a{' '}
          <em>prepaid usage wallet</em> covers everything beyond that — drawn down per call or
          byte, auto-topped-up from your saved card when your balance runs low.
        </p>
      </div>

      {/* Steps */}
      <div className="divide-y divide-border">
        {WALLET_STEPS.map(({ icon: Icon, color, bg, title, body }) => (
          <div key={title} className="flex gap-4 px-5 py-4">
            <div className={`flex-none w-8 h-8 rounded-xl flex items-center justify-center ${bg}`}>
              <Icon size={15} className={color} />
            </div>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-fg">{title}</p>
              <p className="text-xs text-muted mt-0.5 leading-relaxed">{body}</p>
            </div>
          </div>
        ))}
      </div>

      {/* Callout: what counts as metered usage */}
      <div className="px-5 py-4 border-t border-border bg-surface-2">
        <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">Metered usage drawn from wallet</p>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {[
            { label: 'AI / LLM calls', note: 'per call' },
            { label: 'Storage', note: 'per GB-month' },
            { label: 'Compute units', note: 'per 1,000 CUs' },
            { label: 'Embedded sessions', note: 'per 10,000' },
          ].map(({ label, note }) => (
            <div key={label} className="rounded-lg border border-border bg-surface px-3 py-2">
              <p className="text-xs font-semibold text-fg">{label}</p>
              <p className="text-[11px] text-muted">{note}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// OverageTable
// ---------------------------------------------------------------------------

const DEFAULT_OVERAGES = [
  { metric: 'Storage',            rate: 'R 1.50 / GB-month',       note: 'Available on all paid tiers; drawn from wallet' },
  { metric: 'Compute',            rate: 'R 100 / 1,000 CU',        note: 'Starter+; drawn from wallet' },
  { metric: 'AI / LLM calls',     rate: 'R 5 / call',              note: 'Haiku grounding or Sonnet chat; drawn from wallet' },
  { metric: 'Embedded sessions',  rate: 'R 50 / 10,000 sessions',  note: 'Free on Enterprise; drawn from wallet' },
  { metric: 'Agent / kernel run', rate: 'R 2 / run',               note: 'Team+ remote kernel (E2B); drawn from wallet' },
]

/**
 * @param {{
 *   overages?: Array<{ metric: string, rate: string, note?: string }>,
 *   defaultOpen?: boolean,
 * }} props
 */
export function OverageTable({ overages = DEFAULT_OVERAGES, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <section>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 text-sm font-medium text-muted hover:text-fg transition-colors"
        aria-expanded={open}
      >
        {open ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
        Wallet overage rate schedule
      </button>

      {open && (
        <div className="mt-3">
          <div className="overflow-x-auto rounded-xl border border-border">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-surface-2">
                  <th className="text-left px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide">
                    Metric
                  </th>
                  <th className="text-left px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide">
                    Rate (ZAR)
                  </th>
                  <th className="text-left px-4 py-2.5 font-semibold text-muted text-xs uppercase tracking-wide hidden sm:table-cell">
                    Notes
                  </th>
                </tr>
              </thead>
              <tbody>
                {overages.map((row, i) => (
                  <tr key={row.metric} className={i % 2 === 0 ? 'bg-surface' : 'bg-surface-2'}>
                    <td className="px-4 py-2.5 font-medium text-fg">{row.metric}</td>
                    <td className="px-4 py-2.5 text-fg font-mono">{row.rate}</td>
                    <td className="px-4 py-2.5 text-muted hidden sm:table-cell">{row.note ?? ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted mt-2">
            Overages are debited in real-time from your usage wallet.
            Seats are unlimited at every tier — no per-seat charges ever.
          </p>
        </div>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------
// FxDisclosure — full or compact mode
// ---------------------------------------------------------------------------

function fmtDate(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString('en-ZA', {
      day: 'numeric', month: 'short', year: 'numeric',
    })
  } catch {
    return ''
  }
}

const FX_DISCLOSURE =
  "Nubi's subscription prices are set in US dollars (USD) and converted to South African rand (ZAR) " +
  'using a daily exchange rate. The ZAR amount shown at checkout and charged each billing cycle may ' +
  'vary slightly from cycle to cycle as the exchange rate changes. Your USD price remains fixed for the ' +
  'duration of your plan. Exchange rate information is sourced from a tier-1 FX provider and refreshed daily.'

/**
 * @param {{
 *   rate?: number | null,
 *   updatedAt?: string | null,
 *   isFallback?: boolean,
 *   compact?: boolean,
 *   className?: string,
 * }} props
 */
export function FxDisclosure({
  rate = null,
  updatedAt = null,
  isFallback = false,
  compact = false,
  className,
}) {
  const dateStr = fmtDate(updatedAt)
  const rateStr = rate != null ? `1 USD = R ${rate.toFixed(2)}` : null

  if (compact) {
    return (
      <p className={`text-xs text-muted leading-relaxed ${className ?? ''}`}>
        {FX_DISCLOSURE}
        {rateStr && (
          <span className="ml-1 text-muted/70">
            ({rateStr}{dateStr ? `, updated ${dateStr}` : ''}{isFallback ? ' — reference estimate' : ''}.)
          </span>
        )}
      </p>
    )
  }

  return (
    <aside
      className={`rounded-xl border border-border bg-surface-2 px-5 py-4 space-y-2 ${className ?? ''}`}
      aria-label="ZAR pricing disclosure"
    >
      <p className="text-xs font-semibold text-muted uppercase tracking-wider">About ZAR pricing</p>
      <p className="text-sm text-muted leading-relaxed">{FX_DISCLOSURE}</p>
      {(rateStr || isFallback) && (
        <p className="text-xs text-muted/70">
          {rateStr && (
            <>
              Today's rate: <span className="font-mono font-medium text-fg/70">{rateStr}</span>
              {dateStr && <span className="ml-1">(updated {dateStr})</span>}
            </>
          )}
          {isFallback && (
            <span className="ml-1 text-amber-600 dark:text-amber-400">
              — Using a reference estimate; live rate unavailable.
            </span>
          )}
        </p>
      )}
      <p className="text-xs text-muted/70">
        Questions?{' '}
        <a href="mailto:billing@nubi.io" className="underline hover:text-fg transition-colors">
          billing@nubi.io
        </a>
      </p>
    </aside>
  )
}
