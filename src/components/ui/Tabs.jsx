/**
 * Tabs — Nubi tab navigation primitive.
 *
 * WAI-ARIA tablist pattern (automatic activation).
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
 *   // Pills variant
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
 *   badge     string | number — small badge rendered after label
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

export function TabsList({ className, children, ...rest }) {
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

export function TabsTrigger({ value, disabled = false, badge, className, children, ...rest }) {
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
        disabled && 'opacity-40 pointer-events-none',
        className,
      )}
      {...rest}
    >
      {children}
      {badge !== undefined && (
        <span className="ml-1 inline-flex items-center justify-center min-w-[1.125rem] h-[1.125rem] px-1 rounded-full text-[10px] font-semibold bg-surface-2 text-muted">
          {badge}
        </span>
      )}
    </button>
  )
}

// ── TabsContent ───────────────────────────────────────────────────────────

export function TabsContent({ value, className, children, ...rest }) {
  const { active, id } = useTabs()
  const isActive = active === value
  return (
    <div
      id={`${id}-panel-${value}`}
      role="tabpanel"
      aria-labelledby={`${id}-tab-${value}`}
      hidden={!isActive}
      tabIndex={0}
      className={cx('rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring', className)}
      {...rest}
    >
      {isActive ? children : null}
    </div>
  )
}
