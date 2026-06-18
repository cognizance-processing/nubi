/**
 * svg-composer.mjs — Compose per-widget SVGs into a full page/slide SVG.
 *
 * This module can be used both as an ESM import and as a CLI (reads JSON
 * from stdin, writes JSON to stdout — same protocol as echarts-ssr.mjs).
 *
 * Coordinate model
 * ================
 *
 * Grid surface layout
 * -------------------
 * The dashboard grid uses CSS-grid units (1-based x, y; spans w, h):
 *   - x: column start (1-based, range 1..cols)
 *   - y: row start (1-based, range 1..)
 *   - w: column span
 *   - h: row span
 *
 * Page coordinate space
 * ---------------------
 * The composed SVG uses pixel coordinates:
 *   - Page width:  PAGE_WIDTH_PX  (default 1200 px)
 *   - Col width:   PAGE_WIDTH_PX / GRID_COLS  (e.g. 1200 / 12 = 100 px/col)
 *   - Row height:  ROW_HEIGHT_PX  (default 120 px/row — ~2× the default 60 px
 *                  row_height used by the frontend grid, scaled up for print)
 *   - Widget pixel rect:
 *       px  = (entry.x - 1) * colW + MARGIN
 *       py  = (entry.y - 1) * rowH + MARGIN
 *       pw  = entry.w * colW - GAP
 *       ph  = entry.h * rowH - GAP
 *
 * The outer SVG viewBox is:
 *   0 0 PAGE_WIDTH_PX PAGE_HEIGHT_PX
 * where PAGE_HEIGHT_PX is derived from the maximum (y + h - 1) across all
 * widgets (dynamic height — pages are as tall as needed).
 *
 * Each widget SVG is embedded as a nested <svg> element at its pixel rect,
 * preserving the inner SVG's coordinate system via x/y/width/height attributes
 * (no transforms applied — the outer viewBox handles scaling).
 *
 * Slide surface
 * -------------
 * For a slide surface, all widgets on one slide share a fixed-height page
 * (SLIDE_HEIGHT_PX).  The same column/row coordinate model applies; the
 * slide height is a hard cap rather than dynamic.
 *
 * Usage as CLI:
 *   echo '<payload>' | node scripts/render/svg-composer.mjs
 *
 * Payload shape (JSON on stdin):
 *   {
 *     surface: 'grid' | 'slides',    // default 'grid'
 *     cols: 12,                       // grid columns (default 12)
 *     row_height: 60,                 // grid row height in grid units (default 60)
 *     page_width_px: 1200,            // output page width in pixels (default 1200)
 *     row_height_px: 120,             // output row height in pixels (default 120)
 *     margin_px: 16,                  // outer margin in pixels (default 16)
 *     gap_px: 8,                      // inter-widget gap in pixels (default 8)
 *     widgets: [
 *       {
 *         id: string,
 *         layout: { x, y, w, h },     // 1-based grid position
 *         svg: string | null,          // pre-rendered widget SVG
 *         webgl: boolean,              // true → raster fallback placeholder
 *       },
 *       ...
 *     ]
 *   }
 *
 * Response shape (JSON on stdout):
 *   {
 *     svg: string,         // composed page SVG string
 *     page_width_px: number,
 *     page_height_px: number,
 *     widget_rects: [
 *       { id, x_px, y_px, w_px, h_px },   // pixel rects for each placed widget
 *       ...
 *     ]
 *   }
 */

// ---------------------------------------------------------------------------
// Constants + defaults
// ---------------------------------------------------------------------------

export const DEFAULT_COLS = 12
export const DEFAULT_PAGE_WIDTH_PX = 1200
export const DEFAULT_ROW_HEIGHT_PX = 120  // doubled from the 60-unit frontend grid
export const DEFAULT_MARGIN_PX = 16
export const DEFAULT_GAP_PX = 8
export const DEFAULT_SLIDE_HEIGHT_PX = 675  // 16:9 at 1200 wide ≈ 675 tall

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function svgEsc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/**
 * Build the raster-fallback placeholder SVG for WebGL widgets.
 * In production the Python caller would embed a PNG <image> element instead;
 * this stub indicates where that image would live.
 *
 * @param {number} w  pixel width
 * @param {number} h  pixel height
 * @param {string} id widget id
 * @returns {string}  SVG fragment
 */
function webglPlaceholderSvg(w, h, id) {
  return [
    `<svg width="${w}" height="${h}" xmlns="http://www.w3.org/2000/svg">`,
    `  <rect width="${w}" height="${h}" fill="#1f2937" rx="4"/>`,
    `  <text x="${w / 2}" y="${h / 2 - 10}" text-anchor="middle"`,
    `    font-family="sans-serif" font-size="13" fill="#9ca3af">WebGL widget</text>`,
    `  <text x="${w / 2}" y="${h / 2 + 12}" text-anchor="middle"`,
    `    font-family="sans-serif" font-size="11" fill="#6b7280">${svgEsc(id)}</text>`,
    `</svg>`,
  ].join('\n')
}

// ---------------------------------------------------------------------------
// Core composer
// ---------------------------------------------------------------------------

/**
 * Compose widget SVGs onto a single page SVG.
 *
 * @param {object} params
 * @param {string}  [params.surface='grid']          – Surface type.
 * @param {number}  [params.cols=12]                 – Grid column count.
 * @param {number}  [params.page_width_px=1200]      – Output width in px.
 * @param {number}  [params.row_height_px=120]       – Output row height in px.
 * @param {number}  [params.margin_px=16]            – Outer margin in px.
 * @param {number}  [params.gap_px=8]                – Inter-widget gap in px.
 * @param {Array}   params.widgets                   – Widget entries (see payload shape).
 * @returns {{ svg: string, page_width_px: number, page_height_px: number, widget_rects: Array }}
 */
export function composePage({
  surface = 'grid',
  cols = DEFAULT_COLS,
  page_width_px = DEFAULT_PAGE_WIDTH_PX,
  row_height_px = DEFAULT_ROW_HEIGHT_PX,
  margin_px = DEFAULT_MARGIN_PX,
  gap_px = DEFAULT_GAP_PX,
  widgets = [],
}) {
  const colW = (page_width_px - 2 * margin_px) / cols

  // Compute pixel rects for each widget.
  const rects = []
  let maxRow = 0

  for (const w of widgets) {
    const layout = w.layout
    if (!layout || typeof layout.x !== 'number') continue  // skip unpositoned

    const { x, y, ww: wspan, h: hspan } = { ww: layout.w, h: layout.h, ...layout }
    const xPx = margin_px + (x - 1) * colW
    const yPx = margin_px + (y - 1) * row_height_px
    const wPx = Math.max(1, wspan * colW - gap_px)
    const hPx = Math.max(1, hspan * row_height_px - gap_px)

    rects.push({ id: w.id, x_px: xPx, y_px: yPx, w_px: wPx, h_px: hPx, widget: w })
    const bottomRow = y + hspan - 1
    if (bottomRow > maxRow) maxRow = bottomRow
  }

  // Dynamic height for grid surface; fixed cap for slides.
  let page_height_px
  if (surface === 'slides') {
    page_height_px = DEFAULT_SLIDE_HEIGHT_PX
  } else {
    page_height_px = maxRow > 0
      ? maxRow * row_height_px + 2 * margin_px
      : row_height_px + 2 * margin_px
  }

  // Build the composed SVG.
  const svgParts = [
    `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"`,
    `  width="${page_width_px}" height="${page_height_px}"`,
    `  viewBox="0 0 ${page_width_px} ${page_height_px}">`,
    `  <rect width="${page_width_px}" height="${page_height_px}" fill="#ffffff"/>`,
  ]

  for (const rect of rects) {
    const { id, x_px, y_px, w_px, h_px, widget } = rect

    // Choose SVG content: rendered SVG, webgl placeholder, or empty box.
    let innerSvg
    if (widget.webgl) {
      innerSvg = webglPlaceholderSvg(w_px, h_px, id)
    } else if (widget.svg) {
      innerSvg = widget.svg
    } else {
      // Error / no data: empty placeholder box.
      innerSvg = [
        `<svg width="${w_px}" height="${h_px}" xmlns="http://www.w3.org/2000/svg">`,
        `  <rect width="${w_px}" height="${h_px}" fill="#f9fafb" rx="4"`,
        `    stroke="#e5e7eb" stroke-width="1"/>`,
        `  <text x="${w_px / 2}" y="${h_px / 2 + 5}" text-anchor="middle"`,
        `    font-family="sans-serif" font-size="12" fill="#d1d5db">`,
        `    ${svgEsc(widget.error || id)}`,
        `  </text>`,
        `</svg>`,
      ].join('\n')
    }

    // Embed as a nested <svg> at the widget's pixel position.
    // We strip the outer <svg ...> tag and re-emit with x/y/width/height
    // attributes so the nested SVG positions correctly within the page.
    const stripped = innerSvg
      .replace(/^<svg\b[^>]*>/i, '')
      .replace(/<\/svg>\s*$/i, '')
      .trim()

    svgParts.push(
      `  <!-- widget: ${svgEsc(id)} -->`,
      `  <svg x="${x_px.toFixed(1)}" y="${y_px.toFixed(1)}"`,
      `       width="${w_px.toFixed(1)}" height="${h_px.toFixed(1)}"`,
      `       viewBox="0 0 ${w_px.toFixed(1)} ${h_px.toFixed(1)}"`,
      `       preserveAspectRatio="xMidYMid meet">`,
      `    ${stripped}`,
      `  </svg>`,
    )
  }

  svgParts.push(`</svg>`)

  const widget_rects = rects.map(({ id, x_px, y_px, w_px, h_px }) => ({
    id, x_px, y_px, w_px, h_px,
  }))

  return {
    svg: svgParts.join('\n'),
    page_width_px,
    page_height_px,
    widget_rects,
  }
}

// ---------------------------------------------------------------------------
// CLI entry point (stdin → stdout JSON protocol)
// ---------------------------------------------------------------------------

async function main() {
  const chunks = []
  for await (const chunk of process.stdin) chunks.push(chunk)
  const raw = Buffer.concat(chunks).toString('utf8')

  let payload
  try {
    payload = JSON.parse(raw)
  } catch (err) {
    process.stderr.write(`[svg-composer] invalid JSON on stdin: ${err.message}\n`)
    process.exit(1)
  }

  const result = composePage(payload)
  process.stdout.write(JSON.stringify(result) + '\n')
}

// Run as CLI when invoked directly.
const isMain = process.argv[1] && (
  process.argv[1].endsWith('svg-composer.mjs') ||
  process.argv[1].endsWith('svg-composer')
)
if (isMain) {
  main().catch(err => {
    process.stderr.write(`[svg-composer] fatal: ${err.message}\n${err.stack}\n`)
    process.exit(1)
  })
}
