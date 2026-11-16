/**
 * MapGroupNode.jsx — custom React Flow node for a map (fan-out) task.
 *
 * Renders as a collapsible node with:
 *   - Task key (title) + "map" kind tag
 *   - Collapsed: item_expr snippet + max_concurrency hint
 *   - Expanded: body task list as compact ruled rows
 *   - Item count when a task_run result is available
 *   - Square status dot keyed by task_run.state (includes waiting_children)
 *   - Source (bottom) and Target (top) handles
 */

import { memo, useState } from 'react'
import { Handle, Position } from 'reactflow'
import { ChevronDown, ChevronRight, GitBranch } from 'lucide-react'
import Tag from '../../components/ui/Tag.jsx'
import {
  StateDot, STATE_LABEL, FAILURE_STATES, WARNING_STATES, HANDLE_CLASS, nodeClass, fmtDuration,
} from './nodeChrome.jsx'

function MapGroupNode({ data, selected }) {
  const { task, taskRun } = data
  const config    = task?.config ?? {}
  const bodyTasks = config.body ?? []
  const itemExpr  = config.item_expr ?? ''
  const itemVar   = config.item_var ?? 'item'
  const maxConc   = config.max_concurrency ?? 0
  const state     = taskRun?.state ?? null

  const [expanded, setExpanded] = useState(false)

  // Item count from a completed run
  const itemCount = taskRun?.result?.item_count ?? null

  const isFailure = !!state && FAILURE_STATES.has(state)
  const isWarning = !!state && WARNING_STATES.has(state)
  const durationLabel = fmtDuration(taskRun?.duration_s)

  // Truncate item_expr for display
  const exprSnippet = itemExpr.length > 40 ? itemExpr.slice(0, 38) + '…' : itemExpr

  return (
    <div className={nodeClass({ selected, failure: isFailure, warning: isWarning })} style={{ minWidth: 210, maxWidth: 260 }}>
      {/* Target handle (top) */}
      <Handle type="target" position={Position.Top} className={HANDLE_CLASS} />

      {/* Node body */}
      <div className="select-none px-3 py-2.5">
        {/* Header row: expand toggle + key + count + status dot */}
        <div className="mb-1.5 flex items-center justify-between gap-1.5">
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); setExpanded(v => !v) }}
            className="shrink-0 text-muted hover:text-fg"
            title={expanded ? 'Collapse body' : 'Expand body'}
            aria-label={expanded ? 'Collapse map body' : 'Expand map body'}
            aria-expanded={expanded}
          >
            {expanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
          </button>

          <span className="fn-key flex-1">{task?.key ?? '(untitled)'}</span>

          {itemCount != null && <Tag caps={false} className="shrink-0">×{itemCount}</Tag>}
          <StateDot state={state} className="shrink-0" />
        </div>

        {/* Kind tag + state label */}
        <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
          <Tag><GitBranch size={10} aria-hidden="true" /> map</Tag>
          {state && state !== 'pending' && (
            <span className="fn-meta" style={isFailure ? { color: 'var(--danger-fg)' } : isWarning ? { color: 'var(--warning-fg)' } : undefined}>
              {STATE_LABEL[state]}
            </span>
          )}
        </div>

        {/* item_expr snippet (collapsed view) */}
        {!expanded && itemExpr && (
          <p className="fn-meta m-0 mb-1 truncate" title={itemExpr}>{exprSnippet}</p>
        )}

        {/* item_var + max_concurrency hint */}
        <div className="flex items-center gap-3">
          <span className="fn-meta">var <span style={{ color: 'var(--text)' }}>{itemVar}</span></span>
          {maxConc > 0 && <span className="fn-meta">conc <span style={{ color: 'var(--text)' }}>{maxConc}</span></span>}
        </div>

        {/* Expanded body: list of body task keys */}
        {expanded && bodyTasks.length > 0 && (
          <div className="mt-2 border-t border-border pt-2">
            <p className="t-label mb-1">Body ({bodyTasks.length} task{bodyTasks.length !== 1 ? 's' : ''})</p>
            {bodyTasks.map((bt) => (
              <div key={bt.key} className="flex items-center gap-1.5 border-b border-border py-1">
                <span className="fn-key flex-1" style={{ fontWeight: 400, fontSize: 12 }}>{bt.key}</span>
                <span className="fn-meta shrink-0 uppercase">{bt.kind}</span>
              </div>
            ))}
          </div>
        )}

        {expanded && bodyTasks.length === 0 && (
          <p className="fn-meta m-0 mt-2 border-t border-border pt-2 text-center">No body tasks defined</p>
        )}

        {/* Duration */}
        {durationLabel && <p className="fn-meta m-0 mt-0.5">{durationLabel}</p>}

        {/* Error excerpt */}
        {isFailure && taskRun?.error && (
          <p className="fn-err m-0 mt-1" title={taskRun.error}>
            {taskRun.error.slice(0, 60)}{taskRun.error.length > 60 ? '…' : ''}
          </p>
        )}
      </div>

      {/* Source handle (bottom) */}
      <Handle type="source" position={Position.Bottom} className={HANDLE_CLASS} />
    </div>
  )
}

export default memo(MapGroupNode)
