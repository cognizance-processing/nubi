/**
 * Marketing kit — the small set of Swiss building blocks shared by pricing,
 * compare, reporting, onboarding and the docs home. Styling lives in
 * MarketingStyles (mk- classes); this file only composes it.
 */
import {
  useEffect, useLayoutEffect, useRef, useState,
  type ElementType, type ReactNode, type CSSProperties,
} from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Check, Copy } from 'lucide-react'
import UiTag from '../ui/Tag.jsx'
import './code.css'

export const cx = (...p: unknown[]) => p.filter(Boolean).join(' ')

// ── Reveal ───────────────────────────────────────────────────────────────────
// Content is visible by default. Only blocks the page measured below the fold
// are hidden until they scroll in, and reduced motion shows everything.

export function Reveal({
  children, className = '', delay = 0, as: As = 'div', id,
}: {
  children: ReactNode
  className?: string
  delay?: number
  as?: ElementType
  id?: string
}) {
  const ref = useRef<HTMLElement | null>(null)
  const [hidden, setHidden] = useState(false)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el || typeof window === 'undefined') return
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    if (typeof IntersectionObserver === 'undefined') return
    if (el.getBoundingClientRect().top < window.innerHeight * 0.92) return
    setHidden(true)
    const io = new IntersectionObserver(
      ([e]) => { if (e.isIntersecting) { setHidden(false); io.disconnect() } },
      { rootMargin: '0px 0px -6% 0px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [])

  return (
    <As
      ref={ref}
      id={id}
      className={cx('mk-reveal', className)}
      data-mk-r={hidden ? 'hidden' : undefined}
      style={delay ? ({ transitionDelay: `${delay}ms` } as CSSProperties) : undefined}
    >
      {children}
    </As>
  )
}

// ── Sections ─────────────────────────────────────────────────────────────────

/** Section opener: 2px rule above (from .mk-sec), mono index left, headline right. */
export function SecHead({
  index, label, title, children,
}: {
  index: string
  label: string
  title: ReactNode
  children?: ReactNode
}) {
  return (
    <header className="mk-sec-h">
      <div className="mk-idx"><b>{index}</b><span>{label}</span></div>
      <div>
        <h2 className="mk-h2">{title}</h2>
        {children && <p className="mk-lead">{children}</p>}
      </div>
    </header>
  )
}

export function Section({
  id, index, label, title, lead, sunk = false, children, className = '',
}: {
  id?: string
  index: string
  label: string
  title: ReactNode
  lead?: ReactNode
  sunk?: boolean
  children?: ReactNode
  className?: string
}) {
  return (
    <section id={id} className={cx('mk-sec', sunk && 'mk-sunk', className)}>
      <div className="mk-wrap">
        <Reveal><SecHead index={index} label={label} title={title}>{lead}</SecHead></Reveal>
        {children}
      </div>
    </section>
  )
}

/** Page hero: index line, display headline, lead, actions, optional aside. */
export function PageHero({
  eyebrow, meta, title, lead, actions, aside, children,
}: {
  eyebrow: string
  meta?: string
  title: ReactNode
  lead?: ReactNode
  actions?: ReactNode
  aside?: ReactNode
  children?: ReactNode
}) {
  return (
    <section className="mk-hero">
      <div className="mk-wrap">
        <div className="mk-idx"><b>NUBI</b><span>{eyebrow}</span>{meta && <span className="mk-hide-sm">{meta}</span>}</div>
        <div className="mk-hero-grid" style={aside ? undefined : { gridTemplateColumns: 'minmax(0,1fr)' }}>
          <div className="mk-hero-copy">
            <h1 className="mk-h1">{title}</h1>
            {lead && <p className="mk-lead">{lead}</p>}
            {actions && <div className="mk-ctas">{actions}</div>}
          </div>
          {aside}
        </div>
        {children}
      </div>
    </section>
  )
}

export function Facts({ items, three = false }: { items: { v: ReactNode; l: ReactNode }[]; three?: boolean }) {
  return (
    <div className={cx('mk-facts', three && 'mk-facts-3')}>
      {items.map((f, i) => (<div key={i}><b>{f.v}</b><span>{f.l}</span></div>))}
    </div>
  )
}

// ── Small parts ──────────────────────────────────────────────────────────────

export function Tag({
  children, tone = 'neutral', dot = false, caps = true, className = '',
}: { children: ReactNode; tone?: 'neutral' | 'ink' | 'accent' | 'success' | 'warning' | 'danger'; dot?: boolean; caps?: boolean; className?: string }) {
  return <UiTag variant={tone} dot={dot} caps={caps} className={className}>{children}</UiTag>
}

export function ArrowLink({ to, href, children, external = false }: { to?: string; href?: string; children: ReactNode; external?: boolean }) {
  const inner = <>{children}<ArrowRight size={14} strokeWidth={1.75} aria-hidden="true" /></>
  if (to) return <Link to={to} className="mk-arrow">{inner}</Link>
  return <a href={href} className="mk-arrow" {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}>{inner}</a>
}

export function Checks({ items }: { items: (string | { text: string; head?: boolean; soon?: boolean })[] }) {
  return (
    <ul className="mk-checks">
      {items.map((it, i) => {
        const o = typeof it === 'string' ? { text: it } : it
        if (o.head) return <li key={i} className="mk-head">{o.text}</li>
        return (
          <li key={i} className={o.soon ? 'mk-soon' : undefined}>
            <Check size={14} strokeWidth={2} aria-hidden="true" />
            <span>{o.text}{o.soon && <> <Tag>Soon</Tag></>}</span>
          </li>
        )
      })}
    </ul>
  )
}

/** Disclosure list: mono numerals, ruled rows. */
export function Faq({ items }: { items: { q: string; a: ReactNode }[] }) {
  return (
    <div className="mk-faq">
      {items.map((it, i) => (
        <details key={it.q}>
          <summary>
            <span className="mk-faq-n">{String(i + 1).padStart(2, '0')}</span>
            <span className="mk-faq-q">{it.q}</span>
          </summary>
          <div className="mk-faq-a"><p className="mk-p">{it.a}</p></div>
        </details>
      ))}
    </div>
  )
}

/** Segmented control. `role="tablist"` when `tabs`, otherwise pressed buttons. */
export function Seg<T extends string>({
  value, onChange, options, label, tabs = false,
}: {
  value: T
  onChange: (v: T) => void
  options: { value: T; label: ReactNode }[]
  label: string
  tabs?: boolean
}) {
  return (
    <div className="mk-seg" role={tabs ? 'tablist' : 'group'} aria-label={label}>
      {options.map(o => (
        <button
          key={o.value}
          type="button"
          role={tabs ? 'tab' : undefined}
          aria-selected={tabs ? value === o.value : undefined}
          aria-pressed={tabs ? undefined : value === o.value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** Horizontal bars, drawn to scale: ink for others, accent for Nubi. */
export function CostBars({
  rows,
  max,
  seen = true,
}: {
  rows: { name: ReactNode; note?: ReactNode; value: number; ext?: number; label: ReactNode; nubi?: boolean; hatch?: boolean }[]
  max: number
  seen?: boolean
}) {
  const pct = (n: number) => `${Math.max(0, Math.min(100, (n / max) * 100))}%`
  return (
    <div className="mk-bars">
      {rows.map((r, i) => (
        <div key={i} className={cx('mk-bar', r.nubi && 'mk-nubi')}>
          <div className="mk-bar-name">{r.name}{r.note && <small>{r.note}</small>}</div>
          <div className="mk-bar-track" aria-hidden="true">
            {r.ext != null && r.ext > r.value && <div className="mk-bar-fill mk-ext" style={{ width: seen ? pct(r.ext) : '0%' }} />}
            <div className={cx('mk-bar-fill', r.nubi && 'mk-us')} style={{ width: seen ? pct(r.value) : '0%' }} />
          </div>
          <div className="mk-bar-val">{r.label}</div>
        </div>
      ))}
    </div>
  )
}

/** Scroll-in-view flag for one-shot bar animations. */
export function useSeen<T extends HTMLElement = HTMLDivElement>() {
  const ref = useRef<T | null>(null)
  const [seen, setSeen] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (typeof IntersectionObserver === 'undefined' || window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      setSeen(true)
      return
    }
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setSeen(true); io.disconnect() } }, { threshold: 0.15 })
    io.observe(el)
    return () => io.disconnect()
  }, [])
  return [ref, seen] as const
}

/** A table wrapper that scrolls sideways with a cue, sticky first column. */
export function TableScroll({
  children, cue = 'Scroll to compare', caption, min = 720, label = 'Comparison table',
}: { children: ReactNode; cue?: string; caption?: ReactNode; min?: number; label?: string }) {
  return (
    <div>
      <div className="mk-scroll-cue" aria-hidden="true">
        <span>{caption ?? ''}</span><span>{cue} <ArrowRight size={11} style={{ display: 'inline', verticalAlign: '-1px' }} /></span>
      </div>
      <div className="mk-tw">
        <div className="mk-ts" tabIndex={0} role="region" aria-label={`${label} (scrolls sideways)`}>
          <div style={{ minWidth: min }}>{children}</div>
        </div>
      </div>
    </div>
  )
}

/** A short snippet on the ink surface with a copy button. `#` lines read as comments. */
export function CodePanel({ label, code }: { label: string; code: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    if (!navigator.clipboard) return
    navigator.clipboard.writeText(code).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1600) }, () => {})
  }
  return (
    <figure className="mk-code" style={{ margin: 0 }}>
      <figcaption className="mk-code-bar">
        <span>{label}</span>
        <button type="button" className="mk-copy" onClick={copy} aria-label={copied ? 'Copied to clipboard' : `Copy ${label} snippet`}>
          {copied ? <Check size={12} strokeWidth={1.75} aria-hidden="true" /> : <Copy size={12} strokeWidth={1.75} aria-hidden="true" />}
          {copied ? 'Copied' : 'Copy'}
        </button>
      </figcaption>
      <pre tabIndex={0}><code>{code.split('\n').map((ln, i, a) => (
        <span key={i} className={ln.trimStart().startsWith('#') ? 'cm' : undefined}>{ln}{i < a.length - 1 ? '\n' : ''}</span>
      ))}</code></pre>
    </figure>
  )
}
