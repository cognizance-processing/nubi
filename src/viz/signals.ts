/**
 * signals.js — reusable conditional-formatting "signal" system.
 *
 * Takes a scalar value and an ordered list of threshold rules; returns a
 * { color, label, severity } descriptor that can be applied to table cells,
 * KPI tiles, chips, or any other visual element.
 *
 * ── Rule shape ────────────────────────────────────────────────────────────────
 * {
 *   op       : 'gt'|'gte'|'lt'|'lte'|'eq'|'ne'|'between'|'contains'
 *   value    : number|string        // primary operand
 *   value2?  : number|string        // upper bound for 'between'
 *   color    : string               // CSS colour to apply, e.g. '#ef4444'
 *   label?   : string               // optional text label, e.g. 'Critical'
 *   severity?: 'red'|'amber'|'green'|'info'|'neutral'
 *              // semantic severity — maps to a default colour when `color` is absent
 * }
 *
 * ── Output shape ──────────────────────────────────────────────────────────────
 * {
 *   color    : string | null   // resolved CSS colour, or null if no rule matched
 *   label    : string | null   // resolved label, or null
 *   severity : string | null   // resolved severity, or null
 *   matched  : boolean         // true if any rule matched
 * }
 *
 * Rules are evaluated in order; the FIRST matching rule wins. This lets you
 * express priority: put the most urgent threshold first.
 *
 * ── Exports ───────────────────────────────────────────────────────────────────
 * applySignal(value, rules)   → SignalResult
 * SEVERITY_COLORS             → Record<string, string>  (semantic → CSS colour)
 * DEFAULT_SIGNAL              → SignalResult (no match)
 */

// ---------------------------------------------------------------------------
// Semantic severity colour map
// ---------------------------------------------------------------------------

export const SEVERITY_COLORS: Record<string, string> = {
  red:     '#ef4444',
  amber:   '#f59e0b',
  green:   '#10b981',
  info:    '#3b82f6',
  neutral: '#9ca3af',
}

export interface SignalRule {
  op: 'gt' | 'gte' | 'lt' | 'lte' | 'eq' | 'ne' | 'between' | 'contains'
  value: number | string
  value2?: number | string
  color?: string
  label?: string
  severity?: 'red' | 'amber' | 'green' | 'info' | 'neutral'
}

export interface SignalResult {
  color: string | null
  label: string | null
  severity: string | null
  matched: boolean
}

// ---------------------------------------------------------------------------
// Default / empty result
// ---------------------------------------------------------------------------

export const DEFAULT_SIGNAL: SignalResult = { color: null, label: null, severity: null, matched: false }

// ---------------------------------------------------------------------------
// Operator evaluator
// ---------------------------------------------------------------------------

function evalOp(v: unknown, op: SignalRule['op'], a: unknown, b: unknown): boolean {
  switch (op) {
    case 'gt':      return Number(v) > Number(a)
    case 'gte':     return Number(v) >= Number(a)
    case 'lt':      return Number(v) < Number(a)
    case 'lte':     return Number(v) <= Number(a)
    case 'eq':      return v == a  // loose equality intentional: '42' == 42
    case 'ne':      return v != a  // loose inequality intentional
    case 'between': return Number(v) >= Number(a) && Number(v) <= Number(b)
    case 'contains': {
      if (v == null) return false
      return String(v).toLowerCase().includes(String(a).toLowerCase())
    }
    default: return false
  }
}

// ---------------------------------------------------------------------------
// Main API
// ---------------------------------------------------------------------------

/**
 * Evaluate signal rules against a scalar value.
 * Returns the first matching rule's signal, or DEFAULT_SIGNAL.
 */
export function applySignal(value: unknown, rules: SignalRule[] | null | undefined): SignalResult {
  if (!Array.isArray(rules) || rules.length === 0) return DEFAULT_SIGNAL
  if (value == null) return DEFAULT_SIGNAL

  for (const rule of rules) {
    const { op, value: a, value2: b } = rule
    if (!op) continue
    if (!evalOp(value, op, a, b)) continue

    // Rule matched — resolve colour from explicit `color` or severity fallback.
    const severity = rule.severity ?? null
    const color = rule.color ?? (severity ? SEVERITY_COLORS[severity] ?? null : null)
    const label = rule.label ?? null
    return { color, label, severity, matched: true }
  }

  return DEFAULT_SIGNAL
}
