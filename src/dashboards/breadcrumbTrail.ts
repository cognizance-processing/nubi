/**
 * breadcrumbTrail.ts — pure trail logic for BreadcrumbWidget.
 *
 * Kept out of the component (same rationale as legacyWidgetText.ts) so the
 * branching — append vs hierarchical, clear-downstream, display slicing — is
 * testable under node:test without React.
 *
 * Drill order is the order of `props.variables` (outermost first). In
 * hierarchical mode each tracked variable owns one level of the trail, at its
 * index in that list.
 *
 * Future importer (not implemented): legacy BasicBreadcrumb settings map as
 *   hierarchicalTrail      → trailMode: 'hierarchical'
 *   resetDownstreamFilters → clearDownstream
 *   showHistory === false  → trailDisplay: 'current'
 *   trackedWidgetIds       → variables (via the board's filter → variable mapping)
 */

import { chipLabel, isEmptyValue } from "./legacyWidgetText.js";

export type TrailStep = {
  /** Variable that owns this level (hierarchical) or that changed (append). */
  name: string;
  label: string;
  /** Values of every tracked variable to restore when this crumb is clicked. */
  snapshot: Record<string, unknown>;
};

type Values = Record<string, unknown>;
type Labels = Record<string, string>;

/** Crumb text: "Region: West", or the bare value when the label is ''. */
export function stepLabel(
  name: string,
  value: unknown,
  labels: Labels = {},
): string {
  const text = chipLabel(value);
  const prefix = labels[name] ?? name;
  return prefix === "" ? text : `${prefix}: ${text}`;
}

/**
 * Append mode (the default, and the original behaviour): the first tracked
 * variable that changed to a non-empty value appends one step carrying the
 * full snapshot; the trail keeps its newest `maxSteps`. Returns null when
 * nothing should be appended.
 */
export function appendModeUpdate(
  steps: TrailStep[],
  prev: Values,
  snapshot: Values,
  tracked: string[],
  labels: Labels,
  maxSteps: number,
): TrailStep[] | null {
  for (const name of tracked) {
    const before = prev[name];
    const after = snapshot[name];
    if (JSON.stringify(before) === JSON.stringify(after)) continue;
    if (isEmptyValue(after)) continue; // clearing a filter drills OUT, it isn't a new step
    const next = [
      ...steps,
      { name, label: stepLabel(name, after, labels), snapshot },
    ];
    return next.length > maxSteps ? next.slice(-maxSteps) : next;
  }
  return null;
}

/** One step per tracked variable, in drill order; the latest step for a name wins. */
export function normalizeStepsByVariableOrder(
  steps: TrailStep[],
  tracked: string[],
): TrailStep[] {
  const byName = new Map<string, TrailStep>();
  for (const step of steps) {
    if (step?.name && tracked.includes(step.name)) byName.set(step.name, step);
  }
  return tracked
    .map((name) => byName.get(name))
    .filter((s): s is TrailStep => Boolean(s));
}

/**
 * Tracked values up to and including `upToIndex`; variables below that level
 * are null when `clearDownstream`, otherwise their live value.
 */
export function buildSnapshot(
  tracked: string[],
  values: Values,
  upToIndex: number,
  clearDownstream: boolean,
): Values {
  const out: Values = {};
  tracked.forEach((name, i) => {
    out[name] =
      i <= upToIndex || !clearDownstream ? (values[name] ?? null) : null;
  });
  return out;
}

function sameValue(a: unknown, b: unknown): boolean {
  // null / undefined / '' / [] all mean "unset" — a variable cleared to null
  // must not read as a change from an absent key.
  if (isEmptyValue(a) && isEmptyValue(b)) return true;
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Index (in drill order) of the outermost tracked variable that changed, or -1. */
export function findHierarchicalChange(
  prev: Values,
  snapshot: Values,
  tracked: string[],
): number {
  return tracked.findIndex((name) => !sameValue(prev[name], snapshot[name]));
}

/** Tracked variables below `index` that currently hold a value (to be set to null). */
export function downstreamToClear(
  tracked: string[],
  values: Values,
  index: number,
): string[] {
  return tracked.slice(index + 1).filter((name) => !isEmptyValue(values[name]));
}

/**
 * Re-add crumbs below `fromIndex` from live values, stopping at the first
 * unset level — used when downstream values are kept rather than cleared.
 */
function appendDownstreamSteps(
  trail: TrailStep[],
  fromIndex: number,
  tracked: string[],
  values: Values,
  labels: Labels,
): TrailStep[] {
  const out = [...trail];
  for (let i = fromIndex + 1; i < tracked.length; i++) {
    const name = tracked[i];
    if (isEmptyValue(values[name])) break;
    out.push({
      name,
      label: stepLabel(name, values[name], labels),
      snapshot: buildSnapshot(tracked, values, i, false),
    });
  }
  return out;
}

/**
 * Hierarchical update for a change at drill level `index`:
 *  - new value  → replace that level, drop the levels below it;
 *  - cleared    → drop that level and the levels below it.
 * With `clearDownstream` off, levels below whose variables still hold a value
 * are rebuilt from those live values.
 */
export function hierarchicalTrail(
  steps: TrailStep[],
  index: number,
  tracked: string[],
  values: Values,
  labels: Labels,
  clearDownstream: boolean,
): TrailStep[] {
  const name = tracked[index];
  if (name === undefined) return normalizeStepsByVariableOrder(steps, tracked);
  const head = normalizeStepsByVariableOrder(steps, tracked).filter(
    (step) => tracked.indexOf(step.name) < index,
  );
  const value = values[name];
  if (!isEmptyValue(value)) {
    head.push({
      name,
      label: stepLabel(name, value, labels),
      snapshot: buildSnapshot(tracked, values, index, clearDownstream),
    });
  }
  return clearDownstream
    ? head
    : appendDownstreamSteps(head, index, tracked, values, labels);
}

/**
 * Crumbs to render: 'full' keeps the newest `maxSteps`, 'current' only the
 * deepest. The result is always a suffix of `steps`, so the original index of
 * displayed[k] is `steps.length - displayed.length + k`.
 */
export function displayedSteps(
  steps: TrailStep[],
  {
    trailDisplay = "full",
    maxSteps = 10,
  }: { trailDisplay?: "full" | "current"; maxSteps?: number } = {},
): TrailStep[] {
  if (trailDisplay === "current")
    return steps.length > 0 ? [steps[steps.length - 1]] : [];
  return steps.length > maxSteps ? steps.slice(-maxSteps) : steps;
}
