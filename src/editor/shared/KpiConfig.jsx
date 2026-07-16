/**
 * shared/KpiConfig.jsx — KPI and Metric widget configuration panel.
 * Shared editor primitives (used by DashboardEditor).
 *
 * Props:
 *   widget    object    — spec widget (type='kpi' or type='metric')
 *   onChange  (w)=>void — full widget update callback
 */

import { Database, TrendingUp, Gauge, Shapes } from 'lucide-react'
import { inputCls, selectCls, FieldLabel, Section, ToggleRow, ColumnSelect, ColorField } from './inspectorPrimitives.jsx'
import { useColumnIntrospection } from './useInspectorData.js'
import { IconPicker } from './IconPicker.jsx'

export function KpiConfig({ widget, onChange }) {
  const { columns, introspecting } = useColumnIntrospection(widget.query_id)
  const enc = widget.encoding ?? {}
  const props = widget.props ?? {}
  const style = widget.style ?? {}
  const setEncoding = (key, val) => onChange({ ...widget, encoding: { ...enc, [key]: val } })
  const setProps = (key, val) => onChange({ ...widget, props: { ...props, [key]: val } })
  const deltaFormat = props.deltaFormat || 'percent'

  // Accent color lives on props.accentColor (falls back to style.color at render
  // time); the picker writes props.accentColor so it's independent of the card's
  // overall text color.
  const accent = props.accentColor ?? (typeof style.color === 'string' ? style.color : '') ?? ''

  // Progress bar: props.progress = { segments, target }. `target` is the value
  // at a full bar (100 for a 0–100 metric, 1 for a 0–1 fraction). Toggling on
  // seeds a sensible default keyed to the chosen format.
  const progress = props.progress
  const setProgressEnabled = (on) => {
    if (on) {
      const target = props.format === 'percent' ? 1 : 100
      setProps('progress', { segments: 10, target })
    } else {
      setProps('progress', undefined)
    }
  }
  const setProgressField = (key, val) => setProps('progress', { ...(progress ?? { segments: 10, target: 100 }), [key]: val })

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
        <div>
          <FieldLabel>Label position</FieldLabel>
          <select
            className={selectCls}
            value={props.labelPosition === 'top' ? 'top' : 'bottom'}
            onChange={e => setProps('labelPosition', e.target.value === 'top' ? 'top' : undefined)}
          >
            <option value="bottom">Below value</option>
            <option value="top">Above value (eyebrow)</option>
          </select>
        </div>
      </Section>

      <Section title="Icon" defaultOpen={false} icon={Shapes}>
        <IconPicker value={props.icon} onChange={v => setProps('icon', v)} />
        <p className="text-[10px] text-muted/70">A duotone badge above the value, tinted with the accent color. Pairs well with the Glass card style.</p>
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

      <Section title="Progress & accent" defaultOpen={false} icon={Gauge}>
        <div>
          <FieldLabel>Accent color</FieldLabel>
          <ColorField
            value={accent}
            onChange={v => setProps('accentColor', v)}
            placeholder="inherit (theme default)"
          />
          <p className="text-[10px] text-muted/70 mt-1">Tints the headline number and progress bar. Leave blank to inherit the card text color.</p>
        </div>

        <ToggleRow
          label="Progress bar"
          hint="Segmented bar showing value ÷ target"
          checked={!!progress}
          onChange={setProgressEnabled}
        />
        {progress && (
          <div>
            <FieldLabel>Style</FieldLabel>
            <div className="flex h-8 rounded-lg border border-border overflow-hidden">
              {[['segments', 'Segments'], ['bar', 'Continuous']].map(([v, lbl]) => (
                <button key={v} onClick={() => setProgressField('style', v === 'bar' ? 'bar' : undefined)}
                  className={`flex-1 text-[11px] font-medium transition-colors ${(progress.style === 'bar' ? 'bar' : 'segments') === v ? 'bg-primary text-primary-fg' : 'bg-surface text-muted hover:text-primary'}`}>
                  {lbl}
                </button>
              ))}
            </div>
          </div>
        )}
        {progress && (
          <div className="flex gap-1.5">
            <div className="flex-1">
              <FieldLabel>Target (100% fill)</FieldLabel>
              <input type="number" className={inputCls} placeholder="100"
                value={progress.target ?? ''}
                onChange={e => setProgressField('target', e.target.value === '' ? undefined : parseFloat(e.target.value))} />
            </div>
            {progress.style !== 'bar' && (
              <div className="flex-1">
                <FieldLabel>Segments</FieldLabel>
                <input type="number" min={2} max={40} className={inputCls} placeholder="10"
                  value={progress.segments ?? ''}
                  onChange={e => setProgressField('segments', e.target.value === '' ? undefined : (parseInt(e.target.value, 10) || undefined))} />
              </div>
            )}
          </div>
        )}
        {progress && (
          <p className="text-[10px] text-muted/70">Target = the value at a full bar. Use 100 for a 0–100 metric, or 1 for a 0–1 fraction.</p>
        )}
      </Section>
    </div>
  )
}
