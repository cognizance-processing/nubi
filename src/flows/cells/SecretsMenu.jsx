/**
 * SecretsMenu.jsx — "Secrets" dropdown for notebook cells (SQL + Python).
 *
 * Lists the org's secret NAMES (values are write-only and never returned by
 * the API — see src/lib/secrets.js) and inserts a reference at the cursor:
 *   - SQL cells:    {{ secrets.NAME }}
 *   - Python cells: secrets["NAME"]
 *
 * Names are fetched lazily on first open via the same client SecretsPage
 * uses (GET /secrets). Empty state links to /flows/secrets.
 *
 * Props:
 *   onInsert {Function(name)} — called with the secret NAME when picked.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import { KeyRound } from 'lucide-react'
import { listSecrets } from '../../lib/secrets.js'

export default function SecretsMenu({ onInsert }) {
  const [open, setOpen] = useState(false)
  const [names, setNames] = useState(null) // null = not fetched yet
  const [loading, setLoading] = useState(false)

  // Lazy fetch on first open (names only — values never leave the server).
  const toggleOpen = () => {
    setOpen(v => !v)
    if (names === null && !loading) {
      setLoading(true)
      listSecrets()
        .then(rows => setNames((rows ?? []).map(r => r?.name).filter(Boolean)))
        .catch(() => setNames([]))
        .finally(() => setLoading(false))
    }
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={toggleOpen}
        title="Insert a secret reference"
        className="flex items-center gap-1 px-2 py-1 text-[10px] font-medium rounded border border-border bg-surface hover:bg-surface-2 text-muted hover:text-fg transition-colors"
      >
        <KeyRound size={10} />
        Secrets
      </button>

      {open && (
        <div className="absolute z-20 top-full right-0 mt-1 min-w-[230px] py-1.5 rounded-xl bg-surface border border-border shadow-lg shadow-black/10">
          {loading && (
            <p className="px-3 py-2 text-xs text-muted">Loading…</p>
          )}

          {!loading && names !== null && names.length === 0 && (
            <p className="px-3 py-2 text-xs text-muted">
              No secrets yet —{' '}
              <Link to="/flows/secrets" className="text-primary hover:underline">
                add one in Flows → Secrets
              </Link>
              .
            </p>
          )}

          {!loading && names?.map(name => (
            <button
              key={name}
              type="button"
              onClick={() => { onInsert?.(name); setOpen(false) }}
              className="w-full text-left px-3 py-1.5 text-xs font-mono text-fg hover:bg-surface-2 transition-colors truncate"
            >
              {name}
            </button>
          ))}

          {!loading && names !== null && names.length > 0 && (
            <p className="px-3 pt-1.5 pb-0.5 mt-1 border-t border-border text-[10px] text-muted/60">
              Resolved server-side at run time. Values never reach the browser.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
