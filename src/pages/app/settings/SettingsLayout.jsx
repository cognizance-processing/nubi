/**
 * SettingsLayout — settings sub-layout with a persistent left nav.
 *
 * Routes:
 *   /settings/profile       → ProfileSettings
 *   /settings/organization  → OrgSettings
 *   /settings/project       → ProjectSettings
 *   /settings/security      → SecuritySettings
 *
 * The left sub-nav stays visible on every settings page so the user always
 * knows where they are and can switch sections without going back.
 */

import { NavLink, Outlet } from 'react-router-dom'
import { Settings, User, Building2, FolderGit2, ShieldCheck } from 'lucide-react'

const SETTINGS_NAV = [
  {
    to: '/settings/profile',
    label: 'Profile',
    icon: User,
    scope: 'Account',
    description: 'Your name and avatar',
  },
  {
    to: '/settings/organization',
    label: 'Organization',
    icon: Building2,
    scope: 'Organization',
    description: 'Name, branding, members, and deletion',
  },
  {
    to: '/settings/project',
    label: 'Project',
    icon: FolderGit2,
    scope: 'Project',
    description: 'Name, git sync, and deletion',
  },
  {
    to: '/settings/security',
    label: 'Security',
    icon: ShieldCheck,
    scope: 'Organization',
    description: 'Embed JWT issuers (org-wide)',
  },
]

// Per-scope chip colour so each section's scope (Account / Organization /
// Project) reads at a glance — this is what was previously unclear.
const SCOPE_STYLE = {
  Account:      'bg-slate-500/10 text-slate-600 dark:text-slate-300',
  Organization: 'bg-brand-blue/10 text-brand-blue dark:text-blue-300',
  Project:      'bg-brand-teal/10 text-brand-teal dark:text-teal-300',
}

export default function SettingsLayout() {
  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      {/* Page header */}
      <header className="flex items-center gap-3 mb-8">
        <div
          className="flex items-center justify-center w-11 h-11 rounded-2xl shrink-0"
          style={{ background: 'linear-gradient(135deg, #1b2363, #2456a6, #17b3a3)' }}
        >
          <Settings size={22} className="text-white" />
        </div>
        <div>
          <h1 className="font-display font-semibold text-2xl text-fg">Settings</h1>
          <p className="text-muted text-sm">
            Manage your profile, organisation, project, and security configuration.
          </p>
        </div>
      </header>

      {/* Two-column layout: left sub-nav + right content */}
      <div className="flex gap-8 items-start">
        {/* Left sub-nav */}
        <nav
          className="w-[200px] shrink-0 flex flex-col gap-0.5"
          aria-label="Settings navigation"
        >
          {SETTINGS_NAV.map(({ to, label, icon: Icon, description, scope }) => (
            <NavLink
              key={to}
              to={to}
              title={description}
              className={({ isActive }) =>
                [
                  'group flex flex-col gap-1 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-primary/10 text-primary dark:bg-primary/15'
                    : 'text-muted hover:text-fg hover:bg-surface-2',
                ].join(' ')
              }
            >
              {({ isActive }) => (
                <>
                  <div className="flex items-center gap-2.5">
                    <Icon
                      size={15}
                      className={`shrink-0 ${isActive ? 'text-primary' : 'text-muted group-hover:text-fg'}`}
                    />
                    <span className="truncate">{label}</span>
                    {isActive && (
                      <span className="ml-auto block w-1.5 h-1.5 rounded-full bg-primary shrink-0" />
                    )}
                  </div>
                  <span
                    className={`self-start text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded ${SCOPE_STYLE[scope] ?? SCOPE_STYLE.Account}`}
                  >
                    {scope}
                  </span>
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Right content — rendered by child routes */}
        <div className="flex-1 min-w-0">
          <Outlet />
        </div>
      </div>
    </div>
  )
}
