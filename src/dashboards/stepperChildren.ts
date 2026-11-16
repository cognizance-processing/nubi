/**
 * Flatten a widget list so stepper children (props.steps[].widget, nested to
 * any depth) sit alongside the top-level widgets. SpecRenderer builds its
 * provider-table map from this list, so a provider-bound widget inside a
 * stepper gets its rows like a grid widget does.
 */
export function withStepperChildren(widgets: unknown): Record<string, any>[] {
  const out: Record<string, any>[] = []
  const visit = (list: unknown) => {
    if (!Array.isArray(list)) return
    for (const w of list) {
      if (!w || typeof w !== 'object') continue
      out.push(w)
      if (w.type === 'stepper' && Array.isArray(w.props?.steps)) {
        visit(w.props.steps.map((s) => s?.widget))
      }
    }
  }
  visit(widgets)
  return out
}
