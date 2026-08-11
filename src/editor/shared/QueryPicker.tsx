/**
 * shared/QueryPicker.jsx — Query-ID selector with a fallback free-text input.
 * Shared editor primitives (used by DashboardEditor).
 *
 * Props:
 *   value    string                                — current query_id
 *   onChange (id)=>void
 *   extraIds (string | {id, name})[]  — additional known queries to list
 *            (beyond DEMO_QUERY_IDS). A bare string is shown by its id; an
 *            {id, name} entry is shown by name (falling back to id).
 */

import { useState, useMemo } from 'react'
import { inputCls, selectCls } from './inspectorPrimitives.jsx'
import { DEMO_QUERY_IDS } from './constants.js'

export function QueryPicker({ value, onChange, extraIds = [] }) {
  const [freeText, setFreeText] = useState('')
  const allEntries = useMemo(() => {
    const map = new Map(DEMO_QUERY_IDS.map(id => [id, { id, name: null }]))
    extraIds.forEach(entry => {
      const { id, name } = typeof entry === 'string' ? { id: entry, name: null } : entry
      map.set(id, { id, name })
    })
    if (value && !map.has(value)) map.set(value, { id: value, name: null })
    return [...map.values()]
  }, [extraIds, value])
  const allIds = useMemo(() => allEntries.map(e => e.id), [allEntries])

  return (
    <div className="space-y-1.5">
      <select
        className={selectCls}
        value={allIds.includes(value) ? value : '__custom__'}
        onChange={e => { if (e.target.value !== '__custom__') onChange(e.target.value) }}
      >
        {allEntries.map(({ id, name }) => <option key={id} value={id}>{name || id}</option>)}
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
