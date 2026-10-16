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
 *     /settings/integrations   → IntegrationsSettings
 *     /settings/ai-providers   → AiProvidersSettings (model picker overview + BYO provider keys)
 *     /settings/bridges        → BridgesSettings    (VPC / on-prem agents + tokens)
 *     /settings/security       → SecuritySettings   (org-level: embed JWT trust)
 *     /settings/usage          → UsageSettings      (open-core usage metering)
 *     /settings/mcp            → McpSettings        (MCP servers)
 *     /settings/connections    → ConnectionsSettings (connect your own Claude via MCP)
 *     /billing                 → EE billing page (link-out; only when the
 *                                 billing feature is enabled)
 *
 *   Project  (shows the active project name)
 *     /settings/project        → ProjectSettings
 *
 * The sidebar stays visible on every settings page; each section is its own
 * URL-addressable route.
 */

import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
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
  Cpu,
  Key,
  Bot,
  Sparkles,
  ChevronDown,
} from 'lucide-react'
import { useOrg } from '../../../contexts/OrgContext.jsx'
import { useProject } from '../../../contexts/ProjectContext.jsx'
import { useFeature } from '../../../lib/features.js'
import { SettingsScopeContext } from './SettingsUI.jsx'
import '../../../shell/shell.css'
import '../../../components/app/appPatterns.css'

// ---------------------------------------------------------------------------
// Nav item: 32px row, active = 2px ink marker + ink text (the sidebar's rule)
// ---------------------------------------------------------------------------

function SettingsNavItem({ to, label, Icon, end = true, external = false }) {
  return (
    <NavLink to={to} end={end} className={({ isActive }) => `st-nav-item ${isActive ? 'is-active' : ''}`}>
      <Icon size={16} strokeWidth={1.5} aria-hidden="true" className="st-nav-icon" />
      <span className="truncate">{label}</span>
      {external && <ArrowUpRight size={12} aria-hidden="true" className="ml-auto shrink-0 text-subtle" />}
    </NavLink>
  )
}

function NavGroup({ label, context = undefined, children }) {
  return (
    <div className="st-nav-group">
      <div className="st-nav-label">
        <span className="shrink-0">{label}</span>
        {context && <span className="truncate normal-case tracking-normal" title={context}>{context}</span>}
      </div>
      <div className="st-nav-items">{children}</div>
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
  const { pathname } = useLocation()
  const navigate = useNavigate()

  // The scope shown above each page title: which group the current route is in.
  const scope = pathname.startsWith('/settings/profile')
    ? 'Settings · Account'
    : pathname.startsWith('/settings/project')
      ? `Settings · Project${activeProject?.name ? ` · ${activeProject.name}` : ''}`
      : `Settings · Organization${activeOrg?.name ? ` · ${activeOrg.name}` : ''}`

  // Same sections as the grouped nav, as data, for the compact mobile select.
  const mobileGroups = [
    { label: 'Account', items: [{ to: '/settings/profile', label: 'Profile' }] },
    {
      label: 'Organization',
      items: [
        { to: '/settings/organization', label: 'General' },
        { to: '/settings/members', label: 'Members' },
        { to: '/settings/integrations', label: 'Integrations' },
        { to: '/settings/ai-providers', label: 'AI providers' },
        { to: '/settings/connections', label: 'Connections' },
        { to: '/settings/mcp', label: 'MCP servers' },
        { to: '/settings/bridges', label: 'Bridges' },
        { to: '/settings/security', label: 'Security' },
        { to: '/settings/usage', label: 'Usage' },
        { to: '/settings/access-grants', label: 'Access grants' },
        ...(billingEnabled ? [{ to: '/billing', label: 'Billing', external: true }] : []),
      ],
    },
    { label: 'Project', items: [{ to: '/settings/project', label: 'General' }] },
  ] as { label: string; items: { to: string; label: string; external?: boolean }[] }[]
  const flat = mobileGroups.flatMap((g) => g.items)
  const current = flat.find((i) => pathname === i.to || pathname.startsWith(`${i.to}/`))

  const navGroups = (
    <>
      <NavGroup label="Account">
        <SettingsNavItem to="/settings/profile" label="Profile" Icon={User} />
      </NavGroup>

      <NavGroup label="Organization" context={activeOrg?.name}>
        <SettingsNavItem to="/settings/organization" label="General"      Icon={Building2} />
        <SettingsNavItem to="/settings/members"      label="Members"      Icon={Users} />
        <SettingsNavItem to="/settings/integrations" label="Integrations" Icon={Plug} />
        <SettingsNavItem to="/settings/ai-providers" label="AI providers" Icon={Bot} />
        <SettingsNavItem to="/settings/connections"  label="Connections"  Icon={Sparkles} />
        <SettingsNavItem to="/settings/mcp"          label="MCP servers"  Icon={Cpu} />
        <SettingsNavItem to="/settings/bridges"      label="Bridges"      Icon={Network} />
        <SettingsNavItem to="/settings/security"     label="Security"     Icon={ShieldCheck} />
        <SettingsNavItem to="/settings/usage"        label="Usage"        Icon={Gauge} />
        <SettingsNavItem to="/settings/access-grants" label="Access grants" Icon={Key} />
        {billingEnabled && (
          <SettingsNavItem to="/billing" label="Billing" Icon={CreditCard} external />
        )}
      </NavGroup>

      <NavGroup label="Project" context={activeProject?.name}>
        <SettingsNavItem to="/settings/project" label="General" Icon={FolderGit2} />
      </NavGroup>
    </>
  )

  return (
    <div className="sh-page" data-width="default">
      <div className="sh-page-inner st-layout">
        {/* Below lg: one "Settings / <current>" select (grouped by scope) so the
            page content starts right under it. At lg+ the grouped left nav shows. */}
        <div className="st-strip">
          <label className="st-select" htmlFor="st-section">
            <span className="st-select-label">Settings</span>
            <span className="st-select-wrap">
              <select
                id="st-section"
                value={current?.to ?? ''}
                onChange={(e) => {
                  const item = flat.find((i) => i.to === e.target.value)
                  if (!item) return
                  if (item.external) window.location.assign(item.to)
                  else navigate(item.to)
                }}
              >
                {!current && <option value="" disabled>Choose a section</option>}
                {mobileGroups.map((g) => (
                  <optgroup key={g.label} label={g.label}>
                    {g.items.map((i) => <option key={i.to} value={i.to}>{i.label}</option>)}
                  </optgroup>
                ))}
              </select>
              <ChevronDown size={14} aria-hidden="true" />
            </span>
          </label>
        </div>

        <nav className="st-side" aria-label="Settings navigation">
          {navGroups}
        </nav>

        {/* Section content: the narrow (720px) column. Each section renders its
            own h1 through SettingsPageHeader, so there is no "Settings" h1 here. */}
        <SettingsScopeContext.Provider value={scope}>
          <div className="st-main">
            <Outlet />
          </div>
        </SettingsScopeContext.Provider>
      </div>
    </div>
  )
}
