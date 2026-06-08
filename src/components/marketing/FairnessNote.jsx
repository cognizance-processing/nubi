/**
 * FairnessNote — Visible commitment to fair competitor comparisons.
 *
 * Displayed prominently on the /compare page.
 * Invites anyone who finds a factual error or outdated pricing to open
 * a GitHub issue at https://github.com/nu-bi/nubi/issues/new
 *
 * Tokens: bg-surface, bg-surface-2, border-border, text-fg, text-muted,
 *         text-brand-teal, bg-brand-teal.
 */

import { ExternalLink, Flag, Info } from 'lucide-react'

const GITHUB_ISSUE_URL =
  'https://github.com/nu-bi/nubi/issues/new' +
  '?labels=compare-page%2Cfairness&title=%5BCompare+page%5D+Inaccurate+or+outdated+competitor+data' +
  '&body=**Competitor%3A**+%0A**Claim+that+is+wrong+or+outdated%3A**+%0A**Correct+information+%2F+source%3A**+'

/**
 * FairnessNote
 *
 * @param {{ asOf?: string }} props
 *   asOf — human-readable date string for the research cut-off (default "June 2026")
 */
export default function FairnessNote({ asOf = 'June 2026' }) {
  return (
    <aside
      role="note"
      aria-label="Comparison fairness commitment"
      className="rounded-2xl border border-border bg-surface overflow-hidden"
    >
      {/* Accent top bar */}
      <div className="h-1 w-full bg-gradient-to-r from-brand-teal via-brand-blue to-brand-navy" />

      <div className="px-5 sm:px-6 py-5 flex flex-col sm:flex-row gap-4 sm:gap-6">
        {/* Icon */}
        <div className="shrink-0 flex items-start pt-0.5">
          <div className="w-9 h-9 rounded-xl bg-accent/10 flex items-center justify-center">
            <Flag size={17} className="text-brand-teal" strokeWidth={1.75} />
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-fg mb-1">
            Our commitment to a fair comparison
          </p>
          <p className="text-xs text-muted leading-relaxed mb-3">
            Every competitor fact on this page was sourced from public pricing pages or independent
            analysts and is accurate as of <strong className="text-fg">{asOf}</strong>. Pricing and
            features change frequently — we update this page when we become aware of changes, but we
            cannot guarantee real-time accuracy.{' '}
            <strong className="text-fg">
              We do not cherry-pick only Nubi-favourable data.
            </strong>{' '}
            Where competitors are genuinely stronger — ecosystem maturity, enterprise certifications,
            analyst mindshare — we say so. Where pricing data is estimated (not available publicly),
            it is marked with an "est." badge.
          </p>

          <div className="flex flex-col sm:flex-row gap-3 sm:gap-5 sm:items-center">
            {/* Issue link */}
            <a
              href={GITHUB_ISSUE_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-xs font-semibold text-brand-teal
                hover:text-brand-teal/80 transition-colors focus-visible:outline-none
                focus-visible:ring-2 focus-visible:ring-ring rounded"
            >
              <ExternalLink size={12} strokeWidth={2.5} />
              Found an error or outdated price? Open a GitHub issue
            </a>

            {/* Staleness notice */}
            <span className="inline-flex items-center gap-1 text-[11px] text-muted">
              <Info size={11} className="shrink-0" />
              Data as of {asOf} — prices change; verify before switching
            </span>
          </div>
        </div>
      </div>
    </aside>
  )
}
