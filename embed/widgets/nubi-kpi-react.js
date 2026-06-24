/**
 * nubi-kpi-react.js — React-based KPI card custom element.
 *
 * Proof-of-concept for the defineNubiElement factory.
 * Mirrors the vanilla <nubi-kpi> widget but renders via React.
 *
 * Registered as: <nubi-kpi-react>
 *
 * Attributes (same surface as nubi-kpi):
 *   query-id    — registered query ID to execute
 *   value-col   — column name to read the KPI value from
 *   label       — card heading
 *   format      — 'number' | 'currency' | 'percent' | 'integer' (default 'number')
 *   token       — static JWT string
 *   get-token   — name of a window.* function that returns a JWT
 *   backend     — API base URL (default 'http://localhost:8000')
 */

import { useState, useEffect, useRef } from 'react'
import { resolveToken, fetchArrow, makeSampleKpiTable } from './shared.js'
import { defineNubiElement } from '../react-wc.js'

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function formatValue(val, format) {
  if (val == null) return '—'
  switch (format) {
    case 'currency':
      return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(val)
    case 'percent':
      return `${(Number(val) * 100).toFixed(1)}%`
    case 'integer':
      return Math.round(Number(val)).toLocaleString()
    default: {
      // Compact: 1.2M, 45.3K, etc.
      const n = Number(val)
      if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
      if (Math.abs(n) >= 1_000)     return `${(n / 1_000).toFixed(1)}K`
      return n.toLocaleString()
    }
  }
}

// ---------------------------------------------------------------------------
// React component
// ---------------------------------------------------------------------------

function NubiKpiReactComponent({
  'query-id':   queryId,
  'value-col':  valueCol = 'revenue',
  label = 'KPI',
  format = 'number',
  token,
  'get-token':  getTokenAttr,
  backend = 'http://localhost:8000',
}) {
  const [displayVal, setDisplayVal] = useState(null)
  const [isSample, setIsSample]     = useState(false)
  const [loading, setLoading]       = useState(true)
  const [errorMsg, setErrorMsg]     = useState(null)
  const elemRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    const ac = new AbortController()

    async function load() {
      setLoading(true)
      setErrorMsg(null)

      try {
        // Fabricate a minimal element-like object for resolveToken
        const fakeElem = {
          getAttribute: (attr) => {
            if (attr === 'token')     return token     ?? null
            if (attr === 'get-token') return getTokenAttr ?? null
            return null
          },
        }
        const tok = await resolveToken(fakeElem)

        let table
        if (queryId && tok) {
          table = await fetchArrow(backend, queryId, tok, ac.signal)
          setIsSample(false)
        } else {
          table = makeSampleKpiTable()
          setIsSample(true)
        }

        if (!cancelled) {
          const col = table.getChild(valueCol) ?? table.getChild(table.schema.fields[0]?.name)
          setDisplayVal(col ? col.get(0) : null)
          setLoading(false)
        }
      } catch (err) {
        if (!cancelled) {
          // Fall back to sample on any error
          const table = makeSampleKpiTable()
          const col = table.getChild(valueCol) ?? table.getChild(table.schema.fields[0]?.name)
          setDisplayVal(col ? col.get(0) : null)
          setIsSample(true)
          setErrorMsg(err.message)
          setLoading(false)
        }
      }
    }

    load()
    return () => { cancelled = true; ac.abort() }
  }, [queryId, valueCol, token, getTokenAttr, backend])

  const styles = {
    wrap: {
      display: 'flex',
      flexDirection: 'column',
      padding: '16px 20px',
      height: '100%',
      boxSizing: 'border-box',
    },
    top: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      marginBottom: 8,
    },
    labelEl: {
      fontSize: 'var(--nubi-font-size-sm, 11px)',
      color: 'var(--nubi-fg-muted, #718096)',
      textTransform: 'uppercase',
      letterSpacing: '0.06em',
      fontWeight: 600,
    },
    badge: {
      fontSize: 'var(--nubi-font-size-xs, 10px)',
      padding: '2px 6px',
      borderRadius: 4,
      fontWeight: 600,
      background: isSample ? '#422006' : '#064e3b',
      color:      isSample ? '#fed7aa' : '#6ee7b7',
    },
    value: {
      fontSize: 32,
      fontWeight: 700,
      lineHeight: 1.2,
      color: 'var(--nubi-fg, #e2e8f0)',
      opacity: loading ? 0.3 : 1,
      transition: 'opacity 0.2s ease',
    },
  }

  return (
    <div style={styles.wrap} ref={elemRef}>
      <div style={styles.top}>
        <span style={styles.labelEl}>{label}</span>
        <span style={styles.badge}>{isSample ? 'SAMPLE' : 'LIVE'}</span>
      </div>
      <div style={styles.value}>
        {loading ? '…' : formatValue(displayVal, format)}
      </div>
      {errorMsg && (
        <div style={{ fontSize: 10, color: 'var(--nubi-error, #ef4444)', marginTop: 4 }}>
          {errorMsg}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Custom element registration
// ---------------------------------------------------------------------------

export const NubiKpiReact = defineNubiElement('nubi-kpi-react', NubiKpiReactComponent, {
  observedAttributes: ['query-id', 'value-col', 'label', 'format', 'token', 'get-token', 'backend'],
  propTypes: {},
  defaultTheme: 'dark',
})
