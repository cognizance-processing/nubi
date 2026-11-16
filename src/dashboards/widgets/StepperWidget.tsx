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
 *       variant?:     'dots' | 'labels' | 'numbered', // default 'labels'
 *       display?:     'one' | 'all',               // default 'one'
 *       title?:       string,                      // heading above the step bar
 *       titleSize?:   number,                      // heading size in px
 *       minHeight?:   number,                      // min height of one 'all' row, px
 *       hideLabels?:  boolean,                     // 'all': no caption above children
 *     }
 *   }
 *
 * `variant: 'numbered'` spreads the steps across the tile as numbered circles
 * joined by a line. In `display: 'all'`, a step may carry `span` (a share of a
 * 12-column row, default: equal) and `height` (px), so a group can mix three
 * tables across with a chart underneath rather than splitting the tile evenly.
 *
 * `display: 'all'` drops the step bar and lays every child out at once, side by
 * side (or stacked, when orientation is 'vertical'), each under its own label.
 * That is the OTHER legacy container — a group of widgets sharing one tile,
 * with no drill-down — and it is the same children in the same spec shape, so a
 * board can switch between "one at a time" and "all at once" without being
 * rebuilt. Cross-filter still works in this mode; only the stepping does not
 * apply, so no StepperProvider is installed.
 *
 * Children are full widget specs and are rendered through `renderChild`, which
 * SpecRenderer supplies — that keeps the recursion out of this module and avoids
 * a circular import.
 */

import { useCallback, useMemo, useState } from 'react'
import { styleToCss } from '../widgetHtml.js'
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
  const rawVariant = widget?.props?.variant
  const variant = rawVariant === 'dots' || rawVariant === 'numbered' ? rawVariant : 'labels'
  const showAll = widget?.props?.display === 'all'
  const title = widget?.props?.title
  const heading = title ? (
    <p
      className="shrink-0 text-fg mb-3"
      style={{ fontSize: widget?.props?.titleSize ?? 24, lineHeight: 1.2 }}
    >
      {title}
    </p>
  ) : null

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

  // Group mode: every child at once, no step bar, no stepping context.
  if (showAll) {
    const hasLayout = steps.some(s => s.span != null || s.height != null)
    const hideLabels = widget?.props?.hideLabels === true
    const caption = (step) => (step.label && !hideLabels ? (
      <p className="shrink-0 text-[11px] font-medium text-muted truncate mb-1">{step.label}</p>
    ) : null)
    // With per-child spans the group is a grid (12 columns unless props.columns
    // says otherwise) that scrolls within its tile; without them the original
    // equal split is unchanged.
    if (hasLayout) {
      const columns = Number(widget?.props?.columns) > 0 ? Number(widget.props.columns) : 12
      const cellStyle = styleToCss(widget?.props?.cellStyle)
      return (
        <div className="flex flex-col w-full h-full min-h-0">
          {heading}
          <div
            className="grid gap-3 flex-1 min-h-0 overflow-y-auto"
            style={{
              gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
              gap: widget?.props?.gap,
              gridAutoRows: widget?.props?.minHeight ? `minmax(${widget.props.minHeight}px, auto)` : undefined,
              alignContent: 'start',
            }}
          >
            {steps.map((step, i) => (
              <div
                key={step.id ?? `${step.label ?? 'item'}-${i}`}
                className="min-w-0 min-h-0 flex flex-col"
                style={{ gridColumn: `span ${Math.min(columns, step.span ?? columns)}`, height: step.height, ...cellStyle }}
              >
                {caption(step)}
                <div className="flex-1 min-h-0 min-w-0">
                  {renderChild ? renderChild(step.widget) : null}
                </div>
              </div>
            ))}
          </div>
        </div>
      )
    }
    return (
      <div className="flex flex-col w-full h-full min-h-0">
        {heading}
        <div
          className={cx(
            'flex flex-1 w-full min-h-0 gap-3',
            orientation === 'vertical' ? 'flex-col' : 'flex-row',
          )}
        >
          {steps.map((step, i) => (
            <div
              key={step.id ?? `${step.label ?? 'item'}-${i}`}
              className="flex-1 min-w-0 min-h-0 flex flex-col"
            >
              {caption(step)}
              <div className="flex-1 min-h-0 min-w-0">
                {renderChild ? renderChild(step.widget) : null}
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  const activeStep = steps[Math.min(active, steps.length - 1)]

  const stepBar = variant === 'numbered' ? (
    <div role="tablist" aria-orientation="horizontal" className="flex items-center shrink-0 mb-4">
      {steps.map((step, i) => {
        const isActive = i === active
        return (
          <div
            key={step.id ?? `${step.label ?? 'step'}-${i}`}
            className={cx('flex items-center min-w-0', i < steps.length - 1 && 'flex-1')}
          >
            <button
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => goTo(i)}
              className="flex items-center gap-2.5 px-2 py-1 rounded-full shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span
                aria-hidden="true"
                className="inline-flex items-center justify-center rounded-full text-xs shrink-0"
                style={{
                  width: 30,
                  height: 30,
                  color: 'var(--ink-fg)',
                  background: isActive
                    ? (widget?.style?.color ?? 'var(--ink)')
                    : (widget?.style?.mutedColor ?? 'var(--border-control)'),
                }}
              >
                {i + 1}
              </span>
              <span className="text-sm text-fg whitespace-nowrap">{step.label ?? `Step ${i + 1}`}</span>
            </button>
            {i < steps.length - 1 && <span aria-hidden="true" className="flex-1 h-px mx-2 min-w-4 bg-border" />}
          </div>
        )
      })}
    </div>
  ) : (
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
                'inline-block w-2 h-2 shrink-0',
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
        'flex flex-col w-full h-full min-h-0',
      )}
    >
      {heading}
      <div
        className={cx(
          'flex flex-1 w-full min-h-0',
          orientation === 'vertical' && variant !== 'numbered' ? 'flex-row' : 'flex-col',
        )}
      >
        {stepBar}
        <div className="flex-1 min-h-0 min-w-0">
          <StepperProvider advance={advance}>
            {renderChild ? renderChild(activeStep.widget) : null}
          </StepperProvider>
        </div>
      </div>
    </div>
  )
}
