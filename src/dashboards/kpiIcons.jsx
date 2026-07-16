/**
 * kpiIcons.jsx — curated icon registry for KPI cards.
 *
 * Icons come from @phosphor-icons/react (the app's SVG icon library) — NOT
 * lucide-react, which is reserved for editor/app chrome. The set is a hand-
 * picked, business/field-force-oriented subset kept small so Vite tree-shakes
 * only these components into the bundle (importing the whole Phosphor set would
 * bloat it). Adding an icon is a one-line entry here — the renderer
 * (KpiWidget) and the picker (KpiConfig) both map over this single source.
 *
 * A KPI widget selects one by key via `widget.props.icon` (e.g. 'users').
 * Unknown / unset keys render no icon (regression-safe: existing cards are
 * untouched).
 */

import {
  Users, UsersThree, MapPin, Truck, Compass, Path,
  ChartLineUp, ChartBar, TrendUp, TrendDown, Pulse, Target, Ranking, Medal,
  CurrencyDollar, Coins, Percent, Receipt, ShoppingCart,
  Gauge, Clock, Timer, Calendar,
  CheckCircle, Warning, Lightning, Fire, Star,
  Phone, Handshake, Briefcase, Buildings, Wrench, Package,
} from '@phosphor-icons/react'

/**
 * Ordered, lightly-grouped catalogue. `group` drives the picker's section
 * headers; `label` is the human name shown on hover.
 */
export const KPI_ICONS = [
  // People & field
  { key: 'users', label: 'People', group: 'People & field', Icon: Users },
  { key: 'team', label: 'Team', group: 'People & field', Icon: UsersThree },
  { key: 'map-pin', label: 'Location', group: 'People & field', Icon: MapPin },
  { key: 'truck', label: 'Delivery', group: 'People & field', Icon: Truck },
  { key: 'compass', label: 'Compass', group: 'People & field', Icon: Compass },
  { key: 'route', label: 'Route', group: 'People & field', Icon: Path },
  { key: 'phone', label: 'Calls', group: 'People & field', Icon: Phone },
  { key: 'handshake', label: 'Deals', group: 'People & field', Icon: Handshake },
  // Performance
  { key: 'chart-up', label: 'Growth', group: 'Performance', Icon: ChartLineUp },
  { key: 'chart-bar', label: 'Volume', group: 'Performance', Icon: ChartBar },
  { key: 'trend-up', label: 'Trend up', group: 'Performance', Icon: TrendUp },
  { key: 'trend-down', label: 'Trend down', group: 'Performance', Icon: TrendDown },
  { key: 'pulse', label: 'Activity', group: 'Performance', Icon: Pulse },
  { key: 'target', label: 'Target', group: 'Performance', Icon: Target },
  { key: 'ranking', label: 'Ranking', group: 'Performance', Icon: Ranking },
  { key: 'medal', label: 'Award', group: 'Performance', Icon: Medal },
  { key: 'gauge', label: 'Rate', group: 'Performance', Icon: Gauge },
  // Money
  { key: 'currency', label: 'Revenue', group: 'Money', Icon: CurrencyDollar },
  { key: 'coins', label: 'Cost', group: 'Money', Icon: Coins },
  { key: 'percent', label: 'Margin', group: 'Money', Icon: Percent },
  { key: 'receipt', label: 'Invoices', group: 'Money', Icon: Receipt },
  { key: 'cart', label: 'Orders', group: 'Money', Icon: ShoppingCart },
  // Status & time
  { key: 'check', label: 'Complete', group: 'Status & time', Icon: CheckCircle },
  { key: 'warning', label: 'Alert', group: 'Status & time', Icon: Warning },
  { key: 'bolt', label: 'Fast', group: 'Status & time', Icon: Lightning },
  { key: 'fire', label: 'Hot', group: 'Status & time', Icon: Fire },
  { key: 'star', label: 'Rating', group: 'Status & time', Icon: Star },
  { key: 'clock', label: 'Hours', group: 'Status & time', Icon: Clock },
  { key: 'timer', label: 'Duration', group: 'Status & time', Icon: Timer },
  { key: 'calendar', label: 'Schedule', group: 'Status & time', Icon: Calendar },
  // Assets
  { key: 'briefcase', label: 'Accounts', group: 'Assets', Icon: Briefcase },
  { key: 'buildings', label: 'Sites', group: 'Assets', Icon: Buildings },
  { key: 'wrench', label: 'Service', group: 'Assets', Icon: Wrench },
  { key: 'package', label: 'Stock', group: 'Assets', Icon: Package },
]

const KPI_ICON_MAP = Object.fromEntries(KPI_ICONS.map(i => [i.key, i.Icon]))

/** Resolve an icon key to its Phosphor component, or null when unset/unknown. */
export function findKpiIcon(key) {
  return (key && KPI_ICON_MAP[key]) || null
}
