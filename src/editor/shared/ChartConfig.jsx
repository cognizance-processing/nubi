/**
 * shared/ChartConfig.jsx — Chart widget configuration panel.
 * Shared between DashboardEditor and CanvasEditor.
 *
 * Props:
 *   widget    object    — spec widget (type='chart')
 *   onChange  (w)=>void — full widget update callback
 */

import { Plus, Trash2, Database, SlidersHorizontal, BarChart3 } from 'lucide-react'
import { inputCls, selectCls, FieldLabel, Section, ToggleRow, ColumnSelect } from './inspectorPrimitives.jsx'
import { useColumnIntrospection } from './useInspectorData.js'
import { CHART_TYPES, SERIES_TYPES } from './constants.js'

// Icon map for the chart-type grid.
import {
  LineChart, BarChartHorizontal, ScatterChart, AreaChart, PieChart, Gauge, Grid3x3,
} from 'lucide-react'

const CHART_ICONS = {
  line: LineChart, bar: BarChart3, hbar: BarChartHorizontal, scatter: ScatterChart,
  area: AreaChart, pie: PieChart, donut: PieChart, heatmap: Grid3x3, gauge: Gauge,
}

/** Normalise encoding.y (string | SeriesDef[]) into an editable SeriesDef[]. */
function normalizeSeries(encY, baseType) {
  if (Array.isArray(encY)) {
    return encY.map(s => ({ col: s.col ?? '', type: s.type ?? baseType, axis: s.axis === 'right' ? 'right' : 'left' }))
  }
  if (typeof encY === 'string' && encY) return [{ col: encY, type: baseType, axis: 'left' }]
  return []
}

/** Serialise a SeriesDef[] back to the most compact encoding.y form. */
function serializeSeries(list, baseType) {
  if (list.length === 0) return ''
  if (list.length === 1 && list[0].axis !== 'right' && list[0].type === baseType) return list[0].col
  return list.map(s => ({ col: s.col, type: s.type, axis: s.axis }))
}

export function ChartConfig({ widget, onChange }) {
  const { columns, introspecting } = useColumnIntrospection(widget.query_id)
  const enc = widget.encoding ?? {}
  const props = widget.props ?? {}
  const baseType = widget.chart_type || 'bar'
  const setEncoding = (key, val) => onChange({ ...widget, encoding: { ...enc, [key]: val } })
  const setProps = (key, val) => onChange({ ...widget, props: { ...props, [key]: val } })

  const series = normalizeSeries(enc.y, baseType)
  const writeSeries = (list) =>
    onChange({ ...widget, encoding: { ...enc, y: serializeSeries(list, baseType) } })
  const setSeries = (idx, patch) => writeSeries(series.map((s, i) => i === idx ? { ...s, ...patch } : s))
  const addSeries = () => writeSeries([...series, { col: columns[0] ?? '', type: baseType, axis: 'left' }])
  const removeSeries = (idx) => writeSeries(series.filter((_, i) => i !== idx))

  const isPie = baseType === 'pie' || baseType === 'donut'
  const isHeatmap = baseType === 'heatmap'
  const isGauge = baseType === 'gauge'
  const usesSeries = !isPie && !isHeatmap && !isGauge

  return (
    <div className="space-y-3">
      <Section title="Chart type" icon={BarChart3}>
        <div className="grid grid-cols-3 gap-1.5">
          {CHART_TYPES.map(t => {
            const Icon = CHART_ICONS[t] ?? BarChart3
            const active = baseType === t
            return (
              <button key={t} onClick={() => onChange({ ...widget, chart_type: t })}
                className={`flex flex-col items-center justify-center gap-1 h-14 px-1 text-[11px] font-medium rounded-lg border capitalize transition-all focus:outline-none focus:ring-2 focus:ring-ring/50 ${
                  active ? 'bg-primary text-primary-fg border-primary shadow-sm' : 'bg-surface text-muted border-border hover:border-primary/60 hover:text-primary'
                }`}>
                <Icon size={17} className={active ? '' : 'text-muted'} />
                {t}
              </button>
            )
          })}
        </div>
      </Section>

      <Section title="Data" icon={Database}>
        {introspecting && <p className="text-xs text-muted animate-pulse">Introspecting columns…</p>}

        {isGauge ? (
          <ColumnSelect label="Value column" value={enc.value} onChange={v => setEncoding('value', v)} columns={columns} />
        ) : isHeatmap ? (
          <>
            <ColumnSelect label="X column (category)" value={enc.x} onChange={v => setEncoding('x', v)} columns={columns} />
            <ColumnSelect label="Y column (category)" value={typeof enc.y === 'string' ? enc.y : ''} onChange={v => setEncoding('y', v)} columns={columns} />
            <ColumnSelect label="Value column (heat)" value={enc.value} onChange={v => setEncoding('value', v)} columns={columns} />
          </>
        ) : (
          <>
            <ColumnSelect
              label={isPie ? 'Category column' : (baseType === 'hbar' ? 'Category (Y) column' : 'X column')}
              value={enc.x}
              onChange={v => setEncoding('x', v)}
              columns={columns}
            />

            {isPie ? (
              <ColumnSelect label="Value column" value={typeof enc.y === 'string' ? enc.y : ''} onChange={v => setEncoding('y', v)} columns={columns} />
            ) : (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <FieldLabel className="mb-0">Series (Y)</FieldLabel>
                  <button onClick={addSeries}
                    className="flex items-center gap-1 text-[11px] font-medium pl-1.5 pr-2 h-6 rounded-lg border border-dashed border-border hover:border-primary text-muted hover:text-primary transition-colors focus:outline-none focus:ring-2 focus:ring-ring/50">
                    <Plus size={12} /> Add series
                  </button>
                </div>
                {series.length === 0 && (
                  <p className="text-xs text-muted/70 rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-2 text-center">
                    No series yet — add one to plot data.
                  </p>
                )}
                {series.map((s, idx) => (
                  <div key={idx} className="rounded-lg border border-border p-2 space-y-1.5 bg-surface">
                    <div className="flex items-center gap-1.5">
                      <select className={`${selectCls} flex-1`} value={s.col || ''} onChange={e => setSeries(idx, { col: e.target.value })}>
                        {!s.col && <option value="">Select column…</option>}
                        {columns.map(c => <option key={c} value={c}>{c}</option>)}
                      </select>
                      <button onClick={() => removeSeries(idx)} title="Remove series"
                        className="w-7 h-7 shrink-0 flex items-center justify-center rounded-lg border border-transparent hover:border-red-300 hover:bg-red-50 text-muted hover:text-red-500 transition-colors">
                        <Trash2 size={13} />
                      </button>
                    </div>
                    <div className="flex gap-1.5">
                      <select className={`${selectCls} flex-1`} value={s.type} onChange={e => setSeries(idx, { type: e.target.value })}>
                        {SERIES_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                      </select>
                      <div className="flex h-8 rounded-lg border border-border overflow-hidden shrink-0">
                        {['left', 'right'].map(ax => (
                          <button key={ax} onClick={() => setSeries(idx, { axis: ax })}
                            className={`w-8 text-[11px] font-medium transition-colors ${s.axis === ax ? 'bg-primary text-primary-fg' : 'bg-surface text-muted hover:text-primary'}`}
                            title={`${ax} y-axis`}>
                            {ax === 'left' ? 'L' : 'R'}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {usesSeries && (
              <ColumnSelect label="Group / color column" value={enc.color} onChange={v => setEncoding('color', v)} columns={columns} optional />
            )}
          </>
        )}
      </Section>

      {(usesSeries || isGauge) && (
        <Section title="Display" defaultOpen={false} icon={SlidersHorizontal}>
          {usesSeries && (
            <ToggleRow label="Stack series" hint="Bar / line / area share a stack"
              checked={props.stack === true || typeof props.stack === 'string'}
              onChange={v => setProps('stack', v)} />
          )}
          {isGauge && (
            <div>
              <FieldLabel>Max (gauge range)</FieldLabel>
              <input type="number" className={inputCls} value={props.max ?? ''} placeholder="auto (value × 1.5)"
                onChange={e => setProps('max', e.target.value === '' ? undefined : (parseFloat(e.target.value) || undefined))} />
            </div>
          )}
          <div>
            <FieldLabel>Height (px)</FieldLabel>
            <input type="number" min={120} max={1200} className={inputCls} value={props.height ?? 260}
              onChange={e => setProps('height', parseInt(e.target.value, 10) || 260)} />
          </div>
        </Section>
      )}
    </div>
  )
}
