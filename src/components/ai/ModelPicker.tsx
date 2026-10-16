/**
 * ModelPicker — provider-branded LLM model selector.
 *
 * Lists every enabled AI provider (GET /api/v1/ai/providers) with its logo
 * and the models it offers, grouped by provider, and shows which model is
 * currently active. A leading "Nubi Default" entry represents "no explicit
 * model" (`value` is the sentinel `'default'`) — the backend then uses the
 * resolved provider's own default model.
 *
 * A provider the OPERATOR hasn't configured a vendor key for (`configured:
 * false`) still lists its models for visibility, but they're disabled with a
 * "needs setup" hint, since selecting one would 503 (`llm_not_configured`).
 *
 * Used by src/chat/ChatPanel.jsx's header. Degrades gracefully to just the
 * "Nubi Default" entry if GET /ai/providers fails or returns nothing.
 */

import { useState, useEffect, useRef, useCallback, useId } from 'react'
import { ChevronDown, Check, Sparkles } from 'lucide-react'
import Tag from '../ui/Tag.jsx'
import { fetchAiProviders, formatModelPrice } from '../../lib/aiApi.js'
import ProviderIcon from './ProviderIcon.jsx'

const DEFAULT_VALUE = 'default'

/**
 * @param {{
 *   value: string,               // model id, or 'default'
 *   onChange: (id: string) => void,
 *   disabled?: boolean,
 *   className?: string,
 * }} props
 */
export default function ModelPicker({ value, onChange, disabled = false, className = '' }) {
  const [providers, setProviders] = useState([])
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)
  const listboxId = useId()

  useEffect(() => {
    let cancelled = false
    fetchAiProviders().then(({ providers: list }) => {
      if (!cancelled) setProviders(list)
    })
    return () => { cancelled = true }
  }, [])

  // Reset to the default entry if a stored/selected model is no longer valid
  // for the fetched provider list (e.g. the operator disabled a provider).
  useEffect(() => {
    if (!providers.length || value === DEFAULT_VALUE) return
    const known = providers.some((p) => p.models.some((m) => m.id === value))
    if (!known) onChange(DEFAULT_VALUE)
    // Only re-check when the provider list itself changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providers])

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return undefined
    function onDocClick(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false)
    }
    function onKey(e) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const pick = useCallback((id, isDisabled) => {
    if (isDisabled) return
    onChange(id)
    setOpen(false)
  }, [onChange])

  // Resolve the active entry for the trigger button.
  let activeProvider = null
  let activeModel = null
  for (const p of providers) {
    const m = p.models.find((mm) => mm.id === value)
    if (m) { activeProvider = p; activeModel = m; break }
  }
  const isDefault = value === DEFAULT_VALUE || !activeModel

  return (
    <div className={`relative ${className}`} ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-label="Select AI model"
        className="inline-flex items-center gap-1.5 pl-1.5 pr-2 py-1 text-[11px] font-sans font-medium text-muted bg-surface-2 border border-border rounded-[3px] hover:border-fg hover:text-fg disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer focus:outline-none focus:ring-2 focus:ring-ring transition-colors"
      >
        {isDefault ? (
          <span className="flex items-center justify-center w-[20px] h-[20px] border border-frame-line bg-surface shrink-0" style={{ borderRadius: 2 }}>
            <Sparkles size={11} strokeWidth={1.5} className="text-fg" aria-hidden="true" />
          </span>
        ) : (
          <ProviderIcon provider={activeProvider?.id} size={14} />
        )}
        <span className="max-w-[110px] truncate">
          {isDefault ? 'Nubi Default' : (activeModel?.display_name ?? value)}
        </span>
        <ChevronDown size={11} className="shrink-0" />
      </button>

      {open && (
        <div
          id={listboxId}
          role="listbox"
          aria-label="AI model"
          className="absolute right-0 z-50 mt-1.5 w-72 max-h-[70vh] overflow-y-auto rounded-[3px] border border-border bg-surface shadow-xl py-1.5"
        >
          {/* Nubi Default entry */}
          <button
            type="button"
            role="option"
            aria-selected={isDefault}
            onClick={() => pick(DEFAULT_VALUE, false)}
            className="w-full flex items-center gap-2.5 px-3 py-2 text-left hover:bg-surface-2 transition-colors"
          >
            <span className="flex items-center justify-center w-7 h-7 border border-frame-line bg-surface shrink-0" style={{ borderRadius: 2 }}>
              <Sparkles size={14} strokeWidth={1.5} className="text-fg" aria-hidden="true" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[12.5px] font-medium text-fg">Nubi Default</span>
              <span className="block text-[11px] text-muted">Recommended — provider picks its own default model</span>
            </span>
            {isDefault && <Check size={13} className="text-primary shrink-0" />}
          </button>

          {providers.length === 0 && (
            <p className="px-3 py-3 text-[11px] text-muted">
              No AI providers configured on this deployment yet.
            </p>
          )}

          {providers.map((p) => (
            <div key={p.id} className="mt-1 first:mt-0 border-t border-border/60 pt-1 first:border-t-0 first:pt-0">
              <div className="flex items-center gap-2 px-3 py-1.5">
                <ProviderIcon provider={p.id} size={13} />
                <span className="text-[11px] font-semibold text-muted uppercase tracking-wide truncate">
                  {p.display_name}
                </span>
                {!p.configured && (
                  <Tag variant="warning" dot className="ml-auto">Needs setup</Tag>
                )}
              </div>
              {p.models.map((m) => {
                const selected = value === m.id
                const isDisabled = !p.configured
                const price = formatModelPrice(m)
                return (
                  <button
                    key={m.id}
                    type="button"
                    role="option"
                    aria-selected={selected}
                    aria-disabled={isDisabled}
                    disabled={isDisabled}
                    title={isDisabled ? `${p.display_name} isn't configured on this deployment` : undefined}
                    onClick={() => pick(m.id, isDisabled)}
                    className={[
                      'w-full flex items-start gap-2.5 pl-9 pr-3 py-1.5 text-left transition-colors',
                      isDisabled ? 'opacity-45 cursor-not-allowed' : 'hover:bg-surface-2 cursor-pointer',
                    ].join(' ')}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5">
                        <span className="min-w-0 flex-1 text-[12px] text-fg truncate">
                          {m.display_name}
                        </span>
                        {m.default && (
                          <span className="text-[11px] font-semibold text-muted uppercase tracking-wide shrink-0">
                            default
                          </span>
                        )}
                      </span>
                      {price && (
                        <span className="block text-[11px] text-muted tabular-nums truncate">
                          {price}
                        </span>
                      )}
                    </span>
                    {selected && <Check size={12} className="text-primary shrink-0 mt-0.5" />}
                  </button>
                )
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export { DEFAULT_VALUE as MODEL_DEFAULT_VALUE }
