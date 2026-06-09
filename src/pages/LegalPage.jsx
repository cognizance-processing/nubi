/**
 * LegalPage — renders the Privacy Policy and Terms of Service.
 *
 * Content lives as Markdown in src/content/legal/*.md and is rendered with the
 * shared MarkdownRenderer (same typography as the docs). These are reachable at
 * /privacy and /terms and linked from the footer.
 *
 * NOTE: the documents are drafted as POPIA + GDPR-aware templates and are not
 * legal advice — each carries an in-document "have it reviewed by counsel"
 * banner and [PLACEHOLDERS] for entity-specific facts.
 */
import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import MarkdownRenderer from '../components/MarkdownRenderer.jsx'
import privacyMd from '../content/legal/privacy.md?raw'
import termsMd from '../content/legal/terms.md?raw'

const DOCS = {
  privacy: { title: 'Privacy Policy', content: privacyMd },
  terms: { title: 'Terms of Service', content: termsMd },
}

export default function LegalPage({ doc }) {
  const entry = DOCS[doc] ?? DOCS.privacy

  return (
    <main className="bg-bg min-h-screen">
      <div className="max-w-3xl mx-auto px-5 sm:px-8 py-12 lg:py-16">
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg transition-colors mb-8"
        >
          <ArrowLeft size={15} /> Back to home
        </Link>

        <div className="docs-prose">
          <MarkdownRenderer content={entry.content} />
        </div>

        <div className="mt-12 pt-6 border-t border-border flex flex-wrap gap-x-6 gap-y-2 text-sm text-muted">
          <Link to="/privacy" className="hover:text-fg transition-colors">Privacy Policy</Link>
          <Link to="/terms" className="hover:text-fg transition-colors">Terms of Service</Link>
          <Link to="/docs" className="hover:text-fg transition-colors">Docs</Link>
          <a href="mailto:privacy@nubi.io" className="hover:text-fg transition-colors">privacy@nubi.io</a>
        </div>
      </div>
    </main>
  )
}
