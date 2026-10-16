/**
 * TaskNode.jsx — custom React Flow node for a flow task.
 *
 * Displays:
 *   - Task key (title, mono)
 *   - Kind tag (query | python | agent | noop …)
 *   - Square status dot keyed by task_run.state
 *   - Attempt count (when retrying or attempt > 0)
 *   - Duration (when finished)
 *   - Error excerpt (when failed/timed_out)
 *   - Source (bottom) and Target (top) handles
 *
 * Swiss chrome (see nodeChrome.tsx): hairline box, 2px ink rule, no tints.
 * State is told by the dot and, for failure / warning, the border:
 *   failed / timed_out          danger border
 *   upstream_failed / retrying  warning border
 */

import { memo } from 'react'
import { Handle, Position } from 'reactflow'
import { Database, Layers, Filter } from 'lucide-react'
import Tag from '../../components/ui/Tag.jsx'
import {
  StateDot, STATE_LABEL, FAILURE_STATES, WARNING_STATES, HANDLE_CLASS, nodeClass, fmtDuration,
} from './nodeChrome.jsx'

function TaskNode({ data, selected }) {
  const { task, taskRun, cellBadges } = data
  const kind = task?.kind ?? 'noop'
  const badges = cellBadges ?? {}
  const hasRunWhen = !!badges.runWhen
  const state = taskRun?.state ?? null

  const isFailure = !!state && FAILURE_STATES.has(state)
  const isWarning = !!state && WARNING_STATES.has(state)
  const durationLabel = fmtDuration(taskRun?.duration_s)

  return (
    <div className={nodeClass({ selected, failure: isFailure, warning: isWarning, conditional: hasRunWhen })}>
      {/* Target handle (top) */}
      <Handle type="target" position={Position.Top} className={HANDLE_CLASS} />

      {/* Node body */}
      <div className="select-none px-3 py-2.5">
        {/* Header row: key + status dot */}
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <span className="fn-key flex-1">{task?.key ?? '(untitled)'}</span>
          <StateDot state={state} className="shrink-0" />
        </div>

        {/* Kind tag + state label */}
        <div className="flex flex-wrap items-center gap-1.5">
          <Tag>{kind}</Tag>
          {/* State label (only when a run is active) */}
          {state && state !== 'pending' && (
            <span className="fn-meta" style={isFailure ? { color: 'var(--danger-fg)' } : isWarning ? { color: 'var(--warning-fg)' } : undefined}>
              {STATE_LABEL[state]}
            </span>
          )}
        </div>

        {/* Cell-config tags (v4 "cells, not kinds"): materialized / for_each /
            run_when derived from config in specGraph.deriveCellBadges. */}
        {(badges.materialized || badges.forEach || badges.runWhen) && (
          <div className="mt-1.5 flex flex-wrap items-center gap-1">
            {badges.materialized && (
              <Tag
                caps={false}
                title={badges.materialized.target ? `→ table: ${badges.materialized.target}` : `→ table (${badges.materialized.kind})`}
              >
                <Database size={10} className="shrink-0" aria-hidden="true" />
                → table ({badges.materialized.kind})
              </Tag>
            )}
            {badges.forEach && (
              <Tag caps={false} title={`for each: ${badges.forEach.items}`}>
                <Layers size={10} className="shrink-0" aria-hidden="true" />
                for each
              </Tag>
            )}
            {badges.runWhen && (
              <Tag caps={false} title={`runs when: ${badges.runWhen}`}>
                <Filter size={10} className="shrink-0" aria-hidden="true" />
                if
              </Tag>
            )}
          </div>
        )}

        {/* Attempt count (retrying or failed with attempt > 0) */}
        {taskRun && (taskRun.attempt ?? 0) > 0 && (
          <p className="fn-meta m-0 mt-1" style={{ color: 'var(--warning-fg)' }}>
            attempt {(taskRun.attempt ?? 0) + 1}
          </p>
        )}

        {/* Duration */}
        {durationLabel && <p className="fn-meta m-0 mt-0.5">{durationLabel}</p>}

        {/* Error excerpt */}
        {isFailure && taskRun?.error && (
          <p className="fn-err m-0 mt-1" title={taskRun.error}>
            {taskRun.error.slice(0, 60)}{taskRun.error.length > 60 ? '…' : ''}
          </p>
        )}

        {/* Retries hint (from spec) */}
        {!taskRun && (task?.retries ?? 0) > 0 && (
          <p className="fn-meta m-0 mt-1">retries: {task.retries}</p>
        )}
      </div>

      {/* Source handle (bottom) */}
      <Handle type="source" position={Position.Bottom} className={HANDLE_CLASS} />
    </div>
  )
}

export default memo(TaskNode)
