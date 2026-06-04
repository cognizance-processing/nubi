import { Link } from 'react-router-dom'
import { Github, Twitter, Linkedin } from 'lucide-react'
import Logo from './Logo'

// ─── Navigation columns ───────────────────────────────────────────────────────
const NAV_COLUMNS = [
  {
    label: 'Product',
    links: [
      { text: 'Dashboards', to: '#' },
      { text: 'Editor',     to: '/editor', internal: true },
      { text: 'Embedding',  to: '#' },
      { text: 'Connectors', to: '#' },
    ],
  },
  {
    label: 'Developers',
    links: [
      { text: 'Docs', to: '/docs',  internal: true },
      { text: 'SDK',  to: '#' },
      { text: 'CLI',  to: '#' },
      { text: 'MCP',  to: '#' },
    ],
  },
  {
    label: 'Compare',
    links: [
      { text: 'Overview',    to: '/compare', internal: true },
      { text: 'vs Metabase', to: '#' },
      { text: 'vs Superset', to: '#' },
      { text: 'vs Tableau',  to: '#' },
    ],
  },
  {
    label: 'Company',
    links: [
      { text: 'About',   to: '#' },
      { text: 'Blog',    to: '#' },
      { text: 'Careers', to: '#' },
    ],
  },
  {
    label: 'Resources',
    links: [
      { text: 'GitHub',    to: '#' },
      { text: 'Changelog', to: '#' },
      { text: 'Status',    to: '#' },
    ],
  },
]

const SOCIAL = [
  { Icon: Github,   href: '#', label: 'GitHub' },
  { Icon: Twitter,  href: '#', label: 'X (Twitter)' },
  { Icon: Linkedin, href: '#', label: 'LinkedIn' },
]

// ─── Utility: nav link (internal = react-router Link, else <a>) ───────────────
function NavLink({ text, to, internal }) {
  const cls =
    'text-sm text-muted hover:text-fg transition-colors duration-150 leading-relaxed'
  if (internal) {
    return <Link to={to} className={cls}>{text}</Link>
  }
  return <a href={to} className={cls}>{text}</a>
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

            {/* Logo with wordmark using the real Logo component */}
            <Link to="/" aria-label="Nubi home" className="w-fit group">
              <Logo size={34} showName className="group-hover:opacity-90 transition-opacity duration-150" />
            </Link>

            {/* Tagline */}
            <p className="text-sm font-display font-medium text-fg leading-snug max-w-[200px]">
              BI that runs in the browser.
            </p>

            {/* Short descriptor */}
            <p className="text-sm text-muted leading-relaxed max-w-[210px]">
              Open-source, embeddable analytics — no server required.
            </p>

            {/* Social icons */}
            <div className="flex items-center gap-2.5 mt-1">
              {SOCIAL.map(({ Icon, href, label }) => (
                <a
                  key={label}
                  href={href}
                  aria-label={label}
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

          <nav className="flex items-center gap-4" aria-label="Legal navigation">
            {[
              { text: 'Docs',    to: '/docs',    internal: true },
              { text: 'Compare', to: '/compare', internal: true },
              { text: 'Privacy', to: '#' },
              { text: 'Terms',   to: '#' },
            ].map(({ text, to, internal }) => (
              <NavLink key={text} text={text} to={to} internal={internal} />
            ))}
          </nav>
        </div>

      </div>
    </footer>
  )
}
