/**
 * canvasFallback.js — Canvas 2D scatter fallback (M5-A).
 *
 * Used when WebGL / regl is unavailable.
 * Samples down to at most MAX_POINTS before drawing so the browser thread
 * does not freeze on large datasets.
 *
 * API:
 *   draw2dScatter(canvas, x, y, color?)
 *     x, y: Float32Array in [-1, 1] clip space (caller normalizes)
 *     color: optional Float32Array (RGB, length = n*3) or Uint8Array (RGBA, n*4)
 */

const MAX_POINTS = 50_000

// Palette for default coloring when no color array is provided
const DEFAULT_FILL = 'rgba(79, 70, 229, 0.6)' // indigo-600 semi-transparent

/**
 * Draw a scatter plot via Canvas 2D API.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {Float32Array} x — x positions in [-1, 1]
 * @param {Float32Array} y — y positions in [-1, 1]
 * @param {Float32Array|Uint8Array|null} [color]
 *   Optional per-point color. Float32Array = RGB [0,1], Uint8Array = RGBA [0,255].
 * @param {number} [pointRadius=2] — circle radius in CSS pixels
 */
export function draw2dScatter(canvas, x, y, color = null, pointRadius = 2) {
  if (!canvas || !x || !y || x.length === 0 || y.length === 0) return

  const ctx = canvas.getContext('2d')
  if (!ctx) return

  const w = canvas.width
  const h = canvas.height

  ctx.clearRect(0, 0, w, h)

  // Fill background
  ctx.fillStyle = '#fafafa'
  ctx.fillRect(0, 0, w, h)

  const n = Math.min(x.length, y.length)

  // Compute stride for downsampling
  const stride = n > MAX_POINTS ? Math.ceil(n / MAX_POINTS) : 1
  const drawn  = Math.ceil(n / stride)

  // Determine if we have per-point color data
  const hasColorRGB  = color instanceof Float32Array && color.length >= n * 3
  const hasColorRGBA = color instanceof Uint8Array  && color.length >= n * 4

  // Clip-space [-1,1] -> canvas pixel coords
  // Note: y is flipped (WebGL y+ up, canvas y+ down)
  const toCanvasX = (v) => ((v + 1) / 2) * w
  const toCanvasY = (v) => ((1 - v) / 2) * h // invert y

  if (!hasColorRGB && !hasColorRGBA) {
    // Fast path: uniform color, batch all points in one path
    ctx.beginPath()
    ctx.fillStyle = DEFAULT_FILL
    for (let i = 0; i < n; i += stride) {
      const cx = toCanvasX(x[i])
      const cy = toCanvasY(y[i])
      ctx.moveTo(cx + pointRadius, cy)
      ctx.arc(cx, cy, pointRadius, 0, Math.PI * 2)
    }
    ctx.fill()
  } else {
    // Per-point color path — one arc per point (slower but correct)
    for (let i = 0; i < n; i += stride) {
      const cx = toCanvasX(x[i])
      const cy = toCanvasY(y[i])

      let r, g, b, a
      if (hasColorRGB) {
        r = Math.round(color[i * 3]     * 255)
        g = Math.round(color[i * 3 + 1] * 255)
        b = Math.round(color[i * 3 + 2] * 255)
        a = 0.75
      } else {
        r = color[i * 4]
        g = color[i * 4 + 1]
        b = color[i * 4 + 2]
        a = (color[i * 4 + 3] / 255).toFixed(2)
      }

      ctx.beginPath()
      ctx.fillStyle = `rgba(${r},${g},${b},${a})`
      ctx.arc(cx, cy, pointRadius, 0, Math.PI * 2)
      ctx.fill()
    }
  }

  // Overlay: point count + downsampling note
  ctx.fillStyle = 'rgba(0,0,0,0.45)'
  ctx.font = '11px system-ui, sans-serif'
  const label = stride > 1
    ? `Canvas 2D fallback — showing ${drawn.toLocaleString()} / ${n.toLocaleString()} pts`
    : `Canvas 2D fallback — ${drawn.toLocaleString()} pts`
  ctx.fillText(label, 8, h - 8)
}
