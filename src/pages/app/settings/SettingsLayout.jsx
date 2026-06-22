/**
 * SettingsLayout — single settings area with a grouped left sidebar
 * (the Linear/Vercel pattern: one place for everything, sections grouped by
 * scope with the scope name as the group header).
 *
 * Groups & routes:
 *
 *   Account
 *     /settings/profile        → ProfileSettings
 *
 *   Organization  (shows the active org name)
 *     /settings/organization   → OrgSettings        (general)
 *     /settings/members        → MembersSettings
 *     /settings/bridges        → BridgesSettings    (VPC / on-prem agents + tokens)
 *     /settings/security       → SecuritySettings   (org-level: embed JWT trust)
 *     /settings/usage          → UsageSettings      (open-core usage metering)
 *     /billing                 → EE billing page (link-out; only when the
 *                                 billing feature is enabled)
 *
 *   Project  (shows the active project name)
 *     /settings/project        → ProjectSettings
 *
 * The sidebar stays visible on every settings page; each section is its own
 * URL-addressable route.
 */

import { NavLink, Outlet } from 'react-router-dom'
import {
  User,
  Building2,
  Users,
  ShieldCheck,
  CreditCard,
  FolderGit2,
  ArrowUpRight,
  Plug,
  Network,
  Gauge,
} from 'lucide-react'
import { useOrg } from '../../../contexts/OrgContext.jsx'
import { useProject } from '../../../contexts/ProjectContext.jsx'
import { useFeature } from '../../../lib/features.js'

// ---------------------------------------------------------------------------
// Nav item — matches the AppSidebar active style
// ---------------------------------------------------------------------------

function SettingsNavItem({ to, label, Icon, end = true, external = false }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        [
          'group flex items-center gap-2 px-2.5 py-[6px] rounded-lg text-[0.8125rem] font-medium transition-colors duration-100',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          isActive
            ? 'bg-primary/10 text-primary dark:bg-primary/15 font-semibold'
            : 'text-muted hover:text-fg hover:bg-surface-2',
        ].join(' ')
      }
    >
      {({ isActive }) => (
        <>
          <Icon
            size={14}
            aria-hidden="true"
            className={[
              'shrink-0 transition-colors',
              isActive ? 'text-primary' : 'text-muted group-hover:text-fg',
            ].join(' ')}
          />
          <span className="truncate leading-none">{label}</span>
          {external && (
            <ArrowUpRight
              size={11}
              aria-hidden="true"
              className="ml-auto shrink-0 text-muted/50 group-hover:text-muted"
            />
          )}
        </>
      )}
    </NavLink>
  )
}

function NavGroup({ label, context, children }) {
  return (
    <div>
      <div className="flex items-baseline gap-1 px-2.5 mb-1 min-w-0">
        <span className="text-[10px] font-semibold uppercase tracking-widest text-muted/60 shrink-0">
          {label}
        </span>
        {context && (
          <span className="text-[10px] text-muted/50 truncate" title={context}>
            · {context}
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-0.5 lg:flex-col lg:flex-nowrap">
        {children}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------

export default function SettingsLayout() {
  const { activeOrg } = useOrg()
  const { activeProject } = useProject()
  const billingEnabled = useFeature('billing')

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 py-8 lg:py-10">
      {/* Page header */}
      <header className="mb-8">
        <h1 className="font-display font-semibold text-2xl text-fg tracking-tight">Settings</h1>
        <p className="text-muted text-sm mt-1 leading-relaxed">
          Manage your account, organisation, and project configuration.
        </p>
      </header>

      {/* Sidebar + content */}
      <div className="flex flex-col lg:flex-row gap-8 lg:gap-12 items-start">
        <nav
          className="
            w-full lg:w-48 shrink-0 lg:sticky lg:top-6
            flex flex-col gap-5
            p-3 lg:p-0
            rounded-xl lg:rounded-none
            border border-border lg:border-0
            bg-surface lg:bg-transparent
          "
          aria-label="Settings navigation"
        >
          <NavGroup label="Account">
            <SettingsNavItem to="/settings/profile" label="Profile" Icon={User} />
          </NavGroup>

          <NavGroup label="Organization" context={activeOrg?.name}>
            <SettingsNavItem to="/settings/organization" label="General"      Icon={Building2} />
            <SettingsNavItem to="/settings/members"      label="Members"      Icon={Users} />
            <SettingsNavItem to="/settings/integrations" label="Integrations" Icon={Plug} />
            <SettingsNavItem to="/settings/bridges"      label="Bridges"      Icon={Network} />
            <SettingsNavItem to="/settings/security"     label="Security"     Icon={ShieldCheck} />
            <SettingsNavItem to="/settings/usage"        label="Usage"        Icon={Gauge} />
            {billingEnabled && (
              <SettingsNavItem to="/billing" label="Billing" Icon={CreditCard} external />
            )}
          </NavGroup>

          <NavGroup label="Project" context={activeProject?.name}>
            <SettingsNavItem to="/settings/project" label="General" Icon={FolderGit2} />
          </NavGroup>
        </nav>

        {/* Section content */}
        <div className="flex-1 min-w-0 w-full">
          <Outlet />
        </div>
      </div>
    </div>
  )
}
