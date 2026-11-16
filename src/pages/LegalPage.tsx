/**
 * LegalPage — renders the Privacy Policy and Terms of Service.
 *
 * Content lives as Markdown in src/content/legal/*.md and is rendered with the
 * shared MarkdownRenderer in the docs reading style. Reachable at /privacy and
 * /terms, linked from the footer.
 *
 * NOTE: the documents are drafted as POPIA + GDPR-aware templates and are not
 * legal advice. Each carries an in-document "have it reviewed by counsel"
 * banner and [PLACEHOLDERS] for entity-specific facts.
 */
import { Link } from 'react-router-dom'
import MarkdownRenderer from '../components/MarkdownRenderer.jsx'
import MarketingStyles from '../components/marketing/MarketingStyles.jsx'
import privacyMd from '../content/legal/privacy.md?raw'
import termsMd from '../content/legal/terms.md?raw'
import './docs/docs.css'

const DOCS = {
  privacy: { title: 'Privacy Policy', content: privacyMd },
  terms: { title: 'Terms of Service', content: termsMd },
}

export default function LegalPage({ doc }: { doc: 'privacy' | 'terms' }) {
  const entry = DOCS[doc] ?? DOCS.privacy

  return (
    <div className="mk">
      <MarketingStyles />
      <div className="mk-wrap" style={{ paddingBlock: '40px 96px' }}>
        <div className="mk-idx"><b>LEGAL</b><span>{entry.title}</span></div>

        <div className="docs-prose" style={{ maxWidth: 760, marginTop: 40 }}>
          <MarkdownRenderer content={entry.content} />
        </div>

        <nav aria-label="Legal and help" className="dx-edit" style={{ maxWidth: 760, marginTop: 64 }}>
          <Link to="/privacy">Privacy Policy</Link>
          <Link to="/terms">Terms of Service</Link>
          <Link to="/docs">Docs</Link>
          <a href="mailto:privacy@nubi.io">privacy@nubi.io</a>
        </nav>
      </div>
    </div>
  )
}
