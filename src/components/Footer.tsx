/**
 * Footer — the shared site footer (public pages and the landing).
 *
 * A ruled sitemap in hairline cells: lockup and one line on the left, four
 * columns of links, then a bar with the license and the theme switch.
 * Colours come from global tokens only, so it also reads correctly inside the
 * landing's `.nl` scope.
 */

import { Link } from 'react-router-dom'
import { useTheme } from '../contexts/ThemeContext.jsx'
import Logo from './Logo.jsx'
import Brand from './marketing/Brand.jsx'

const GITHUB_URL = 'https://github.com/nu-bi/nubi'

type FooterLink = { text: string; to?: string; href?: string }

const COLUMNS: { label: string; links: FooterLink[] }[] = [
  {
    label: 'Product',
    links: [
      { text: 'Overview', to: '/' },
      { text: 'Live demo', to: '/#live' },
      { text: 'Dashboards', to: '/docs/dashboards' },
      { text: 'Embedding', to: '/docs/embedding' },
      { text: 'AI and MCP', to: '/docs/ai-and-mcp' },
    ],
  },
  {
    label: 'Developers',
    links: [
      { text: 'Docs', to: '/docs' },
      { text: 'Quickstart', to: '/docs/quickstart' },
      { text: 'API reference', to: '/docs/api-reference' },
      { text: 'SDK and CLI', to: '/docs/sdk-and-cli' },
      { text: 'Self-host', to: '/docs/self-host' },
    ],
  },
  {
    label: 'Resources',
    links: [
      { text: 'Pricing', to: '/pricing' },
      { text: 'Compare', to: '/compare' },
      { text: 'Reporting', to: '/reporting' },
      { text: 'GitHub', href: GITHUB_URL },
      { text: 'Releases', href: `${GITHUB_URL}/releases` },
    ],
  },
  {
    label: 'Legal',
    links: [
      { text: 'Privacy', to: '/privacy' },
      { text: 'Terms', to: '/terms' },
      { text: 'Apache-2.0 license', href: `${GITHUB_URL}/blob/main/LICENSE` },
      { text: 'Open core', to: '/docs/open-core' },
    ],
  },
]

const CSS = `
.mk-foot { border-top: 2px solid var(--ink); background: var(--surface); color: var(--text); font-family: var(--font-ui); }
.mk-foot-grid { display: grid; gap: 1px; background: var(--border); border-inline: 1px solid var(--border); grid-template-columns: repeat(2, minmax(0, 1fr)); }
.mk-foot-grid > * { background: var(--surface); padding: 20px 16px 24px; min-width: 0; }
.mk-foot-brand { grid-column: 1 / -1; display: flex; flex-direction: column; gap: 14px; }
@media (min-width: 900px) {
  .mk-foot-grid { grid-template-columns: minmax(0, 1.4fr) repeat(4, minmax(0, 1fr)); }
  .mk-foot-brand { grid-column: auto; padding: 24px 24px 28px; }
  .mk-foot-grid > * { padding: 24px 20px 28px; }
}
.mk-foot h2 { margin: 0 0 12px; font: 500 11px/1.4 var(--font-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--text-muted); }
.mk-foot ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
.mk-foot li a { display: inline-block; padding: 4px 0; font-size: 14px; color: var(--text); text-decoration: none; }
.mk-foot li a:hover { color: var(--accent); text-decoration: underline; text-underline-offset: 3px; }
.mk-foot-tag { font-size: 15px; font-weight: 600; letter-spacing: -.015em; line-height: 1.35; max-width: 24ch; margin: 0; }
.mk-foot-sub { font-size: 13px; line-height: 1.5; color: var(--text-muted); max-width: 32ch; margin: 0; }
.mk-foot-bar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px 24px; padding-block: 14px 18px; border-top: 1px solid var(--border); font: 12px/1.4 var(--font-mono); color: var(--text-muted); }
.mk-foot-bar > div { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.mk-foot-bar a { color: var(--text-muted); text-decoration: none; display: inline-flex; align-items: center; gap: 6px; }
.mk-foot-bar a:hover { color: var(--text); }
.mk-foot-theme { display: inline-flex; border: 1px solid var(--frame-line); border-radius: 2px; }
.mk-foot-theme button { height: 26px; padding: 0 10px; font: 500 11px var(--font-mono); letter-spacing: .06em; text-transform: uppercase; color: var(--text-muted); background: transparent; border: 0; cursor: pointer; }
.mk-foot-theme button + button { border-left: 1px solid var(--frame-line); }
.mk-foot-theme button[aria-pressed="true"] { background: var(--ink); color: var(--ink-fg); }
.mk-foot-theme button:not([aria-pressed="true"]):hover { color: var(--text); background: var(--frame-hover); }
.mk-foot :focus-visible { outline: 2px solid var(--ring); outline-offset: 2px; }
.mk-foot-wrap { width: 100%; max-width: 1320px; margin: 0 auto; padding-inline: 16px; }
@media (min-width: 640px) { .mk-foot-wrap { padding-inline: 24px; } }
@media (min-width: 1024px) { .mk-foot-wrap { padding-inline: 32px; } }
`

function FootLink({ link }: { link: FooterLink }) {
  if (link.to) {
    // Anchors on the landing need a real navigation; everything else is client-side.
    return link.to.includes('#')
      ? <a href={link.to}>{link.text}</a>
      : <Link to={link.to}>{link.text}</Link>
  }
  return <a href={link.href} target="_blank" rel="noopener noreferrer">{link.text}</a>
}

export default function Footer() {
  const year = new Date().getFullYear()
  const { theme, setTheme } = useTheme()

  return (
    <footer className="mk-foot" aria-label="Site">
      <style href="nubi-footer" precedence="default">{CSS}</style>
      <div className="mk-foot-wrap">
        <div className="mk-foot-grid">
          <div className="mk-foot-brand">
            <Link to="/" aria-label="Nubi home" style={{ width: 'fit-content', color: 'inherit', marginBottom: 6 }}>
              <Logo size={28} showName />
            </Link>
            <p className="mk-foot-tag">BI that runs in your browser. Viewers are free.</p>
            <p className="mk-foot-sub">Open source under Apache-2.0. Self-host it or use Nubi Cloud.</p>
          </div>
          {COLUMNS.map(col => (
            <nav key={col.label} aria-label={col.label}>
              <h2>{col.label}</h2>
              <ul>
                {col.links.map(l => (<li key={l.text}><FootLink link={l} /></li>))}
              </ul>
            </nav>
          ))}
        </div>

        <div className="mk-foot-bar">
          <div>
            <span>&copy; {year} Nubi</span>
            <span aria-hidden="true">/</span>
            <span>Apache-2.0</span>
          </div>
          <div>
            <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer" aria-label="Nubi on GitHub">
              <Brand slug="github" name="GitHub" size={14} mode="mono" />
              {/* the mark alone is unlabeled text for sighted users */}
            </a>
            <div className="mk-foot-theme" role="group" aria-label="Colour theme">
              <button type="button" aria-pressed={theme === 'light'} onClick={() => setTheme('light')}>Light</button>
              <button type="button" aria-pressed={theme === 'dark'} onClick={() => setTheme('dark')}>Dark</button>
            </div>
          </div>
        </div>
      </div>
    </footer>
  )
}
