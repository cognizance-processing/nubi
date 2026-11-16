/**
 * conditionalFormat.js — Pure conditional-formatting + value-formatting logic.
 *
 * No DOM, no React, no side effects. Fully unit-testable in Node.
 *
 * ── Rule shape ───────────────────────────────────────────────────────────────
 * {
 *   column   : string          // column name the rule tests
 *   op       : 'eq'|'ne'|'gt'|'gte'|'lt'|'lte'|'between'|'contains'|'not_contains'
 *   value    : any             // primary comparison value
 *   value2?  : any             // upper bound for 'between'
 *   compareColumn?  : string   // compare against this column of the same row
 *                              // instead of `value` (lower bound for 'between')
 *   compareColumn2? : string   // 'between' upper bound from a column instead
 *                              // of `value2`. Each bound resolves on its own,
 *                              // so a column lower bound + static upper works.
 *   enabled? : boolean         // false → rule is skipped (`isActive: false` alias)
 *   style    : {               // CSS-in-JS style fragment to apply
 *     backgroundColor? : string
 *     color?           : string
 *     fontWeight?      : string|number
 *     border?          : string // any CSS border shorthand, passed through
 *     borderWidth?     : number // with/without borderColor: folded into
 *     borderColor?     : string //   `border` = `${w ?? 1}px solid ${c}`
 *   }
 *   scope    : 'cell'|'row'    // 'cell' → apply to that cell only;
 *                              // 'row'  → apply to the entire row
 * }
 *
 * ── Format shape ─────────────────────────────────────────────────────────────
 * {
 *   type     : 'number'|'date'|'currency'|'percent'
 *   // additional Intl options forwarded directly
 *   locale?  : string          // default 'en-US'
 *   currency? : string         // for type:'currency', default 'USD'
 *   decimals? : number         // explicit fraction digits
 *   dateStyle?: string         // 'short'|'medium'|'long'|'full' (date)
 *   timeStyle?: string         // optional time portion
 *   delta?   : boolean         // number/percent only: force a leading sign
 *                              // (+/-) so formatValue() and the table's
 *                              // sign-based text color stay in sync
 *   bar?     : boolean         // number/percent only: TableWidget renders an
 *                              // inline magnitude bar alongside the text
 *   barMax?  : number          // explicit scale for `bar`; default is the
 *                              // column's own max(abs(value)) across rows
 *   barColor?: string          // bar fill colour (default: primary)
 *   barStyle?: 'inline'|'fill' // 'fill' = full-cell meter, value centred
 *   barMaxColumn?: string      // bar scale from another column on the same row;
 *                              // falls back to barMax / column max when empty
 *   numberFormat?: 'commas'|'spaces'|'abbreviate'  // type:'number' only.
 *                              // commas = en-US grouping regardless of locale,
 *                              // spaces = space grouping, abbreviate = 1.23 M
 *   prefix?, suffix?: string   // wrapped around the formatted text
 *   chip?    : boolean         // render the value as a pill (any value type)
 *   chipColor?, chipTextColor?: string
 *   link?    : {               // per-column hyperlink
 *     template      : string   // URL; {{value}} = this cell, {{col}} or {col}
 *                              // = another column of the row (URL-encoded
 *                              // unless the placeholder is the whole template)
 *     openInNewTab? : boolean  // default true
 *     labelMode?    : 'cellValue'|'customText'|'button'
 *     customText?   : string
 *     buttonColor?  : string
 *   }                          // only http(s), mailto and same-origin paths
 *                              // render as links — see resolveLinkHref()
 * }
 *
 * ── Exports ──────────────────────────────────────────────────────────────────
 * evalRules(rules, row, columns) → { cellStyles: {col: style}, rowStyle: style|null }
 * formatValue(value, fmt)        → string
 * resolveBarMax(fmt, row, colMax) → number
 * resolveLinkHref(link, value, row) → string|null (null = render as text)
 * DELTA_COLORS                   → { up, down, flat } hex colors for sign-based text
 */

// ---------------------------------------------------------------------------
// Operator evaluation
// ---------------------------------------------------------------------------

/**
 * Cast a value for numeric comparison. Returns the value coerced to a number,
 * or NaN if not representable.
 */
function toNum(v) {
  if (v == null) return NaN;
  const n = Number(v);
  return n;
}

/**
 * Evaluate a single rule against a cell value.
 * Returns true if the rule condition matches.
 *
 * @param {*} cellValue  – raw value from the row object
 * @param {string} op    – operator
 * @param {*} value      – primary operand
 * @param {*} value2     – secondary operand (between upper bound)
 * @returns {boolean}
 */
function matchesOp(cellValue, op, value, value2) {
  switch (op) {
    case "eq":
      // loose equality so '42' == 42 when appropriate; use == intentionally

      return cellValue == value;

    case "ne":
      return cellValue != value;

    case "gt":
      return toNum(cellValue) > toNum(value);

    case "gte":
      return toNum(cellValue) >= toNum(value);

    case "lt":
      return toNum(cellValue) < toNum(value);

    case "lte":
      return toNum(cellValue) <= toNum(value);

    case "between": {
      const n = toNum(cellValue);
      return n >= toNum(value) && n <= toNum(value2);
    }

    case "contains": {
      if (cellValue == null) return false;
      return String(cellValue)
        .toLowerCase()
        .includes(String(value).toLowerCase());
    }

    case "not_contains": {
      // An empty cell contains nothing, so it matches — the exact inverse of
      // 'contains' above for every input.
      if (cellValue == null) return true;
      return !String(cellValue)
        .toLowerCase()
        .includes(String(value).toLowerCase());
    }

    default:
      return false;
  }
}

// ---------------------------------------------------------------------------
// Rule evaluator
// ---------------------------------------------------------------------------

/**
 * Evaluate all formatting rules against a single row and return the computed
 * cell styles and an optional row style.
 *
 * Rules are applied in order; later rules win (last-writer-wins merge per cell).
 * Row-scope rules merge into rowStyle (same last-writer-wins).
 *
 * @param rules   – array of rule objects (see file header)
 * @param row     – plain key→value row object
 * @param _columns – columns present in the display (unused by eval
 *                   but kept for future column-existence guards)
 */
export function evalRules(
  rules: Record<string, any>[],
  row: Record<string, any>,
  _columns?: string[],
): {
  cellStyles: Record<string, Record<string, any>>;
  rowStyle: Record<string, any> | null;
} {
  if (!rules || rules.length === 0) {
    return { cellStyles: {}, rowStyle: null };
  }

  const cellStyles: Record<string, Record<string, any>> = {};
  let rowStyle: Record<string, any> | null = null;

  for (const rule of rules) {
    if (!rule) continue;
    const { column, op, value, value2, compareColumn, compareColumn2, scope } =
      rule;
    if (!column || !op || !rule.style) continue;
    if (rule.enabled === false || rule.isActive === false) continue;

    const cellValue = row[column];
    // compareColumn / compareColumn2: compare against other columns of the
    // same row. Each bound falls back to its static value independently.
    const target = compareColumn ? row[compareColumn] : value;
    const target2 = compareColumn2 ? row[compareColumn2] : value2;
    // A column bound that is empty on this row never matches — a missing
    // target is not treated as zero.
    if (compareColumn && target == null) continue;
    if (op === "between" && compareColumn2 && target2 == null) continue;
    if (!matchesOp(cellValue, op, target, target2)) continue;
    const style = normalizeStyle(rule.style);

    if (scope === "row") {
      rowStyle = rowStyle ? { ...rowStyle, ...style } : { ...style };
    } else {
      // default scope is 'cell'
      cellStyles[column] = cellStyles[column]
        ? { ...cellStyles[column], ...style }
        : { ...style };
    }
  }

  return { cellStyles, rowStyle };
}

/**
 * Fold the editor's separate borderWidth / borderColor into a `border`
 * shorthand — on their own they paint nothing, because the default
 * border-style is `none`. An explicit `border` wins and is passed through.
 */
function normalizeStyle(style: Record<string, any>): Record<string, any> {
  if (style.borderWidth == null && style.borderColor == null) return style;
  const { borderWidth, borderColor, ...rest } = style;
  if (rest.border == null) {
    const w = Number(borderWidth);
    const width = Number.isFinite(w) && w > 0 ? w : 1;
    rest.border = `${width}px solid ${borderColor || "currentColor"}`;
  }
  return rest;
}

// ---------------------------------------------------------------------------
// Shared sign-based coloring (mirrors KpiWidget's delta convention)
// ---------------------------------------------------------------------------

export const DELTA_COLORS = { up: "#10b981", down: "#ef4444", flat: "#9ca3af" };

// ---------------------------------------------------------------------------
// Value formatter
// ---------------------------------------------------------------------------

/**
 * Format a raw value according to a format descriptor.
 *
 * Supported format types:
 *   'number'   – Intl.NumberFormat with optional decimals
 *   'currency' – Intl.NumberFormat style:'currency', currency:'USD' (or fmt.currency)
 *   'percent'  – Intl.NumberFormat style:'percent', value is already a ratio (0–1)
 *   'date'     – Intl.DateTimeFormat; value may be a Date, ISO string, or epoch ms
 *
 * If value is null/undefined, returns '—'.
 * If fmt is null/undefined, returns String(value).
 *
 * @param value – raw cell value
 * @param fmt   – format descriptor { type, locale?, ...opts }
 */
export function formatValue(value: any, fmt: Record<string, any>): string {
  if (value == null) return "—";
  const text = formatBare(value, fmt);
  return fmt?.prefix || fmt?.suffix
    ? `${fmt.prefix ?? ""}${text}${fmt.suffix ?? ""}`
    : text;
}

function formatBare(value: any, fmt: Record<string, any>): string {
  if (!fmt || !fmt.type) return String(value);
  if (fmt.type === "number" && !Number.isFinite(Number(value)))
    return String(value);

  const locale = fmt.locale ?? "en-US";

  try {
    switch (fmt.type) {
      case "number": {
        const opts: Intl.NumberFormatOptions = {};
        if (fmt.decimals != null) {
          opts.minimumFractionDigits = fmt.decimals;
          opts.maximumFractionDigits = fmt.decimals;
        }
        if (fmt.delta) opts.signDisplay = "exceptZero";
        if (fmt.grouping === false) opts.useGrouping = false;
        if (fmt.numberFormat === "abbreviate")
          return abbreviate(Number(value), fmt);
        if (fmt.numberFormat === "commas" || fmt.numberFormat === "spaces") {
          // Fixed separators, independent of locale: en-US gives ',' and '.'.
          const text = new Intl.NumberFormat("en-US", {
            ...opts,
            useGrouping: true,
          }).format(Number(value));
          return fmt.numberFormat === "spaces" ? text.replace(/,/g, " ") : text;
        }
        return new Intl.NumberFormat(locale, opts).format(Number(value));
      }

      case "currency": {
        const opts: Intl.NumberFormatOptions = {
          style: "currency",
          currency: fmt.currency ?? "USD",
        };
        if (fmt.decimals != null) {
          opts.minimumFractionDigits = fmt.decimals;
          opts.maximumFractionDigits = fmt.decimals;
        }
        return new Intl.NumberFormat(locale, opts).format(Number(value));
      }

      case "percent": {
        const opts: Intl.NumberFormatOptions = {
          style: "percent",
        };
        if (fmt.decimals != null) {
          opts.minimumFractionDigits = fmt.decimals;
          opts.maximumFractionDigits = fmt.decimals;
        }
        if (fmt.delta) opts.signDisplay = "exceptZero";
        return new Intl.NumberFormat(locale, opts).format(Number(value));
      }

      case "date": {
        const dateObj =
          value instanceof Date
            ? value
            : typeof value === "number"
              ? new Date(value)
              : new Date(String(value));

        const opts: Intl.DateTimeFormatOptions = {};
        if (fmt.dateStyle) opts.dateStyle = fmt.dateStyle;
        if (fmt.timeStyle) opts.timeStyle = fmt.timeStyle;
        // default: short date if nothing specified
        if (!opts.dateStyle && !opts.timeStyle) opts.dateStyle = "short";

        return new Intl.DateTimeFormat(locale, opts).format(dateObj);
      }

      default:
        return String(value);
    }
  } catch {
    // If formatting fails (invalid date, etc.), fall back to raw string
    return String(value);
  }
}

/**
 * Compact numeric labels: 1234567 → '1.23 M' (decimals default 2, trailing
 * zeros dropped, space before the unit). Negative values keep a leading sign;
 * 999 999 still rolls over to '1 M'.
 */
function abbreviate(n: number, fmt: Record<string, any>): string {
  const places = fmt.decimals != null ? fmt.decimals : 2;
  const scale = Math.pow(10, places);
  const units = [" K", " M", " B", " T"];
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : fmt.delta && n > 0 ? "+" : "";
  for (let i = units.length - 1; i >= 0; i--) {
    const size = Math.pow(10, (i + 1) * 3);
    if (size <= abs) {
      let r = Math.round((abs * scale) / size) / scale;
      let u = i;
      if (r === 1000 && u < units.length - 1) {
        r = 1;
        u++;
      }
      return `${sign}${r}${units[u]}`;
    }
  }
  return `${sign}${Math.round(abs * scale) / scale}`;
}

// ---------------------------------------------------------------------------
// Bars
// ---------------------------------------------------------------------------

/**
 * Scale for a `bar` cell. Precedence: the row's `barMaxColumn` value (when it
 * is a non-zero number), then an explicit `barMax`, then the column's own
 * max(|value|) computed by the caller.
 */
export function resolveBarMax(
  fmt: Record<string, any>,
  row: Record<string, any> | null | undefined,
  colMax: number,
): number {
  if (fmt?.barMaxColumn && row && row[fmt.barMaxColumn] != null) {
    const m = Math.abs(Number(row[fmt.barMaxColumn]));
    if (Number.isFinite(m) && m > 0) return m;
  }
  if (fmt?.barMax != null && Number.isFinite(Number(fmt.barMax)))
    return Number(fmt.barMax);
  return colMax ?? 0;
}

// ---------------------------------------------------------------------------
// Hyperlinks
// ---------------------------------------------------------------------------

// Placeholders: {{token}} or {token}.
const PLACEHOLDER = /\{\{?\s*([^{}]+?)\s*\}?\}/g;
const WHOLE_PLACEHOLDER = /^\{\{?\s*([^{}]+?)\s*\}?\}$/;
// eslint-disable-next-line no-control-regex
const CONTROL_CHARS = /[\u0000-\u001f\u007f]/;

/**
 * Accept only http(s)/mailto URLs and same-origin paths ('/x', not '//x').
 * Anything else — javascript:, data:, vbscript:, protocol-relative — is null.
 */
export function safeHref(raw: string): string | null {
  const url = String(raw ?? "").trim();
  if (!url || CONTROL_CHARS.test(url)) return null;
  if (url.startsWith("/") && !url.startsWith("//") && !url.startsWith("/\\"))
    return url;
  try {
    const parsed = new URL(url);
    if (["http:", "https:", "mailto:"].includes(parsed.protocol.toLowerCase()))
      return parsed.toString();
  } catch {
    /* not an absolute URL */
  }
  return null;
}

/**
 * Build a cell's link target from `link.template`. `{{value}}` is the cell
 * itself; any other token is a column of the same row. A token with no value
 * on this row yields no link, as does an unsafe result.
 */
export function resolveLinkHref(
  link: Record<string, any> | null | undefined,
  value: any,
  row: Record<string, any> | null | undefined,
): string | null {
  const template =
    typeof link?.template === "string" ? link.template.trim() : "";
  if (!template) return null;
  const lookup = (key: string) =>
    row && key in row ? row[key] : key === "value" ? value : undefined;
  const whole = template.match(WHOLE_PLACEHOLDER);
  if (whole) {
    // The placeholder IS the URL (a column of full links): no encoding.
    const v = lookup(whole[1]);
    return v == null ? null : safeHref(String(v));
  }
  let missing = false;
  const url = template.replace(PLACEHOLDER, (_m, key) => {
    const v = lookup(key);
    if (v == null) {
      missing = true;
      return "";
    }
    return encodeURIComponent(String(v));
  });
  return missing ? null : safeHref(url);
}
