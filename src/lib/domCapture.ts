/**
 * domCapture.ts — the one place that turns a piece of the live app into a
 * bitmap. Both the whole-dashboard export (ExportShareMenu) and the per-widget
 * export (WidgetExportButton) go through `captureElement` so a fix here shows
 * up in every export.
 *
 * html2canvas re-implements CSS painting rather than asking the browser to do
 * it, so five things have to be handled before it sees the DOM. Each of the
 * fixes below is a workaround for a specific way it renders a dashboard wrong:
 *
 * 1. Modern colour syntax. `index.css` leans on `color-mix()`, which Chrome
 *    resolves to `color(srgb …)` in getComputedStyle. html2canvas 1.4.1 only
 *    knows rgb/hsl/hex and *throws* on anything else — a single gradient stop
 *    aborted the entire export. `normalizeColors` rewrites every colour-bearing
 *    declaration in the clone to plain `rgba()`, resolving via a 1x1 canvas so
 *    oklch/lab/display-p3 come out right too.
 *
 * 2. Charts. Reading pixels back out of a live ECharts canvas does not reliably
 *    return what is on screen — a bar series that is plainly visible can come
 *    back missing from `getImageData`, `toDataURL` and the instance's own
 *    `getDataURL` alike, so html2canvas copying that bitmap yields a chart with
 *    axes and no data. Instead we re-render each chart's `getOption()` into a
 *    throwaway offscreen instance with animation disabled, which paints
 *    deterministically and at export resolution rather than being upscaled from
 *    the screen's devicePixelRatio, and swap that <img> into the clone.
 *
 * 3. Clipped text. Tailwind's `truncate` sets overflow:hidden with a tight
 *    line-height; html2canvas's baseline is a hair lower than the browser's, so
 *    descenders get sheared off. We relax overflow only on the labels that
 *    aren't actually overflowing, so genuinely-too-long text still ellipsises
 *    instead of spilling across the tile.
 *
 * 4. Interactive chrome. Search boxes, pagination, sort carets and hover
 *    toolbars are UI, not content, and shouldn't appear in a shared PNG.
 *    Anything marked `data-export-hide` is dropped.
 *
 * 5. Zoomed ancestors. The editor fits the board to the viewport with a CSS
 *    `transform: scale(…)`. html2canvas measures words from screen rectangles
 *    but positions them in unscaled CSS pixels, so under a zoom every word
 *    lands short of where it belongs and "Personal Care" prints as
 *    "PersonaCare". We drop those transforms on the live tree for the duration
 *    of the capture, so everything is measured and drawn at 1:1.
 */

// Elements stamped on the live DOM so `onclone` can find their counterparts in
// html2canvas's cloned document (it copies attributes). Removed afterwards.
const CHART_ATTR = 'data-cap-chart'
const FITS_ATTR = 'data-cap-fits'

/** Declarations that can carry a colour and that html2canvas parses. */
const COLOR_PROPS = [
  'color',
  'background-color',
  'background-image',
  'border-top-color',
  'border-right-color',
  'border-bottom-color',
  'border-left-color',
  'outline-color',
  'text-decoration-color',
  'column-rule-color',
  'caret-color',
  'box-shadow',
  'text-shadow',
  'fill',
  'stroke',
]

/** Colour functions html2canvas 1.4.1 cannot parse. */
const MODERN_COLOR_FN = /(^|[\s,(/])(color|color-mix|oklch|oklab|lab|lch|hwb)\(/i

/**
 * Chrome that is UI rather than content, and so never belongs in an export.
 * html2canvas drops ignored elements from the clone entirely, so an in-flow
 * entry here (the editor's drag strip) collapses its space rather than leaving
 * a gap — which is what we want, since the tile keeps its own outer size.
 */
const CHROME_SELECTOR = [
  '[data-export-hide]',
  '[data-widget-toolbar]',
  '.drag-handle',
  '.grid-resize-handle',
  '.grid-ghost',
].join(',')

export type CaptureOptions = {
  /** Output pixel density. 2 gives a crisp result on retina and in print. */
  scale?: number
  /**
   * Page colour behind the capture. Omit to inherit the app's current theme
   * surface, so a dark-theme board doesn't export as black-on-transparent.
   */
  background?: string
  /** Extra elements to leave out, on top of the standard chrome. */
  ignore?: (el: Element) => boolean
}

// ---------------------------------------------------------------------------
// colour resolution
// ---------------------------------------------------------------------------

/**
 * Build a resolver that rewrites any modern colour function in a CSS value to
 * `rgba()`. Resolution is done by painting the colour onto a 1x1 canvas and
 * reading the pixel back, which delegates to the browser's own colour parser —
 * so every syntax Chrome supports (and any it gains later) is handled, and
 * wide-gamut colours are gamut-mapped into sRGB, which is what a PNG wants.
 */
function makeColorResolver(): (value: string) => string {
  const el = document.createElement('canvas')
  el.width = el.height = 1
  const ctx = el.getContext('2d', { willReadFrequently: true })
  const cache = new Map<string, string | null>()

  function toRgba(value: string): string | null {
    const hit = cache.get(value)
    if (hit !== undefined) return hit
    let out: string | null = null
    try {
      if (ctx) {
        ctx.globalCompositeOperation = 'copy'
        ctx.fillStyle = '#000'
        ctx.fillStyle = value
        ctx.fillRect(0, 0, 1, 1)
        const d = ctx.getImageData(0, 0, 1, 1).data
        out = `rgba(${d[0]}, ${d[1]}, ${d[2]}, ${+(d[3] / 255).toFixed(4)})`
      }
    } catch {
      out = null
    }
    cache.set(value, out)
    return out
  }

  return function rewrite(value: string): string {
    if (!value || value.indexOf('(') < 0) return value
    let out = value
    // Colour functions nest inside gradients and shadows, so walk the string
    // replacing one balanced call at a time until none are left.
    for (let guard = 0; guard < 64; guard++) {
      const m = MODERN_COLOR_FN.exec(out)
      if (!m) break
      const start = m.index + m[1].length
      let depth = 0
      let end = -1
      for (let i = start; i < out.length; i++) {
        const ch = out[i]
        if (ch === '(') depth++
        else if (ch === ')') {
          depth--
          if (depth === 0) { end = i + 1; break }
        }
      }
      if (end < 0) break
      const rgba = toRgba(out.slice(start, end))
      if (!rgba) break
      out = out.slice(0, start) + rgba + out.slice(end)
    }
    return out
  }
}

/** Rewrite every unsupported colour in a cloned document to plain rgba(). */
function normalizeColors(doc: Document) {
  const rewrite = makeColorResolver()
  const win = doc.defaultView ?? window
  for (const el of Array.from(doc.querySelectorAll<HTMLElement>('*'))) {
    let cs: CSSStyleDeclaration
    try { cs = win.getComputedStyle(el) } catch { continue }
    for (const prop of COLOR_PROPS) {
      const value = cs.getPropertyValue(prop)
      if (!value || !MODERN_COLOR_FN.test(value)) continue
      const next = rewrite(value)
      if (next !== value) el.style.setProperty(prop, next, 'important')
    }
  }
}

// ---------------------------------------------------------------------------
// chart snapshots
// ---------------------------------------------------------------------------

type ChartShot = { url: string; width: number; height: number }

/**
 * Re-render one live chart into a throwaway offscreen instance and return its
 * PNG. Going through a fresh instance rather than the on-screen one is what
 * makes the result trustworthy — see the note at the top of this file — and
 * disabling animation means we never catch a series mid-transition.
 */
function renderChartOffscreen(
  echarts: any,
  live: any,
  width: number,
  height: number,
  scale: number,
): string | null {
  const holder = document.createElement('div')
  holder.setAttribute('aria-hidden', 'true')
  holder.style.cssText =
    `position:fixed;left:-100000px;top:0;pointer-events:none;width:${width}px;height:${height}px`
  document.body.appendChild(holder)
  let off: any = null
  try {
    off = echarts.init(holder, null, {
      renderer: 'canvas',
      useDirtyRect: false,
      devicePixelRatio: scale,
    })
    const option = live.getOption()
    option.animation = false
    off.setOption(option, { notMerge: true, lazyUpdate: false, silent: true })
    return off.getDataURL({
      type: 'png',
      pixelRatio: scale,
      backgroundColor: 'transparent',
      excludeComponents: ['toolbox', 'dataZoom'],
    })
  } catch {
    return null
  } finally {
    try { off?.dispose() } catch { /* already gone */ }
    holder.remove()
  }
}

/**
 * Snapshot every chart under `root` before the clone happens, so html2canvas
 * never has to read pixels off a live chart canvas.
 *
 * A canvas that isn't an ECharts instance (the WebGL/2D scatter fallback) is
 * copied straight off the live bitmap, which is the best we can do for it.
 */
async function snapshotCharts(root: HTMLElement, scale: number): Promise<Map<string, ChartShot>> {
  const shots = new Map<string, ChartShot>()
  let echarts: any = null
  try { echarts = await import('echarts') } catch { echarts = null }

  let seq = 0
  const stamp = (el: HTMLElement) => {
    const key = `c${seq++}`
    el.setAttribute(CHART_ATTR, key)
    return key
  }

  // ECharts containers first, and remember their canvases so the plain-canvas
  // sweep below doesn't snapshot them a second time.
  const claimed = new Set<HTMLCanvasElement>()
  if (echarts) {
    for (const host of Array.from(root.querySelectorAll<HTMLElement>('[_echarts_instance_]'))) {
      let live: any
      try { live = echarts.getInstanceByDom(host) } catch { live = null }
      if (!live || live.isDisposed?.()) continue
      const rect = host.getBoundingClientRect()
      if (rect.width < 1 || rect.height < 1) continue

      let url = renderChartOffscreen(echarts, live, rect.width, rect.height, scale)
      if (!url) {
        // Last resort: ask the on-screen instance directly.
        try {
          url = live.getDataURL({ type: 'png', pixelRatio: scale, backgroundColor: 'transparent' })
        } catch { url = null }
      }
      if (!url) continue

      shots.set(stamp(host), { url, width: rect.width, height: rect.height })
      for (const c of Array.from(host.querySelectorAll('canvas'))) claimed.add(c)
    }
  }

  for (const canvas of Array.from(root.querySelectorAll('canvas'))) {
    if (claimed.has(canvas)) continue
    const rect = canvas.getBoundingClientRect()
    if (rect.width < 1 || rect.height < 1) continue
    try {
      shots.set(stamp(canvas), { url: canvas.toDataURL('image/png'), width: rect.width, height: rect.height })
    } catch {
      // Tainted canvas (cross-origin texture) — nothing we can do, skip it.
    }
  }

  return shots
}

/** Swap each snapshotted chart in the clone for a static <img> of its PNG. */
function applyChartShots(doc: Document, shots: Map<string, ChartShot>) {
  for (const el of Array.from(doc.querySelectorAll<HTMLElement>(`[${CHART_ATTR}]`))) {
    const shot = shots.get(el.getAttribute(CHART_ATTR) ?? '')
    if (!shot) continue
    const img = doc.createElement('img')
    img.src = shot.url
    img.width = Math.round(shot.width)
    img.height = Math.round(shot.height)
    img.style.cssText = `display:block;width:${shot.width}px;height:${shot.height}px;object-fit:fill`
    el.replaceWith(img)
  }
}

// ---------------------------------------------------------------------------
// capture
// ---------------------------------------------------------------------------

/** Resolve the surface colour to sit behind the capture. */
function themeBackground(el: HTMLElement): string {
  const resolve = makeColorResolver()
  for (let node: HTMLElement | null = el; node; node = node.parentElement) {
    const bg = getComputedStyle(node).backgroundColor
    if (bg && bg !== 'transparent' && !/rgba\([^)]*,\s*0\)\s*$/.test(bg)) return resolve(bg)
  }
  const body = getComputedStyle(document.body).backgroundColor
  return body && body !== 'transparent' ? resolve(body) : '#ffffff'
}

/**
 * Stamp the `truncate` labels that aren't actually overflowing, so the clone
 * can let their descenders paint without letting long text escape its tile.
 */
function markNonOverflowingLabels(root: HTMLElement): HTMLElement[] {
  const marked: HTMLElement[] = []
  for (const el of Array.from(root.querySelectorAll<HTMLElement>('*'))) {
    const cs = getComputedStyle(el)
    if (cs.overflow !== 'hidden' || cs.whiteSpace !== 'nowrap' || cs.textOverflow !== 'ellipsis') continue
    if (el.scrollWidth <= el.clientWidth + 1) {
      el.setAttribute(FITS_ATTR, '1')
      marked.push(el)
    }
  }
  return marked
}

/**
 * Drop any `transform`/`zoom` on `el` or its ancestors for the duration of the
 * capture, returning a function that puts them back. Layout has to be at 1:1
 * before anything measures it — html2canvas and our own chart rects both read
 * live geometry.
 */
function withUnscaledLayout(el: HTMLElement): () => void {
  const undo: Array<() => void> = []
  for (let node: HTMLElement | null = el; node && node !== document.documentElement; node = node.parentElement) {
    const cs = getComputedStyle(node)
    const scaled = cs.transform !== 'none' || cs.scale !== 'none' || (cs.zoom !== '' && cs.zoom !== '1')
    if (!scaled) continue
    const target = node
    const transform = target.style.transform
    const scaleProp = target.style.scale
    const zoom = target.style.zoom
    target.style.setProperty('transform', 'none', 'important')
    target.style.setProperty('scale', 'none', 'important')
    target.style.setProperty('zoom', '1', 'important')
    undo.push(() => {
      target.style.transform = transform
      target.style.scale = scaleProp
      target.style.zoom = zoom
    })
  }
  // Force a reflow so the un-zoomed geometry is what gets measured.
  if (undo.length) void el.getBoundingClientRect()
  return () => { for (const fn of undo) fn() }
}

/**
 * Render `el` and everything inside it to a canvas at `scale` device pixels
 * per CSS pixel.
 */
export async function captureElement(
  el: HTMLElement,
  opts: CaptureOptions = {},
): Promise<HTMLCanvasElement> {
  if (!el) throw new Error('Nothing to capture.')
  const scale = opts.scale ?? 2
  const background = opts.background ?? themeBackground(el)

  const restoreLayout = withUnscaledLayout(el)
  const shots = await snapshotCharts(el, scale)
  const labels = markNonOverflowingLabels(el)

  try {
    const { default: html2canvas } = await import('html2canvas')
    return await html2canvas(el, {
      scale,
      backgroundColor: background,
      useCORS: true,
      logging: false,
      // Drop chrome before the clone so it never affects layout either.
      ignoreElements: (node) =>
        Boolean((node as Element).closest?.(CHROME_SELECTOR)) || Boolean(opts.ignore?.(node as Element)),
      onclone: (doc) => {
        normalizeColors(doc)
        applyChartShots(doc, shots)
        for (const node of Array.from(doc.querySelectorAll<HTMLElement>(`[${FITS_ATTR}]`))) {
          node.style.setProperty('overflow', 'visible', 'important')
        }
        // Animations mid-flight would otherwise be frozen at whatever frame the
        // clone caught.
        const reset = doc.createElement('style')
        reset.textContent = '*{animation:none!important;transition:none!important;caret-color:transparent!important}'
        doc.head?.appendChild(reset)
      },
    })
  } finally {
    for (const node of Array.from(el.querySelectorAll(`[${CHART_ATTR}]`))) node.removeAttribute(CHART_ATTR)
    for (const node of labels) node.removeAttribute(FITS_ATTR)
    restoreLayout()
  }
}

/** Capture `el` and hand back a PNG blob. */
export async function captureToPngBlob(el: HTMLElement, opts: CaptureOptions = {}): Promise<Blob> {
  const canvas = await captureElement(el, opts)
  return await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error('Could not encode the PNG.'))),
      'image/png',
    )
  })
}
