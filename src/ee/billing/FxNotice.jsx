/**
 * FxNotice.jsx — ZAR/USD conversion disclosure notice (src/ee/billing/FxNotice.jsx)
 *
 * Renders the customer-facing FX disclosure copy mandated by the pricing
 * blueprint.  Always shown on PricingPage below the tier grid and near any
 * checkout CTA.
 *
 * Props
 * -----
 * rate        number | null   Current USD→ZAR rate (e.g. 16.26).  When null
 *                             the notice omits the "Today's rate" line.
 * updatedAt   string | null   ISO timestamp of the last rate refresh.
 * isFallback  boolean         When true, a soft warning is appended noting
 *                             that the rate shown is a reference estimate.
 * compact     boolean         When true, renders a single paragraph instead
 *                             of the full box.  Default: false.
 * className   string          Extra classes applied to the root element.
 */

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Format an ISO timestamp as a short locale date, e.g. "8 Jun 2026".
 *
 * @param {string | null} iso
 * @returns {string}
 */
function fmtDate(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString('en-ZA', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    })
  } catch {
    return ''
  }
}

// ---------------------------------------------------------------------------
// FxNotice
// ---------------------------------------------------------------------------

/**
 * @param {{
 *   rate?: number | null,
 *   updatedAt?: string | null,
 *   isFallback?: boolean,
 *   compact?: boolean,
 *   className?: string,
 * }} props
 */
export default function FxNotice({
  rate = null,
  updatedAt = null,
  isFallback = false,
  compact = false,
  className,
}) {
  const dateStr = fmtDate(updatedAt)
  const rateStr = rate != null ? `1 USD = R ${rate.toFixed(2)}` : null

  const disclosure =
    "Nubi's subscription prices are set in US dollars (USD) and converted to South African rand (ZAR) " +
    'using a daily exchange rate. The ZAR amount shown at checkout and charged each billing cycle may ' +
    'vary slightly from cycle to cycle as the exchange rate changes. Your USD price remains fixed for the ' +
    'duration of your plan. Exchange rate information is sourced from a tier-1 FX provider and refreshed daily.'

  if (compact) {
    return (
      <p className={`text-xs text-muted leading-relaxed ${className ?? ''}`}>
        {disclosure}
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
      <p className="text-xs font-semibold text-muted uppercase tracking-wider">
        About ZAR pricing
      </p>
      <p className="text-sm text-muted leading-relaxed">
        {disclosure}
      </p>
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
        <a
          href="mailto:billing@nubi.io"
          className="underline hover:text-fg transition-colors"
        >
          billing@nubi.io
        </a>
      </p>
    </aside>
  )
}
