/**
 * CalcShell — a ruled frame for a live calculator: a 2px ink rule, a mono
 * caption (`CALC 01 · SLUG`) with a live marker, then the body the caller
 * supplies. No window chrome, no shadow.
 */
import type { ReactNode } from "react"
import { Tag } from "./kit.jsx"

export default function CalcShell({ index, slug, children }: { index: string; slug: string; children: ReactNode }) {
  return (
    <div className="mk-calc-shell" style={{ border: '1px solid var(--border)', background: 'var(--surface)' }}>
      <div className="mk-idx" style={{ padding: '10px 16px', borderBottomWidth: 2 }}>
        <b>CALC {index}</b><span>{slug}</span>
        <Tag tone="accent" dot>Live estimate</Tag>
      </div>
      {children}
    </div>
  )
}
