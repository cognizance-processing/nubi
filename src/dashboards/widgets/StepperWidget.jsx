/**
 * StepperWidget.jsx — a container that shows one child widget at a time.
 *
 * Reproduces the legacy in-tile drill-down: a step bar across the top of a
 * single grid tile, with the active step's widget filling the rest. Clicking a
 * data point in step N advances to step N+1 filtered by the clicked value
 * (the child declares `onClick: { setVar, stepNext: true }` — the value half is
 * the existing cross-filter bus, the advance half is StepperContext).
 *
 * Stepping BACK clears the variables written by the steps being left, so a
 * drill-down doesn't leave a stale filter pinned on the widgets behind it.
 *
 * Spec shape
 * ----------
 *   {
 *     type: 'stepper',
 *     props: {
 *       steps: [ { label: string, widget: <widget spec> }, ... ],
 *       orientation?: 'horizontal' | 'vertical',   // default 'horizontal'
 *       variant?:     'dots' | 'labels',           // default 'labels'
 *       showTitle?:   boolean,
 *       title?:       string,
 *     }
 *   }
 *
 * Children are full widget specs and are rendered through `renderChild`, which
 * SpecRenderer supplies — that keeps the recursion out of this module and avoids
 * a circular import.
 */

import { useCallback, useMemo, useState } from 'react'
import { StepperProvider } from '../StepperContext.jsx'
import { useSetVariable } from '../VariableStore.jsx'
import { clampStep, variablesToClear } from '../stepper.js'

function cx(...parts) {
  return parts.filter(Boolean).join(' ')
}

export default function StepperWidget({ widget, renderChild }) {
  const steps = useMemo(() => {
    const raw = widget?.props?.steps
    return Array.isArray(raw) ? raw.filter(Boolean) : []
  }, [widget])

  const [active, setActive] = useState(0)
  const setVariable = useSetVariable()

  const orientation = widget?.props?.orientation === 'vertical' ? 'vertical' : 'horizontal'
  const variant = widget?.props?.variant === 'dots' ? 'dots' : 'labels'

  // Moving to `next`: if it is BEHIND the current step, clear the variables the
  // steps we're leaving had written, so the drill-down filter doesn't persist.
  // Clearing variables is a side effect, so it happens here rather than inside
  // the setState updater (which React may invoke twice under StrictMode).
  const goTo = useCallback((next) => {
    const target = clampStep(next, steps.length)
    for (const name of variablesToClear(steps, active, target)) {
      setVariable(name, null)
    }
    setActive(target)
  }, [steps, active, setVariable])

  const advance = useCallback(() => {
    setActive((current) => clampStep(current + 1, steps.length))
  }, [steps.length])

  if (steps.length === 0) {
    return (
      <div className="flex items-center justify-center h-full px-4 text-sm text-muted">
        No steps configured.
      </div>
    )
  }

  const activeStep = steps[Math.min(active, steps.length - 1)]

  const stepBar = (
    <div
      role="tablist"
      aria-orientation={orientation}
      className={cx(
        'flex gap-1 shrink-0',
        orientation === 'vertical'
          ? 'flex-col pr-3 border-r border-border'
          : 'flex-row items-center pb-2 mb-2 border-b border-border',
      )}
    >
      {steps.map((step, i) => {
        const isActive = i === active
        const isDone = i < active
        return (
          <button
            key={step.id ?? `${step.label ?? 'step'}-${i}`}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => goTo(i)}
            className={cx(
              'flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-medium transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              isActive ? 'text-fg bg-surface-2' : 'text-muted hover:text-fg hover:bg-surface-2/60',
            )}
          >
            <span
              aria-hidden="true"
              className={cx(
                'inline-block w-2 h-2 rounded-full shrink-0',
                isActive || isDone ? 'bg-primary' : 'bg-border',
              )}
            />
            {variant === 'labels' && <span className="truncate">{step.label ?? `Step ${i + 1}`}</span>}
          </button>
        )
      })}
    </div>
  )

  return (
    <div
      className={cx(
        'flex w-full h-full min-h-0',
        orientation === 'vertical' ? 'flex-row' : 'flex-col',
      )}
    >
      {stepBar}
      <div className="flex-1 min-h-0 min-w-0">
        <StepperProvider advance={advance}>
          {renderChild ? renderChild(activeStep.widget) : null}
        </StepperProvider>
      </div>
    </div>
  )
}
