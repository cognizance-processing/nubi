/**
 * shared/BackgroundEditor.jsx — Background descriptor editor used for
 * dashboard/tab/widget backgrounds.
 * Shared between DashboardEditor and CanvasEditor.
 *
 * Props:
 *   value    object|undefined  — background descriptor { type, color, from, to, ... }
 *   onChange (bg)=>void        — called with the updated descriptor
 */

import { inputCls } from './inspectorPrimitives.jsx'
import { BACKGROUND_TYPES } from './constants.js'

export function BackgroundEditor({ value, onChange }) {
  const bg = value ?? {}
  const type = bg.type ?? 'none'
  const set = (patch) => onChange({ ...bg, ...patch })
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-5 gap-1">
        {BACKGROUND_TYPES.map(t => (
          <button key={t} onClick={() => set({ type: t === 'none' ? undefined : t })}
            className={`h-7 px-1.5 text-[11px] font-medium rounded-lg border capitalize transition-all focus:outline-none focus:ring-2 focus:ring-ring/50 ${
              (type === t || (t === 'none' && !bg.type)) ? 'bg-primary text-primary-fg border-primary' : 'bg-surface text-muted border-border hover:border-primary hover:text-primary'
            }`}>{t}</button>
        ))}
      </div>
      {type === 'solid' && (
        <div className="flex items-center gap-2">
          <input type="color" className="h-8 w-10 shrink-0 rounded-lg border border-border bg-surface cursor-pointer" value={bg.color ?? '#0b0f1a'} onChange={e => set({ color: e.target.value })} />
          <input type="text" className={`${inputCls} flex-1`} value={bg.color ?? ''} placeholder="#0b0f1a or any CSS color" onChange={e => set({ color: e.target.value })} />
        </div>
      )}
      {type === 'gradient' && (
        <div className="flex items-center gap-2">
          <input type="color" className="h-8 w-10 shrink-0 rounded-lg border border-border bg-surface cursor-pointer" value={bg.from ?? '#6366f1'} onChange={e => set({ from: e.target.value })} />
          <span className="text-xs text-muted">→</span>
          <input type="color" className="h-8 w-10 shrink-0 rounded-lg border border-border bg-surface cursor-pointer" value={bg.to ?? '#ec4899'} onChange={e => set({ to: e.target.value })} />
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
