/**
 * exports.js — Self-contained data-export helpers for Nubi dashboards.
 *
 * Exports
 * -------
 *   arrowTableToCSV(table)           Pure function: apache-arrow Table → CSV string.
 *                                    Safe to unit-test in Node with no DOM.
 *   downloadCSV(filename, csv)       Browser-only: triggers a file download.
 *   chartToPNG(echartsInstance, fn)  Browser-only: ECharts getDataURL → download.
 *   elementToPNG(domEl, filename)    Browser-only: domCapture → download.
 *   elementToPDF(domEl, filename)    Browser-only: domCapture → jsPDF → download.
 *
 * Usage (per-widget)
 * ------------------
 *   import { arrowTableToCSV, downloadCSV, chartToPNG, elementToPDF } from '../lib/exports.js'
 *
 *   // CSV: pass the Arrow Table that ChartWidget / TableWidget already holds.
 *   downloadCSV('my-data.csv', arrowTableToCSV(table))
 *
 *   // PNG: pass the ECharts instance stored in a ref via EChart's onEvents or
 *   //      obtained via echarts.getInstanceByDom(domElement).
 *   chartToPNG(echartsInstance, 'chart.png')
 *
 *   // PNG / PDF: pass the root DOM element (the div wrapping the whole widget card).
 *   // Both route through lib/domCapture, which is where the html2canvas
 *   // workarounds live — see that file before changing capture behaviour.
 *   elementToPDF(widgetDomEl, 'dashboard.pdf', { title: 'My board' })
 *
 * Dependencies
 * ------------
 *   papaparse  ^5   (CSV serialisation)
 *   jspdf      ^4   (PDF generation)
 *   lib/domCapture  (DOM → canvas rasterisation)
 */

import Papa from 'papaparse'

// ---------------------------------------------------------------------------
// csvCell / widgetToCsv — serialise one GET /boards/:id/export.json entry
// ({ widget_id, query_id, columns, rows, error }) to CSV. Shared by the
// whole-dashboard export menu and per-widget export.
// ---------------------------------------------------------------------------

/** RFC-4180-ish CSV cell. */
export function csvCell(v) {
  if (v == null) return ''
  const s = String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

/** Serialise one export-entry to CSV rows (header + data, no wrapping comment). */
export function widgetToCsv(w) {
  if (w.error) return `# error: ${w.error}`
  const cols = w.columns ?? []
  const parts = [cols.map(csvCell).join(',')]
  for (const row of w.rows ?? []) {
    parts.push((Array.isArray(row) ? row : cols.map(c => row[c])).map(csvCell).join(','))
  }
  return parts.join('\n')
}

// ---------------------------------------------------------------------------
// arrowTableToCSV — pure function (no DOM, no browser APIs)
// ---------------------------------------------------------------------------

/**
 * Convert an apache-arrow Table to a CSV string using PapaParse.
 *
 * The function materialises column names from `table.schema.fields` and
 * row values from `table.get(rowIndex)` (the StructRow proxy). Null values
 * are serialised as empty strings. Works in Node.js (no DOM required).
 *
 * @param {import('apache-arrow').Table} table  An apache-arrow Table instance.
 * @returns {string}  A RFC-4180-compliant CSV string including a header row.
 */
export function arrowTableToCSV(table) {
  const fields = table.schema.fields.map((f) => f.name)
  const numRows = table.numRows

  // Build a plain-JS array-of-arrays for PapaParse's unparse().
  // Row 0 = header; rows 1..N = data.
  const rows = [fields]
  for (let i = 0; i < numRows; i++) {
    const row = table.get(i)   // StructRow (Map-like) from apache-arrow
    rows.push(fields.map((col) => {
      const val = row[col]
      // Convert null/undefined to empty string; everything else to string.
      if (val === null || val === undefined) return ''
      return val
    }))
  }

  return Papa.unparse(rows, { header: false }) // header row already included
}

// ---------------------------------------------------------------------------
// downloadCSV — browser-only helper
// ---------------------------------------------------------------------------

/**
 * Trigger a browser file download for a CSV string.
 *
 * @param {string} filename  Suggested filename (e.g. 'data.csv').
 * @param {string} csv       CSV string produced by arrowTableToCSV().
 */
export function downloadCSV(filename, csv) {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  _triggerDownload(blob, filename)
}

// ---------------------------------------------------------------------------
// chartToPNG — browser-only, ECharts-specific
// ---------------------------------------------------------------------------

/**
 * Export an ECharts instance as a PNG file download.
 *
 * Obtain the ECharts instance from:
 *   - The `chartRef` stored inside a parent component, OR
 *   - `echarts.getInstanceByDom(containerDomElement)`
 *
 * @param echartsInstance  A live ECharts chart instance (not disposed).
 * @param filename         Suggested filename (e.g. 'chart.png').
 * @param opts             Optional ECharts getDataURL options.
 */
export function chartToPNG(
  echartsInstance: any,
  filename = 'chart.png',
  opts: { pixelRatio?: number; backgroundColor?: string; excludeComponents?: string[] } = {},
) {
  if (!echartsInstance || typeof echartsInstance.getDataURL !== 'function') {
    throw new Error('chartToPNG: first argument must be a live ECharts instance.')
  }

  const dataURL = echartsInstance.getDataURL({
    type: 'png',
    pixelRatio: opts.pixelRatio ?? 2,
    backgroundColor: opts.backgroundColor ?? '#ffffff',
    excludeComponents: opts.excludeComponents ?? ['toolbox'],
  })

  // Convert data URL → Blob → download
  const [, b64] = dataURL.split(',')
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  const blob = new Blob([bytes], { type: 'image/png' })
  _triggerDownload(blob, filename)
}

// ---------------------------------------------------------------------------
// elementToPDF / elementToPNG — browser-only, built on lib/domCapture
// ---------------------------------------------------------------------------

/** A4 in points, and the breathing room around the artwork. */
const PAGE = { w: 595.28, h: 841.89 }
const MARGIN = 32
const HEADER_H = 26
const FOOTER_H = 18

/**
 * Decide how a capture of `imgW` x `imgH` device pixels sits on a page whose
 * usable area is `contentW` x `contentH` points.
 *
 * Two ways to lay the artwork out: fill the page width and spill onto more
 * pages, or shrink until the whole thing fits on one. A board only a little
 * taller than a page reads far better whole than split across a page and a
 * sliver, so we only paginate once fitting would shrink it past readability.
 *
 * Pure, and exported for tests.
 */
export function planPdfLayout(imgW, imgH, contentW, contentH) {
  const widthScale = contentW / imgW
  const pageScale = Math.min(widthScale, contentH / imgH)
  const onePage = pageScale >= widthScale * 0.6

  const ptPerPx = onePage ? pageScale : widthScale
  const sliceH = onePage ? imgH : Math.max(1, Math.floor(contentH / ptPerPx))
  return {
    ptPerPx,
    sliceH,
    pages: onePage ? 1 : Math.max(1, Math.ceil(imgH / sliceH)),
    drawW: imgW * ptPerPx,
  }
}

/**
 * Capture a DOM element and download it as a PNG.
 *
 * @param domEl     The root DOM element to capture.
 * @param filename  Suggested filename (e.g. 'dashboard.png').
 * @param opts      Capture options — see lib/domCapture.
 */
export async function elementToPNG(domEl, filename = 'dashboard.png', opts = {}) {
  const { captureToPngBlob } = await import('./domCapture.js')
  _triggerDownload(await captureToPngBlob(domEl, opts), filename)
}

/**
 * Capture a DOM element and download it as a PDF.
 *
 * The page is A4, oriented to match the capture's own aspect so a wide
 * dashboard gets a wide page. A board that fits on one page — or nearly does —
 * is scaled to fit it whole; a genuinely long one keeps full width and is
 * sliced across further pages rather than shrunk until it is unreadable. Each
 * page carries the dashboard title and the capture date, plus a page counter
 * when there is more than one.
 *
 * @param domEl     The root DOM element to capture.
 * @param filename  Suggested filename (e.g. 'dashboard.pdf').
 * @param opts      `title` for the running header, plus lib/domCapture options.
 */
export async function elementToPDF(domEl, filename = 'dashboard.pdf', opts: any = {}) {
  const { title, ...captureOpts } = opts
  const [{ captureElement }, { jsPDF }] = await Promise.all([
    import('./domCapture.js'),
    import('jspdf'),
  ])

  const canvas = await captureElement(domEl, captureOpts)
  const landscape = canvas.width >= canvas.height
  const pageW = landscape ? PAGE.h : PAGE.w
  const pageH = landscape ? PAGE.w : PAGE.h

  const contentW = pageW - MARGIN * 2
  const contentH = pageH - MARGIN * 2 - HEADER_H - FOOTER_H
  const { ptPerPx, sliceH, pages, drawW } = planPdfLayout(canvas.width, canvas.height, contentW, contentH)
  // Centre it when shrinking left room on either side.
  const drawX = MARGIN + (contentW - drawW) / 2

  const pdf = new jsPDF({ orientation: landscape ? 'l' : 'p', unit: 'pt', format: [pageW, pageH] })
  const heading = title || filename.replace(/\.pdf$/i, '')
  const stamp = new Date().toLocaleString()

  const slice = document.createElement('canvas')
  const sctx = slice.getContext('2d')

  for (let i = 0; i < pages; i++) {
    if (i > 0) pdf.addPage([pageW, pageH], landscape ? 'l' : 'p')

    const srcY = i * sliceH
    const srcH = Math.min(sliceH, canvas.height - srcY)
    slice.width = canvas.width
    slice.height = srcH
    sctx.clearRect(0, 0, slice.width, slice.height)
    sctx.drawImage(canvas, 0, srcY, canvas.width, srcH, 0, 0, canvas.width, srcH)

    pdf.setFontSize(11)
    pdf.setTextColor(31, 41, 55)
    pdf.text(heading, MARGIN, MARGIN + 11)
    pdf.setFontSize(8)
    pdf.setTextColor(140, 148, 160)
    pdf.text(stamp, pageW - MARGIN, MARGIN + 11, { align: 'right' })

    pdf.addImage(
      slice.toDataURL('image/png'),
      'PNG',
      drawX,
      MARGIN + HEADER_H,
      drawW,
      srcH * ptPerPx,
      undefined,
      'FAST',
    )

    if (pages > 1) {
      pdf.setFontSize(8)
      pdf.setTextColor(140, 148, 160)
      pdf.text(`${i + 1} / ${pages}`, pageW / 2, pageH - MARGIN + 4, { align: 'center' })
    }
  }

  pdf.save(filename)
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

/**
 * Create a temporary <a> element and click it to trigger a browser download.
 *
 * @param {Blob}   blob      The file payload.
 * @param {string} filename  Suggested filename for the download.
 */
function _triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  // Clean up asynchronously so the browser has time to start the download.
  setTimeout(() => {
    URL.revokeObjectURL(url)
    a.remove()
  }, 100)
}
