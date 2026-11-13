/**
 * breadcrumbTrail.test.ts — pure trail logic behind BreadcrumbWidget:
 * append (default), hierarchical replace/trim, clear-downstream on/off and
 * full/current display. Rendering is not covered here.
 *
 * Run (Windows-safe): node --import tsx --test "src/dashboards/breadcrumbTrail.test.ts"
 */

import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  appendModeUpdate,
  buildSnapshot,
  displayedSteps,
  downstreamToClear,
  findHierarchicalChange,
  hierarchicalTrail,
  normalizeStepsByVariableOrder,
  stepLabel,
} from "./breadcrumbTrail.js";

const TRACKED = ["region", "store", "sku"];
const step = (name, label, snapshot = {}) => ({ name, label, snapshot });

describe("stepLabel", () => {
  test("prefixes with the variable name by default", () => {
    assert.equal(stepLabel("region", "West"), "region: West");
  });
  test("uses the authored label, and a bare value for an empty label", () => {
    assert.equal(
      stepLabel("region", "West", { region: "Region" }),
      "Region: West",
    );
    assert.equal(stepLabel("region", "West", { region: "" }), "West");
  });
});

describe("appendModeUpdate (default trail mode)", () => {
  test("appends one step for the first changed non-empty variable", () => {
    const prev = { region: null, store: null, sku: null };
    const snap = { region: "West", store: null, sku: null };
    const next = appendModeUpdate([], prev, snap, TRACKED, {}, 10);
    assert.deepEqual(next, [
      { name: "region", label: "region: West", snapshot: snap },
    ]);
  });

  test("only the first changed variable appends, even if several changed", () => {
    const prev = { region: null, store: null, sku: null };
    const snap = { region: "West", store: "42", sku: null };
    const next = appendModeUpdate([], prev, snap, TRACKED, {}, 10);
    assert.equal(next.length, 1);
    assert.equal(next[0].name, "region");
  });

  test("a cleared variable is skipped, not appended", () => {
    const prev = { region: "West", store: null, sku: null };
    const snap = { region: null, store: null, sku: null };
    assert.equal(appendModeUpdate([], prev, snap, TRACKED, {}, 10), null);
  });

  test("a cleared variable does not hide a later change", () => {
    const prev = { region: "West", store: null, sku: null };
    const snap = { region: null, store: "42", sku: null };
    const next = appendModeUpdate([], prev, snap, TRACKED, {}, 10);
    assert.equal(next[0].name, "store");
  });

  test("re-selecting an upstream variable appends, never replaces", () => {
    const s1 = step("region", "region: West");
    const s2 = step("store", "store: 42");
    const prev = { region: "West", store: "42", sku: null };
    const snap = { region: "East", store: "42", sku: null };
    const next = appendModeUpdate([s1, s2], prev, snap, TRACKED, {}, 10);
    assert.deepEqual(
      next.map((s) => s.label),
      ["region: West", "store: 42", "region: East"],
    );
  });

  test("keeps only the newest maxSteps", () => {
    const steps = [step("region", "a"), step("region", "b")];
    const next = appendModeUpdate(
      steps,
      { region: "b" },
      { region: "c" },
      ["region"],
      {},
      2,
    );
    assert.deepEqual(
      next.map((s) => s.label),
      ["b", "region: c"],
    );
  });

  test("no change → null", () => {
    const snap = { region: "West" };
    assert.equal(
      appendModeUpdate([], snap, { ...snap }, ["region"], {}, 10),
      null,
    );
  });
});

describe("normalizeStepsByVariableOrder", () => {
  test("one step per variable, in drill order, latest wins", () => {
    const steps = [
      step("store", "s1"),
      step("region", "r1"),
      step("store", "s2"),
      step("gone", "x"),
    ];
    assert.deepEqual(
      normalizeStepsByVariableOrder(steps, TRACKED).map((s) => s.label),
      ["r1", "s2"],
    );
  });
});

describe("buildSnapshot", () => {
  const values = { region: "West", store: "42", sku: "A" };
  test("clearDownstream nulls levels after the index", () => {
    assert.deepEqual(buildSnapshot(TRACKED, values, 0, true), {
      region: "West",
      store: null,
      sku: null,
    });
  });
  test("without clearDownstream keeps live values", () => {
    assert.deepEqual(buildSnapshot(TRACKED, values, 0, false), values);
  });
  test("missing values become null", () => {
    assert.deepEqual(buildSnapshot(TRACKED, {}, 2, false), {
      region: null,
      store: null,
      sku: null,
    });
  });
});

describe("findHierarchicalChange", () => {
  test("returns the outermost changed index", () => {
    const prev = { region: "West", store: "42", sku: "A" };
    assert.equal(
      findHierarchicalChange(
        prev,
        { region: "West", store: "7", sku: "B" },
        TRACKED,
      ),
      1,
    );
  });
  test('null and undefined are the same "unset"', () => {
    assert.equal(findHierarchicalChange({ region: null }, {}, TRACKED), -1);
    assert.equal(
      findHierarchicalChange({ region: [] }, { region: null }, TRACKED),
      -1,
    );
  });
});

describe("hierarchicalTrail", () => {
  const r = step("region", "region: West");
  const s = step("store", "store: 42");
  const k = step("sku", "sku: A");

  test("adds a level for a fresh drill", () => {
    const values = { region: "West", store: "42", sku: null };
    const next = hierarchicalTrail([r], 1, TRACKED, values, {}, true);
    assert.deepEqual(
      next.map((x) => x.label),
      ["region: West", "store: 42"],
    );
    assert.deepEqual(next[1].snapshot, {
      region: "West",
      store: "42",
      sku: null,
    });
  });

  test("upstream change replaces that level and trims below (clearDownstream on)", () => {
    const values = { region: "East", store: "42", sku: "A" };
    const next = hierarchicalTrail([r, s, k], 0, TRACKED, values, {}, true);
    assert.deepEqual(
      next.map((x) => x.label),
      ["region: East"],
    );
    assert.deepEqual(next[0].snapshot, {
      region: "East",
      store: null,
      sku: null,
    });
  });

  test("at most one step per variable", () => {
    const values = { region: "West", store: "7", sku: null };
    const next = hierarchicalTrail([r, s], 1, TRACKED, values, {}, true);
    assert.deepEqual(
      next.map((x) => x.name),
      ["region", "store"],
    );
    assert.equal(next[1].label, "store: 7");
  });

  test("clearDownstream off: downstream crumbs rebuilt from live values", () => {
    const values = { region: "East", store: "42", sku: "A" };
    const next = hierarchicalTrail([r, s, k], 0, TRACKED, values, {}, false);
    assert.deepEqual(
      next.map((x) => x.label),
      ["region: East", "store: 42", "sku: A"],
    );
    assert.deepEqual(next[0].snapshot, values);
  });

  test("clearDownstream off: rebuild stops at the first unset level", () => {
    const values = { region: "East", store: null, sku: "A" };
    const next = hierarchicalTrail([r, s, k], 0, TRACKED, values, {}, false);
    assert.deepEqual(
      next.map((x) => x.label),
      ["region: East"],
    );
  });

  test("clear at a level trims it and everything below (clearDownstream on)", () => {
    const values = { region: "West", store: null, sku: "A" };
    const next = hierarchicalTrail([r, s, k], 1, TRACKED, values, {}, true);
    assert.deepEqual(
      next.map((x) => x.label),
      ["region: West"],
    );
  });

  test("clear at a level keeps live downstream crumbs (clearDownstream off)", () => {
    const values = { region: "West", store: null, sku: "A" };
    const next = hierarchicalTrail([r, s, k], 1, TRACKED, values, {}, false);
    assert.deepEqual(
      next.map((x) => x.label),
      ["region: West", "sku: A"],
    );
  });

  test("collapses a legacy append trail", () => {
    const values = { region: "West", store: "42", sku: "B" };
    const appendTrail = [r, s, step("region", "region: West"), k];
    const next = hierarchicalTrail(appendTrail, 2, TRACKED, values, {}, true);
    assert.deepEqual(
      next.map((x) => x.name),
      ["region", "store", "sku"],
    );
    assert.equal(next[2].label, "sku: B");
  });
});

describe("downstreamToClear", () => {
  test("lists only set variables below the index", () => {
    assert.deepEqual(
      downstreamToClear(TRACKED, { region: "W", store: null, sku: "A" }, 0),
      ["sku"],
    );
    assert.deepEqual(
      downstreamToClear(TRACKED, { region: "W", store: "1", sku: "A" }, 2),
      [],
    );
  });
});

describe("displayedSteps", () => {
  const steps = ["a", "b", "c"].map((l) => step("region", l));
  test("full is the default and caps at maxSteps", () => {
    assert.equal(displayedSteps(steps).length, 3);
    assert.deepEqual(
      displayedSteps(steps, { maxSteps: 2 }).map((s) => s.label),
      ["b", "c"],
    );
  });
  test("current shows only the deepest step, ignoring maxSteps", () => {
    assert.deepEqual(
      displayedSteps(steps, { trailDisplay: "current", maxSteps: 10 }).map(
        (s) => s.label,
      ),
      ["c"],
    );
    assert.deepEqual(displayedSteps([], { trailDisplay: "current" }), []);
  });
});
