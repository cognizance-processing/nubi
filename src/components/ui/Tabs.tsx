/**
 * Tabs — Nubi tab navigation primitive (Swiss).
 *
 * WAI-ARIA tablist pattern (automatic activation). The underline variant is a
 * 1px hairline baseline with a 2px ink underline on the active tab; counts are
 * mono tabular text, not pills. The list scrolls horizontally on narrow screens
 * instead of wrapping. Focus rings are drawn inside the tab.
 *
 * Usage:
 *   <Tabs defaultValue="overview">
 *     <TabsList>
 *       <TabsTrigger value="overview">Overview</TabsTrigger>
 *       <TabsTrigger value="settings">Settings</TabsTrigger>
 *       <TabsTrigger value="logs" disabled>Logs</TabsTrigger>
 *     </TabsList>
 *     <TabsContent value="overview">Overview content</TabsContent>
 *     <TabsContent value="settings">Settings content</TabsContent>
 *   </Tabs>
 *
 *   // Controlled
 *   <Tabs value={activeTab} onValueChange={setActiveTab}>
 *     ...
 *   </Tabs>
 *
 *   // Chip variant: square hairline chips, active = ink fill
 *   <Tabs variant="pills" defaultValue="all">
 *     <TabsList>
 *       <TabsTrigger value="all">All</TabsTrigger>
 *       <TabsTrigger value="active">Active</TabsTrigger>
 *     </TabsList>
 *     ...
 *   </Tabs>
 *
 * Props (Tabs):
 *   value          string — controlled active tab
 *   defaultValue   string — initial active tab (uncontrolled)
 *   onValueChange  (value: string) => void
 *   variant        'underline' (default) | 'pills'
 *
 * Props (TabsTrigger):
 *   value     string (required)
 *   disabled  boolean
 *   badge     string | number — mono count rendered after the label
 */

import { createContext, useCallback, useContext, useId, useRef, useState } from 'react'

function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

// ── Context ────────────────────────────────────────────────────────────────

const TabsCtx = createContext(null)

function useTabs() {
  const ctx = useContext(TabsCtx)
  if (!ctx) throw new Error('Tabs subcomponents must be used inside <Tabs>')
  return ctx
}

// ── Tabs root ─────────────────────────────────────────────────────────────

export function Tabs({
  value: valueProp,
  defaultValue,
  onValueChange,
  variant = 'underline',
  className,
  children,
  ...rest
}: {
  value?: string
  defaultValue?: string
  onValueChange?: (value: string) => void
  variant?: 'underline' | 'pills'
  className?: string
  children?: React.ReactNode
  [k: string]: any
}) {
  const id = useId()
  const [uncontrolledValue, setUncontrolledValue] = useState(defaultValue ?? '')
  const controlled = valueProp !== undefined
  const active = controlled ? valueProp : uncontrolledValue

  const activate = useCallback((v) => {
    if (!controlled) setUncontrolledValue(v)
    onValueChange?.(v)
  }, [controlled, onValueChange])

  return (
    <TabsCtx.Provider value={{ active, activate, id, variant }}>
      <div className={className} {...rest}>
        {children}
      </div>
    </TabsCtx.Provider>
  )
}

// ── TabsList ──────────────────────────────────────────────────────────────

export function TabsList({ className, children, ...rest }: { className?: string; children?: React.ReactNode; [k: string]: any }) {
  const { variant } = useTabs()
  const listRef = useRef(null)

  const handleKeyDown = (e) => {
    const tabs = Array.from(listRef.current?.querySelectorAll('[role="tab"]:not([disabled])') ?? [])
    const idx = tabs.indexOf(document.activeElement)
    let next = null
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown')  next = tabs[(idx + 1) % tabs.length]
    if (e.key === 'ArrowLeft'  || e.key === 'ArrowUp')    next = tabs[(idx - 1 + tabs.length) % tabs.length]
    if (e.key === 'Home') next = tabs[0]
    if (e.key === 'End')  next = tabs[tabs.length - 1]
    if (next) { e.preventDefault(); next.focus(); next.click() }
  }

  return (
    <div
      ref={listRef}
      role="tablist"
      onKeyDown={handleKeyDown}
      className={cx(
        variant === 'pills' ? 'nubi-tablist-pills' : 'nubi-tablist',
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  )
}

// ── TabsTrigger ───────────────────────────────────────────────────────────

export function TabsTrigger({ value, disabled = false, badge, className, children, ...rest }: { value: string; disabled?: boolean; badge?: string | number; className?: string; children?: React.ReactNode; [k: string]: any }) {
  const { active, activate, id, variant } = useTabs()
  const isActive = active === value
  const tabId    = `${id}-tab-${value}`
  const panelId  = `${id}-panel-${value}`

  return (
    <button
      id={tabId}
      role="tab"
      type="button"
      aria-selected={isActive}
      aria-controls={panelId}
      tabIndex={isActive ? 0 : -1}
      disabled={disabled}
      onClick={() => !disabled && activate(value)}
      className={cx(
        variant === 'pills' ? 'nubi-tab-pill' : 'nubi-tab',
        'nubi-focus-inset',
        className,
      )}
      {...rest}
    >
      {children}
      {badge !== undefined && <span className="nubi-tab-count">{badge}</span>}
    </button>
  )
}

// ── TabsContent ───────────────────────────────────────────────────────────

export function TabsContent({ value, className, children, ...rest }: { value: string; className?: string; children?: React.ReactNode; [k: string]: any }) {
  const { active, id } = useTabs()
  const isActive = active === value
  return (
    <div
      id={`${id}-panel-${value}`}
      role="tabpanel"
      aria-labelledby={`${id}-tab-${value}`}
      hidden={!isActive}
      tabIndex={0}
      className={cx('nubi-focus-inset', className)}
      {...rest}
    >
      {isActive ? children : null}
    </div>
  )
}
