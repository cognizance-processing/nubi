/**
 * QuerySizeEstimate — the BigQuery-style "This query will process 4.71 GiB
 * when run." line under the query editor.
 *
 * Takes a plan from planQueryEstimate (pages/app/queryEstimate.logic.ts) —
 * always the same request Run would send — and, once typing or highlighting
 * pauses, asks POST /query/estimate: a dry run that never executes the query.
 * Answers are kept per request for a few minutes, so moving back to a query
 * or highlight already checked doesn't ask again. Shows nothing for
 * databases that can't estimate. Purely informational: never blocks Run.
 */

import { useEffect, useState } from 'react'
import { AlertCircle, CheckCircle2, Loader2 } from 'lucide-react'
import { estimateQuery } from '../../lib/api.js'
import {
  bodyFromKey,
  estimateMessage,
  readEstimateResponse,
  type EstimatePlan,
  type EstimateState,
} from '../../pages/app/queryEstimate.logic.js'

const PAUSE_MS = 600 // wait for typing / highlighting to settle
const REUSE_MS = 5 * 60_000 // reuse an answer for the same request this long
const KEEP = 50 // answers remembered per editor

type Answer = { at: number; state: EstimateState }

export default function QuerySizeEstimate({ plan }: { plan: EstimatePlan }) {
  const [answers, setAnswers] = useState<Record<string, Answer>>({})
  // The key carries the request body, so it alone says what to ask.
  const key = plan?.kind === 'request' ? plan.key : null

  useEffect(() => {
    if (!key) return
    const known = answers[key]
    if (known && Date.now() - known.at < REUSE_MS) return
    let live = true
    const timer = setTimeout(() => {
      const remember = (state: EstimateState) => {
        if (!live) return
        setAnswers((prev) => {
          const next = { ...prev, [key]: { at: Date.now(), state } }
          const keys = Object.keys(next)
          for (const old of keys.slice(0, Math.max(0, keys.length - KEEP))) delete next[old]
          return next
        })
      }
      estimateQuery(bodyFromKey(key))
        .then((res) => remember(readEstimateResponse(res)))
        .catch((err) => remember({ status: 'failed', detail: err?.message }))
    }, PAUSE_MS)
    return () => { live = false; clearTimeout(timer) }
  }, [key, answers])

  const state: EstimateState = !key ? { status: 'idle' } : (answers[key]?.state ?? { status: 'checking' })
  const msg = estimateMessage(plan, state)
  if (!msg) return null

  const Icon = msg.tone === 'busy' ? Loader2 : msg.tone === 'ok' ? CheckCircle2 : AlertCircle
  const iconClass =
    msg.tone === 'busy' ? 'animate-spin text-subtle'
      : msg.tone === 'ok' ? 'text-success'
        : msg.tone === 'warn' ? 'text-warning'
          : 'text-subtle'
  return (
    <p
      className={`mt-2 mb-0 text-sm flex items-start gap-2 ${msg.tone === 'ok' ? 'text-fg' : 'text-muted'}`}
      role="status"
      aria-live="polite"
      title={msg.title}
      data-testid="query-size-estimate"
    >
      <Icon size={16} className={`shrink-0 mt-[2px] ${iconClass}`} aria-hidden="true" />
      <span>{msg.text}</span>
    </p>
  )
}
