/**
 * scatterRenderer.js — regl-based WebGL scatter renderer (M5-A).
 *
 * API:
 *   createScatter(canvas) -> { draw({x, y, color?, pointSize?}), destroy() }
 *
 * - x, y: Float32Array of already-normalized values in [-1, 1] clip space.
 *   (Caller normalizes in JS via min/max before passing here.)
 * - color: optional Uint8Array (RGBA per point, length = n*4) or Float32Array
 *   (RGB, length = n*3). If omitted, defaults to a solid indigo.
 * - pointSize: number of CSS pixels per point (default 3).
 * - Renders up to ~1M points as gl.POINTS with circular fragment discard.
 * - Throws a descriptive Error if WebGL or regl init fails so Chart.jsx
 *   can fall back to the 2D canvas path.
 * - Handles empty arrays gracefully (no-op draw).
 */

// We import regl as a default import (regl exports a factory function).
import createRegl from 'regl'

// ---------------------------------------------------------------------------
// GLSL shaders
// ---------------------------------------------------------------------------

/**
 * Vertex shader:
 *   attribute vec2 position   — already-normalized clip-space x, y
 *   attribute vec4 color      — per-point RGBA in [0,1]
 *   uniform float pointSize   — point diameter in pixels
 *
 * Writes gl_Position and passes color to the fragment stage.
 */
const VERT = `
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

/**
 * Fragment shader:
 *   Discards pixels outside a unit circle centred on gl_PointCoord
 *   so points appear circular rather than square.
 */
const FRAG = `
precision mediump float;

varying vec4 vColor;

void main() {
  // gl_PointCoord is in [0,1]x[0,1]; map to [-1,1] for distance test
  vec2 pc = gl_PointCoord * 2.0 - 1.0;
  if (dot(pc, pc) > 1.0) discard;
  gl_FragColor = vColor;
}
`

// Default point color: indigo-600 (#4F46E5) fully opaque
const DEFAULT_COLOR = new Float32Array([0.31, 0.275, 0.898, 0.85])

// ---------------------------------------------------------------------------
// createScatter
// ---------------------------------------------------------------------------

/**
 * Initialise a regl-backed WebGL scatter renderer on the given canvas element.
 *
 * @param {HTMLCanvasElement} canvas
 * @returns {{ draw: Function, destroy: Function }}
 * @throws {Error} if WebGL context or regl init fails
 */
export function createScatter(canvas) {
  if (!canvas || !(canvas instanceof HTMLCanvasElement)) {
    throw new Error('[scatterRenderer] canvas must be an HTMLCanvasElement')
  }

  // Test for WebGL support before regl tries (gives a cleaner error message)
  const testCtx = canvas.getContext('webgl') || canvas.getContext('experimental-webgl')
  if (!testCtx) {
    throw new Error('[scatterRenderer] WebGL is not supported in this browser/environment')
  }

  let regl
  try {
    regl = createRegl({
      canvas,
      // Allow large point sizes on all drivers
      extensions: ['OES_standard_derivatives'],
      // Suppress regl's default console warnings for missing extensions
      optionalExtensions: ['OES_standard_derivatives'],
    })
  } catch (err) {
    throw new Error(`[scatterRenderer] regl init failed: ${err.message}`)
  }

  // ---------------------------------------------------------------------------
  // Buffer allocation: we pre-allocate regl buffers and update them in draw().
  // Using dynamic buffers avoids re-creating GPU objects on every call.
  // ---------------------------------------------------------------------------

  const positionBuf = regl.buffer({ usage: 'dynamic', type: 'float', data: new Float32Array(2) })
  const colorBuf    = regl.buffer({ usage: 'dynamic', type: 'float', data: new Float32Array(4) })

  // ---------------------------------------------------------------------------
  // Draw command
  // ---------------------------------------------------------------------------

  const drawCmd = regl({
    vert: VERT,
    frag: FRAG,

    attributes: {
      position: {
        buffer: positionBuf,
        size: 2,
      },
      color: {
        buffer: colorBuf,
        size: 4,
      },
    },

    uniforms: {
      pointSize: regl.prop('pointSize'),
    },

    count:     regl.prop('count'),
    primitive: 'points',

    // Enable blending for semi-transparent overdraw (looks nicer at 1M pts)
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
  // Public draw API
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
      // Nothing to draw — clear and return
      regl.clear({ color: [0.98, 0.98, 0.99, 1], depth: 1 })
      return
    }

    const n = Math.min(x.length, y.length)

    // Interleave x, y into a flat [x0,y0, x1,y1, ...] Float32Array
    const posData = new Float32Array(n * 2)
    for (let i = 0; i < n; i++) {
      posData[i * 2]     = x[i]
      posData[i * 2 + 1] = y[i]
    }

    // Build RGBA Float32Array for colors
    let colorData
    if (color instanceof Uint8Array && color.length >= n * 4) {
      // RGBA bytes [0,255] -> normalize to [0,1]
      colorData = new Float32Array(n * 4)
      for (let i = 0; i < n * 4; i++) {
        colorData[i] = color[i] / 255.0
      }
    } else if (color instanceof Float32Array && color.length >= n * 3) {
      // RGB floats [0,1] -> expand to RGBA with alpha=0.85
      colorData = new Float32Array(n * 4)
      for (let i = 0; i < n; i++) {
        colorData[i * 4]     = color[i * 3]
        colorData[i * 4 + 1] = color[i * 3 + 1]
        colorData[i * 4 + 2] = color[i * 3 + 2]
        colorData[i * 4 + 3] = 0.85
      }
    } else {
      // Default: fill with the indigo constant
      colorData = new Float32Array(n * 4)
      for (let i = 0; i < n; i++) {
        colorData[i * 4]     = DEFAULT_COLOR[0]
        colorData[i * 4 + 1] = DEFAULT_COLOR[1]
        colorData[i * 4 + 2] = DEFAULT_COLOR[2]
        colorData[i * 4 + 3] = DEFAULT_COLOR[3]
      }
    }

    // Upload to GPU buffers
    positionBuf.subdata(posData)
    colorBuf.subdata(colorData)

    regl.clear({ color: [0.98, 0.98, 0.99, 1], depth: 1 })
    drawCmd({ count: n, pointSize })
  }

  // ---------------------------------------------------------------------------
  // Cleanup
  // ---------------------------------------------------------------------------

  function destroy() {
    try {
      positionBuf.destroy()
      colorBuf.destroy()
      regl.destroy()
    } catch (_) {
      // Ignore errors during teardown (e.g., context already lost)
    }
  }

  return { draw, destroy }
}
