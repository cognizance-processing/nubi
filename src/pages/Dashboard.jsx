/**
 * Dashboard — authenticated home page.
 *
 * Shows the signed-in user's greeting, quick-access cards, and account info.
 * Protected by <ProtectedRoute> in App.jsx — this component only renders
 * when a user object is present in AuthContext.
 */

import { useNavigate, Link } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext.jsx'

// Quick-access card definitions
const QUICK_LINKS = [
  {
    to: '/playground',
    icon: (
      <svg viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
        <path fillRule="evenodd" d="M2 5a2 2 0 012-2h12a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V5zm3.293 1.293a1 1 0 011.414 0l3 3a1 1 0 010 1.414l-3 3a1 1 0 01-1.414-1.414L7.586 10 5.293 7.707a1 1 0 010-1.414zM11 12a1 1 0 100 2h3a1 1 0 100-2h-3z" clipRule="evenodd" />
      </svg>
    ),
    label: 'SQL Playground',
    description: 'Run queries against your datastores with DuckDB-WASM',
    accent: 'text-brand-teal',
    border: 'hover:border-brand-teal',
  },
  {
    to: '/editor',
    icon: (
      <svg viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
        <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z" />
      </svg>
    ),
    label: 'Dashboard Editor',
    description: 'Drag-and-drop widgets, AI-generated layouts',
    accent: 'text-brand-blue',
    border: 'hover:border-brand-blue',
  },
  {
    to: '/d/sample',
    icon: (
      <svg viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
        <path d="M2 11a1 1 0 011-1h2a1 1 0 011 1v5a1 1 0 01-1 1H3a1 1 0 01-1-1v-5zM8 7a1 1 0 011-1h2a1 1 0 011 1v9a1 1 0 01-1 1H9a1 1 0 01-1-1V7zM14 4a1 1 0 011-1h2a1 1 0 011 1v12a1 1 0 01-1 1h-2a1 1 0 01-1-1V4z" />
      </svg>
    ),
    label: 'Sample Dashboard',
    description: 'See KPI, table, and chart widgets in action',
    accent: 'text-brand-cyan',
    border: 'hover:border-brand-cyan',
  },
  {
    to: '/docs',
    icon: (
      <svg viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5">
        <path fillRule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm2 6a1 1 0 011-1h6a1 1 0 110 2H7a1 1 0 01-1-1zm1 3a1 1 0 100 2h6a1 1 0 100-2H7z" clipRule="evenodd" />
      </svg>
    ),
    label: 'Documentation',
    description: 'Widget reference, API specs, and embed guide',
    accent: 'text-muted',
    border: 'hover:border-border',
  },
]

export default function Dashboard() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  async function handleLogout() {
    await logout()
    navigate('/login', { replace: true })
  }

  const firstName = user?.name?.split(' ')[0] ?? user?.email?.split('@')[0] ?? 'there'

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-10">

      {/* Page header */}
      <div className="mb-10 flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-3xl font-bold font-display text-fg">
            Hello, {firstName} 👋
          </h1>
          <p className="mt-1 text-sm text-muted">
            Welcome back to your Nubi workspace.
          </p>
        </div>
        <button
          onClick={handleLogout}
          className="px-4 py-2 text-sm font-medium text-fg border border-border bg-surface rounded-lg hover:bg-surface-2 transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1"
        >
          Log out
        </button>
      </div>

      {/* Quick-access cards */}
      <section className="mb-10">
        <h2 className="text-xs font-semibold text-muted uppercase tracking-wider mb-4">Quick access</h2>
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {QUICK_LINKS.map(link => (
            <Link
              key={link.to}
              to={link.to}
              className={`group block bg-surface border border-border rounded-xl p-5 transition-all hover:shadow-md hover:-translate-y-0.5 ${link.border} focus:outline-none focus:ring-2 focus:ring-ring`}
            >
              <div className={`mb-3 ${link.accent}`}>
                {link.icon}
              </div>
              <p className="text-sm font-semibold text-fg group-hover:text-primary transition-colors">
                {link.label}
              </p>
              <p className="text-xs text-muted mt-1 leading-relaxed">
                {link.description}
              </p>
            </Link>
          ))}
        </div>
      </section>

      {/* Coming soon placeholders */}
      <section className="mb-10">
        <h2 className="text-xs font-semibold text-muted uppercase tracking-wider mb-4">Workspace</h2>
        <div className="grid sm:grid-cols-3 gap-4">
          {['Datastores', 'Boards', 'Saved Queries'].map(label => (
            <div
              key={label}
              className="p-5 rounded-xl border border-dashed border-border bg-surface-2 text-center"
            >
              <p className="text-sm font-medium text-muted">{label}</p>
              <p className="text-xs text-muted/60 mt-1">Coming in later milestones</p>
            </div>
          ))}
        </div>
      </section>

      {/* Account info card */}
      <section>
        <h2 className="text-xs font-semibold text-muted uppercase tracking-wider mb-4">Account</h2>
        <div className="bg-surface border border-border rounded-xl p-6 max-w-md">
          <dl className="space-y-3 text-sm">
            <div className="flex gap-3">
              <dt className="w-28 text-muted shrink-0 font-medium">Name</dt>
              <dd className="text-fg">{user?.name ?? '—'}</dd>
            </div>
            <div className="flex gap-3">
              <dt className="w-28 text-muted shrink-0 font-medium">Email</dt>
              <dd className="text-fg">{user?.email}</dd>
            </div>
            <div className="flex gap-3">
              <dt className="w-28 text-muted shrink-0 font-medium">Verified</dt>
              <dd className={user?.email_verified ? 'text-brand-teal font-medium' : 'text-fg'}>
                {user?.email_verified ? 'Yes' : 'No'}
              </dd>
            </div>
            <div className="flex gap-3">
              <dt className="w-28 text-muted shrink-0 font-medium">Member since</dt>
              <dd className="text-fg">
                {user?.created_at
                  ? new Date(user.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
                  : '—'}
              </dd>
            </div>
          </dl>
        </div>
      </section>
    </div>
  )
}
