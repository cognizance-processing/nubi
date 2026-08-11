/**
 * useSurfaceState.js — dashboard surface-state hook.
 *
 * Holds the active surface (only 'dashboard' is supported) and exposes the
 * read API the grid canvas uses to interact with spec.surfaces.grid.
 *
 * CONTRACT:
 *
 *   const {
 *     activeSurface,          // 'dashboard'
 *     setActiveSurface,       // (surface: string) => void
 *
 *     // Grid (dashboard) surface — delegates to responsiveLayout helpers
 *     getGridLayout,          // () => { [widgetId]: {x,y,w,h} }
 *   } = useSurfaceState(spec)
 *
 * spec is the SAME spec the DashboardEditor already holds; the shell simply
 * threads it down. No new spec duplication.
 *
 * The report (paginated document) and presentation (slide deck) surfaces that
 * previously lived here have been removed — Nubi ships one authoring surface:
 * the dashboard grid. Scheduled email PDF delivery of a dashboard (report_send)
 * is unaffected — it renders the dashboard surface, not a separate document.
 */

import { useState, useCallback } from 'react'
import { getSurfaceLayout } from '../dashboards/responsiveLayout.js'

// Surface ids the shell supports.
export const SURFACE_IDS = /** @type {const} */ (['dashboard'])

/**
 * @param {object} spec  – Live dashboard spec (from editor history).
 * @returns {object}     – Surface state + API (see CONTRACT above).
 */
export function useSurfaceState(spec) {
  const [activeSurface, setActiveSurface] = useState('dashboard')

  // ── Grid (dashboard) surface ──────────────────────────────────────────────
  const getGridLayout = useCallback(
    () => getSurfaceLayout(spec, 'grid'),
    [spec],
  )

  return {
    activeSurface,
    setActiveSurface,

    // grid
    getGridLayout,
  }
}
