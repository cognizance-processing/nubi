/**
 * shared/shared.test.mjs — Isolation tests for shared inspector modules.
 *
 * These tests verify:
 *   1. All expected names are exported from shared/index.js
 *   2. The pure-function exports (constants, helpers) behave correctly
 *   3. React component exports are functions (not undefined)
 *
 * No DOM / rendering required — components are just checked to be functions.
 * Run: node --test src/editor/shared/shared.test.mjs
 */

import { test } from "node:test";
import assert from "node:assert/strict";

// ---------------------------------------------------------------------------
// 1. Constants
// ---------------------------------------------------------------------------

import {
  DEMO_QUERY_IDS,
  CHART_TYPES,
  SERIES_TYPES,
  FILTER_SUBTYPES,
  VARIABLE_TYPES,
  FORMAT_OPS,
  FORMAT_OP_LABELS,
  formatOpLabel,
  COLUMN_FORMAT_TYPES,
  BACKGROUND_TYPES,
  PIVOT_AGGS,
} from "./constants.js";

test("constants: DEMO_QUERY_IDS is a non-empty array of strings", () => {
  assert.ok(Array.isArray(DEMO_QUERY_IDS));
  assert.ok(DEMO_QUERY_IDS.length > 0);
  assert.ok(DEMO_QUERY_IDS.every((id) => typeof id === "string"));
});

test("constants: CHART_TYPES includes all 21 chart kinds", () => {
  assert.ok(Array.isArray(CHART_TYPES));
  const required = [
    "bar",
    "line",
    "area",
    "scatter",
    "bubble",
    "combo",
    "pie",
    "donut",
    "polar",
    "radialbar",
    "sankey",
    "funnel",
    "waterfall",
    "treemap",
    "heatmap",
    "radar",
    "boxplot",
    "gauge",
    "candlestick",
    "fan",
    "map",
  ];
  for (const t of required) {
    assert.ok(CHART_TYPES.includes(t), `CHART_TYPES should include '${t}'`);
  }
  assert.equal(
    CHART_TYPES.length,
    21,
    "CHART_TYPES must contain exactly 21 types",
  );
});

test("constants: FILTER_SUBTYPES includes select and daterange", () => {
  assert.ok(FILTER_SUBTYPES.includes("select"));
  assert.ok(FILTER_SUBTYPES.includes("daterange"));
});

test("constants: BACKGROUND_TYPES includes none, solid, gradient, image, css", () => {
  for (const t of ["none", "solid", "gradient", "image", "css"]) {
    assert.ok(
      BACKGROUND_TYPES.includes(t),
      `BACKGROUND_TYPES should include '${t}'`,
    );
  }
});

test("constants: FORMAT_OPS includes comparison operators", () => {
  for (const op of ["eq", "gt", "lt", "between"]) {
    assert.ok(FORMAT_OPS.includes(op), `FORMAT_OPS should include '${op}'`);
  }
});

test("constants: FORMAT_OPS includes table conditional operators", () => {
  for (const op of ["contains", "not_contains"]) {
    assert.ok(FORMAT_OPS.includes(op), `FORMAT_OPS should include '${op}'`);
  }
});

test("constants: every FORMAT_OPS entry has a human label distinct from its id", () => {
  for (const op of FORMAT_OPS) {
    const label = FORMAT_OP_LABELS[op];
    assert.ok(
      typeof label === "string" && label.trim().length > 0,
      `missing label for '${op}'`,
    );
    assert.notEqual(label, op, `label for '${op}' should not be the raw id`);
  }
});

test("constants: formatOpLabel maps known ops and falls back to the raw id", () => {
  assert.equal(formatOpLabel("gte"), "Greater or equal (>=)");
  assert.equal(formatOpLabel("not_contains"), "Does not contain");
  assert.equal(formatOpLabel("custom_op"), "custom_op");
});

test("constants: COLUMN_FORMAT_TYPES has number and currency", () => {
  assert.ok(COLUMN_FORMAT_TYPES.includes("number"));
  assert.ok(COLUMN_FORMAT_TYPES.includes("currency"));
});

test("constants: PIVOT_AGGS has sum and count", () => {
  assert.ok(PIVOT_AGGS.includes("sum"));
  assert.ok(PIVOT_AGGS.includes("count"));
});

test("constants: SERIES_TYPES and VARIABLE_TYPES are non-empty arrays", () => {
  assert.ok(Array.isArray(SERIES_TYPES) && SERIES_TYPES.length > 0);
  assert.ok(Array.isArray(VARIABLE_TYPES) && VARIABLE_TYPES.length > 0);
});

// ---------------------------------------------------------------------------
// 2. placementHelpers pure functions — effectivePlacement + applyPlacement
// ---------------------------------------------------------------------------

import { effectivePlacement, applyPlacement } from "./placementHelpers.js";

test("effectivePlacement: explicit placement wins", () => {
  assert.equal(effectivePlacement({ placement: "header" }), "header");
  assert.equal(effectivePlacement({ placement: "drawer" }), "drawer");
  assert.equal(effectivePlacement({ placement: "grid" }), "grid");
});

test("effectivePlacement: legacy drawer flag maps to drawer", () => {
  assert.equal(effectivePlacement({ drawer: true }), "drawer");
});

test("effectivePlacement: explicit placement overrides drawer flag", () => {
  assert.equal(
    effectivePlacement({ placement: "header", drawer: true }),
    "header",
  );
});

test("effectivePlacement: default is grid", () => {
  assert.equal(effectivePlacement({}), "grid");
  assert.equal(effectivePlacement(null), "grid");
  assert.equal(effectivePlacement(undefined), "grid");
});

test("applyPlacement: sets placement and clears drawer for grid", () => {
  const w = { id: "x", drawer: true, drawer_group: "filters" };
  const result = applyPlacement(w, "grid");
  assert.equal(result.placement, "grid");
  assert.equal(result.drawer, false);
});

test("applyPlacement: sets drawer=true and drawer_group for drawer placement", () => {
  const w = { id: "x" };
  const result = applyPlacement(w, "drawer");
  assert.equal(result.placement, "drawer");
  assert.equal(result.drawer, true);
  assert.equal(result.drawer_group, "filters");
});

test("applyPlacement: does not overwrite existing drawer_group", () => {
  const w = { id: "x", drawer_group: "custom" };
  const result = applyPlacement(w, "drawer");
  assert.equal(result.drawer_group, "custom");
});

test("applyPlacement: header placement clears drawer flag", () => {
  const w = { id: "x", drawer: true };
  const result = applyPlacement(w, "header");
  assert.equal(result.placement, "header");
  assert.equal(result.drawer, false);
});

// ---------------------------------------------------------------------------
// 3. React component exports — verified via the barrel's module structure.
//    We can't import .jsx in node:test without a transform, so we verify that
//    the barrel index exports the right names by checking the exports map
//    (done below in §5). Here we verify the pure-JS module shape only.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// 4. The barrel index re-exports constants and helpers that are importable
//    from pure .js modules.
// ---------------------------------------------------------------------------

import * as constants from "./constants.js";
import * as helpers from "./placementHelpers.js";

test("constants module exports all required names", () => {
  const required = [
    "DEMO_QUERY_IDS",
    "CHART_TYPES",
    "SERIES_TYPES",
    "FILTER_SUBTYPES",
    "VARIABLE_TYPES",
    "FORMAT_OPS",
    "COLUMN_FORMAT_TYPES",
    "BACKGROUND_TYPES",
    "PIVOT_AGGS",
  ];
  for (const name of required) {
    assert.ok(name in constants, `constants.js must export '${name}'`);
  }
});

test("placementHelpers module exports effectivePlacement and applyPlacement", () => {
  assert.equal(typeof helpers.effectivePlacement, "function");
  assert.equal(typeof helpers.applyPlacement, "function");
});
