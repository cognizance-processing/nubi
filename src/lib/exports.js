/**
 * exports.js — Self-contained data-export helpers for Nubi dashboards.
 *
 * Exports
 * -------
 *   arrowTableToCSV(table)           Pure function: apache-arrow Table → CSV string.
 *                                    Safe to unit-test in Node with no DOM.
 *   downloadCSV(filename, csv)       Browser-only: triggers a file download.
 *   chartToPNG(echartsInstance, fn)  Browser-only: ECharts getDataURL → download.
 *   elementToPDF(domEl, filename)    Browser-only: html2canvas → jsPDF → download.
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
 *   // PDF: pass the widget's root DOM element (the div wrapping the whole widget card).
 *   elementToPDF(widgetDomEl, 'dashboard.pdf')
 *
 * Dependencies
 * ------------
 *   papaparse  ^5   (CSV serialisation)
 *   jspdf      ^2   (PDF generation)
 *   html2canvas ^1  (DOM → canvas rasterisation)
 */

import Papa from 'papaparse'

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
 * @param {object} echartsInstance  A live ECharts chart instance (not disposed).
 * @param {string} filename         Suggested filename (e.g. 'chart.png').
 * @param {object} [opts]           Optional ECharts getDataURL options.
 * @param {number} [opts.pixelRatio=2]    Device-pixel ratio for the exported image.
 * @param {string} [opts.backgroundColor='#ffffff']  Background fill colour.
 */
export function chartToPNG(echartsInstance, filename = 'chart.png', opts = {}) {
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
// elementToPDF — browser-only, html2canvas + jsPDF
// ---------------------------------------------------------------------------

/**
 * Rasterise a DOM element with html2canvas then embed it into a PDF page
 * (jsPDF, A4 landscape) and trigger a download.
 *
 * The page dimensions are computed from the element's bounding box so the
 * content fills the PDF without letterboxing.
 *
 * @param {HTMLElement} domEl     The root DOM element to capture.
 * @param {string}      filename  Suggested filename (e.g. 'dashboard.pdf').
 * @returns {Promise<void>}
 */
export async function elementToPDF(domEl, filename = 'dashboard.pdf') {
  const [{ default: html2canvas }, { jsPDF }] = await Promise.all([
    import('html2canvas'),
    import('jspdf'),
  ])

  const canvas = await html2canvas(domEl, {
    scale: 2,
    useCORS: true,
    logging: false,
  })

  const imgData = canvas.toDataURL('image/png')
  const imgW = canvas.width
  const imgH = canvas.height

  // Choose orientation based on aspect ratio
  const orientation = imgW >= imgH ? 'l' : 'p'

  const pdf = new jsPDF({
    orientation,
    unit: 'px',
    format: [imgW / 2, imgH / 2],   // device-pixels → CSS-pixels
    hotfixes: ['px_scaling'],
  })

  pdf.addImage(imgData, 'PNG', 0, 0, imgW / 2, imgH / 2)
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
