/**
 * ComparePage — Nubi against the field.
 *
 * Long-form content comes from Markdown in src/content/compare/ via
 * src/compare/registry.ts; the hand-curated tables live in
 * src/compare/competitors.ts.
 *
 * Swiss: one section recipe (2px rule, mono index, headline, hairline cells),
 * tables with a sticky first column and a scroll cue on small screens, the
 * Nubi row and column as the one accented line, ink and accent only.
 */

import { Fragment, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, ChevronDown } from 'lucide-react'
import MarketingStyles from '../components/marketing/MarketingStyles.jsx'
import {
  Reveal, Section, PageHero, Facts, Tag, Seg, TableScroll, cx,
} from '../components/marketing/kit.jsx'
import Figure from '../components/marketing/Figure.jsx'
import FairnessNote from '../components/marketing/FairnessNote.jsx'
import {
  INTRO, WHY_NUBI, MATRIX_META, COMPETITORS, ORCHESTRATORS,
  COMPARE_DIMENSIONS, MATRIX, MATRIX_COLUMNS,
} from '../compare/registry.js'
import {
  ALL_COMPETITORS, PRIMARY_ROWS, GENERAL_BI, EMBEDDED, DIMENSION_EDGE, DIMENSION_ORDER,
  ORCH_COLUMNS, ORCH_ROWS,
} from '../compare/competitors.js'

const CSS = `
.cp-hero-aside { align-self: center; }
.cp-edge { margin: 0; padding: 0; list-style: none; display: grid; gap: 10px; }
.cp-edge li { display: flex; gap: 10px; font-size: 15px; line-height: 1.5; color: var(--text-muted); }
.cp-edge li::before { content: ''; width: 6px; height: 6px; margin-top: 8px; background: var(--ink); flex: none; }
.cp-edge b { color: var(--text); font-weight: 600; }
.cp-prof { padding: 20px 20px 18px; display: flex; flex-direction: column; gap: 12px; }
.cp-prof h3 { margin: 0; font-size: 18px; font-weight: 600; letter-spacing: -.015em; line-height: 1.2; }
.cp-prof .cp-tl { margin: 4px 0 0; font-size: 13px; line-height: 1.45; color: var(--text-muted); }
.cp-prof dl { margin: 0; display: grid; gap: 10px; }
.cp-prof dt { font: 500 11px/1.4 var(--font-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--text-muted); }
.cp-prof dd { margin: 2px 0 0; font-size: 13.5px; line-height: 1.55; color: var(--text); }
.cp-prof dd.cp-lim { color: var(--text-muted); }
.cp-prof-f { margin-top: auto; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 14px; padding-top: 12px; border-top: 1px solid var(--border); }
.cp-prof-f a { font: 12px var(--font-mono); color: var(--text-muted); text-decoration: underline; text-underline-offset: 3px; }
.cp-prof-f a:hover { color: var(--accent); }
.cp-expand td { background: var(--surface-2) !important; padding: 18px 16px 22px; }
.cp-expand-g { display: grid; gap: 20px; }
@media (min-width: 800px) { .cp-expand-g { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 32px; } }
.cp-expand h4 { margin: 0 0 6px; font: 500 11px/1.4 var(--font-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--text-muted); }
.cp-expand p { margin: 0; font-size: 14px; line-height: 1.55; color: var(--text); }
.cp-tog { display: inline-flex; align-items: center; gap: 4px; background: none; border: 0; padding: 4px 0; font: 600 13px var(--font-ui); color: var(--text); cursor: pointer; text-decoration: underline; text-decoration-color: var(--text-muted); text-underline-offset: 3px; }
.cp-tog:hover { color: var(--accent); text-decoration-color: var(--accent); }
.cp-tog svg { transition: transform 120ms; }
.cp-tog[aria-expanded="true"] svg { transform: rotate(180deg); }
.cp-flows { display: grid; gap: 1px; background: var(--border); border: 1px solid var(--border); box-shadow: inset 0 3px 0 var(--accent); }
@media (min-width: 720px) { .cp-flows { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (min-width: 1100px) { .cp-flows { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
.cp-flows > div { background: var(--surface); padding: 20px; }
.cp-flows p { margin: 6px 0 0; font-size: 14px; line-height: 1.55; }
`

/* ── helpers ──────────────────────────────────────────────────────────────── */

const B = ({ children }: { children: ReactNode }) => <b>{children}</b>

function isEstimate(val: unknown) {
  return typeof val === 'string' && (val.toLowerCase().includes('unverified') || val.toLowerCase().includes('(est.)'))
}

/** Short cells read plainly; long ones clamp to two lines with a "more" toggle. */
function CellText({ text }: { text?: string }) {
  const [open, setOpen] = useState(false)
  if (!text || text === '—') return <span aria-label="No data" style={{ color: 'var(--text-muted)' }}>—</span>
  if (text.length <= 68) return <span>{text}</span>
  return (
    <div>
      <p style={{ margin: 0 }} className={open ? undefined : 'mk-clamp'}>{text}</p>
      <button type="button" className="mk-more" aria-expanded={open} onClick={() => setOpen(o => !o)}>{open ? 'Less' : 'More'}</button>
    </div>
  )
}

/** "Yes — detail" / "No — detail" → a tag; anything else is a partial. */
function SelfHost({ text }: { text: string }) {
  const t = text || ''
  const [tone, label] = /^Yes\b/i.test(t) ? (['success', 'Yes'] as const) : /^No\b/i.test(t) ? (['neutral', 'No'] as const) : (['warning', 'Partial'] as const)
  return <span title={text}><Tag tone={tone} dot={tone !== 'neutral'}>{label}</Tag></span>
}

function parseSections(content: string) {
  const out = { strength: '', limitation: '', notes: '' }
  for (const part of content.split(/^##\s+/m)) {
    const lines = part.split('\n')
    const h = lines[0]?.trim().toLowerCase()
    const body = lines.slice(1).join('\n').trim()
    if (h?.includes('strength')) out.strength = body
    else if (h?.includes('limitation')) out.limitation = body
    else if (h?.includes('note')) out.notes = body
  }
  return out
}

/** Up to two source links, one per host. */
function sourceLinks(urls: unknown): { url: string; label: string }[] {
  const seen = new Set<string>()
  const out: { url: string; label: string }[] = []
  for (const u of Array.isArray(urls) ? urls : []) {
    if (typeof u !== 'string') continue
    let label = u
    try { label = new URL(u).hostname.replace('www.', '') } catch { /* keep the raw string */ }
    if (seen.has(label)) continue
    seen.add(label)
    out.push({ url: u, label })
    if (out.length === 2) break
  }
  return out
}

function pricingTags(text: string) {
  const t: { label: string; good?: boolean }[] = []
  if (/free tier|free forever|community free|open source|OSS|free \(|Starter free/i.test(text)) t.push({ label: 'Free tier', good: true })
  if (/per.seat|per-seat|\$.*\/user|\$.*\/editor/i.test(text)) t.push({ label: 'Per seat' })
  if (/usage.based|per.view|per.token|per.second|connector bytes/i.test(text)) t.push({ label: 'Usage-based', good: true })
  if (/contact sales|no public|negotiated|custom pricing/i.test(text)) t.push({ label: 'Contact sales' })
  if (/capacity|F-SKU|SKU/i.test(text)) t.push({ label: 'Capacity' })
  return t.slice(0, 3)
}

/* ── Tables ───────────────────────────────────────────────────────────────── */

function AllCompetitorsTable() {
  const [open, setOpen] = useState<Set<string>>(() => new Set())
  const toggle = (n: string) => setOpen(prev => { const x = new Set(prev); if (x.has(n)) x.delete(n); else x.add(n); return x })
  const nubi = ALL_COMPETITORS.filter(c => c.isNubi)
  const general = ALL_COMPETITORS.filter(c => GENERAL_BI.includes(c.category))
  const embedded = ALL_COMPETITORS.filter(c => EMBEDDED.includes(c.category) && !c.isNubi)

  const row = (c: (typeof ALL_COMPETITORS)[number]) => {
    const isOpen = open.has(c.name)
    return (
      <Fragment key={c.name}>
        <tr className={c.isNubi ? 'mk-row-nubi' : undefined}>
          <td>
            {c.name}
            <small style={{ display: 'block', font: '400 12px/1.4 var(--font-ui)', color: 'var(--text-muted)', marginTop: 2 }}>{c.category}</small>
          </td>
          <td>{c.pricingModel}</td>
          <td>{c.entryPrice}{c.entryEst && <> <Tag>est.</Tag></>}</td>
          <td><SelfHost text={c.selfHost} /></td>
          <td>
            <button type="button" className="cp-tog" aria-expanded={isOpen} aria-controls={`cp-x-${c.name.replace(/\W+/g, '-')}`} onClick={() => toggle(c.name)}>
              Details<ChevronDown size={13} strokeWidth={1.75} aria-hidden="true" />
            </button>
          </td>
        </tr>
        {isOpen && (
          <tr className="cp-expand" id={`cp-x-${c.name.replace(/\W+/g, '-')}`}>
            <td colSpan={5}>
              <div className="cp-expand-g">
                <div><h4>Seat and viewer model</h4><p>{c.seatModel}</p></div>
                <div><h4>Embedding</h4><p>{c.embedding}</p></div>
                <div><h4>{c.isNubi ? 'Honest limitations' : 'Where this tool is stronger'}</h4><p>{c.isNubi ? c.weaknesses : c.strengths}</p></div>
              </div>
            </td>
          </tr>
        )}
      </Fragment>
    )
  }

  return (
    <TableScroll min={860} label="All platforms" caption={`${ALL_COMPETITORS.length - 1} competitors and Nubi`}>
      <table className="mk-table">
        <caption className="mk-sr">Pricing model, entry price and self-hosting for {ALL_COMPETITORS.length} platforms</caption>
        <thead>
          <tr>
            <th scope="col">Platform</th>
            <th scope="col">Pricing model</th>
            <th scope="col">Entry price</th>
            <th scope="col">Self-host</th>
            <th scope="col"><span className="mk-sr">Details</span></th>
          </tr>
        </thead>
        <tbody>
          {nubi.map(row)}
          <tr className="mk-row-cat"><td colSpan={5}>General BI tools</td></tr>
          {general.map(row)}
          <tr className="mk-row-cat"><td colSpan={5}>Embedded analytics specialists</td></tr>
          {embedded.map(row)}
        </tbody>
      </table>
    </TableScroll>
  )
}

function PrimaryTable() {
  return (
    <TableScroll min={760} label="Nubi against Hex and Cube">
      <table className="mk-table">
        <caption className="mk-sr">Nubi compared with Hex and Cube across eight dimensions</caption>
        <thead>
          <tr>
            <th scope="col">Dimension</th>
            <th scope="col">Hex<small>Notebook and apps</small></th>
            <th scope="col">Cube<small>Headless semantic layer</small></th>
            <th scope="col" className="mk-col-nubi">Nubi<small>Batteries-included BI and embed</small></th>
          </tr>
        </thead>
        <tbody>
          {PRIMARY_ROWS.map(r => (
            <tr key={r.label}>
              <td>{r.label}</td>
              <td><CellText text={r.hex} /></td>
              <td><CellText text={r.cube} /></td>
              <td className="mk-col-nubi"><CellText text={r.nubi} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  )
}

function FullMatrix() {
  const dims = COMPARE_DIMENSIONS
  // Nubi first, so the comparison reads left to right from our column.
  const cols = [...MATRIX_COLUMNS].sort((a: any, b: any) => Number(!!b.isNubi) - Number(!!a.isNubi))
  return (
    <TableScroll min={Math.max(900, 160 + cols.length * 190)} label="Feature matrix" caption={`${cols.length} tools`} cue="Scroll for all tools">
      <table className="mk-table" style={{ ['--mk-s2' as any]: '160px' }}>
        <caption className="mk-sr">Feature matrix: {dims.length} dimensions across {cols.length} tools</caption>
        <thead>
          <tr>
            <th scope="col" style={{ width: 160, minWidth: 160 }}>Dimension</th>
            {cols.map((col: any, i: number) => (
              <th key={`${col.key}-${i}`} scope="col" className={cx(col.isNubi && 'mk-col-nubi mk-s2')} style={{ minWidth: col.isNubi ? 240 : 180 }}>
                {col.label}{col.subtitle && <small>{col.subtitle}</small>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {dims.map((dim: any) => (
            <tr key={dim.key}>
              <th scope="row" className="mk-rowh" title={dim.description}>
                {dim.label}
              </th>
              {cols.map((col: any, i: number) => {
                const value = (MATRIX as any)[dim.key]?.[col.key] ?? '—'
                return (
                  <td key={`${dim.key}-${col.key}-${i}`} className={cx(col.isNubi && 'mk-col-nubi mk-s2')}>
                    <CellText text={value} />{isEstimate(value) && <> <Tag>est.</Tag></>}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  )
}

/* ── Profiles ─────────────────────────────────────────────────────────────── */

function Profile({ c }: { c: any }) {
  const [open, setOpen] = useState(false)
  const sec = parseSections(c.content ?? '')
  const tags = pricingTags(c.pricing ?? '')
  const id = `prof-${String(c.name).replace(/\W+/g, '-')}`
  return (
    <article className="cp-prof">
      <div>
        <h3>{c.name}</h3>
        <p className="cp-tl">{c.tagline}</p>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        <SelfHost text={c.selfHost} />
        {tags.map(t => <Tag key={t.label}>{t.label}</Tag>)}
      </div>
      <dl>
        {sec.strength && <div><dt>Strength</dt><dd>{sec.strength}</dd></div>}
        {sec.limitation && <div><dt>Limitation</dt><dd className="cp-lim">{sec.limitation}</dd></div>}
        {open && (
          <div id={id}>
            <dt>Pricing detail</dt>
            <dd className="cp-lim">{c.pricing}{c.pricingUnverified && <> <Tag>est.</Tag></>}</dd>
          </div>
        )}
      </dl>
      <div className="cp-prof-f">
        <button type="button" className="cp-tog" aria-expanded={open} aria-controls={id} onClick={() => setOpen(o => !o)}>
          {open ? 'Less' : 'Pricing detail'}<ChevronDown size={13} strokeWidth={1.75} aria-hidden="true" />
        </button>
        {sourceLinks(c.sourceUrls).map(l => (
          <a key={l.url} href={l.url} target="_blank" rel="noopener noreferrer">{l.label}</a>
        ))}
      </div>
    </article>
  )
}

/* ── Why Nubi ─────────────────────────────────────────────────────────────── */

const WHY_CARDS = [
  {
    index: '01', tag: 'COST ARCHITECTURE', title: 'Near-zero marginal cost',
    body: (<>Compute runs in the viewer&rsquo;s browser (<B>DuckDB-WASM, SQL</B>). 500 concurrent embedded viewers of the same dashboard <B>collapse to one warehouse query</B>. The advantage is real at high cache-hit rates and reaches diverse workloads through automatic pre-aggregations.</>),
    chip: '500 viewers, 1 warehouse query',
  },
  {
    index: '02', tag: 'RENDERING', title: 'Arrow IPC end to end',
    body: (<>Results move as <B>columnar Arrow buffers over WebSocket</B> and the viz layer reads them directly, with no JSON round trip. <B>&lt;nubi-chart&gt; renders on canvas through Apache ECharts</B>, so charts stay fast on large result sets.</>),
    chip: 'arrow ipc → echarts canvas',
  },
  {
    index: '03', tag: 'SECURITY', title: 'Auth as code',
    body: (<>Publish your JWKS, implement getToken(), mount &lt;nubi-dashboard&gt;. <B>JWT claims drive row-level security</B>, enforced server-side in the connector before any buffer reaches the browser. <B>Policies are TypeScript and SQL in your repo, diffable in pull requests.</B></>),
    chip: 'policies reviewed in pull requests',
  },
]

/* ── Page ─────────────────────────────────────────────────────────────────── */

export default function ComparePage() {
  const { data: introData } = INTRO
  const [tab, setTab] = useState<'bi' | 'orch'>('bi')
  const [showAll, setShowAll] = useState(false)
  const PREVIEW = 8
  const profiles = showAll ? COMPETITORS : COMPETITORS.slice(0, PREVIEW)

  function jumpToMatrix() {
    setTab('bi')
    requestAnimationFrame(() => document.getElementById('matrix')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  }

  const total = COMPETITORS.length

  return (
    <div className="mk">
      <MarketingStyles />
      <style href="nubi-compare-page" precedence="default">{CSS}</style>

      <PageHero
        eyebrow={String((introData?.eyebrow as string) ?? 'COMPETITIVE OVERVIEW · 2026').toUpperCase()}
        meta="UPDATED JUNE 2026"
        title={<>How Nubi <em>compares.</em></>}
        lead={`An honest comparison against ${total} platforms, from Hex and Cube to Looker, Tableau and Power BI. Where a tool is stronger than Nubi, we say so.`}
        aside={
          <div className="cp-hero-aside">
            <Figure name="CostVsViewers" title="Annual cost against number of viewers: a flat Nubi line beside per-seat lines that climb" className="w-full h-auto" />
          </div>
        }
        actions={
          <>
            <Link to="/register" className="nubi-btn nubi-btn-primary nubi-btn-xl">Start free</Link>
            <button type="button" onClick={jumpToMatrix} className="nubi-btn nubi-btn-ghost nubi-btn-xl">Jump to the matrix</button>
          </>
        }
      >
        <div style={{ marginTop: 48, display: 'grid', gap: 40 }}>
          <ul className="cp-edge" style={{ maxWidth: 760 }}>
            <li><span><B>Analytics compute runs in the viewer&rsquo;s browser</B> by default, so the marginal cost per embed view is about $0 at high cache-hit rates.</span></li>
            <li><span><B>Unlimited users and viewers on every plan.</B> There is no per-seat penalty.</span></li>
            <li><span><B>Arrow IPC and ECharts canvas rendering</B> keep large result sets fast in the browser.</span></li>
          </ul>
          <Facts items={[
            { v: '≈ $0', l: 'marginal cost per dashboard view' },
            { v: '0 s', l: 'cold start, the kernel runs in the tab' },
            { v: '∞', l: 'users and viewers, no per-seat pricing' },
            { v: String(COMPETITORS.length), l: 'platforms compared' },
          ]} />
        </div>
      </PageHero>

      {/* 01 · Method */}
      <Section
        index="01" label="METHOD"
        title={<>How we compared, <em>and where we are weaker.</em></>}
      >
        <Reveal>
          <div style={{ display: 'grid', gap: 32, maxWidth: 980 }}>
            <FairnessNote asOf="June 2026" />
            <div className="mk-cells mk-cells-2">
              <div className="mk-cell">
                <p className="mk-label">Data transparency</p>
                <p>Competitor data was researched in June 2026 from <b>public pricing pages and independent analysts</b>. Fields marked <Tag>est.</Tag> hold estimates. Sources are linked on each profile.</p>
              </div>
              <div className="mk-cell">
                <p className="mk-label">Scope of the cost claim</p>
                <p>The 10–50× advantage holds at <b>high cache-hit rates</b>: for example, 500 viewers of the same dashboard. For 500 analysts each slicing differently, the hit rate drops. Automatic pre-aggregations extend the advantage to varied workloads.</p>
              </div>
            </div>
          </div>
        </Reveal>
      </Section>

      {/* 02 · Why Nubi */}
      <Section
        index="02" label="WHY NUBI" sunk
        title={<>The structural <em>difference.</em></>}
        lead={(WHY_NUBI.data?.tagline as string) ?? 'Three architectural bets that change what is possible, and what it costs.'}
      >
        <Reveal>
          <div className="mk-cells mk-cells-3">
            {WHY_CARDS.map(c => (
              <article key={c.index} className="mk-cell" style={{ padding: '22px 22px 26px', gap: 12 }}>
                <p className="mk-idx" style={{ border: 0, padding: 0, justifyContent: 'space-between' }}><b>{c.index}</b><span>{c.tag}</span></p>
                <h3 className="mk-h3">{c.title}</h3>
                <p style={{ fontSize: 15 }}>{c.body}</p>
                <p className="mk-mono mk-small" style={{ marginTop: 'auto', paddingTop: 12, borderTop: '1px solid var(--border)', color: 'var(--accent)' }}>{c.chip}</p>
              </article>
            ))}
          </div>
          <div className="mk-note mk-ink" style={{ marginTop: 28, maxWidth: '84ch' }}>
            <p className="mk-label">Honest limitations</p>
            <p>
              The cost advantage is real <b>only at high cache-hit or pre-aggregation rates</b>: 500 analysts each slicing differently revert to warehouse scans.
              The browser memory cap (about 4 GB) calls for aggressive pushdown. The browser only runs SQL (DuckDB-WASM), so Python and native-wheel workloads
              route to the on-demand server kernel. NoSQL is out of scope. The M10 self-host stack is not shipped yet.
            </p>
          </div>
        </Reveal>
      </Section>

      {/* Category switch */}
      <section style={{ paddingBlock: '28px 28px', borderTop: '2px solid var(--ink)' }}>
        <div className="mk-wrap">
          <Seg<'bi' | 'orch'>
            tabs label="Comparison category" value={tab} onChange={setTab}
            options={[
              { value: 'bi', label: <>BI and embedded analytics <span className="mk-mono" style={{ opacity: .7 }}>{COMPETITORS.length}</span></> },
              { value: 'orch', label: <>Workflow orchestration <span className="mk-mono" style={{ opacity: .7 }}>{ORCHESTRATORS.length}</span></> },
            ]}
          />
          {tab === 'bi' && (
            <nav aria-label="On this tab" style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 20px', marginTop: 16 }}>
              {[['#glance', 'At a glance'], ['#primary', 'Hex and Cube'], ['#pricing-table', 'Pricing and platforms'], ['#matrix', 'Feature matrix'], ['#profiles', 'Profiles']].map(([h, l]) => (
                <a key={h} href={h} className="mk-link" style={{ fontSize: 13 }}>{l}</a>
              ))}
            </nav>
          )}
        </div>
      </section>

      {tab === 'bi' ? (
        <>
          <Section id="glance" index="03" label="AT A GLANCE" title={<>Nubi&rsquo;s edge, <em>dimension by dimension.</em></>}
            lead={<>Nine dimensions the tables below unpack in full. Each maps to a row of the <b>feature matrix</b>.</>}>
            <Reveal>
              <div className="mk-cells mk-cells-3">
                {DIMENSION_ORDER.map((key, i) => {
                  const d = DIMENSION_EDGE[key]
                  return (
                    <div key={key} className="mk-cell">
                      <p className="mk-label">{String(i + 1).padStart(2, '0')} · {d.label}</p>
                      <p style={{ color: 'var(--text)', fontSize: 15, fontWeight: 500 }}>{d.edge}</p>
                    </div>
                  )
                })}
              </div>
            </Reveal>
          </Section>

          <Section id="primary" index="04" label="PRIMARY POSITIONING" sunk title={<>Nubi vs Hex vs <em>Cube.</em></>}
            lead={<>Hex and Cube bracket the space. <b>Hex is the best collaborative notebook.</b> <b>Cube is the gold-standard headless semantic layer.</b> Nubi is batteries-included BI built for embedding.</>}>
            <Reveal><PrimaryTable /></Reveal>
          </Section>

          <Section id="pricing-table" index="05" label="ALL PLATFORMS" title={<>The full picture. <em>No cherry-picking.</em></>}
            lead={<>Every platform in the June 2026 research. Pricing comes from <b>public pages or third-party analysts</b>; estimates are marked. Open a row for the seat model, embedding and <b>where that tool is stronger than Nubi</b>.</>}>
            <Reveal>
              <div className="mk-note" style={{ marginBottom: 28, maxWidth: '84ch' }}>
                <p><b>Nubi&rsquo;s headline difference:</b> unlimited users and viewers on every plan, with no per-seat charge. You pay for compute, storage, AI calls and embed sessions. Never for headcount.</p>
              </div>
              <AllCompetitorsTable />
              <p className="mk-small mk-mono" style={{ marginTop: 12 }}>Data as of June 2026. Prices change often: check each platform&rsquo;s pricing page before you decide.</p>
            </Reveal>
          </Section>

          <Section id="matrix" index="06" label="FEATURE MATRIX" sunk
            title={<>All tools, <em>side by side.</em></>}
            lead={MATRIX_META?.data?.subtitle ? String(MATRIX_META.data.subtitle) : 'Scroll sideways to compare every tool across every dimension. The Nubi column is highlighted.'}>
            <Reveal>
              <FullMatrix />
              <p className="mk-small mk-mono" style={{ marginTop: 12 }}>Researched June 2026. Features and pricing change often: verify before you publish.</p>
            </Reveal>
          </Section>

          <Section id="profiles" index="07" label="PROFILES" title={<>Know <em>the field.</em></>}
            lead={<><b>Strengths, limitations, pricing model and sources</b> for each tool. Open a card for the full pricing detail.</>}>
            <div className="mk-cells mk-cells-4">
              {profiles.map((c: any, i: number) => <Profile key={`${c.name}-${i}`} c={c} />)}
            </div>
            {COMPETITORS.length > PREVIEW && (
              <div style={{ marginTop: 24 }}>
                <button type="button" className="nubi-btn nubi-btn-secondary nubi-btn-lg" onClick={() => setShowAll(v => !v)}>
                  {showAll ? 'Show fewer' : `Show all ${COMPETITORS.length} platforms`}
                </button>
              </div>
            )}
          </Section>
        </>
      ) : (
        <>
          <Section id="flows" index="03" label="NUBI FLOWS"
            title={<>Workflow <em>orchestration.</em></>}
            lead={<>Nubi Flows is a <b>lightweight, LLM-native orchestrator built into the Nubi stack</b>: a Prefect alternative for analytics workflows that need per-user RLS, agent steps and no extra infrastructure. It is a <b>separate category</b> from the BI tools. The tools below are orchestrators, not dashboards.</>}>
            <Reveal>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 20 }}>
                <Tag tone="accent" dot>Included in Nubi</Tag>
                {['Postgres-backed (SKIP LOCKED)', 'RLS-aware', 'Agent task kind', 'React Flow DAG builder'].map(t => <Tag key={t}>{t}</Tag>)}
              </div>
              <div className="cp-flows">
                {ORCH_ROWS.slice(0, 4).map(r => (
                  <div key={r.label}>
                    <p className="mk-label">{r.label}</p>
                    <p>{r.nubi}</p>
                  </div>
                ))}
              </div>
            </Reveal>
          </Section>

          <Section id="orch-table" index="04" label="SIDE BY SIDE" sunk title={<>Flows against <em>four orchestrators.</em></>}>
            <Reveal>
              <TableScroll min={900} label="Orchestrator comparison">
                <table className="mk-table">
                  <caption className="mk-sr">Nubi Flows compared with Prefect, Apache Airflow, Dagster and n8n</caption>
                  <thead>
                    <tr>
                      <th scope="col">Dimension</th>
                      <th scope="col" className="mk-col-nubi">Nubi Flows<small>Embedded in Nubi</small></th>
                      {ORCH_COLUMNS.map(c => <th key={c.key} scope="col">{c.key}<small>{c.subtitle}</small></th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {ORCH_ROWS.map(r => (
                      <tr key={r.label}>
                        <td>{r.label}</td>
                        <td className="mk-col-nubi">{r.nubi}</td>
                        {ORCH_COLUMNS.map(c => <td key={c.key}>{r[c.key]}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
            </Reveal>
          </Section>

          <Section id="orch-profiles" index="05" label="PROFILES" title={<>The <em>orchestrators.</em></>}>
            <div className="mk-cells mk-cells-4">
              {ORCHESTRATORS.map((o: any, i: number) => <Profile key={`${o.name}-${i}`} c={o} />)}
            </div>
            <p className="mk-small mk-mono" style={{ marginTop: 12 }}>Researched June 2026. Orchestration tooling changes quickly: verify pricing and features before you publish.</p>
          </Section>
        </>
      )}

      {/* CTA */}
      <section className="mk-sec mk-ink-band">
        <div className="mk-wrap">
          <div className="mk-idx" style={{ borderBottomColor: 'var(--ink-fg)', color: 'inherit' }}><b style={{ color: 'inherit' }}>TRY IT</b><span>NO CREDIT CARD</span></div>
          <div style={{ display: 'grid', gap: 28, marginTop: 40, maxWidth: 820 }}>
            <h2 className="mk-h2" style={{ fontSize: 'clamp(36px, 5vw, 72px)', lineHeight: 1 }}>Embed live dashboards <em>at near-zero cost.</em></h2>
            <p className="mk-lead">Connect your warehouse and embed a dashboard in minutes. The free plan has unlimited viewers.</p>
            <div className="mk-ctas">
              <Link to="/register" className="nubi-btn nubi-btn-primary nubi-btn-xl">Start free <ArrowRight size={16} strokeWidth={1.75} aria-hidden="true" /></Link>
              <Link to="/docs" className="nubi-btn nubi-btn-secondary nubi-btn-xl">Read the docs</Link>
              <Link to="/reporting" className="nubi-btn nubi-btn-secondary nubi-btn-xl">How reporting works</Link>
            </div>
            <p className="mk-small mk-mono" style={{ color: 'inherit', opacity: .7 }}>Check primary sources before you switch.</p>
          </div>
        </div>
      </section>
    </div>
  )
}
