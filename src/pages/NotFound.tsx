import { Link, useLocation } from 'react-router-dom'
import { ArrowRight, Search } from 'lucide-react'
import Logo from '../components/Logo.jsx'
import MarketingStyles from '../components/marketing/MarketingStyles.jsx'
import PublicPalette, { openPublicSearch, isMacLike } from '../components/marketing/PublicPalette.jsx'
import Figure from '../components/marketing/Figure.jsx'
import { useAuth } from '../contexts/AuthContext.jsx'

/**
 * NotFound — rendered for any unmatched route ("*"). A lockup, the 404 index
 * line, one sentence, a search hint (⌘K), the missing-cube figure and four ways
 * out.
 */

const CSS = `
.nf { min-height: 100vh; display: flex; flex-direction: column; background: var(--surface); color: var(--text); }
.nf-top { height: 56px; border-bottom: 1px solid var(--border); }
.nf-top .mk-wrap { height: 100%; display: flex; align-items: center; justify-content: space-between; }
.nf-main { flex: 1; display: grid; gap: 40px; align-content: center; padding-block: 48px 64px; }
@media (min-width: 960px) { .nf-main { grid-template-columns: minmax(0, 7fr) minmax(0, 5fr); gap: 64px; align-items: center; } }
.nf-path { font: 13px var(--font-mono); color: var(--text-muted); overflow-wrap: anywhere; }
.nf-hint { display: inline-flex; align-items: center; gap: 10px; height: 40px; padding: 0 12px; border: 1px solid var(--frame-line); background: var(--surface-2); color: var(--text-muted); font: 14px var(--font-ui); border-radius: 2px; cursor: pointer; }
.nf-hint:hover { border-color: var(--ink); color: var(--text); }
.nf-links { display: grid; gap: 1px; background: var(--border); border: 1px solid var(--border); margin-top: 8px; }
@media (min-width: 560px) { .nf-links { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
.nf-links a { background: var(--surface); padding: 14px 16px 16px; display: flex; flex-direction: column; gap: 4px; color: var(--text); text-decoration: none; }
.nf-links a:hover { box-shadow: inset 0 0 0 1px var(--ink); }
.nf-links b { display: flex; align-items: center; justify-content: space-between; font-size: 15px; font-weight: 600; }
.nf-links span { font-size: 13px; color: var(--text-muted); }
`

export default function NotFound() {
  const { pathname } = useLocation()
  const { user } = useAuth()
  const mod = isMacLike() ? '⌘K' : 'Ctrl K'
  const links = [
    user ? { to: '/home', t: 'Open app', d: 'Your workspace' } : { to: '/register', t: 'Start free', d: 'Unlimited viewers' },
    { to: '/docs', t: 'Docs', d: 'Guides and reference' },
    { to: '/pricing', t: 'Pricing', d: 'Five plans, zero seat math' },
    { to: '/', t: 'Home', d: 'Back to the start' },
  ]

  return (
    <div className="mk nf">
      <MarketingStyles />
      <style href="nubi-notfound" precedence="default">{CSS}</style>
      <PublicPalette />

      <header className="nf-top">
        <div className="mk-wrap">
          <Link to="/" aria-label="Nubi home" style={{ color: 'inherit' }}><Logo size={26} showName /></Link>
          {user
            ? <Link to="/home" className="nubi-btn nubi-btn-ghost nubi-btn-sm">Open app</Link>
            : <Link to="/login" className="nubi-btn nubi-btn-ghost nubi-btn-sm">Sign in</Link>}
        </div>
      </header>

      <main id="main" className="mk-wrap nf-main">
        <div style={{ display: 'grid', gap: 24 }}>
          <div className="mk-idx"><b>404</b><span>PAGE NOT FOUND</span></div>
          <h1 className="mk-h1" style={{ fontSize: 'clamp(56px, 11vw, 148px)', lineHeight: .86, letterSpacing: '-.06em' }}>
            Nothing <em>here.</em>
          </h1>
          <p className="mk-lead">This page doesn&rsquo;t exist or has moved. Check the address, search, or start again from one of these.</p>
          <p className="nf-path" aria-label="Requested address">{pathname}</p>
          <div className="mk-ctas">
            <Link to="/" className="nubi-btn nubi-btn-primary nubi-btn-xl">Back to home</Link>
            <button type="button" className="nf-hint" onClick={openPublicSearch} aria-label={`Search (${mod})`}>
              <Search size={15} strokeWidth={1.75} aria-hidden="true" />
              Search the docs
              <kbd className="mk-kbd">{mod}</kbd>
            </button>
          </div>
        </div>
        <Figure name="NotFoundFigure" title="A field of cubes with one missing, drawn as a dashed outline" className="w-full h-auto" />
        <nav aria-label="Where to go" style={{ gridColumn: '1 / -1' }}>
          <div className="nf-links">
            {links.map(l => (
              <Link key={l.to} to={l.to}><b>{l.t}<ArrowRight size={15} strokeWidth={1.5} aria-hidden="true" /></b><span>{l.d}</span></Link>
            ))}
          </div>
        </nav>
      </main>
    </div>
  )
}
