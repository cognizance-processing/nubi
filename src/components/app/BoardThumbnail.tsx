/**
 * BoardThumbnail.jsx — a dashboard card's picture of the REAL board.
 *
 * Two layers, deliberately:
 *
 *   <BoardMiniature>  — instant, free, derived from the spec's widget positions.
 *                       Drawn immediately so a card is never empty.
 *   the SSR render    — the actual board (real charts, real numbers), fetched
 *                       lazily and cross-faded in when it lands.
 *
 * That ordering is the whole design. The real render costs a board-wide query
 * run plus a Node/ECharts subprocess (~7-10s cold, ~25ms cached), so making a
 * card WAIT for it would give a gallery of grey boxes. Other BI tools'
 * cards simply go blank when they have no snapshot; showing the true layout
 * immediately and upgrading to the true picture is strictly better.
 *
 * Fetching is gated three ways: only when the card is actually near the
 * viewport (IntersectionObserver), only two at a time (see lib/boardThumbnail),
 * and aborted if the card unmounts mid-flight.
 */

import { useEffect, useRef, useState } from 'react'
import BoardMiniature from './BoardMiniature.jsx'
import { fetchBoardThumbnail } from '../../lib/boardThumbnail.js'
import { useTheme } from '../../contexts/ThemeContext.jsx'

/**
 * @param {{
 *   boardId: string,
 *   spec?: object|null,
 *   enabled?: boolean,
 *   className?: string,
 * }} props
 *   enabled: set false to stay on the miniature (e.g. legacy boards).
 */
export default function BoardThumbnail({ boardId, spec = undefined, enabled = true, className = '' }) {
  // The render is theme-specific (the board is styled in theme tokens the
  // server must resolve), so a theme flip needs a NEW picture — hence theme
  // is a dependency of the fetch, not a class on the img.
  const { theme } = useTheme()
  const isDark = theme === 'dark'
  const hostRef = useRef(null)
  const [url, setUrl] = useState(null)
  // Only start fetching once the card is near the viewport — a long gallery
  // shouldn't render boards the user never scrolls to.
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    if (!enabled) return
    const el = hostRef.current
    if (!el) return
    // No IntersectionObserver (jsdom/old browsers) → just load; correctness of
    // the picture matters more than the optimisation.
    if (typeof IntersectionObserver === 'undefined') { setVisible(true); return }

    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some(e => e.isIntersecting)) {
          setVisible(true)
          io.disconnect()   // one-shot: once seen, always load
        }
      },
      // Start a little before the card scrolls in, so the swap feels instant.
      { rootMargin: '300px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [enabled])

  useEffect(() => {
    if (!enabled || !visible || !boardId) return
    setUrl(null)   // drop the old theme's picture while the new one loads
    const ctrl = new AbortController()
    let objectUrl = null

    fetchBoardThumbnail(boardId, { signal: ctrl.signal, theme: isDark ? 'dark' : 'light' })
      .then(u => {
        if (ctrl.signal.aborted) {
          // Landed after unmount — free it rather than leak the blob.
          if (u) URL.revokeObjectURL(u)
          return
        }
        objectUrl = u
        setUrl(u)
      })
      .catch(() => { /* keep the miniature; a card is not worth an error state */ })

    return () => {
      ctrl.abort()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [enabled, visible, boardId, isDark])

  return (
    <div
      ref={hostRef}
      className={`relative w-full h-full transition-colors duration-500 ${className}`}
      // Match the backdrop to whatever is on show, so the letterbox left by
      // `object-contain` is invisible rather than a mystery band. When the real
      // render is up this mirrors its page colour for the same theme
      // (svg_render._PAGE_BG); the wireframe uses theme tokens directly.
      style={{ background: url ? (isDark ? '#111a2e' : '#ffffff') : 'var(--surface)' }}
    >
      {/* The miniature stays mounted underneath: it is the fallback for boards
          the renderer can't do (no Node, legacy HTML, unparseable spec) and it
          prevents a flash of empty card before the render arrives. */}
      <div
        className="absolute inset-0 transition-opacity duration-500"
        style={{ opacity: url ? 0 : 1 }}
      >
        <BoardMiniature spec={spec} className="w-full h-full" />
      </div>

      {url && (
        <img
          src={url}
          alt=""
          aria-hidden="true"
          // `contain`, NOT `cover`: the render must be shown whole. Cover would
          // scale a wide board until it filled the frame and crop its left/right
          // edges off — silently deleting real widgets from the picture. The
          // server caps the render's aspect so the letterbox stays small, and
          // top-anchored so a tall board shows its head.
          className="absolute inset-0 w-full h-full object-contain object-top transition-opacity duration-500"
          style={{ opacity: 1 }}
        />
      )}
    </div>
  )
}

export { BoardThumbnail }
