/**
 * useSurfaceState.js — T8 surface-state hook.
 *
 * Holds the active surface ('dashboard' | 'report' | 'presentation') and
 * exposes the read/write API that the per-surface canvases (T9/T10/T11) use to
 * interact with spec.surfaces.{grid,report,slides}.
 *
 * CONTRACT (canvases depend on this):
 *
 *   const {
 *     activeSurface,          // 'dashboard' | 'report' | 'presentation'
 *     setActiveSurface,       // (surface: string) => void
 *
 *     // Grid (dashboard) surface — delegates to responsiveLayout helpers
 *     getGridLayout,          // () => { [widgetId]: {x,y,w,h} }
 *
 *     // Report surface
 *     reportSurface,          // { pages: [...] }
 *     addReportPage,          // (page?) => void
 *     removeReportPage,       // (pageId) => void
 *     reorderReportPages,     // (orderedIds) => void
 *     updateReportPage,       // (pageId, patch) => void
 *
 *     // Slides surface
 *     slidesSurface,          // { aspect, slides: [...] }
 *     addSlide,               // (slide?) => void
 *     removeSlide,            // (slideId) => void
 *     reorderSlides,          // (orderedIds) => void
 *     updateSlide,            // (slideId, patch) => void
 *     updateSlideWidgetPos,   // (slideId, widgetId, pos) => void
 *   } = useSurfaceState(spec, setSpec)
 *
 * spec / setSpec are the SAME pair the DashboardEditor already holds; the shell
 * simply threads them down. No new spec duplication.
 *
 * Surface labels used by the shell switch:
 *   'dashboard'    → grid editor (current DashboardEditor canvas)
 *   'report'       → paginated document (T9)
 *   'presentation' → slide deck (T10)
 */

import { useState, useCallback } from 'react'
import {
  getSurfaceLayout,
  getReportSurface,
  addReportPage   as _addReportPage,
  removeReportPage as _removeReportPage,
  reorderReportPages as _reorderReportPages,
  updateReportPage as _updateReportPage,
  getSlidesSurface,
  addSlide        as _addSlide,
  removeSlide     as _removeSlide,
  reorderSlides   as _reorderSlides,
  updateSlide     as _updateSlide,
  updateSlideWidgetPos as _updateSlideWidgetPos,
} from '../dashboards/responsiveLayout.js'

// Surface ids the shell switch cycles through.
export const SURFACE_IDS = /** @type {const} */ (['dashboard', 'report', 'presentation'])

/**
 * @param {object}   spec     – Live dashboard spec (from editor history).
 * @param {Function} setSpec  – Spec setter (triggers history push in the shell).
 * @returns {object}          – Surface state + API (see CONTRACT above).
 */
export function useSurfaceState(spec, setSpec) {
  const [activeSurface, setActiveSurface] = useState('dashboard')

  // ── Grid (dashboard) surface ──────────────────────────────────────────────
  const getGridLayout = useCallback(
    () => getSurfaceLayout(spec, 'grid'),
    [spec],
  )

  // ── Report surface ────────────────────────────────────────────────────────
  const reportSurface = getReportSurface(spec)

  const addReportPage = useCallback((page) => {
    setSpec(prev => _addReportPage(prev, page))
  }, [setSpec])

  const removeReportPage = useCallback((pageId) => {
    setSpec(prev => _removeReportPage(prev, pageId))
  }, [setSpec])

  const reorderReportPages = useCallback((orderedIds) => {
    setSpec(prev => _reorderReportPages(prev, orderedIds))
  }, [setSpec])

  const updateReportPage = useCallback((pageId, patch) => {
    setSpec(prev => _updateReportPage(prev, pageId, patch))
  }, [setSpec])

  // ── Slides surface ────────────────────────────────────────────────────────
  const slidesSurface = getSlidesSurface(spec)

  const addSlide = useCallback((slide) => {
    setSpec(prev => _addSlide(prev, slide))
  }, [setSpec])

  const removeSlide = useCallback((slideId) => {
    setSpec(prev => _removeSlide(prev, slideId))
  }, [setSpec])

  const reorderSlides = useCallback((orderedIds) => {
    setSpec(prev => _reorderSlides(prev, orderedIds))
  }, [setSpec])

  const updateSlide = useCallback((slideId, patch) => {
    setSpec(prev => _updateSlide(prev, slideId, patch))
  }, [setSpec])

  const updateSlideWidgetPos = useCallback((slideId, widgetId, pos) => {
    setSpec(prev => _updateSlideWidgetPos(prev, slideId, widgetId, pos))
  }, [setSpec])

  return {
    activeSurface,
    setActiveSurface,

    // grid
    getGridLayout,

    // report
    reportSurface,
    addReportPage,
    removeReportPage,
    reorderReportPages,
    updateReportPage,

    // slides
    slidesSurface,
    addSlide,
    removeSlide,
    reorderSlides,
    updateSlide,
    updateSlideWidgetPos,
  }
}
