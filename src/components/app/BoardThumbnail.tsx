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
 * viewport (IntersectionObserver), only two cold renders at a time (see
 * lib/boardThumbnail), and aborted if the card unmounts mid-flight. A picture
 * already in the client cache skips all of that and mounts in place.
 */

import { useEffect, useRef, useState } from 'react'
import BoardMiniature from './BoardMiniature.jsx'
import { fetchBoardThumbnail, peekBoardThumbnail } from '../../lib/boardThumbnail.js'
import { useTheme } from '../../contexts/ThemeContext.jsx'

/**
 * @param {{
 *   boardId: string,
 *   spec?: object|null,
 *   version?: string|null,
 *   enabled?: boolean,
 *   className?: string,
 * }} props
 *   version: the board's `updated_at` — keys the client cache, so a saved
 *            board is a new picture.
 *   enabled: set false to stay on the miniature (e.g. legacy boards).
 */
export default function BoardThumbnail({ boardId, spec = undefined, version = undefined, enabled = true, className = '' }) {
  // The render is theme-specific (the board is styled in theme tokens the
  // server must resolve), so a theme flip needs a NEW picture — hence theme
  // is a dependency of the fetch, not a class on the img.
  const { theme } = useTheme()
  const thumbTheme = theme === 'dark' ? 'dark' : 'light'
  const hostRef = useRef(null)
  // A picture already in memory (back from a board, say) mounts in place — no
  // miniature flash, no fade.
  const [url, setUrl] = useState(() => peekBoardThumbnail(boardId, { theme: thumbTheme, version }))
  // Only start fetching once the card is near the viewport — a long gallery
  // shouldn't render boards the user never scrolls to. A cached card is
  // "visible" from the start: it costs nothing to show.
  const [visible, setVisible] = useState(() => url !== null)

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
    // Show what's cached for this theme/version right away; otherwise drop
    // the old theme's picture while the new one loads.
    setUrl(peekBoardThumbnail(boardId, { theme: thumbTheme, version }))
    const ctrl = new AbortController()

    fetchBoardThumbnail(boardId, { signal: ctrl.signal, theme: thumbTheme, version })
      .then(u => {
        if (!ctrl.signal.aborted && u) setUrl(u)
      })
      .catch(() => { /* keep the miniature; a card is not worth an error state */ })

    // The object URL belongs to lib/boardThumbnail's cache — never revoke it here.
    return () => ctrl.abort()
  }, [enabled, visible, boardId, thumbTheme, version])

  return (
    <div
      ref={hostRef}
      className={`relative w-full h-full ${className}`}
      // Match the backdrop to whatever is on show, so the letterbox left by
      // `object-contain` is a quiet sheet colour rather than a mystery band.
      style={{ background: 'var(--surface)' }}
    >
      {/* The miniature stays mounted underneath: it is the fallback for boards
          the renderer can't do (no Node, legacy HTML, unparseable spec) and it
          prevents a flash of empty card before the render arrives. */}
      <div
        className="absolute inset-0 transition-opacity duration-300"
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
          className="absolute inset-0 w-full h-full object-contain object-top transition-opacity duration-300"
          style={{ opacity: 1 }}
        />
      )}
    </div>
  )
}

export { BoardThumbnail }
