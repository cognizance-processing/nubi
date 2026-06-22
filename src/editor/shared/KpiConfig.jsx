/**
 * shared/KpiConfig.jsx — KPI and Metric widget configuration panel.
 * Shared between DashboardEditor and CanvasEditor.
 *
 * Props:
 *   widget    object    — spec widget (type='kpi' or type='metric')
 *   onChange  (w)=>void — full widget update callback
 */

import { Database, TrendingUp } from 'lucide-react'
import { inputCls, selectCls, FieldLabel, Section, ColumnSelect } from './inspectorPrimitives.jsx'
import { useColumnIntrospection } from './useInspectorData.js'

export function KpiConfig({ widget, onChange }) {
  const { columns, introspecting } = useColumnIntrospection(widget.query_id)
  const enc = widget.encoding ?? {}
  const props = widget.props ?? {}
  const setEncoding = (key, val) => onChange({ ...widget, encoding: { ...enc, [key]: val } })
  const setProps = (key, val) => onChange({ ...widget, props: { ...props, [key]: val } })
  const deltaFormat = props.deltaFormat || 'percent'

  return (
    <div className="space-y-3">
      <Section title="Data" icon={Database}>
        {introspecting && <p className="text-xs text-muted animate-pulse">Introspecting columns…</p>}
        <ColumnSelect label="Value column" value={enc.value} onChange={v => setEncoding('value', v)} columns={columns} />
        <div>
          <FieldLabel>Label</FieldLabel>
          <input type="text" className={inputCls} value={props.label ?? ''} placeholder="e.g. Total revenue"
            onChange={e => setProps('label', e.target.value)} />
        </div>
        <div>
          <FieldLabel>Format</FieldLabel>
          <select className={selectCls} value={props.format ?? 'number'} onChange={e => setProps('format', e.target.value)}>
            {['number', 'integer', 'percent', 'currency'].map(f => <option key={f} value={f}>{f}</option>)}
          </select>
        </div>
      </Section>

      <Section title="Delta & sparkline" defaultOpen={false} icon={TrendingUp}>
        <ColumnSelect label="Comparison column" value={enc.compare} onChange={v => setEncoding('compare', v)} columns={columns} optional />
        {enc.compare && (
          <div>
            <FieldLabel>Delta format</FieldLabel>
            <div className="flex h-8 rounded-lg border border-border overflow-hidden">
              {['percent', 'absolute'].map(f => (
                <button key={f} onClick={() => setProps('deltaFormat', f)}
                  className={`flex-1 text-[11px] font-medium capitalize transition-colors ${deltaFormat === f ? 'bg-primary text-primary-fg' : 'bg-surface text-muted hover:text-primary'}`}>
                  {f}
                </button>
              ))}
            </div>
          </div>
        )}
        <ColumnSelect label="Sparkline column" value={enc.spark} onChange={v => setEncoding('spark', v)} columns={columns} optional />
      </Section>
    </div>
  )
}
