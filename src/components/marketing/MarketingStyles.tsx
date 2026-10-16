/**
 * MarketingStyles — the Swiss construction kit for every public page
 * (pricing, compare, reporting, docs, auth, onboarding, 404, footer).
 *
 * One recipe: a 2px ink rule opens each section, a mono index sits beside the
 * headline, structure comes from hairline cells and size contrast. No shadows
 * at rest, no gradients, 2px corners, one accent (blue) for emphasis, state and
 * links. Primary buttons are ink.
 *
 * Every selector is `mk-` prefixed. Render <MarketingStyles /> on any page that
 * uses them. React 19 hoists and dedupes `<style href precedence>`, so
 * rendering it more than once costs nothing.
 *
 * Colours only come from the global tokens (--surface, --ink, --accent, …), so
 * both themes flip together. The one exception is the code-surface palette,
 * which lives in code.css next to the highlighter.
 */
const CSS = `
/* ── Scope ───────────────────────────────────────────────────────────────── */
.mk { background: var(--surface); color: var(--text); font-family: var(--font-ui); -webkit-font-smoothing: antialiased; }
.mk *, .mk *::before, .mk *::after { box-sizing: border-box; }
.mk :focus-visible { outline: 2px solid var(--ring); outline-offset: 2px; }
.mk ::selection { background: var(--accent); color: var(--primary-fg); }
.mk-mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.mk-num  { font-variant-numeric: tabular-nums; }

/* ── Layout ──────────────────────────────────────────────────────────────── */
.mk-wrap { width: 100%; max-width: 1320px; margin: 0 auto; padding-inline: max(16px, env(safe-area-inset-left, 0px)); }
@media (min-width: 640px)  { .mk-wrap { padding-inline: 24px; } }
@media (min-width: 1024px) { .mk-wrap { padding-inline: 32px; } }
.mk-narrow { max-width: 880px; }

/* Index line: the 2px ink rule with a mono label row, as on the landing */
.mk-idx {
  display: flex; align-items: center; gap: 10px; padding-bottom: 10px; border-bottom: 2px solid var(--ink);
  font: 500 12px/1.2 var(--font-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--text-muted);
}
.mk-idx b { font-weight: 500; color: var(--text); }
.mk-idx > :last-child:not(:first-child) { margin-left: auto; }

/* ── Type ────────────────────────────────────────────────────────────────── */
.mk-h1 {
  margin: 0; font-weight: 600; font-size: clamp(40px, 6vw, 88px); line-height: .96; letter-spacing: -.04em; text-wrap: balance;
}
.mk-h1 em, .mk-h2 em { font-style: normal; color: var(--accent); }
.mk-h2 {
  margin: 0; font-weight: 600; font-size: clamp(30px, 3.6vw, 48px); line-height: 1.05; letter-spacing: -.03em; text-wrap: balance;
}
.mk-h3 { margin: 0; font-weight: 600; font-size: 22px; line-height: 1.2; letter-spacing: -.02em; }
.mk-lead { margin: 0; font-size: 18px; line-height: 1.55; color: var(--text-muted); max-width: 58ch; text-wrap: pretty; }
.mk-p { margin: 0; font-size: 15px; line-height: 1.6; color: var(--text-muted); text-wrap: pretty; }
.mk-p b, .mk-lead b, .mk-p strong, .mk-lead strong { color: var(--text); font-weight: 600; }
.mk-label { font: 500 11px/1.4 var(--font-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--text-muted); }
.mk-small { font-size: 13px; line-height: 1.5; color: var(--text-muted); }

/* ── Page hero + sections ────────────────────────────────────────────────── */
.mk-hero { padding-block: 40px 56px; }
@media (min-width: 1024px) { .mk-hero { padding-block: 56px 72px; } }
.mk-hero-grid { display: grid; gap: 40px; margin-top: 32px; align-items: end; }
@media (min-width: 1024px) { .mk-hero-grid { grid-template-columns: minmax(0, 7fr) minmax(0, 5fr); gap: 56px; } }
.mk-hero-copy { display: flex; flex-direction: column; gap: 24px; }
.mk-ctas { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }

.mk-sec { border-top: 2px solid var(--ink); padding-block: 56px; scroll-margin-top: 72px; }
@media (min-width: 1024px) { .mk-sec { padding-block: 88px; } }
.mk-sec.mk-sunk { background: var(--surface-2); }
.mk-sec-h { display: grid; gap: 14px; margin-bottom: 32px; }
@media (min-width: 900px) { .mk-sec-h { grid-template-columns: minmax(0, 4fr) minmax(0, 8fr); gap: 32px; align-items: start; margin-bottom: 44px; } }
.mk-sec-h .mk-idx { border: 0; padding: 10px 0 0; align-self: start; }
.mk-sec-h .mk-idx > :last-child:not(:first-child) { margin-left: 0; }
.mk-sec-h .mk-lead { margin-top: 14px; }
.mk-sec-h .mk-lead + .mk-lead { margin-top: 8px; }

/* Facts row: mono captions under big tabular numerals */
.mk-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border-top: 1px solid var(--ink); }
@media (min-width: 720px) { .mk-facts { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
.mk-facts.mk-facts-3 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
@media (min-width: 720px) { .mk-facts.mk-facts-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.mk-facts > div { padding: 12px 16px 16px 0; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.mk-facts b { font: 500 clamp(24px, 2.6vw, 34px)/1.1 var(--font-ui); letter-spacing: -.03em; font-variant-numeric: tabular-nums; }
.mk-facts span { font: 12px/1.4 var(--font-mono); color: var(--text-muted); }

/* ── Hairline cells ──────────────────────────────────────────────────────── */
.mk-cells { display: grid; gap: 1px; background: var(--border); border: 1px solid var(--border); }
.mk-cells > * { background: var(--surface); min-width: 0; }
.mk-sunk .mk-cells > * { background: var(--surface); }
.mk-cell { padding: 20px 20px 24px; display: flex; flex-direction: column; gap: 8px; }
.mk-cell h3 { margin: 0; font-size: 17px; font-weight: 600; letter-spacing: -.015em; line-height: 1.25; }
.mk-cell p { margin: 0; font-size: 14px; line-height: 1.55; color: var(--text-muted); }
.mk-cell p b { color: var(--text); font-weight: 600; }
.mk-cells-2 { grid-template-columns: repeat(1, minmax(0,1fr)); }
.mk-cells-3 { grid-template-columns: repeat(1, minmax(0,1fr)); }
.mk-cells-4 { grid-template-columns: repeat(1, minmax(0,1fr)); }
@media (min-width: 640px) { .mk-cells-2 { grid-template-columns: repeat(2, minmax(0,1fr)); } .mk-cells-3 { grid-template-columns: repeat(2, minmax(0,1fr)); } .mk-cells-4 { grid-template-columns: repeat(2, minmax(0,1fr)); } }
@media (min-width: 1024px) { .mk-cells-3 { grid-template-columns: repeat(3, minmax(0,1fr)); } .mk-cells-4 { grid-template-columns: repeat(4, minmax(0,1fr)); } }
a.mk-cell, .mk-cell-link { text-decoration: none; color: inherit; transition: box-shadow 100ms; }
a.mk-cell:hover, .mk-cell-link:hover { box-shadow: inset 0 0 0 1px var(--ink); }

/* Icon in a cell: a 1.5px stroke glyph in ink, no tile */
.mk-glyph { width: 20px; height: 20px; color: var(--text); flex: none; }

/* ── Tag: square, hairline, mono ─────────────────────────────────────────── */
.mk-tag {
  display: inline-flex; align-items: center; gap: 6px; height: 20px; padding: 0 7px;
  font: 500 11px/1 var(--font-mono); letter-spacing: .06em; text-transform: uppercase; white-space: nowrap;
  border: 1px solid var(--border); border-radius: 2px; color: var(--text-muted); background: transparent;
}
.mk-tag-ink { background: var(--ink); border-color: var(--ink); color: var(--ink-fg); }
.mk-tag-accent { border-color: var(--accent); color: var(--accent); }
.mk-tag-dot::before { content: ''; width: 6px; height: 6px; background: currentColor; flex: none; }

/* ── Links ───────────────────────────────────────────────────────────────── */
.mk-link { color: var(--text); font-weight: 600; text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 3px; text-decoration-color: var(--text-muted); }
.mk-link:hover { color: var(--accent); text-decoration-color: var(--accent); }
.mk-arrow { display: inline-flex; align-items: center; gap: 6px; font-weight: 600; font-size: 14px; color: var(--text); text-decoration: none; border-bottom: 1px solid var(--ink); padding-bottom: 1px; }
.mk-arrow:hover { color: var(--accent); border-color: var(--accent); }

/* ── Checks (ink) ────────────────────────────────────────────────────────── */
.mk-checks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.mk-checks li { display: flex; gap: 10px; align-items: flex-start; font-size: 14px; line-height: 1.45; color: var(--text); }
.mk-checks li > svg { flex: none; margin-top: 2px; color: var(--text); }
.mk-checks li.mk-head { font: 500 11px/1.4 var(--font-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--text-muted); padding-top: 6px; }
.mk-checks li.mk-soon { color: var(--text-muted); }
.mk-checks li.mk-soon > svg { color: var(--text-muted); }

/* ── Buttons that need to match the landing at hero scale ───────────────── */
.mk .nubi-btn-xl { height: 48px; padding-inline: 22px; font-size: 15px; font-weight: 600; }
.mk .nubi-btn-lg { font-weight: 600; }

/* ── Tables: sticky first column, scroll cue, hairlines ─────────────────── */
.mk-tw { position: relative; border: 1px solid var(--border); background: var(--surface); }
.mk-ts {
  position: relative; overflow-x: auto; overflow-y: clip; overscroll-behavior-x: contain; -webkit-overflow-scrolling: touch;
  background:
    linear-gradient(to right, var(--surface), var(--surface)) left / 24px 100% no-repeat local,
    linear-gradient(to left, var(--surface), var(--surface)) right / 24px 100% no-repeat local,
    linear-gradient(to right, color-mix(in srgb, var(--ink) 16%, transparent), transparent) left / 10px 100% no-repeat scroll,
    linear-gradient(to left, color-mix(in srgb, var(--ink) 16%, transparent), transparent) right / 10px 100% no-repeat scroll;
}
.mk-table { border-collapse: separate; border-spacing: 0; width: 100%; font-size: 14px; line-height: 1.5; }
.mk-table th, .mk-table td { padding: 12px 16px; text-align: left; vertical-align: top; border-bottom: 1px solid var(--border); background: var(--surface); }
.mk-table th { font: 500 11px/1.35 var(--font-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--text-muted); border-bottom: 2px solid var(--ink); background: var(--surface); white-space: nowrap; vertical-align: bottom; }
.mk-table th.mk-rowh { font: 600 14px/1.5 var(--font-ui); letter-spacing: 0; text-transform: none; color: var(--text); border-bottom: 1px solid var(--border); border-bottom-width: 1px; white-space: normal; vertical-align: top; }
.mk-table tbody tr:last-child th.mk-rowh { border-bottom: 0; }
.mk-table th small { display: block; margin-top: 2px; font: 12px/1.3 var(--font-ui); letter-spacing: 0; text-transform: none; color: var(--text-muted); white-space: normal; }
.mk-table tbody tr:last-child td { border-bottom: 0; }
.mk-table tbody tr:hover td { background: var(--surface-2); }
.mk-table td { color: var(--text-muted); }
.mk-table td:first-child, .mk-table th:first-child { position: sticky; left: 0; z-index: 2; border-right: 1px solid var(--border); }
.mk-table td:first-child { color: var(--text); font-weight: 600; }
@media (max-width: 899px) { .mk-table td:first-child, .mk-table th:first-child { min-width: 112px; max-width: 168px; } }
.mk-table td b, .mk-table td strong { color: var(--text); font-weight: 600; }
.mk-table .mk-row-cat td { background: var(--surface-2); font: 500 11px/1.4 var(--font-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--text-muted); padding-block: 8px; border-bottom: 1px solid var(--border); }
.mk-table .mk-row-cat:hover td { background: var(--surface-2); }
/* The Nubi row/column is the one accented line */
.mk-table tr.mk-row-nubi td { background: var(--accent-soft); color: var(--text); }
.mk-table tr.mk-row-nubi:hover td { background: var(--accent-soft); }
.mk-table td.mk-s2, .mk-table th.mk-s2 { }
@media (min-width: 900px) { .mk-table td.mk-s2, .mk-table th.mk-s2 { position: sticky; left: var(--mk-s2, 160px); z-index: 2; box-shadow: 1px 0 0 var(--border); } }
.mk-table th.mk-col-nubi { box-shadow: inset 0 3px 0 var(--accent); color: var(--text); }
.mk-table td.mk-col-nubi { background: var(--accent-soft); color: var(--text); }
.mk-table tbody tr:hover td.mk-col-nubi { background: var(--accent-soft); }
.mk-scroll-cue { display: none; font: 11px/1 var(--font-mono); letter-spacing: .06em; text-transform: uppercase; color: var(--text-muted); padding-bottom: 8px; justify-content: space-between; align-items: center; }
@media (max-width: 899px) { .mk-scroll-cue { display: flex; } }
.mk-more { background: none; border: 0; padding: 0; margin-top: 4px; font: 500 11px/1 var(--font-mono); letter-spacing: .06em; text-transform: uppercase; color: var(--accent); cursor: pointer; text-decoration: underline; text-underline-offset: 3px; }
.mk-clamp { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

/* ── Segmented control (tabs, billing toggle) ───────────────────────────── */
.mk-seg { display: inline-flex; border: 1px solid var(--ink); border-radius: 2px; max-width: 100%; overflow-x: auto; }
.mk-seg button { font: 500 13px/1 var(--font-ui); padding: 10px 14px; border: 0; background: transparent; color: var(--text); cursor: pointer; white-space: nowrap; display: inline-flex; align-items: center; gap: 8px; }
.mk-seg button + button { border-left: 1px solid var(--ink); }
.mk-seg button[aria-selected="true"], .mk-seg button[aria-pressed="true"] { background: var(--ink); color: var(--ink-fg); }
.mk-seg button:hover:not([aria-selected="true"]):not([aria-pressed="true"]) { background: var(--accent-soft); }
.mk-seg button:focus-visible { outline-offset: -3px; }

/* ── Cost bars: ink for them, accent for us ─────────────────────────────── */
.mk-bars { display: flex; flex-direction: column; }
.mk-bar { display: grid; grid-template-columns: minmax(0, 1fr) auto; grid-template-areas: "name val" "track track"; gap: 6px 14px; align-items: baseline; padding-block: 12px; border-bottom: 1px solid var(--border); }
@media (min-width: 800px) { .mk-bar { grid-template-columns: 220px minmax(0, 1fr) 128px; grid-template-areas: "name track val"; gap: 18px; align-items: center; } }
.mk-bar-name { grid-area: name; }
.mk-bar-track { grid-area: track; }
.mk-bar-val { grid-area: val; }
.mk-bar:first-child { border-top: 1px solid var(--ink); }
.mk-bar-name { font-size: 14px; font-weight: 600; min-width: 0; overflow-wrap: anywhere; }
.mk-bar-name small { display: block; font: 400 12px/1.4 var(--font-ui); color: var(--text-muted); margin-top: 2px; }
.mk-bar-track { position: relative; height: 18px; background: var(--surface-2); }
.mk-bar-fill { position: absolute; inset: 0 auto 0 0; background: var(--ink); transition: width 400ms cubic-bezier(.22,1,.36,1); }
.mk-bar-fill.mk-ext { background: color-mix(in srgb, var(--ink) 30%, transparent); }
.mk-bar-fill.mk-us { background: var(--accent); min-width: 3px; }
.mk-bar-val { font: 500 14px/1.2 var(--font-mono); text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.mk-bar.mk-nubi .mk-bar-val, .mk-bar.mk-nubi .mk-bar-name { color: var(--accent); }
@media (prefers-reduced-motion: reduce) { .mk-bar-fill { transition: none; } }

/* ── Callout: 2px left rule + mono label ────────────────────────────────── */
.mk-note { border-left: 2px solid var(--accent); padding: 4px 0 4px 16px; display: flex; flex-direction: column; gap: 6px; }
.mk-note.mk-ink { border-left-color: var(--ink); }
.mk-note p { margin: 0; font-size: 14px; line-height: 1.6; color: var(--text-muted); }
.mk-note p b { color: var(--text); font-weight: 600; }

/* ── Ink surfaces (calculator header, code, CTA band) ───────────────────── */
.mk-ink-band { background: var(--ink); color: var(--ink-fg); }
.mk-ink-band .mk-label, .mk-ink-band .mk-lead, .mk-ink-band .mk-p { color: color-mix(in srgb, var(--ink-fg) 72%, transparent); }
.mk-ink-band .mk-h2 em { color: var(--accent-on-ink, var(--ink-fg)); text-decoration: underline; text-decoration-thickness: 3px; text-underline-offset: 6px; text-decoration-color: var(--accent); }
.mk-ink-band .nubi-btn-primary { background: var(--ink-fg); color: var(--ink); }
.mk-ink-band .nubi-btn-primary:hover:not(:disabled) { background: var(--accent); color: var(--primary-fg); }
.mk-ink-band .nubi-btn-secondary { background: transparent; color: var(--ink-fg); border-color: color-mix(in srgb, var(--ink-fg) 45%, transparent); box-shadow: none; }
.mk-ink-band .nubi-btn-secondary:hover:not(:disabled) { background: color-mix(in srgb, var(--ink-fg) 12%, transparent); }
.mk-ink-band :focus-visible { outline-color: var(--ink-fg); }

/* ── Range input: ink track, square accent thumb ────────────────────────── */
.mk-range { -webkit-appearance: none; appearance: none; width: 100%; height: 24px; background: transparent; cursor: pointer; }
.mk-range::-webkit-slider-runnable-track { height: 2px; background: var(--ink); }
.mk-range::-moz-range-track { height: 2px; background: var(--ink); }
.mk-range::-webkit-slider-thumb { -webkit-appearance: none; appearance: none; width: 16px; height: 16px; margin-top: -7px; border-radius: 0; background: var(--accent); border: 2px solid var(--surface); box-shadow: 0 0 0 1px var(--ink); }
.mk-range::-moz-range-thumb { width: 12px; height: 12px; border-radius: 0; background: var(--accent); border: 2px solid var(--surface); box-shadow: 0 0 0 1px var(--ink); }
.mk-range:focus-visible::-webkit-slider-thumb { outline: 2px solid var(--ring); outline-offset: 2px; }
.mk-range:focus-visible::-moz-range-thumb { outline: 2px solid var(--ring); outline-offset: 2px; }

/* ── FAQ disclosure ──────────────────────────────────────────────────────── */
.mk-faq { border-top: 1px solid var(--ink); }
.mk-faq details { border-bottom: 1px solid var(--border); }
.mk-faq summary { list-style: none; cursor: pointer; display: flex; gap: 16px; align-items: baseline; padding: 18px 0; font-size: 17px; font-weight: 600; letter-spacing: -.01em; }
.mk-faq summary::-webkit-details-marker { display: none; }
.mk-faq summary .mk-faq-n { font: 500 12px/1 var(--font-mono); color: var(--text-muted); flex: none; width: 28px; }
.mk-faq summary .mk-faq-q { flex: 1; }
.mk-faq summary::after { content: '+'; font: 500 18px/1 var(--font-mono); color: var(--text-muted); flex: none; }
.mk-faq details[open] summary::after { content: '\\2212'; color: var(--text); }
.mk-faq .mk-faq-a { padding: 0 44px 22px 44px; max-width: 78ch; }
@media (max-width: 639px) { .mk-faq .mk-faq-a { padding: 0 0 20px 0; } }

/* ── Scroll reveal (honest by default: content is visible unless the page
      measured it below the fold). Reduced motion shows everything. ─────── */
.mk-reveal[data-mk-r="hidden"] { opacity: 0; transform: translateY(10px); }
.mk-reveal { transition: opacity 360ms cubic-bezier(.22,1,.36,1), transform 360ms cubic-bezier(.22,1,.36,1); }
@media (prefers-reduced-motion: reduce) {
  .mk-reveal, .lp-reveal { transition: none !important; opacity: 1 !important; transform: none !important; }
}
/* back-compat for the shared hook's classes: never leave content invisible */
.lp-reveal { opacity: 1; transform: none; }

/* ── Forms (auth, onboarding, consent) ──────────────────────────────────── */
.mk-form { display: flex; flex-direction: column; gap: 18px; }
.mk-field { display: flex; flex-direction: column; gap: 6px; }
.mk-field-row { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.mk-flabel { font-size: 13px; font-weight: 600; color: var(--text); }
.mk-opt { font: 11px/1 var(--font-mono); color: var(--text-muted); letter-spacing: .04em; }
.mk-control { position: relative; display: block; }
.mk-input {
  display: block; width: 100%; height: 44px; padding: 0 12px; font: 400 15px/1.2 var(--font-ui); color: var(--text);
  background: var(--surface); border: 1px solid var(--border-control, color-mix(in srgb, var(--ink) 48%, var(--surface))); border-radius: 2px;
  transition: border-color 100ms, box-shadow 100ms;
}
.mk-input::placeholder { color: color-mix(in srgb, var(--text-muted) 80%, var(--surface)); }
.mk-input:hover:not(:disabled) { border-color: var(--ink); }
.mk-input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 2px var(--surface), 0 0 0 4px var(--accent); }
.mk-input:disabled { opacity: .55; cursor: not-allowed; }
.mk-input[aria-invalid="true"] { border-color: var(--danger); }
.mk-input.mk-pr { padding-right: 44px; }
.mk-eye { position: absolute; top: 0; right: 0; width: 44px; height: 44px; display: inline-flex; align-items: center; justify-content: center; background: transparent; border: 0; color: var(--text-muted); cursor: pointer; border-radius: 0 2px 2px 0; }
.mk-eye:hover { color: var(--text); }
.mk-eye:focus-visible { outline-offset: -3px; }
.mk-ferr { display: flex; gap: 6px; align-items: flex-start; font-size: 13px; line-height: 1.4; color: var(--danger-fg, var(--danger)); }
.dark .mk-ferr { color: var(--danger); }
.mk-ferr::before { content: ''; width: 6px; height: 6px; margin-top: 6px; background: currentColor; flex: none; }
.mk-fhint { font-size: 12.5px; line-height: 1.4; color: var(--text-muted); }
.mk-banner { display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; font-size: 14px; line-height: 1.45; border-left: 2px solid var(--danger); background: var(--danger-bg); color: var(--danger-fg); }
.dark .mk-banner { color: var(--text); }
.mk-banner.mk-ok { border-left-color: var(--success); background: var(--success-bg); color: var(--success-fg); }
.dark .mk-banner.mk-ok { color: var(--text); }
.mk-banner svg { flex: none; margin-top: 2px; }
.mk-sso {
  display: inline-flex; align-items: center; justify-content: center; gap: 10px; width: 100%; height: 46px; padding: 0 16px;
  font: 600 14px/1 var(--font-ui); color: var(--text); background: var(--surface); border: 1px solid var(--ink); border-radius: 2px; cursor: pointer;
}
.mk-sso:hover:not(:disabled) { background: var(--surface-2); }
.mk-sso:disabled { opacity: .55; cursor: not-allowed; }
.mk-or { display: flex; align-items: center; gap: 12px; font: 11px/1 var(--font-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--text-muted); }
.mk-or::before, .mk-or::after { content: ''; flex: 1; height: 1px; background: var(--border); }
.mk-meter { display: grid; grid-template-columns: repeat(4, 1fr); gap: 3px; margin-top: 2px; }
.mk-meter i { height: 3px; background: var(--border); }
.mk-meter i.on { background: var(--ink); }
.mk-check { display: flex; gap: 12px; align-items: flex-start; padding: 12px 14px; border: 1px solid var(--border); border-radius: 2px; cursor: pointer; }
.mk-check:hover { border-color: var(--ink); }
.mk-check input { width: 16px; height: 16px; margin-top: 2px; accent-color: var(--ink); flex: none; }
.mk-check b { display: block; font-size: 14px; font-weight: 600; }
.mk-check span span { display: block; font-size: 13px; line-height: 1.4; color: var(--text-muted); margin-top: 2px; }
.mk-pair { display: grid; gap: 18px; }
@media (min-width: 440px) { .mk-pair { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; } }
.mk-meta { list-style: none; margin: 8px 0 0; padding: 0; border-top: 1px solid var(--border); }
.mk-meta li { display: flex; justify-content: space-between; gap: 16px; padding: 10px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.mk-meta b { font: 500 11px/1.6 var(--font-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--text); }
.mk-meta span { color: var(--text-muted); text-align: right; }
.mk-submit { width: 100%; height: 48px; font-size: 15px; font-weight: 600; }

@keyframes mk-spin { to { transform: rotate(360deg); } }
.mk-spin { width: 14px; height: 14px; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; animation: mk-spin .7s linear infinite; flex: none; }
@media (prefers-reduced-motion: reduce) { .mk-spin { animation-duration: 2s; } }

/* ── Print-ish helpers ───────────────────────────────────────────────────── */
.mk-hide-sm { display: none; }
@media (min-width: 640px) { .mk-hide-sm { display: initial; } }
.mk-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
`

const MarketingStyles = () => (
  // React 19 hoists <style href precedence> into <head> and dedupes by href.
  <style href="nubi-marketing-kit" precedence="default">{CSS}</style>
)

export default MarketingStyles
