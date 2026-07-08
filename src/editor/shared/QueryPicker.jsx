/**
 * shared/QueryPicker.jsx — Query-ID selector with a fallback free-text input.
 * Shared editor primitives (used by DashboardEditor).
 *
 * Props:
 *   value    string    — current query_id
 *   onChange (id)=>void
 *   extraIds string[]  — additional known query IDs to list (beyond DEMO_QUERY_IDS)
 */

import { useState, useMemo } from 'react'
import { inputCls, selectCls } from './inspectorPrimitives.jsx'
import { DEMO_QUERY_IDS } from './constants.js'

export function QueryPicker({ value, onChange, extraIds = [] }) {
  const [freeText, setFreeText] = useState('')
  const allIds = useMemo(() => {
    const set = new Set([...DEMO_QUERY_IDS, ...extraIds])
    if (value && !set.has(value)) set.add(value)
    return [...set]
  }, [extraIds, value])

  return (
    <div className="space-y-1.5">
      <select
        className={selectCls}
        value={allIds.includes(value) ? value : '__custom__'}
        onChange={e => { if (e.target.value !== '__custom__') onChange(e.target.value) }}
      >
        {allIds.map(id => <option key={id} value={id}>{id}</option>)}
        <option value="__custom__">Custom...</option>
      </select>
      {(!allIds.includes(value) || !value) && (
        <input
          type="text"
          placeholder="Enter query_id..."
          className={inputCls}
          value={freeText || value}
          onChange={e => { setFreeText(e.target.value); onChange(e.target.value) }}
        />
      )}
    </div>
  )
}
