/**
 * Landing chrome: everything that has to work before the three.js chunks arrive.
 * Menu drawer, in-page anchors, SPA link routing, scrollable code fades and the
 * ⌘K palette. The figure modules add their own palette actions through
 * root.__nl.addCommands(). mount(root, { navigate }) returns a cleanup.
 */

export function toast(root, msg, ms = 2200) {
  const t = root.querySelector('#toast')
  if (!t) return
  t.textContent = msg; t.hidden = false
  clearTimeout(toast.h)
  toast.h = setTimeout(() => { t.hidden = true }, ms)
}

export function mount(root, { navigate }) {
  const cleanups = []
  const on = (el, type, fn, opts) => { el.addEventListener(type, fn, opts); cleanups.push(() => el.removeEventListener(type, fn, opts)) }
  const $ = s => root.querySelector(s)
  const reduceMq = window.matchMedia('(prefers-reduced-motion: reduce)')
  const behavior = () => (reduceMq.matches ? 'auto' : 'smooth')

  /* ───── in-page anchors + SPA links ───── */
  // the story installs root.__nl.scrollTo (GSAP/Lenis aware); until then, native scrolling
  const jump = id => { if (root.__nl && root.__nl.scrollTo) return root.__nl.scrollTo(id); const el = document.getElementById(id); if (el) el.scrollIntoView({ behavior: behavior(), block: 'start' }) }
  on(root, 'click', e => {
    const a = e.target.closest && e.target.closest('a')
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || a.target || e.defaultPrevented) return
    const href = a.getAttribute('href') || ''
    if (href.startsWith('#') && href.length > 1) {
      const el = document.getElementById(href.slice(1))
      if (el) { e.preventDefault(); closeMenu(); jump(href.slice(1)) }
    } else if (href.startsWith('/') && !href.startsWith('//')) {
      e.preventDefault(); closeMenu(); navigate(href)
    }
  })

  /* ───── mobile menu ───── */
  const btn = $('#menu-btn'), drawer = $('#drawer')
  function openMenu() { if (!drawer) return; drawer.hidden = false; btn.setAttribute('aria-expanded', 'true'); btn.setAttribute('aria-label', 'Close menu'); root.classList.add('menu-open') }
  function closeMenu() { if (!drawer || drawer.hidden) return; drawer.hidden = true; btn.setAttribute('aria-expanded', 'false'); btn.setAttribute('aria-label', 'Open menu'); root.classList.remove('menu-open') }
  if (btn && drawer) {
    on(btn, 'click', () => (drawer.hidden ? openMenu() : closeMenu()))
    const wide = window.matchMedia('(min-width: 1024px)')
    on(wide, 'change', e => { if (e.matches) closeMenu() })
  }

  /* ───── scrollable code blocks: keyboard reachable, with an edge fade where more is hidden ───── */
  root.querySelectorAll('.scrollx').forEach(el => {
    const update = () => {
      const max = el.scrollWidth - el.clientWidth
      el.classList.toggle('fade-l', el.scrollLeft > 1)
      el.classList.toggle('fade-r', max > 1 && el.scrollLeft < max - 1)
      if (max > 1) { el.tabIndex = 0; el.setAttribute('role', 'region'); if (!el.hasAttribute('aria-label')) el.setAttribute('aria-label', 'Code, scrolls sideways') }
      else { el.removeAttribute('tabindex'); el.removeAttribute('role') }
    }
    on(el, 'scroll', update, { passive: true })
    const ro = new ResizeObserver(update); ro.observe(el); cleanups.push(() => ro.disconnect())
    const mo = new MutationObserver(update); mo.observe(el, { childList: true, subtree: true, characterData: true }); cleanups.push(() => mo.disconnect())
    update()
  })

  /* ───── ⌘K palette ───── */
  const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)
  const kmod = $('#kmod'), kmod2 = $('#kmod2')
  if (isMac) { if (kmod) kmod.textContent = '⌘K'; if (kmod2) kmod2.textContent = '⌘K to search' }
  const go = href => () => navigate(href)
  const section = id => () => jump(id)
  // Chapters 00–05 (the story) are added by the page through addCommands(); the outro sections are fixed here.
  const SECTIONS = [
    ['Run it in this tab', 'Section 06', section('live')], ['One tag in your product', 'Section 07', section('embed')],
    ['What you get', 'Section 08', section('specs')], ['Open core and self-host', 'Section 09', section('selfhost')], ['Pricing', 'Section 10', section('pricing')],
    ['Start: point it at a warehouse', 'Section 11', section('start')],
  ]
  const PAGES = [
    ['Open the docs', 'Page', go('/docs')], ['Compare with other BI tools', 'Page', go('/compare')], ['See pricing', 'Page', go('/pricing')],
    ['Sign in', 'Account', go('/login')], ['Start free', 'Account', go('/register')],
  ]
  const CHAPTERS = [], ACTIONS = []
  const CMDS = []
  const rebuild = () => { CMDS.length = 0; CMDS.push(...CHAPTERS, ...SECTIONS, ...ACTIONS, ...PAGES) }
  rebuild()
  // entries whose hint starts with "Chapter" go first (story order), everything else after the sections
  root.__nl = { jump, addCommands: list => { list.forEach(c => (/^Chapter/.test(c[1]) ? CHAPTERS : ACTIONS).push(c)); rebuild() } }

  const pal = $('#pal'), pq = $('#pal-q'), pl = $('#pal-list'), pn = $('#pal-n')
  let sel = 0, list = CMDS, lastFocus = null
  function renderPal() {
    const q = pq.value.trim().toLowerCase()
    list = CMDS.filter(c => !q || q.split(/\s+/).every(t => (c[0] + ' ' + c[1]).toLowerCase().includes(t)))
    sel = Math.min(sel, Math.max(0, list.length - 1))
    pl.innerHTML = list.length
      ? list.map((c, i) => `<li role="option" id="po${i}" aria-selected="${i === sel}" data-i="${i}"><span class="t">${c[0]}</span><span class="h">${c[1]}</span></li>`).join('')
      : '<li role="presentation" class="none">No matches. Try “cache”, “embed” or “pricing”.</li>'
    pq.setAttribute('aria-activedescendant', list.length ? 'po' + sel : '')
    if (pn) pn.textContent = list.length ? `${list.length} result${list.length === 1 ? '' : 's'}` : 'No results'
    const cur = pl.querySelector('[aria-selected="true"]'); if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: 'nearest' })
  }
  function openPal() { lastFocus = document.activeElement; pal.hidden = false; pq.value = ''; sel = 0; renderPal(); pq.focus() }
  function closePal() { pal.hidden = true; if (lastFocus && lastFocus.focus) lastFocus.focus() }
  function runSel(i) { const c = list[i]; if (!c) return; closePal(); c[2]() }
  const kbtn = $('#kbtn'); if (kbtn) on(kbtn, 'click', openPal)
  on(window, 'keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); if (pal.hidden) { closeMenu(); openPal() } else closePal(); return }
    if (e.key === 'Escape' && drawer && !drawer.hidden && pal.hidden) { closeMenu(); btn.focus(); return }
    if (pal.hidden) return
    if (e.key === 'Escape') { e.preventDefault(); closePal() }
    else if (e.key === 'ArrowDown') { e.preventDefault(); sel = (sel + 1) % Math.max(1, list.length); renderPal() }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = (sel - 1 + list.length) % Math.max(1, list.length); renderPal() }
    else if (e.key === 'Enter') { e.preventDefault(); runSel(sel) }
    else if (e.key === 'Tab') { e.preventDefault(); pq.focus() }
  })
  on(pq, 'input', () => { sel = 0; renderPal() })
  on(pl, 'click', e => { const li = e.target.closest('li[data-i]'); if (li) runSel(+li.dataset.i) })
  on(pal, 'click', e => { if (e.target === pal) closePal() })

  return () => { cleanups.forEach(f => { try { f() } catch { /* already gone */ } }); delete root.__nl }
}
