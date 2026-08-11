/**
 * shared/IconPicker.jsx — grouped grid picker for a widget's Phosphor icon.
 *
 * Backed by the single curated registry in dashboards/kpiIcons.jsx (Phosphor,
 * not lucide). Used by KpiConfig (props.icon) and SectionConfig (props.icon) so
 * both surfaces offer the exact same catalogue. Selecting the active icon again
 * clears it; the "None" chip clears explicitly.
 *
 * Props:
 *   value    string|undefined   — current icon key
 *   onChange (key|undefined)=>void
 */

import { KPI_ICONS } from '../../dashboards/kpiIcons.jsx'

const btnBase = 'w-8 h-8 flex items-center justify-center rounded-lg border transition-colors'
const activeCls = 'border-primary bg-primary/10 text-primary'
const idleCls = 'border-border bg-surface text-muted hover:text-primary hover:border-primary/60'

export function IconPicker({ value, onChange }) {
  // Group the flat catalogue into its section headers, preserving order.
  const groups = []
  for (const icon of KPI_ICONS) {
    let g = groups.find(x => x.name === icon.group)
    if (!g) { g = { name: icon.group, items: [] }; groups.push(g) }
    g.items.push(icon)
  }
  return (
    <div className="space-y-2.5">
      <button
        type="button"
        onClick={() => onChange(undefined)}
        title="No icon"
        className={`${btnBase} text-[10px] font-medium ${!value ? activeCls : idleCls}`}
      >
        None
      </button>
      {groups.map(g => (
        <div key={g.name} className="space-y-1">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted/60">{g.name}</p>
          <div className="flex flex-wrap gap-1.5">
            {g.items.map(({ key, label, Icon }) => (
              <button
                key={key}
                type="button"
                onClick={() => onChange(value === key ? undefined : key)}
                title={label}
                aria-label={label}
                aria-pressed={value === key}
                className={`${btnBase} ${value === key ? activeCls : idleCls}`}
              >
                <Icon size={17} weight="duotone" />
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
