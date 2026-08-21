/**
 * shared/BackgroundEditor.jsx — Background descriptor editor used for
 * dashboard/tab/widget backgrounds.
 * Shared editor primitives (used by DashboardEditor).
 *
 * Props:
 *   value    object|undefined  — background descriptor { type, color, from, to, ... }
 *   onChange (bg)=>void        — called with the updated descriptor
 */

import { inputCls, ColorField, ColorSwatch } from './inspectorPrimitives.jsx'
import { BACKGROUND_TYPES } from './constants.js'

export function BackgroundEditor({ value, onChange }) {
  const bg = value ?? {}
  const type = bg.type ?? 'none'
  const set = (patch) => onChange({ ...bg, ...patch })
  return (
    <div className="space-y-2">
      {/* flex-wrap, not a fixed grid: "transparent"/"gradient" don't fit an
          equal 1/5-1/6 column at readable size, so match the STYLE preset
          chips above — natural width per chip, extras wrap to the next row
          instead of getting clipped mid-word. */}
      <div className="flex flex-wrap gap-1.5">
        {BACKGROUND_TYPES.map(t => (
          <button key={t} type="button" onClick={() => set({ type: t === 'none' ? undefined : t })}
            className={`px-3 py-1.5 text-xs font-medium rounded-lg border capitalize transition-all duration-150 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 ${
              (type === t || (t === 'none' && !bg.type))
                ? 'bg-primary text-primary-fg border-primary shadow-sm'
                : 'bg-surface text-muted border-border hover:border-primary/60 hover:text-fg hover:bg-surface-2'
            }`}>{t}</button>
        ))}
      </div>
      {type === 'solid' && (
        <ColorField
          value={bg.color}
          onChange={v => set({ color: v })}
          placeholder="#0b0f1a or any CSS color"
          fallback="#0b0f1a"
          clearable={false}
        />
      )}
      {type === 'gradient' && (
        <div className="flex items-center gap-2">
          <ColorSwatch value={bg.from} onChange={v => set({ from: v })} fallback="#6366f1" title="Gradient start" />
          <span className="text-xs text-muted">→</span>
          <ColorSwatch value={bg.to} onChange={v => set({ to: v })} fallback="#ec4899" title="Gradient end" />
          <input type="number" className={`${inputCls} flex-1`} placeholder="angle" value={bg.angle ?? 135} onChange={e => set({ angle: parseInt(e.target.value, 10) })} />
          <span className="text-xs text-muted">°</span>
        </div>
      )}
      {type === 'image' && (
        <input type="text" className={inputCls} placeholder="https://…/image.png" value={bg.imageUrl ?? ''} onChange={e => set({ imageUrl: e.target.value })} />
      )}
      {type === 'css' && (
        <textarea rows={3} className={`${inputCls} h-auto py-1.5 font-mono text-xs resize-y`} placeholder="background: radial-gradient(…); border-radius: 16px;"
          value={bg.css ?? ''} onChange={e => set({ css: e.target.value })} />
      )}
    </div>
  )
}
