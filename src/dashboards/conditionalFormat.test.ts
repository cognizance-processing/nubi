/**
 * conditionalFormat.test.mjs — Unit tests for evalRules() and formatValue().
 *
 * Run with:
 *   node --test src/dashboards/conditionalFormat.test.mjs
 *   # or via the project script:
 *   npm run test:dash
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  evalRules,
  formatValue,
  resolveBarMax,
  resolveLinkHref,
  safeHref,
} from "./widgets/conditionalFormat.js";

// ---------------------------------------------------------------------------
// evalRules — no rules / empty
// ---------------------------------------------------------------------------

test("no rules → empty cellStyles and null rowStyle", () => {
  const result = evalRules([], { amount: 500 }, ["amount"]);
  assert.deepEqual(result.cellStyles, {});
  assert.equal(result.rowStyle, null);
});

test("null rules → empty cellStyles and null rowStyle", () => {
  const result = evalRules(null, { amount: 500 }, ["amount"]);
  assert.deepEqual(result.cellStyles, {});
  assert.equal(result.rowStyle, null);
});

// ---------------------------------------------------------------------------
// evalRules — gt (cell scope)
// ---------------------------------------------------------------------------

test("gt rule: value above threshold → cell gets backgroundColor", () => {
  const rules = [
    {
      column: "amount",
      op: "gt",
      value: 100,
      style: { backgroundColor: "#ff0000", color: "#fff" },
      scope: "cell",
    },
  ];
  const row = { amount: 500 };
  const { cellStyles, rowStyle } = evalRules(rules, row, ["amount"]);

  assert.equal(rowStyle, null);
  assert.deepEqual(cellStyles.amount, {
    backgroundColor: "#ff0000",
    color: "#fff",
  });
});

test("gt rule: value at threshold (not strictly greater) → no style", () => {
  const rules = [
    {
      column: "amount",
      op: "gt",
      value: 500,
      style: { backgroundColor: "#ff0000" },
      scope: "cell",
    },
  ];
  const { cellStyles, rowStyle } = evalRules(rules, { amount: 500 }, [
    "amount",
  ]);
  assert.deepEqual(cellStyles, {});
  assert.equal(rowStyle, null);
});

test("gt rule: value below threshold → no style", () => {
  const rules = [
    {
      column: "amount",
      op: "gt",
      value: 100,
      style: { backgroundColor: "#ff0000" },
      scope: "cell",
    },
  ];
  const { cellStyles } = evalRules(rules, { amount: 50 }, ["amount"]);
  assert.deepEqual(cellStyles, {});
});

// ---------------------------------------------------------------------------
// evalRules — between (cell scope)
// ---------------------------------------------------------------------------

test("between rule: value in range → cell style applied", () => {
  const rules = [
    {
      column: "score",
      op: "between",
      value: 70,
      value2: 90,
      style: { backgroundColor: "#fde68a", fontWeight: "bold" },
      scope: "cell",
    },
  ];
  const { cellStyles } = evalRules(rules, { score: 80 }, ["score"]);
  assert.deepEqual(cellStyles.score, {
    backgroundColor: "#fde68a",
    fontWeight: "bold",
  });
});

test("between rule: value exactly at lower bound → style applied", () => {
  const rules = [
    {
      column: "score",
      op: "between",
      value: 70,
      value2: 90,
      style: { backgroundColor: "#fde68a" },
      scope: "cell",
    },
  ];
  const { cellStyles } = evalRules(rules, { score: 70 }, ["score"]);
  assert.deepEqual(cellStyles.score, { backgroundColor: "#fde68a" });
});

test("between rule: value exactly at upper bound → style applied", () => {
  const rules = [
    {
      column: "score",
      op: "between",
      value: 70,
      value2: 90,
      style: { backgroundColor: "#fde68a" },
      scope: "cell",
    },
  ];
  const { cellStyles } = evalRules(rules, { score: 90 }, ["score"]);
  assert.deepEqual(cellStyles.score, { backgroundColor: "#fde68a" });
});

test("between rule: value outside range → no style", () => {
  const rules = [
    {
      column: "score",
      op: "between",
      value: 70,
      value2: 90,
      style: { backgroundColor: "#fde68a" },
      scope: "cell",
    },
  ];
  const { cellStyles } = evalRules(rules, { score: 91 }, ["score"]);
  assert.deepEqual(cellStyles, {});
});

// ---------------------------------------------------------------------------
// evalRules — row scope
// ---------------------------------------------------------------------------

test("row-scope rule: match → rowStyle set, cellStyles empty", () => {
  const rules = [
    {
      column: "status",
      op: "eq",
      value: "error",
      style: { backgroundColor: "#fee2e2", color: "#991b1b" },
      scope: "row",
    },
  ];
  const { cellStyles, rowStyle } = evalRules(
    rules,
    { status: "error", amount: 100 },
    ["status", "amount"],
  );

  assert.deepEqual(cellStyles, {});
  assert.deepEqual(rowStyle, { backgroundColor: "#fee2e2", color: "#991b1b" });
});

test("row-scope rule: no match → rowStyle null", () => {
  const rules = [
    {
      column: "status",
      op: "eq",
      value: "error",
      style: { backgroundColor: "#fee2e2" },
      scope: "row",
    },
  ];
  const { rowStyle } = evalRules(rules, { status: "ok" }, ["status"]);
  assert.equal(rowStyle, null);
});

test("row-scope rules merge (last-writer-wins per property)", () => {
  const rules = [
    {
      column: "score",
      op: "gte",
      value: 0,
      style: { backgroundColor: "#d1fae5", color: "#065f46" },
      scope: "row",
    },
    {
      column: "score",
      op: "gt",
      value: 90,
      style: { backgroundColor: "#fef3c7" }, // overrides first backgroundColor
      scope: "row",
    },
  ];
  const { rowStyle } = evalRules(rules, { score: 95 }, ["score"]);
  // second rule wins for backgroundColor, color from first survives
  assert.deepEqual(rowStyle, { backgroundColor: "#fef3c7", color: "#065f46" });
});

// ---------------------------------------------------------------------------
// evalRules — other operators
// ---------------------------------------------------------------------------

test("ne rule: value differs → cell style applied", () => {
  const rules = [
    {
      column: "flag",
      op: "ne",
      value: 1,
      style: { color: "red" },
      scope: "cell",
    },
  ];
  const { cellStyles } = evalRules(rules, { flag: 0 }, ["flag"]);
  assert.deepEqual(cellStyles.flag, { color: "red" });
});

test("gte rule: value equals threshold → cell style applied", () => {
  const rules = [
    {
      column: "qty",
      op: "gte",
      value: 10,
      style: { fontWeight: "bold" },
      scope: "cell",
    },
  ];
  const { cellStyles } = evalRules(rules, { qty: 10 }, ["qty"]);
  assert.deepEqual(cellStyles.qty, { fontWeight: "bold" });
});

test("lt rule: value below threshold → cell style applied", () => {
  const rules = [
    {
      column: "stock",
      op: "lt",
      value: 5,
      style: { color: "orange" },
      scope: "cell",
    },
  ];
  const { cellStyles } = evalRules(rules, { stock: 3 }, ["stock"]);
  assert.deepEqual(cellStyles.stock, { color: "orange" });
});

test("lte rule: value at threshold → cell style applied", () => {
  const rules = [
    {
      column: "stock",
      op: "lte",
      value: 5,
      style: { color: "orange" },
      scope: "cell",
    },
  ];
  const { cellStyles } = evalRules(rules, { stock: 5 }, ["stock"]);
  assert.deepEqual(cellStyles.stock, { color: "orange" });
});

test("contains rule: substring match (case-insensitive) → cell style applied", () => {
  const rules = [
    {
      column: "label",
      op: "contains",
      value: "ERROR",
      style: { color: "red" },
      scope: "cell",
    },
  ];
  const { cellStyles } = evalRules(rules, { label: "network error" }, [
    "label",
  ]);
  assert.deepEqual(cellStyles.label, { color: "red" });
});

test("contains rule: no match → no style", () => {
  const rules = [
    {
      column: "label",
      op: "contains",
      value: "ERROR",
      style: { color: "red" },
      scope: "cell",
    },
  ];
  const { cellStyles } = evalRules(rules, { label: "all good" }, ["label"]);
  assert.deepEqual(cellStyles, {});
});

// ---------------------------------------------------------------------------
// evalRules — multiple rules on different columns
// ---------------------------------------------------------------------------

test("multiple rules on different columns → independent cellStyles", () => {
  const rules = [
    {
      column: "revenue",
      op: "gt",
      value: 1000,
      style: { color: "green" },
      scope: "cell",
    },
    {
      column: "cost",
      op: "gt",
      value: 500,
      style: { color: "red" },
      scope: "cell",
    },
  ];
  const row = { revenue: 2000, cost: 600 };
  const { cellStyles } = evalRules(rules, row, ["revenue", "cost"]);
  assert.deepEqual(cellStyles.revenue, { color: "green" });
  assert.deepEqual(cellStyles.cost, { color: "red" });
});

// ---------------------------------------------------------------------------
// formatValue — currency
// ---------------------------------------------------------------------------

test("formatValue currency: formats a number as USD", () => {
  const result = formatValue(1234.5, {
    type: "currency",
    currency: "USD",
    locale: "en-US",
  });
  // Must include '$' and the number
  assert.ok(result.includes("$"), `expected $ in: ${result}`);
  assert.ok(
    result.includes("1,234") || result.includes("1234"),
    `expected numeric part in: ${result}`,
  );
});

test("formatValue currency: rounds to 2 decimals by default", () => {
  const result = formatValue(9.9, {
    type: "currency",
    currency: "USD",
    locale: "en-US",
  });
  assert.ok(result.includes("9.90"), `expected 9.90 in: ${result}`);
});

test("formatValue currency: respects explicit decimals:0", () => {
  const result = formatValue(1234.99, {
    type: "currency",
    currency: "USD",
    locale: "en-US",
    decimals: 0,
  });
  assert.ok(!result.includes("."), `expected no decimal point in: ${result}`);
});

// ---------------------------------------------------------------------------
// formatValue — number
// ---------------------------------------------------------------------------

test("formatValue number: formats with locale grouping", () => {
  const result = formatValue(1234567, { type: "number", locale: "en-US" });
  assert.ok(
    result.includes("1,234,567"),
    `expected grouped number in: ${result}`,
  );
});

test("formatValue number: respects decimals", () => {
  const result = formatValue(3.14159, {
    type: "number",
    locale: "en-US",
    decimals: 2,
  });
  assert.equal(result, "3.14");
});

// ---------------------------------------------------------------------------
// formatValue — percent
// ---------------------------------------------------------------------------

test("formatValue percent: treats value as ratio (0-1)", () => {
  const result = formatValue(0.875, { type: "percent", locale: "en-US" });
  // 0.875 → 87.5% rounded by Intl → "88%" or "87.5%" depending on defaults
  assert.ok(result.includes("%"), `expected % sign in: ${result}`);
  assert.ok(
    result.includes("88") || result.includes("87"),
    `expected 87 or 88 in: ${result}`,
  );
});

test("formatValue percent: respects decimals", () => {
  const result = formatValue(0.1234, {
    type: "percent",
    locale: "en-US",
    decimals: 1,
  });
  assert.ok(result.includes("12.3%"), `expected 12.3% in: ${result}`);
});

// ---------------------------------------------------------------------------
// formatValue — date
// ---------------------------------------------------------------------------

test("formatValue date: formats an ISO string", () => {
  const result = formatValue("2024-03-15", { type: "date", locale: "en-US" });
  // short date style → 3/15/2024 in en-US
  assert.ok(
    result.includes("2024") || result.includes("24"),
    `expected year in: ${result}`,
  );
  assert.ok(
    result.includes("15") || result.includes("3"),
    `expected month/day in: ${result}`,
  );
});

test("formatValue date: formats a Date object", () => {
  const d = new Date(2024, 0, 5); // Jan 5 2024
  const result = formatValue(d, { type: "date", locale: "en-US" });
  assert.ok(
    result.includes("2024") || result.includes("24"),
    `expected year in: ${result}`,
  );
});

test("formatValue date: respects dateStyle:long", () => {
  const result = formatValue("2024-06-01", {
    type: "date",
    locale: "en-US",
    dateStyle: "long",
  });
  // long date includes month name
  assert.ok(
    result.includes("June") || result.includes("2024"),
    `expected long date in: ${result}`,
  );
});

// ---------------------------------------------------------------------------
// formatValue — edge cases
// ---------------------------------------------------------------------------

test("formatValue: null value returns em-dash", () => {
  assert.equal(formatValue(null, { type: "currency" }), "—");
});

test("formatValue: undefined value returns em-dash", () => {
  assert.equal(formatValue(undefined, { type: "number" }), "—");
});

test("formatValue: no fmt returns String(value)", () => {
  assert.equal(formatValue(42, null), "42");
  assert.equal(formatValue("hello", undefined), "hello");
});

test("formatValue: unknown type falls back to String(value)", () => {
  assert.equal(formatValue(99, { type: "unknown_type" }), "99");
});

test("a rule can compare against another column of the same row", () => {
  const rule = {
    column: "actual",
    op: "lt",
    compareColumn: "target",
    style: { color: "red" },
  };
  assert.deepEqual(
    evalRules([rule], { actual: 5, target: 9 }).cellStyles.actual,
    { color: "red" },
  );
  assert.equal(
    evalRules([rule], { actual: 9, target: 5 }).cellStyles.actual,
    undefined,
  );
});

test("formatValue wraps the formatted number in prefix and suffix", () => {
  assert.equal(
    formatValue(1234, { type: "number", prefix: "$ ", locale: "en-US" }),
    "$ 1,234",
  );
  assert.equal(formatValue(45, { suffix: "%" }), "45%");
  assert.equal(formatValue(null, { suffix: "%" }), "—");
});

// ---------------------------------------------------------------------------
// Conditional rules — extended operators and column compare
// ---------------------------------------------------------------------------

test("not_contains: case-insensitive inverse of contains; empty cell matches", () => {
  const rules = [
    {
      column: "label",
      op: "not_contains",
      value: "ERROR",
      style: { color: "red" },
    },
  ];
  assert.deepEqual(evalRules(rules, { label: "all good" }).cellStyles.label, {
    color: "red",
  });
  assert.equal(
    evalRules(rules, { label: "Network Error" }).cellStyles.label,
    undefined,
  );
  assert.deepEqual(evalRules(rules, { label: null }).cellStyles.label, {
    color: "red",
  });
});

test("enabled:false and isActive:false both skip a rule; absent means on", () => {
  const base = { column: "n", op: "gt", value: 1, style: { color: "red" } };
  assert.equal(
    evalRules([{ ...base, enabled: false }], { n: 5 }).cellStyles.n,
    undefined,
  );
  assert.equal(
    evalRules([{ ...base, isActive: false }], { n: 5 }).cellStyles.n,
    undefined,
  );
  assert.deepEqual(
    evalRules([{ ...base, enabled: true }], { n: 5 }).cellStyles.n,
    { color: "red" },
  );
  assert.deepEqual(evalRules([base], { n: 5 }).cellStyles.n, { color: "red" });
});

test("a disabled rule does not take part in last-writer-wins", () => {
  const rules = [
    { column: "n", op: "gt", value: 1, style: { color: "green" } },
    {
      column: "n",
      op: "gt",
      value: 1,
      style: { color: "red" },
      enabled: false,
    },
  ];
  assert.deepEqual(evalRules(rules, { n: 5 }).cellStyles.n, { color: "green" });
});

test("compareColumn works for every single-value op", () => {
  const row = { actual: 5, target: 9, name: "alpha", needle: "LP" };
  const hit = (op, col = "actual", cmp = "target") =>
    evalRules(
      [{ column: col, op, compareColumn: cmp, style: { color: "x" } }],
      row,
    ).cellStyles[col] != null;
  assert.equal(hit("lt"), true);
  assert.equal(hit("lte"), true);
  assert.equal(hit("gt"), false);
  assert.equal(hit("gte"), false);
  assert.equal(hit("ne"), true);
  assert.equal(hit("eq"), false);
  assert.equal(hit("contains", "name", "needle"), true);
  assert.equal(hit("not_contains", "name", "needle"), false);
});

test("compareColumn wins over a leftover static value", () => {
  const rule = {
    column: "actual",
    op: "lt",
    value: 1,
    compareColumn: "target",
    style: { color: "red" },
  };
  assert.deepEqual(
    evalRules([rule], { actual: 5, target: 9 }).cellStyles.actual,
    { color: "red" },
  );
});

test("compareColumn with an empty target never matches (missing is not 0)", () => {
  const rule = {
    column: "actual",
    op: "ne",
    compareColumn: "target",
    style: { color: "red" },
  };
  assert.equal(
    evalRules([rule], { actual: 5, target: null }).cellStyles.actual,
    undefined,
  );
});

test("between: both bounds from columns of the same row", () => {
  const rule = {
    column: "v",
    op: "between",
    compareColumn: "lo",
    compareColumn2: "hi",
    style: { color: "red" },
  };
  assert.deepEqual(evalRules([rule], { v: 5, lo: 1, hi: 10 }).cellStyles.v, {
    color: "red",
  });
  assert.deepEqual(evalRules([rule], { v: 10, lo: 1, hi: 10 }).cellStyles.v, {
    color: "red",
  });
  assert.equal(
    evalRules([rule], { v: 11, lo: 1, hi: 10 }).cellStyles.v,
    undefined,
  );
  assert.equal(
    evalRules([rule], { v: 5, lo: 1, hi: null }).cellStyles.v,
    undefined,
  );
});

test("between: mixed — column lower bound with a static upper, and vice versa", () => {
  const colLo = {
    column: "v",
    op: "between",
    compareColumn: "lo",
    value2: 10,
    style: { color: "a" },
  };
  assert.deepEqual(evalRules([colLo], { v: 5, lo: 1 }).cellStyles.v, {
    color: "a",
  });
  assert.equal(evalRules([colLo], { v: 5, lo: 6 }).cellStyles.v, undefined);
  const colHi = {
    column: "v",
    op: "between",
    value: 0,
    compareColumn2: "hi",
    style: { color: "b" },
  };
  assert.deepEqual(evalRules([colHi], { v: 5, hi: 8 }).cellStyles.v, {
    color: "b",
  });
  assert.equal(evalRules([colHi], { v: 9, hi: 8 }).cellStyles.v, undefined);
});

test("borders: explicit border shorthand passes through", () => {
  const rule = {
    column: "n",
    op: "gt",
    value: 0,
    style: { border: "2px dashed red" },
  };
  assert.deepEqual(evalRules([rule], { n: 1 }).cellStyles.n, {
    border: "2px dashed red",
  });
});

test("borders: borderWidth + borderColor fold into border; merged with other styles", () => {
  const rules = [
    {
      column: "n",
      op: "gt",
      value: 0,
      style: { backgroundColor: "#eee", fontWeight: "bold" },
    },
    {
      column: "n",
      op: "gt",
      value: 0,
      style: { borderWidth: 2, borderColor: "#f00" },
    },
  ];
  assert.deepEqual(evalRules(rules, { n: 1 }).cellStyles.n, {
    backgroundColor: "#eee",
    fontWeight: "bold",
    border: "2px solid #f00",
  });
});

test("borders: colour alone defaults to 1px; works on row scope too", () => {
  const rule = {
    column: "n",
    op: "gt",
    value: 0,
    scope: "row",
    style: { borderColor: "blue" },
  };
  assert.deepEqual(evalRules([rule], { n: 1 }).rowStyle, {
    border: "1px solid blue",
  });
});

test("borders: a later rule overrides an earlier border", () => {
  const rules = [
    {
      column: "n",
      op: "gt",
      value: 0,
      style: { borderWidth: 1, borderColor: "red" },
    },
    {
      column: "n",
      op: "gt",
      value: 0,
      style: { borderWidth: 3, borderColor: "blue" },
    },
  ];
  assert.equal(
    evalRules(rules, { n: 1 }).cellStyles.n.border,
    "3px solid blue",
  );
});

test("rules without style or column are ignored (old specs keep rendering)", () => {
  const rules = [
    null,
    { column: "n", op: "gt", value: 0 },
    { op: "gt", value: 0, style: { color: "x" } },
  ];
  assert.deepEqual(evalRules(rules, { n: 1 }), {
    cellStyles: {},
    rowStyle: null,
  });
});

// ---------------------------------------------------------------------------
// Column formats — display modes, bars, links
// ---------------------------------------------------------------------------

test("numberFormat commas: en-US grouping regardless of locale", () => {
  assert.equal(
    formatValue(1234567.5, {
      type: "number",
      numberFormat: "commas",
      locale: "de-DE",
    }),
    "1,234,567.5",
  );
  assert.equal(
    formatValue(1234.567, {
      type: "number",
      numberFormat: "commas",
      decimals: 2,
    }),
    "1,234.57",
  );
});

test("numberFormat spaces: space-grouped with a dot decimal", () => {
  assert.equal(
    formatValue(1234567.5, { type: "number", numberFormat: "spaces" }),
    "1 234 567.5",
  );
  assert.equal(
    formatValue(-9876, { type: "number", numberFormat: "spaces" }),
    "-9 876",
  );
});

test("numberFormat abbreviate: compact SI-style labels", () => {
  const f = (v, extra = {}) =>
    formatValue(v, { type: "number", numberFormat: "abbreviate", ...extra });
  assert.equal(f(999), "999");
  assert.equal(f(1500), "1.5 K");
  assert.equal(f(1234567), "1.23 M");
  assert.equal(f(2_500_000_000), "2.5 B");
  assert.equal(f(999_999), "1 M");
  assert.equal(f(-1500), "-1.5 K");
  assert.equal(f(1234567, { decimals: 0 }), "1 M");
  assert.equal(f(1500, { delta: true }), "+1.5 K");
});

test("numberFormat composes with prefix/suffix; non-numbers pass through", () => {
  assert.equal(
    formatValue(1500, {
      type: "number",
      numberFormat: "abbreviate",
      prefix: "$",
      suffix: " total",
    }),
    "$1.5 K total",
  );
  assert.equal(
    formatValue("n/a", { type: "number", numberFormat: "commas" }),
    "n/a",
  );
});

test("numberFormat is ignored by other types", () => {
  assert.ok(
    formatValue(1234.5, {
      type: "currency",
      numberFormat: "abbreviate",
    }).includes("$1,234.50"),
  );
});

test("resolveBarMax: row column, then barMax, then column max", () => {
  assert.equal(resolveBarMax({ barMaxColumn: "cap" }, { cap: 200 }, 50), 200);
  assert.equal(
    resolveBarMax({ barMaxColumn: "cap", barMax: 80 }, { cap: null }, 50),
    80,
  );
  assert.equal(resolveBarMax({ barMaxColumn: "cap" }, { cap: "x" }, 50), 50);
  assert.equal(resolveBarMax({ barMaxColumn: "cap" }, { cap: 0 }, 50), 50);
  assert.equal(resolveBarMax({ barMaxColumn: "cap" }, {}, 50), 50);
  assert.equal(resolveBarMax({ barMax: 10 }, { cap: 200 }, 50), 10);
  assert.equal(resolveBarMax({}, null, 50), 50);
  assert.equal(resolveBarMax({ barMaxColumn: "cap" }, { cap: -40 }, 50), 40);
});

test("safeHref: only http(s), mailto and same-origin paths", () => {
  assert.equal(
    safeHref("https://example.com/a?b=1"),
    "https://example.com/a?b=1",
  );
  assert.equal(safeHref("mailto:a@b.co"), "mailto:a@b.co");
  assert.equal(safeHref("/boards/42"), "/boards/42");
  for (const bad of [
    "javascript:alert(1)",
    " JavaScript:alert(1)",
    "data:text/html,x",
    "vbscript:x",
    "//evil.com",
    "/\\evil.com",
    "java\nscript:alert(1)",
    "ftp://x",
    "relative/path",
    "",
  ]) {
    assert.equal(safeHref(bad), null, `should block ${JSON.stringify(bad)}`);
  }
});

test("resolveLinkHref: {{value}} and row tokens, encoded inside a template", () => {
  const row = { id: 7, name: "A & B" };
  assert.equal(
    resolveLinkHref({ template: "https://x.io/item/{{value}}" }, "a b", row),
    "https://x.io/item/a%20b",
  );
  assert.equal(
    resolveLinkHref(
      { template: "https://x.io/{{id}}?q={name}" },
      "ignored",
      row,
    ),
    "https://x.io/7?q=A%20%26%20B",
  );
});

test("resolveLinkHref: a whole-template placeholder is the URL itself (not encoded)", () => {
  assert.equal(
    resolveLinkHref({ template: "{{value}}" }, "https://x.io/a?b=1", {}),
    "https://x.io/a?b=1",
  );
  assert.equal(
    resolveLinkHref({ template: "{url}" }, "ignored", { url: "https://x.io/" }),
    "https://x.io/",
  );
});

test("resolveLinkHref: unsafe, missing token or empty template → no link", () => {
  assert.equal(
    resolveLinkHref({ template: "{{value}}" }, "javascript:alert(1)", {}),
    null,
  );
  assert.equal(
    resolveLinkHref({ template: "https://x.io/{{missing}}" }, 1, {}),
    null,
  );
  assert.equal(resolveLinkHref({ template: "" }, 1, {}), null);
  assert.equal(resolveLinkHref(undefined, 1, {}), null);
  // A token can't smuggle a scheme in: the template prefix fixes the origin.
  assert.equal(
    resolveLinkHref({ template: "https://x.io/{{value}}" }, "javascript:1", {}),
    "https://x.io/javascript%3A1",
  );
});
