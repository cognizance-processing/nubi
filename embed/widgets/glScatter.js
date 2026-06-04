/**
 * glScatter.js — Self-contained regl WebGL scatter renderer for the widget kit (M8-A).
 *
 * Adapted from src/viz/scatterRenderer.js — no src/ imports so the embed
 * bundle is fully standalone.
 *
 * API
 * ---
 *   createGlScatter(canvas) → { draw({x, y, color?, pointSize?}), destroy() }
 *
 * - x, y: Float32Array of already-normalized values in [-1, 1] clip space.
 *   Caller normalises via min/max before passing here.
 * - color: optional Uint8Array (RGBA per point, length n*4) or Float32Array
 *   (RGB, length n*3). If omitted defaults to solid indigo.
 * - pointSize: CSS pixels per point (default 3).
 * - Throws a descriptive Error when WebGL or regl init fails so nubi-chart
 *   can fall back to the 2D-canvas / SVG path.
 */

import createRegl from 'regl'

// ---------------------------------------------------------------------------
// GLSL shaders  (identical to scatterRenderer.js — validated there)
// ---------------------------------------------------------------------------

const VERT = /* glsl */ `
precision highp float;

attribute vec2 position;
attribute vec4 color;
uniform float pointSize;

varying vec4 vColor;

void main() {
  gl_Position = vec4(position, 0.0, 1.0);
  gl_PointSize = pointSize;
  vColor = color;
}
`

const FRAG = /* glsl */ `
precision mediump float;

varying vec4 vColor;

void main() {
  vec2 pc = gl_PointCoord * 2.0 - 1.0;
  if (dot(pc, pc) > 1.0) discard;
  gl_FragColor = vColor;
}
`

// Default: indigo-600 (#4F46E5) at 85 % opacity
const DEFAULT_COLOR = new Float32Array([0.31, 0.275, 0.898, 0.85])

// ---------------------------------------------------------------------------
// createGlScatter
// ---------------------------------------------------------------------------

/**
 * Initialise a regl-backed WebGL scatter renderer on the given canvas.
 *
 * @param {HTMLCanvasElement} canvas
 * @returns {{ draw: Function, destroy: Function }}
 * @throws {Error} if WebGL context or regl init fails
 */
export function createGlScatter(canvas) {
  if (!canvas || !(canvas instanceof HTMLCanvasElement)) {
    throw new Error('[glScatter] canvas must be an HTMLCanvasElement')
  }

  // Probe WebGL support before regl attempts (cleaner error message)
  const probe = canvas.getContext('webgl') || canvas.getContext('experimental-webgl')
  if (!probe) {
    throw new Error('[glScatter] WebGL is not supported in this browser/environment')
  }

  let regl
  try {
    regl = createRegl({
      canvas,
      optionalExtensions: ['OES_standard_derivatives'],
    })
  } catch (err) {
    throw new Error(`[glScatter] regl init failed: ${err.message}`)
  }

  // Dynamic GPU buffers — updated in draw() without re-creating GPU objects
  const positionBuf = regl.buffer({ usage: 'dynamic', type: 'float', data: new Float32Array(2) })
  const colorBuf    = regl.buffer({ usage: 'dynamic', type: 'float', data: new Float32Array(4) })

  const drawCmd = regl({
    vert: VERT,
    frag: FRAG,

    attributes: {
      position: { buffer: positionBuf, size: 2 },
      color:    { buffer: colorBuf,    size: 4 },
    },

    uniforms: {
      pointSize: regl.prop('pointSize'),
    },

    count:     regl.prop('count'),
    primitive: 'points',

    blend: {
      enable: true,
      func: {
        srcRGB:   'src alpha',
        dstRGB:   'one minus src alpha',
        srcAlpha: 'one',
        dstAlpha: 'one minus src alpha',
      },
    },

    depth: { enable: false },
  })

  // ---------------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------------

  /**
   * @param {{
   *   x: Float32Array,
   *   y: Float32Array,
   *   color?: Uint8Array|Float32Array,
   *   pointSize?: number
   * }} opts
   */
  function draw({ x, y, color, pointSize = 3 }) {
    if (!x || !y || x.length === 0 || y.length === 0) {
      regl.clear({ color: [0.059, 0.067, 0.09, 1], depth: 1 })
      return
    }

    const n = Math.min(x.length, y.length)

    // Interleave x/y → [x0,y0, x1,y1, …]
    const posData = new Float32Array(n * 2)
    for (let i = 0; i < n; i++) {
      posData[i * 2]     = x[i]
      posData[i * 2 + 1] = y[i]
    }

    // Normalise color input to RGBA Float32Array
    let colorData
    if (color instanceof Uint8Array && color.length >= n * 4) {
      colorData = new Float32Array(n * 4)
      for (let i = 0; i < n * 4; i++) colorData[i] = color[i] / 255.0
    } else if (color instanceof Float32Array && color.length >= n * 3) {
      colorData = new Float32Array(n * 4)
      for (let i = 0; i < n; i++) {
        colorData[i * 4]     = color[i * 3]
        colorData[i * 4 + 1] = color[i * 3 + 1]
        colorData[i * 4 + 2] = color[i * 3 + 2]
        colorData[i * 4 + 3] = 0.85
      }
    } else {
      colorData = new Float32Array(n * 4)
      for (let i = 0; i < n; i++) {
        colorData[i * 4]     = DEFAULT_COLOR[0]
        colorData[i * 4 + 1] = DEFAULT_COLOR[1]
        colorData[i * 4 + 2] = DEFAULT_COLOR[2]
        colorData[i * 4 + 3] = DEFAULT_COLOR[3]
      }
    }

    positionBuf.subdata(posData)
    colorBuf.subdata(colorData)

    regl.clear({ color: [0.059, 0.067, 0.09, 1], depth: 1 })
    drawCmd({ count: n, pointSize })
  }

  function destroy() {
    try { positionBuf.destroy() } catch (_) { /* ignore */ }
    try { colorBuf.destroy()    } catch (_) { /* ignore */ }
    try { regl.destroy()        } catch (_) { /* ignore */ }
  }

  return { draw, destroy }
}
