import { Link } from 'react-router-dom'
import { Github, Twitter, Linkedin } from 'lucide-react'
import Logo from './Logo'

/**
 * Smooth-scroll helper — scrolls to a section on the current page if possible,
 * otherwise navigates to "/" then attempts the scroll after a brief tick.
 */
function scrollToId(id) {
  const el = document.getElementById(id)
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

// ─── Navigation columns ───────────────────────────────────────────────────────
//
// Link kinds:
//   internal: true   → react-router <Link to={to}>
//   scroll: 'id'     → smooth-scroll to #id on the landing page (href="/#id")
//   external: true   → plain <a> with target="_blank"
//   (none / #)       → removed or replaced
//
const GITHUB_URL = 'https://github.com/exo-explore/nubi'

const NAV_COLUMNS = [
  {
    label: 'Product',
    links: [
      { text: 'Dashboards', href: '/#features',    scroll: 'features' },
      { text: 'Embedding',  href: '/#embedding',   scroll: 'embedding' },
      { text: 'Connectors', href: '/#sources',     scroll: 'sources' },
      { text: 'Pricing',    href: '/#pricing',     scroll: 'pricing' },
    ],
  },
  {
    label: 'Developers',
    links: [
      { text: 'Docs',        to: '/docs',                internal: true },
      { text: 'SDK & CLI',   to: '/docs/sdk-and-cli',    internal: true },
      { text: 'AI & MCP',    to: '/docs/ai-and-mcp',     internal: true },
      { text: 'Self-host',   to: '/docs/self-host',      internal: true },
    ],
  },
  {
    label: 'Compare',
    links: [
      { text: 'Overview',     to: '/compare',    internal: true },
      { text: 'vs Hex',       to: '/compare',    internal: true },
      { text: 'vs Cube',      to: '/compare',    internal: true },
      { text: 'vs Metabase',  to: '/compare',    internal: true },
    ],
  },
  {
    label: 'Company',
    links: [
      { text: 'About',       href: '/#about',        scroll: 'about' },
      { text: 'Open core',   to: '/docs/open-core',  internal: true },
      { text: 'Sign up',     to: '/register',        internal: true },
    ],
  },
  {
    label: 'Resources',
    links: [
      { text: 'GitHub',          href: GITHUB_URL,                external: true },
      { text: 'Changelog',       href: `${GITHUB_URL}/releases`,  external: true },
      { text: 'Getting started', to: '/docs/getting-started',     internal: true },
    ],
  },
]

const SOCIAL = [
  { Icon: Github,   href: 'https://github.com/exo-explore/nubi', label: 'GitHub' },
  { Icon: Twitter,  href: 'https://twitter.com/nubi_dev',         label: 'X (Twitter)' },
  { Icon: Linkedin, href: 'https://linkedin.com/company/nubi-dev', label: 'LinkedIn' },
]

// ─── Utility: nav link ────────────────────────────────────────────────────────
function NavLink({ text, to, href, internal, external, scroll }) {
  const cls =
    'text-sm text-muted hover:text-fg transition-colors duration-150 leading-relaxed'

  if (internal) {
    return <Link to={to} className={cls}>{text}</Link>
  }

  if (external) {
    return (
      <a href={href} className={cls} target="_blank" rel="noopener noreferrer">
        {text}
      </a>
    )
  }

  // scroll-to-section link — uses /#id href so it works on fresh loads too
  if (scroll) {
    return (
      <a
        href={href}
        className={cls}
        onClick={(e) => {
          const el = document.getElementById(scroll)
          if (el) {
            e.preventDefault()
            el.scrollIntoView({ behavior: 'smooth', block: 'start' })
          }
          // else: let the browser follow href="/#id" naturally
        }}
      >
        {text}
      </a>
    )
  }

  // Fallback — plain anchor
  return <a href={href ?? '#'} className={cls}>{text}</a>
}

// ─── Main component ───────────────────────────────────────────────────────────
export default function Footer() {
  const year = new Date().getFullYear()

  return (
    <footer className="bg-surface-2 border-t border-border">

      {/* Brand-gradient accent rule at the very top for polish */}
      <div className="h-px bg-brand-gradient opacity-60" />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">

        {/* ── Upper section: brand block + link columns ──────────────────── */}
        <div className="py-14 grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-12 lg:gap-16">

          {/* Brand block */}
          <div className="flex flex-col gap-5">

            {/* Logo with wordmark */}
            <Link to="/" aria-label="Nubi home" className="w-fit group">
              <Logo size={34} showName className="group-hover:opacity-90 transition-opacity duration-150" />
            </Link>

            {/* Tagline */}
            <p className="text-sm font-display font-medium text-fg leading-snug max-w-[200px]">
              BI that runs in the browser.
            </p>

            {/* Short descriptor */}
            <p className="text-sm text-muted leading-relaxed max-w-[220px]">
              Embeddable analytics with near-zero marginal cost per view.
              Powered by DuckDB-WASM and Arrow IPC.
            </p>

            {/* Social icons */}
            <div className="flex items-center gap-2.5 mt-1">
              {SOCIAL.map(({ Icon, href, label }) => (
                <a
                  key={label}
                  href={href}
                  aria-label={label}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="
                    flex items-center justify-center w-8 h-8 rounded-md
                    text-muted hover:text-fg
                    ring-1 ring-border hover:ring-border
                    bg-surface hover:bg-surface-2
                    transition-all duration-150
                  "
                >
                  <Icon size={15} strokeWidth={1.75} />
                </a>
              ))}
            </div>
          </div>

          {/* Nav columns */}
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-8">
            {NAV_COLUMNS.map(({ label, links }) => (
              <div key={label} className="flex flex-col gap-3">
                <span className="text-xs font-display font-semibold uppercase tracking-widest text-muted">
                  {label}
                </span>
                <ul className="flex flex-col gap-2">
                  {links.map((link) => (
                    <li key={link.text}>
                      <NavLink {...link} />
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>

        {/* ── Divider ───────────────────────────────────────────────────── */}
        <div className="h-px bg-border" />

        {/* ── Bottom bar ────────────────────────────────────────────────── */}
        <div className="py-5 flex flex-col sm:flex-row items-center justify-between gap-3">
          <p className="text-xs text-muted tracking-wide">
            &copy; {year} Nubi &middot;{' '}
            <span className="text-muted">Apache-2.0</span>
          </p>

          <nav className="flex items-center gap-4" aria-label="Footer navigation">
            <Link to="/docs" className="text-xs text-muted hover:text-fg transition-colors duration-150">
              Docs
            </Link>
            <Link to="/compare" className="text-xs text-muted hover:text-fg transition-colors duration-150">
              Compare
            </Link>
            <Link to="/docs/self-host" className="text-xs text-muted hover:text-fg transition-colors duration-150">
              Self-host
            </Link>
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-muted hover:text-fg transition-colors duration-150"
            >
              GitHub
            </a>
          </nav>
        </div>

      </div>
    </footer>
  )
}
