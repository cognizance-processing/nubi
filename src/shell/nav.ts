/**
 * nav.ts — the app's information architecture, in one place.
 *
 * Read by the sidebar (grouping, order, icons, chord tooltips), Spotlight ("Go to" section)
 * and the shortcut sheet, so a route can never be listed in one and forgotten in another.
 *
 *   Home
 *   DATA     Connectors, Data
 *   BUILD    Queries, Dashboards, Flows
 *   OPERATE  Watches
 *   ———      Docs, Admin (superadmin), Settings
 */

import {
  Home,
  Plug,
  Table2,
  FileCode2,
  LayoutDashboard,
  Workflow,
  BellRing,
  BookOpen,
  Shield,
  Settings,
  type LucideIcon,
} from 'lucide-react'
import { chordKeyFor } from './shellLogic.js'

export interface NavEntry {
  label: string
  to: string
  Icon: LucideIcon
  /** The key after `g` (derived from NAV_CHORDS). */
  chord?: string
  /** Only shown to superadmins. */
  adminOnly?: boolean
  /** External-ish page that lives outside the app shell (opens in the same tab). */
  outsideShell?: boolean
}

const entry = (label: string, to: string, Icon: LucideIcon, extra: Partial<NavEntry> = {}): NavEntry => ({
  label,
  to,
  Icon,
  chord: chordKeyFor(to),
  ...extra,
})

export const NAV_HOME: NavEntry = entry('Home', '/home', Home)

export const NAV_GROUPS: { id: string; label: string; items: NavEntry[] }[] = [
  {
    id: 'data',
    label: 'Data',
    items: [entry('Connectors', '/connectors', Plug), entry('Data', '/data', Table2)],
  },
  {
    id: 'build',
    label: 'Build',
    items: [
      entry('Queries', '/queries', FileCode2),
      entry('Dashboards', '/dashboards', LayoutDashboard),
      entry('Flows', '/flows', Workflow),
    ],
  },
  { id: 'operate', label: 'Operate', items: [entry('Watches', '/watches', BellRing)] },
]

export const NAV_FOOTER: NavEntry[] = [
  entry('Docs', '/docs', BookOpen, { outsideShell: true }),
  entry('Admin', '/admin', Shield, { adminOnly: true }),
  entry('Settings', '/settings', Settings),
]

/** Every routable page, flat — Spotlight's "Go to" section. */
export const ALL_PAGES: NavEntry[] = [NAV_HOME, ...NAV_GROUPS.flatMap(g => g.items), ...NAV_FOOTER.filter(e => !e.adminOnly)]

/** "Ctrl K" on PC, "⌘K" on Apple platforms. */
export const SPOTLIGHT_KEY: string =
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent) ? '⌘K' : 'Ctrl K'
