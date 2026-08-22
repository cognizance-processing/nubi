/**
 * shared/ChartConfig.jsx — Chart widget configuration panel.
 * Shared editor primitives (used by DashboardEditor).
 *
 * Exposes all 17 chart types supported by embed/widgets/chart-options.js and
 * the full config schema: legend, stacking, orientation, data labels, palette,
 * axis labels/log/min-max/format, secondary y-axis, reference lines.
 *
 * Spec persistence:
 *   widget.chart_type  — selected chart type
 *   widget.encoding    — column-name mapping (x, y, y2, color, value, size,
 *                        source, target, low, high, open, close, lower, upper)
 *   widget.config      — declarative customization (deep-merged into ECharts option)
 *   widget.props       — legacy container (height only, for backward-compat)
 *
 * Props:
 *   widget    object    — spec widget (type='chart')
 *   onChange  (w)=>void — full widget update callback
 */

import { Plus, Trash2, Database, SlidersHorizontal, BarChart3, Layers, Map as MapIcon } from 'lucide-react'
import {
  inputCls, selectCls, FieldLabel, Section, ToggleRow, ColumnSelect, ColorSwatch,
} from './inspectorPrimitives.jsx'
import { useColumnIntrospection } from './useInspectorData.js'
import { CHART_TYPES, SERIES_TYPES } from './constants.js'
import { titleText, withTitleText } from './titleValue.js'
import { registeredMaps, mapLabel, mapCategory } from '../../lib/maps.js'

// Lucide icon map for the chart-type picker grid
import {
  LineChart, BarChartHorizontal, ScatterChart, AreaChart, PieChart, Gauge,
  Grid3x3, GitCommitHorizontal, TrendingDown, Waves, Network, LayoutDashboard,
  Radar, CandlestickChart, Wind,
} from 'lucide-react'

const CHART_ICONS = {
  bar:         BarChart3,
  line:        LineChart,
  area:        AreaChart,
  scatter:     ScatterChart,
  bubble:      ScatterChart,
  pie:         PieChart,
  donut:       PieChart,
  sankey:      GitCommitHorizontal,
  funnel:      TrendingDown,
  waterfall:   Waves,
  treemap:     LayoutDashboard,
  heatmap:     Grid3x3,
  radar:       Radar,
  boxplot:     BarChartHorizontal,
  gauge:       Gauge,
  candlestick: CandlestickChart,
  fan:         Wind,
  combo:       Layers,
  map:         MapIcon,
}

// Human-readable labels for chart types
const CHART_LABELS = {
  bar:         'Bar',
  line:        'Line',
  area:        'Area',
  scatter:     'Scatter',
  bubble:      'Bubble',
  pie:         'Pie',
  donut:       'Donut',
  sankey:      'Sankey',
  funnel:      'Funnel',
  waterfall:   'Waterfall',
  treemap:     'Treemap',
  heatmap:     'Heatmap',
  radar:       'Radar',
  boxplot:     'Box Plot',
  gauge:       'Gauge',
  candlestick: 'Candle',
  fan:         'Fan',
  combo:       'Combo',
  map:         'Map',
}

// Per-type encoding field definitions
// Each entry: { key, label, required?, hint? }
const TYPE_ENCODING = {
  // A choropleth needs the region NAME column (matched against the registered
  // GeoJSON feature names) and the value that colours it.
  map:         [{ key: 'name', label: 'Region column' }, { key: 'value', label: 'Value column' }],
  bar:         [{ key: 'x', label: 'X (category)' }, { key: 'y', label: 'Y (value)' }, { key: 'color', label: 'Group / color', optional: true }],
  line:        [{ key: 'x', label: 'X' }, { key: 'y', label: 'Y' }, { key: 'y2', label: 'Y2 (secondary axis)', optional: true }, { key: 'color', label: 'Group / color', optional: true }],
  area:        [{ key: 'x', label: 'X' }, { key: 'y', label: 'Y' }, { key: 'color', label: 'Group / color', optional: true }],
  scatter:     [{ key: 'x', label: 'X' }, { key: 'y', label: 'Y' }, { key: 'color', label: 'Group / color', optional: true }],
  bubble:      [{ key: 'x', label: 'X' }, { key: 'y', label: 'Y' }, { key: 'size', label: 'Size column', optional: true }, { key: 'color', label: 'Group / color', optional: true }],
  pie:         [{ key: 'x', label: 'Category column' }, { key: 'y', label: 'Value column' }],
  donut:       [{ key: 'x', label: 'Category column' }, { key: 'y', label: 'Value column' }],
  sankey:      [{ key: 'source', label: 'Source column' }, { key: 'target', label: 'Target column' }, { key: 'value', label: 'Value column' }],
  funnel:      [{ key: 'x', label: 'Stage / label column' }, { key: 'y', label: 'Value column' }],
  waterfall:   [{ key: 'x', label: 'Category column' }, { key: 'y', label: 'Delta (change) column' }],
  treemap:     [{ key: 'x', label: 'Name column' }, { key: 'y', label: 'Size (value) column' }, { key: 'color', label: 'Group column', optional: true }],
  heatmap:     [{ key: 'x', label: 'X column' }, { key: 'color', label: 'Y column' }, { key: 'value', label: 'Value column' }],
  radar:       [{ key: 'x', label: 'Indicator column' }, { key: 'y', label: 'Value column' }, { key: 'color', label: 'Series / group', optional: true }],
  boxplot:     [{ key: 'x', label: 'Category column' }, { key: 'y', label: 'Values column (raw — auto-boxed)' }],
  gauge:       [{ key: 'y', label: 'Value column' }],
  candlestick: [{ key: 'x', label: 'Date / time column' }, { key: 'open', label: 'Open column' }, { key: 'close', label: 'Close column' }, { key: 'low', label: 'Low column' }, { key: 'high', label: 'High column' }],
  fan:         [{ key: 'x', label: 'X (time) column' }, { key: 'y', label: 'Forecast (midline) column' }, { key: 'lower', label: 'Lower bound column', optional: true }, { key: 'upper', label: 'Upper bound column', optional: true }],
}

// Chart types that use an x/y cartesian axis system (show axis config +
// reference lines). Combo is cartesian too — buildCombo() honours axes and
// decorateCartesian() applies its reference lines.
const CARTESIAN_TYPES = new Set(['bar', 'line', 'area', 'scatter', 'bubble', 'waterfall', 'boxplot', 'candlestick', 'fan', 'combo'])
// Chart types that support stacking (combo stacks its bar series; lines stay lines)
const STACKABLE_TYPES = new Set(['bar', 'line', 'area', 'combo'])
// Chart types that support orientation (horizontal)
const ORIENTABLE_TYPES = new Set(['bar', 'funnel'])
// Chart types that support dual y-axis (combo is inherently dual: bars left, lines right)
const DUAL_AXIS_TYPES = new Set(['line', 'area', 'combo'])

const VALUE_FORMAT_OPTIONS = [
  { value: '', label: '— auto —' },
  { value: 'number', label: 'Number' },
  { value: 'currency', label: 'Currency' },
  { value: 'percent', label: 'Percent' },
  { value: 'si', label: 'SI (1k, 1M …)' },
  { value: 'date', label: 'Date' },
]

const LEGEND_POSITIONS = ['top', 'bottom', 'left', 'right']

/**
 * SeriesList — an ordered list of column pickers for combo bar/line series.
 * Writes an array of column names (the shape buildCombo reads from
 * encoding.bars / encoding.lines). Columns already chosen are still selectable
 * again (a column can legitimately appear once); duplicates are harmless.
 */
function SeriesList({ label, hint, series, columns, onChange }) {
  const update = (i, val) => {
    if (!val) { onChange(series.filter((_, idx) => idx !== i)); return }
    onChange(series.map((c, idx) => (idx === i ? val : c)))
  }
  const remove = (i) => onChange(series.filter((_, idx) => idx !== i))
  const move = (i, dir) => {
    const j = i + dir
    if (j < 0 || j >= series.length) return
    const next = series.slice()
    ;[next[i], next[j]] = [next[j], next[i]]
    onChange(next)
  }
  const available = columns.filter(c => !series.includes(c))
  return (
    <div className="space-y-1">
      <FieldLabel>{label}</FieldLabel>
      {series.length === 0 && (
        <p className="text-[10px] text-muted/70">{hint}. None yet — add one below.</p>
      )}
      {series.map((col, i) => (
        <div key={`${col}-${i}`} className="flex items-center gap-1">
          <div className="flex flex-col">
            <button type="button" onClick={() => move(i, -1)} disabled={i === 0}
              className="h-3 leading-none text-muted/60 hover:text-primary disabled:opacity-30" title="Move up">▲</button>
            <button type="button" onClick={() => move(i, 1)} disabled={i === series.length - 1}
              className="h-3 leading-none text-muted/60 hover:text-primary disabled:opacity-30" title="Move down">▼</button>
          </div>
          <select className={`${selectCls} flex-1`} value={col} onChange={e => update(i, e.target.value)}>
            {columns.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
          <button type="button" onClick={() => remove(i)}
            className="w-7 h-7 shrink-0 flex items-center justify-center rounded-lg text-muted hover:text-red-500 hover:bg-red-500/10 transition-colors" title="Remove">
            <Trash2 size={12} />
          </button>
        </div>
      ))}
      {available.length > 0 && (
        <select className={selectCls} value="" onChange={e => { if (e.target.value) onChange([...series, e.target.value]) }}>
          <option value="">+ Add {label.toLowerCase().replace(/ series$/, '')} series…</option>
          {available.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      )}
    </div>
  )
}

export function ChartConfig({ widget, onChange }) {
  const { columns, introspecting } = useColumnIntrospection(widget.query_id)
  const enc = widget.encoding ?? {}
  const cfg = widget.config ?? {}
  const wProps = widget.props ?? {}
  const baseType = widget.chart_type || 'bar'

  const setEncoding = (key, val) => onChange({ ...widget, encoding: { ...enc, [key]: val } })
  const setConfig = (key, val) => onChange({ ...widget, config: { ...cfg, [key]: val } })
  const setProps = (key, val) => onChange({ ...widget, props: { ...wProps, [key]: val } })
  const setAxisConfig = (axis, key, val) => {
    const prev = cfg[axis] ?? {}
    onChange({ ...widget, config: { ...cfg, [axis]: { ...prev, [key]: val } } })
  }

  const encodingFields = TYPE_ENCODING[baseType] ?? TYPE_ENCODING.bar

  const isCombo = baseType === 'combo'
  const isDonutLike = baseType === 'donut' || baseType === 'pie'
  // Combo series lists (encoding.bars / encoding.lines are column-name arrays,
  // exactly as buildCombo() reads them).
  const barSeries = Array.isArray(enc.bars) ? enc.bars : []
  const lineSeries = Array.isArray(enc.lines) ? enc.lines : []
  const setSeries = (key, arr) => setEncoding(key, arr.length ? arr : undefined)

  const isCartesian = CARTESIAN_TYPES.has(baseType)
  const isStackable = STACKABLE_TYPES.has(baseType)
  const isOrientable = ORIENTABLE_TYPES.has(baseType)
  const isDualAxis = DUAL_AXIS_TYPES.has(baseType)
  const isGauge = baseType === 'gauge'
  const isMap = baseType === 'map'
  const availableMaps = registeredMaps()
    .map(m => ({ value: m, label: mapLabel(m), category: mapCategory(m) }))
    .sort((a, b) => a.label.localeCompare(b.label))
  const MAP_CATEGORY_LABELS = { world: 'World', continent: 'Continents', country: 'Countries', other: 'Other' }
  const MAP_CATEGORY_ORDER = ['world', 'continent', 'country', 'other']
  const mapGroups = MAP_CATEGORY_ORDER
    .map(cat => ({ cat, label: MAP_CATEGORY_LABELS[cat], options: availableMaps.filter(m => m.category === cat) }))
    .filter(g => g.options.length > 0)

  // Reference lines state helpers
  const refLines = Array.isArray(cfg.referenceLines) ? cfg.referenceLines : []
  const addRefLine = () => setConfig('referenceLines', [...refLines, { value: 0, axis: 'y', label: '', type: 'dashed' }])
  const removeRefLine = (i) => setConfig('referenceLines', refLines.filter((_, idx) => idx !== i))
  const updateRefLine = (i, patch) => setConfig('referenceLines', refLines.map((r, idx) => idx === i ? { ...r, ...patch } : r))

  return (
    <div className="space-y-3">
      {/* ── Chart type picker ── */}
      <Section title="Chart type" icon={BarChart3}>
        <div className="grid grid-cols-3 gap-1.5">
          {CHART_TYPES.map(t => {
            const Icon = CHART_ICONS[t] ?? BarChart3
            const label = CHART_LABELS[t] ?? t
            const active = baseType === t
            return (
              <button
                key={t}
                onClick={() => onChange({ ...widget, chart_type: t })}
                className={`flex flex-col items-center justify-center gap-1 h-14 px-1 text-[10px] font-medium rounded-lg border capitalize transition-all focus:outline-none focus:ring-2 focus:ring-ring/50 ${
                  active
                    ? 'bg-primary text-primary-fg border-primary shadow-sm'
                    : 'bg-surface text-muted border-border hover:border-primary/60 hover:text-primary'
                }`}
              >
                <Icon size={16} className={active ? '' : 'text-muted'} />
                {label}
              </button>
            )
          })}
        </div>
      </Section>

      {/* ── Data / encoding ── */}
      <Section title="Data" icon={Database}>
        {introspecting && (
          <p className="text-xs text-muted animate-pulse">Introspecting columns…</p>
        )}
        {isCombo ? (
          <>
            <ColumnSelect label="X (category)" value={enc.x ?? ''} onChange={v => setEncoding('x', v)} columns={columns} />
            <SeriesList
              label="Bar series"
              hint="Plotted as bars on the left axis"
              series={barSeries}
              columns={columns}
              onChange={arr => setSeries('bars', arr)}
            />
            <SeriesList
              label="Line series"
              hint="Plotted as lines on the right axis"
              series={lineSeries}
              columns={columns}
              onChange={arr => setSeries('lines', arr)}
            />
          </>
        ) : (
          encodingFields.map(({ key, label, optional }) => (
            <ColumnSelect
              key={key}
              label={label}
              value={enc[key] ?? ''}
              onChange={v => setEncoding(key, v)}
              columns={columns}
              optional={!!optional}
            />
          ))
        )}
      </Section>

      {/* ── Display customization ── */}
      <Section title="Display" defaultOpen={false} icon={SlidersHorizontal}>
        {/* Stacking */}
        {isStackable && (
          <div>
            <FieldLabel>Stacking</FieldLabel>
            <select
              className={selectCls}
              value={cfg.stack === 'percent' ? 'percent' : (cfg.stack ? 'normal' : '')}
              onChange={e => setConfig('stack', e.target.value || false)}
            >
              <option value="">None</option>
              <option value="normal">Stacked</option>
              <option value="percent">100% stacked</option>
            </select>
          </div>
        )}

        {/* Orientation */}
        {isOrientable && (
          <div>
            <FieldLabel>Orientation</FieldLabel>
            <select
              className={selectCls}
              value={cfg.orientation || 'vertical'}
              onChange={e => setConfig('orientation', e.target.value)}
            >
              <option value="vertical">Vertical</option>
              <option value="horizontal">Horizontal</option>
            </select>
          </div>
        )}

        {/* Gauge max */}
        {isGauge && (
          <div>
            <FieldLabel>Max (gauge range)</FieldLabel>
            <input
              type="number"
              className={inputCls}
              value={cfg.yAxis?.max ?? wProps.max ?? ''}
              placeholder="auto"
              onChange={e => setAxisConfig('yAxis', 'max', e.target.value === '' ? undefined : (parseFloat(e.target.value) || undefined))}
            />
          </div>
        )}

        {/* Map geography (choropleth region set) */}
        {isMap && (
          <div>
            <FieldLabel>Geography</FieldLabel>
            <select
              className={selectCls}
              value={cfg.map ?? ''}
              onChange={e => setConfig('map', e.target.value || undefined)}
            >
              <option value="">— select a registered map —</option>
              {mapGroups.map(g => (
                <optgroup key={g.cat} label={g.label}>
                  {g.options.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
                </optgroup>
              ))}
            </select>
            {availableMaps.length === 0 && (
              <p className="text-[10px] text-muted/70 mt-1">
                No GeoJSON is registered yet — a developer needs to add one via
                registerMapGeoJson() / registerLazyMap() in src/lib/maps/index.js.
              </p>
            )}
          </div>
        )}

        {/* Pan / zoom (off by default so a map tile doesn't swallow board scroll) */}
        {isMap && (
          <ToggleRow
            label="Pan & zoom"
            hint="Let viewers drag and scroll-zoom the map"
            checked={!!cfg.roam}
            onChange={v => setConfig('roam', v || undefined)}
          />
        )}

        {/* Legend */}
        <div>
          <FieldLabel>Legend</FieldLabel>
          <div className="flex gap-1.5">
            <select
              className={`${selectCls} flex-1`}
              value={cfg.legend === false ? 'none' : ((cfg.legend?.position) || 'top')}
              onChange={e => {
                if (e.target.value === 'none') setConfig('legend', false)
                else setConfig('legend', { position: e.target.value })
              }}
            >
              <option value="none">Hidden</option>
              {LEGEND_POSITIONS.map(p => <option key={p} value={p}>{p.charAt(0).toUpperCase() + p.slice(1)}</option>)}
            </select>
          </div>
        </div>

        {/* Data labels */}
        <ToggleRow
          label="Data labels"
          hint="Show values on chart elements"
          checked={!!cfg.dataLabels}
          onChange={v => setConfig('dataLabels', v || false)}
        />

        {/* Center label (donut / pie) */}
        {isDonutLike && (
          <ToggleRow
            label="Center label"
            hint="Show the first slice's share in the ring's hole"
            checked={!!cfg.centerLabel}
            onChange={v => setConfig('centerLabel', v || undefined)}
          />
        )}

        {/* Smooth lines (line/area/fan and combo line series) */}
        {(baseType === 'line' || baseType === 'area' || baseType === 'fan' || baseType === 'combo') && (
          <ToggleRow
            label="Smooth curves"
            checked={cfg.smooth !== false}
            onChange={v => setConfig('smooth', v)}
          />
        )}

        {/* Custom palette */}
        <div>
          <FieldLabel>Custom palette (comma-separated hex)</FieldLabel>
          <input
            type="text"
            className={inputCls}
            placeholder="e.g. #6366f1, #f59e0b, #10b981"
            value={Array.isArray(cfg.palette) ? cfg.palette.join(', ') : ''}
            onChange={e => {
              const raw = e.target.value.trim()
              if (!raw) { setConfig('palette', undefined); return }
              const colors = raw.split(',').map(s => s.trim()).filter(Boolean)
              setConfig('palette', colors.length ? colors : undefined)
            }}
          />
        </div>

        {/* Chart title */}
        <div>
          <FieldLabel>Title</FieldLabel>
          <input
            type="text"
            className={inputCls}
            placeholder="Optional chart title"
            value={titleText(cfg.title)}
            onChange={e => setConfig('title', withTitleText(cfg.title, e.target.value))}
          />
        </div>

        {/* Height */}
        <div>
          <FieldLabel>Height (px)</FieldLabel>
          <input
            type="number"
            min={120}
            max={1200}
            className={inputCls}
            value={wProps.height ?? 260}
            onChange={e => setProps('height', parseInt(e.target.value, 10) || 260)}
          />
        </div>
      </Section>

      {/* ── Axis configuration (cartesian only) ── */}
      {isCartesian && (
        <Section title="Axes" defaultOpen={false} icon={Layers}>
          {/* X axis */}
          <p className="text-[10px] font-semibold text-muted/70 uppercase tracking-wide">X axis</p>
          <div>
            <FieldLabel>Label</FieldLabel>
            <input
              type="text"
              className={inputCls}
              placeholder="X axis label"
              value={cfg.xAxis?.label ?? ''}
              onChange={e => setAxisConfig('xAxis', 'label', e.target.value || undefined)}
            />
          </div>
          <div>
            <FieldLabel>Value format</FieldLabel>
            <select
              className={selectCls}
              value={cfg.xAxis?.format ?? ''}
              onChange={e => setAxisConfig('xAxis', 'format', e.target.value || undefined)}
            >
              {VALUE_FORMAT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>

          {/* Y axis */}
          <p className="text-[10px] font-semibold text-muted/70 uppercase tracking-wide mt-1">Y axis</p>
          <div>
            <FieldLabel>Label</FieldLabel>
            <input
              type="text"
              className={inputCls}
              placeholder="Y axis label"
              value={cfg.yAxis?.label ?? ''}
              onChange={e => setAxisConfig('yAxis', 'label', e.target.value || undefined)}
            />
          </div>
          <div className="flex gap-1.5">
            <div className="flex-1">
              <FieldLabel>Min</FieldLabel>
              <input
                type="number"
                className={inputCls}
                placeholder="auto"
                value={cfg.yAxis?.min ?? ''}
                onChange={e => setAxisConfig('yAxis', 'min', e.target.value === '' ? undefined : parseFloat(e.target.value))}
              />
            </div>
            <div className="flex-1">
              <FieldLabel>Max</FieldLabel>
              <input
                type="number"
                className={inputCls}
                placeholder="auto"
                value={cfg.yAxis?.max ?? ''}
                onChange={e => setAxisConfig('yAxis', 'max', e.target.value === '' ? undefined : parseFloat(e.target.value))}
              />
            </div>
          </div>
          <ToggleRow
            label="Log scale"
            checked={cfg.yAxis?.log === true}
            onChange={v => setAxisConfig('yAxis', 'log', v || undefined)}
          />
          <div>
            <FieldLabel>Value format</FieldLabel>
            <select
              className={selectCls}
              value={cfg.yAxis?.format ?? ''}
              onChange={e => setAxisConfig('yAxis', 'format', e.target.value || undefined)}
            >
              {VALUE_FORMAT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>

          {/* Secondary Y axis */}
          {isDualAxis && (
            <>
              <p className="text-[10px] font-semibold text-muted/70 uppercase tracking-wide mt-1">Secondary Y axis</p>
              <div>
                <FieldLabel>Label</FieldLabel>
                <input
                  type="text"
                  className={inputCls}
                  placeholder="Right axis label"
                  value={cfg.y2Axis?.label ?? ''}
                  onChange={e => setAxisConfig('y2Axis', 'label', e.target.value || undefined)}
                />
              </div>
              <div>
                <FieldLabel>Value format</FieldLabel>
                <select
                  className={selectCls}
                  value={cfg.y2Axis?.format ?? ''}
                  onChange={e => setAxisConfig('y2Axis', 'format', e.target.value || undefined)}
                >
                  {VALUE_FORMAT_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
            </>
          )}
        </Section>
      )}

      {/* ── Reference lines ── */}
      {isCartesian && (
        <Section title="Reference lines" defaultOpen={false} icon={BarChart3}>
          {refLines.length === 0 && (
            <p className="text-xs text-muted/70 text-center rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-2">
              No reference lines — add one to mark a target or threshold.
            </p>
          )}
          {refLines.map((r, i) => (
            <div key={i} className="rounded-lg border border-border p-2 space-y-1.5 bg-surface">
              <div className="flex items-center gap-1.5">
                <input
                  type="number"
                  className={`${inputCls} flex-1`}
                  placeholder="Value"
                  value={r.value ?? ''}
                  onChange={e => updateRefLine(i, { value: e.target.value === '' ? 0 : parseFloat(e.target.value) })}
                />
                <select
                  className={selectCls}
                  style={{ width: 64 }}
                  value={r.axis || 'y'}
                  onChange={e => updateRefLine(i, { axis: e.target.value })}
                >
                  <option value="y">Y</option>
                  <option value="x">X</option>
                </select>
                <button
                  onClick={() => removeRefLine(i)}
                  className="w-7 h-7 shrink-0 flex items-center justify-center rounded-lg border border-transparent hover:border-red-300 hover:bg-red-50 text-muted hover:text-red-500 transition-colors"
                  title="Remove"
                >
                  <Trash2 size={12} />
                </button>
              </div>
              <input
                type="text"
                className={inputCls}
                placeholder="Label (optional)"
                value={r.label ?? ''}
                onChange={e => updateRefLine(i, { label: e.target.value || '' })}
              />
              <div className="flex gap-1.5">
                <select
                  className={`${selectCls} flex-1`}
                  value={r.type || 'dashed'}
                  onChange={e => updateRefLine(i, { type: e.target.value })}
                >
                  <option value="dashed">Dashed</option>
                  <option value="solid">Solid</option>
                </select>
                <ColorSwatch
                  title="Line color"
                  value={r.color}
                  onChange={v => updateRefLine(i, { color: v })}
                />
              </div>
            </div>
          ))}
          <button
            onClick={addRefLine}
            className="flex items-center gap-1 text-[11px] font-medium pl-1.5 pr-2 h-7 rounded-lg border border-dashed border-border hover:border-primary text-muted hover:text-primary transition-colors focus:outline-none focus:ring-2 focus:ring-ring/50 w-full justify-center"
          >
            <Plus size={12} /> Add reference line
          </button>
        </Section>
      )}
    </div>
  )
}
