/**
 * FairnessNote — the visible commitment to fair competitor comparisons on
 * /compare. A 2px accent rule on the left, a mono label, one honest paragraph,
 * and a link to open an issue when a fact is wrong or out of date.
 */

import { ArrowUpRight } from 'lucide-react'
import { Tag } from './kit.jsx'

const GITHUB_ISSUE_URL =
  'https://github.com/nu-bi/nubi/issues/new' +
  '?labels=compare-page%2Cfairness&title=%5BCompare+page%5D+Inaccurate+or+outdated+competitor+data' +
  '&body=**Competitor%3A**+%0A**Claim+that+is+wrong+or+outdated%3A**+%0A**Correct+information+%2F+source%3A**+'

export default function FairnessNote({ asOf = 'June 2026' }: { asOf?: string }) {
  return (
    <aside role="note" aria-label="Comparison fairness commitment" className="mk-note">
      <p className="mk-label">Our commitment to a fair comparison</p>
      <p style={{ maxWidth: '78ch' }}>
        Every competitor fact on this page comes from public pricing pages or independent analysts and was accurate
        as of <b>{asOf}</b>. Prices and features change often, so verify before you switch. <b>We don&rsquo;t cherry-pick.</b>{' '}
        Where a competitor is genuinely stronger, such as ecosystem maturity, certifications or analyst mindshare, we say so.
        Figures marked <span style={{ verticalAlign: "baseline" }}><Tag>est.</Tag></span> are estimates because no public price exists.
      </p>
      <p>
        <a href={GITHUB_ISSUE_URL} target="_blank" rel="noopener noreferrer" className="mk-arrow">
          Found an error or an outdated price? Open an issue<ArrowUpRight size={14} strokeWidth={1.75} aria-hidden="true" />
        </a>
      </p>
    </aside>
  )
}
