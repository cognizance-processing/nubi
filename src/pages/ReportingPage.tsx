/**
 * ReportingPage — /reporting. How one Nubi board becomes a live embed, a frozen
 * snapshot, a CDN bundle, a PDF and a scheduled delivery, at a flat cost per viewer.
 *
 * Swiss: the shared section recipe, hairline cells, snippets on the ink code
 * surface, ink and accent only. The "frozen, still alive" figure is a small
 * working model: pick a segment and the figures change.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import MarketingStyles from '../components/marketing/MarketingStyles.jsx'
import { Reveal, Section, PageHero, Facts, Tag, CodePanel, ArrowLink, Checks } from '../components/marketing/kit.jsx'
import Figure from '../components/marketing/Figure.jsx'

/* ── Data ─────────────────────────────────────────────────────────────────── */

// Competitor bars are log-scaled so the 100-viewer row already shows contrast;
// Nubi's bar is a fixed hairline because its cost does not move with viewers.
const VIEWER_TIERS = [
  { label: '100 viewers', viewers: 100, comp: '$90 / mo' },
  { label: '1k viewers', viewers: 1_000, comp: '$900 / mo' },
  { label: '10k viewers', viewers: 10_000, comp: '$9k / mo' },
  { label: '100k viewers', viewers: 100_000, comp: '$90k / mo' },
  { label: '1M viewers', viewers: 1_000_000, comp: '$900k / mo' },
]
const compPct = (v: number) => Math.round((Math.log10(v) / Math.log10(1_000_000)) * 85)

const EMBED_MODES = [
  {
    id: 'live', tag: 'LIVE EMBED', title: 'Live embed with a connector override',
    body: 'Drop a <nubi-dashboard> web component into your app. Your backend signs a short-lived JWT with per-viewer RLS claims, and Nubi injects them into the SQL AST before any query runs. Point a viewer at their own connector with an id-based override.',
    chips: ['<nubi-dashboard>', 'JWT RLS', 'connector override', 'cross-filter'],
    code: '<nubi-dashboard\n  dashboard-id="revenue"\n  get-token="getEmbedToken"\n  connector-id="tenant-42" />',
    label: 'html',
  },
  {
    id: 'frozen', tag: 'FROZEN SNAPSHOT', title: 'A frozen snapshot that stays interactive',
    body: 'Schedule a Flows pipeline to snapshot the board into a frozen DuckDB bundle. Viewers open it and the full DuckDB-WASM kernel loads their copy, so cross-filtering, drill-down and hover all keep working, offline if needed.',
    chips: ['duckdb snapshot', 'offline-capable', 'scheduled via Flows', 'cross-filter'],
    code: '# flows/snapshot.yaml\nschedule: "0 */6 * * *"\nsteps:\n  - type: snapshot\n    dashboard: revenue\n    output: public/revenue.nubi',
    label: 'yaml',
  },
  {
    id: 'public', tag: 'PUBLIC / CDN', title: 'A static export on your CDN',
    body: 'Mark a snapshot public and Nubi publishes it as a static bundle: no auth, no backend. The WASM kernel ships inside it, so anyone with the URL gets a fully interactive dashboard.',
    chips: ['static CDN bundle', 'zero backend', 'opt-in', 'public link'],
    code: '# opt-in: unsafe_public: true\nnubi export --dashboard revenue \\\n  --output dist/revenue.nubi \\\n  --public',
    label: 'shell',
  },
]

const PIPELINE = [
  { tag: 'SOURCE', title: 'Your warehouse', body: 'BigQuery, Snowflake, Postgres and more. Nubi pushes queries down to your warehouse. One Flows pipeline queries once and fans the result out to every output.', chip: '20+ connectors' },
  { tag: 'CANVAS', title: 'Dashboard', body: 'One live dashboard with charts, KPIs and cross-filters. Everything as code: edit visually or as files, version in git, deploy from the CLI.', chip: 'echarts · arrow ipc' },
  { tag: 'FLOWS', title: 'Flows auto-refresh', body: 'Schedule a Flows DAG to re-query, snapshot and fan out on a cron. One data pipeline drives every output, so an edit propagates everywhere.', chip: 'scheduled · incremental' },
]

const OUTPUTS = [
  { label: 'Live embed', desc: 'Web component, JWT RLS, real time.' },
  { label: 'PDF report', desc: 'Vector output: ECharts server-side render to PDF.' },
  { label: 'Scheduled delivery', desc: 'Email or webhook on a cron.' },
]

// "Frozen, still alive": a working model with neutral, made-up figures.
const SEGMENTS = [
  { id: 'amer', label: 'AMER', pct: 72, mrr: '$142k', cust: '2,841', churn: '1.9%' },
  { id: 'emea', label: 'EMEA', pct: 54, mrr: '$98k', cust: '1,906', churn: '2.4%' },
  { id: 'apac', label: 'APAC', pct: 38, mrr: '$61k', cust: '1,220', churn: '2.8%' },
  { id: 'latam', label: 'LATAM', pct: 21, mrr: '$34k', cust: '712', churn: '3.1%' },
]

/* ── Styles ───────────────────────────────────────────────────────────────── */

const CSS = `
.rp-bars { display: grid; gap: 14px; }
.rp-row { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 4px 14px; align-items: center; }
@media (min-width: 640px) { .rp-row { grid-template-columns: 120px minmax(0, 1fr); } }
.rp-row > b { font: 500 12px var(--font-mono); color: var(--text-muted); grid-row: span 2; align-self: start; padding-top: 2px; }
.rp-line { display: flex; align-items: center; gap: 10px; }
.rp-track { flex: 1; height: 14px; background: var(--surface-2); position: relative; }
.rp-fill { position: absolute; inset: 0 auto 0 0; background: var(--ink); }
.rp-fill.rp-us { background: var(--accent); }
.rp-val { width: 78px; text-align: right; font: 500 12px var(--font-mono); font-variant-numeric: tabular-nums; white-space: nowrap; }
.rp-val.rp-us { color: var(--accent); }
.rp-key { display: flex; gap: 18px; font: 500 11px var(--font-mono); letter-spacing: .06em; text-transform: uppercase; color: var(--text-muted); }
.rp-key i { display: inline-block; width: 10px; height: 10px; margin-right: 6px; background: var(--ink); vertical-align: -1px; }
.rp-key i.rp-us { background: var(--accent); }
.rp-mode { padding: 22px 20px 24px; display: flex; flex-direction: column; gap: 12px; }
.rp-mode h3 { margin: 0; font-size: 20px; font-weight: 600; letter-spacing: -.02em; line-height: 1.2; }
.rp-mode > p:not(.mk-idx) { margin: 0; font-size: 14.5px; line-height: 1.6; color: var(--text-muted); flex: 1; }
.rp-mode .mk-code { margin-top: 4px; }
.rp-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.rp-flow { display: grid; gap: 1px; background: var(--border); border: 1px solid var(--border); }
@media (min-width: 900px) { .rp-flow { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.rp-flow > article { background: var(--surface); padding: 20px; display: flex; flex-direction: column; gap: 10px; position: relative; }
.rp-flow h3 { margin: 0; font-size: 18px; font-weight: 600; letter-spacing: -.015em; }
.rp-flow p { margin: 0; font-size: 14px; line-height: 1.55; color: var(--text-muted); flex: 1; }
.rp-out { display: grid; gap: 1px; background: var(--border); border: 1px solid var(--border); border-top: 2px solid var(--ink); margin-top: 28px; }
@media (min-width: 720px) { .rp-out { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
.rp-out > div { background: var(--surface); padding: 16px 20px 18px; }
.rp-out b { display: block; font-size: 15px; font-weight: 600; }
.rp-out span { font-size: 13px; color: var(--text-muted); }
.rp-split { display: grid; gap: 40px; align-items: center; }
@media (min-width: 960px) { .rp-split { grid-template-columns: minmax(0, 5fr) minmax(0, 6fr); gap: 64px; } }
.rp-snap { border: 1px solid var(--ink); background: var(--surface); }
.rp-snap-bar { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 14px; border-bottom: 1px solid var(--border); font: 12px var(--font-mono); color: var(--text-muted); }
.rp-snap-bar span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rp-kpis { display: grid; grid-template-columns: repeat(3, 1fr); border-bottom: 1px solid var(--border); }
.rp-kpis > div { padding: 14px; border-right: 1px solid var(--border); }
.rp-kpis > div:last-child { border-right: 0; }
.rp-kpis small { display: block; font: 500 11px var(--font-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--text-muted); }
.rp-kpis b { display: block; margin-top: 4px; font: 500 24px/1.1 var(--font-ui); letter-spacing: -.03em; font-variant-numeric: tabular-nums; }
.rp-seg { padding: 14px; display: grid; gap: 6px; }
.rp-seg button { display: grid; grid-template-columns: 56px minmax(0, 1fr) 40px; gap: 12px; align-items: center; width: 100%; padding: 6px 4px; background: transparent; border: 0; cursor: pointer; text-align: left; color: var(--text); font: 12px var(--font-mono); }
.rp-seg button:hover { background: var(--frame-hover); }
.rp-seg button[aria-pressed="true"] { background: var(--accent-soft); box-shadow: inset 2px 0 0 var(--accent); }
.rp-seg button:focus-visible { outline-offset: -2px; }
.rp-seg .rp-track { height: 12px; }
.rp-seg button[aria-pressed="true"] .rp-fill { background: var(--accent); }
.rp-seg em { font-style: normal; text-align: right; color: var(--text-muted); }
.rp-snap-foot { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center; padding: 10px 14px; border-top: 1px solid var(--border); font: 12px var(--font-mono); color: var(--text-muted); }
`

/* ── Sections ─────────────────────────────────────────────────────────────── */

function EconomicsSection() {
  return (
    <Section
      id="economics" index="01" label="THE ECONOMICS"
      title={<>Cost stays flat as <em>your audience grows.</em></>}
      lead={<>Most BI vendors charge per viewer seat. Add ten thousand users and you owe ten thousand licenses. Nubi bills for <b>embedded sessions, AI tokens and server kernel runs</b>, never for eyeballs. The DuckDB-WASM kernel renders the chart inside each visitor&rsquo;s tab, so the marginal cost of one more viewer is <b>about $0</b>.</>}
    >
      <div className="rp-split" style={{ alignItems: 'start' }}>
        <Reveal>
          <p className="mk-p" style={{ maxWidth: '54ch' }}>
            500 viewers of the same dashboard collapse to a <b>single warehouse hit</b> through a content-hashed edge cache, so the cost
            curve is flat in practice, not just on a slide.
          </p>
          <div className="mk-ctas" style={{ marginTop: 28 }}>
            <Link to="/pricing" className="nubi-btn nubi-btn-secondary nubi-btn-lg">See full pricing</Link>
            <ArrowLink to="/compare">Compare with Hex and Cube</ArrowLink>
          </div>
        </Reveal>

        <Reveal delay={60}>
          <div>
            <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', gap: 8, paddingBottom: 10, borderBottom: '2px solid var(--ink)' }}>
              <span className="mk-label">Cost against viewer count · log scale</span>
              <span className="rp-key"><span><i className="rp-us" />Nubi</span><span><i />Per-seat BI</span></span>
            </div>
            <div className="rp-bars" style={{ paddingTop: 18 }} role="img" aria-label="Per-seat cost grows from $90 a month at 100 viewers to $900k a month at one million. Nubi stays flat.">
              {VIEWER_TIERS.map(t => (
                <div className="rp-row" key={t.label}>
                  <b>{t.label}</b>
                  <div className="rp-line">
                    <div className="rp-track"><div className="rp-fill rp-us" style={{ width: '3%' }} /></div>
                    <span className="rp-val rp-us">flat</span>
                  </div>
                  <div className="rp-line">
                    <div className="rp-track"><div className="rp-fill" style={{ width: `${compPct(t.viewers)}%` }} /></div>
                    <span className="rp-val">{t.comp}</span>
                  </div>
                </div>
              ))}
            </div>
            <p className="mk-small mk-mono" style={{ marginTop: 16 }}>
              Illustrative. The per-seat bar is log-scaled to viewer count at about $9 per viewer per month. Exact numbers depend on the vendor and the tier.
            </p>
          </div>
        </Reveal>
      </div>
    </Section>
  )
}

function EmbedModes() {
  return (
    <Section
      id="embed" index="02" label="EMBEDDING MODES" sunk
      title={<>Three modes, <em>one dashboard.</em></>}
      lead={<>Embed live with per-viewer JWT auth, ship a frozen but interactive snapshot, or publish to a CDN as a static bundle with no backend. Pick the mode that fits your security posture, not your vendor&rsquo;s product limits.</>}
    >
      <Reveal>
        <div className="mk-cells mk-cells-3">
          {EMBED_MODES.map(m => (
            <article key={m.id} className="rp-mode">
              <p className="mk-idx" style={{ border: 0, padding: 0 }}><b>{m.tag}</b></p>
              <h3>{m.title}</h3>
              <p>{m.body}</p>
              <CodePanel label={m.label} code={m.code} />
              <div className="rp-chips">{m.chips.map(c => <Tag key={c} caps={false}>{c}</Tag>)}</div>
            </article>
          ))}
        </div>
        <div className="rp-split" style={{ marginTop: 56 }}>
          <Figure name="EmbedAuth" title="A signed token becomes a row-level predicate in the SQL, then renders in the browser" className="w-full h-auto" />
          <p className="mk-p" style={{ maxWidth: '54ch' }}>
            <b>JWT, then an AST predicate, then rendered in the browser.</b> Auth lives in your repo, not in a vendor UI.
          </p>
        </div>
      </Reveal>
    </Section>
  )
}

function Pipeline() {
  return (
    <Section
      id="pipeline" index="03" label="ONE DASHBOARD, ALL OUTPUTS"
      title={<>Build once. <em>Deliver everywhere.</em></>}
      lead={<>One dashboard canvas is the source of truth for your live embed, your PDF board pack and your scheduled email report. Flows refreshes and delivers each output, with no duplicate pipelines.</>}
    >
      <Reveal>
        <div className="rp-flow">
          {PIPELINE.map((s, i) => (
            <article key={s.tag}>
              <p className="mk-idx" style={{ border: 0, padding: 0 }}><b>{String(i + 1).padStart(2, '0')}</b><span>{s.tag}</span></p>
              <h3>{s.title}</h3>
              <p>{s.body}</p>
              <p><Tag caps={false}>{s.chip}</Tag></p>
            </article>
          ))}
        </div>
        <div className="rp-out">
          {OUTPUTS.map(o => (<div key={o.label}><p className="mk-label" style={{ marginBottom: 4 }}>Output</p><b>{o.label}</b><span>{o.desc}</span></div>))}
        </div>
        <div className="rp-split" style={{ marginTop: 56 }}>
          <Figure name="FlowOrchestration" title="A Flows pipeline: one refresh drives every output format" className="w-full h-auto" />
          <p className="mk-p" style={{ maxWidth: '54ch' }}><b>One pipeline refresh drives every output format.</b> Edit the board once and the embed, the PDF and the scheduled email all follow.</p>
        </div>
      </Reveal>
    </Section>
  )
}

function FrozenAlive() {
  const [sel, setSel] = useState('amer')
  const seg = SEGMENTS.find(s => s.id === sel) ?? SEGMENTS[0]
  return (
    <Section
      id="frozen" index="04" label="FROZEN, STILL ALIVE" sunk
      title={<>Public dashboards: <em>interactive,</em> not screenshots.</>}
    >
      <div className="rp-split">
        <Reveal>
          <div className="rp-snap">
            <div className="rp-snap-bar">
              <span>cdn.example.com/dashboards/q3-review</span>
              <Tag dot>Frozen</Tag>
            </div>
            <div className="rp-kpis" aria-live="polite">
              <div><small>MRR</small><b>{seg.mrr}</b></div>
              <div><small>Customers</small><b>{seg.cust}</b></div>
              <div><small>Churn</small><b>{seg.churn}</b></div>
            </div>
            <div className="rp-seg" role="group" aria-label="Segment breakdown, select a region to filter">
              {SEGMENTS.map(s => (
                <button key={s.id} type="button" aria-pressed={s.id === sel} onClick={() => setSel(s.id)}>
                  <span>{s.label}</span>
                  <span className="rp-track"><span className="rp-fill" style={{ width: `${s.pct}%` }} /></span>
                  <em>{s.pct}%</em>
                </button>
              ))}
            </div>
            <div className="rp-snap-foot">
              <Tag tone="accent" dot>duckdb-wasm active</Tag>
              <span>Sample data. Pick a region to filter.</span>
            </div>
          </div>
        </Reveal>

        <Reveal delay={60}>
          <div style={{ display: 'grid', gap: 20 }}>
            <p className="mk-lead">
              A frozen Nubi snapshot is not an image. It ships the full DuckDB-WASM kernel next to a compressed snapshot of the query results,
              so viewers get the same cross-filter, drill-down and hover as a live dashboard.
            </p>
            <p className="mk-p">
              Scheduled Flows pipelines refresh the snapshot on a cron: every six hours, every morning at 06:00, or on any webhook. The URL never changes.
              CDN edge nodes serve the bundle, so your warehouse is called <b>zero times per view</b>.
            </p>
            <Checks items={[
              'Frozen DuckDB snapshot: compressed query state, not pixels',
              'Full cross-filter UX: the WASM kernel ships with the bundle',
              'Scheduled refresh via Flows: cron, webhook or manual',
              'CDN-served with zero backend: a static bundle, no server on load',
            ]} />
            <div><ArrowLink to="/docs/dashboards">Read the docs</ArrowLink></div>
          </div>
        </Reveal>
      </div>
    </Section>
  )
}

/* ── Page ─────────────────────────────────────────────────────────────────── */

export default function ReportingPage() {
  return (
    <div className="mk">
      <MarketingStyles />
      <style href="nubi-reporting-page" precedence="default">{CSS}</style>

      <PageHero
        eyebrow="REPORTING" meta="BROWSER-NATIVE · DUCKDB-WASM · FLOWS-POWERED"
        title={<>Unlimited viewers. <em>Billed on usage,</em> not on seats.</>}
        lead={<>A dashboard with 10 or 10,000,000 viewers <b>costs the same to view</b>. The DuckDB-WASM kernel runs inside the browser, so viewers are free on every plan. Embed it live, export it to PDF, refresh it with <b>Flows</b>. One board, every output.</>}
        actions={
          <>
            <Link to="/register" className="nubi-btn nubi-btn-primary nubi-btn-xl">Start free</Link>
            <Link to="/pricing" className="nubi-btn nubi-btn-secondary nubi-btn-xl">See pricing</Link>
          </>
        }
        aside={
          <figure style={{ margin: 0 }}>
            <Figure name="EdgeCache" title="500 viewers collapse to one warehouse query through the edge cache" className="w-full h-auto" />
            <figcaption className="mk-label" style={{ marginTop: 10, display: 'flex', justifyContent: 'space-between' }}>
              <span>500 viewers → 1 warehouse hit</span><span>duckdb-wasm · 0 ms cold start</span>
            </figcaption>
          </figure>
        }
      >
        <div style={{ marginTop: 48 }}>
          <Facts three items={[
            { v: '≈ $0', l: 'per dashboard view' },
            { v: '∞', l: 'viewers on every plan' },
            { v: '1', l: 'warehouse hit for 500 viewers' },
          ]} />
        </div>
      </PageHero>

      <EconomicsSection />
      <EmbedModes />
      <Pipeline />
      <FrozenAlive />

      <section className="mk-sec mk-ink-band">
        <div className="mk-wrap">
          <div className="mk-idx" style={{ borderBottomColor: 'var(--ink-fg)', color: 'inherit' }}><b style={{ color: 'inherit' }}>START</b><span>NO CREDIT CARD</span></div>
          <div style={{ display: 'grid', gap: 28, marginTop: 40, maxWidth: 820 }}>
            <h2 className="mk-h2" style={{ fontSize: 'clamp(36px, 5vw, 72px)', lineHeight: 1 }}>Unlimited viewers. <em>One flat bill.</em></h2>
            <p className="mk-lead">Connect your warehouse and embed a live dashboard in minutes. Freeze it, export it to PDF. Frozen snapshots ship on every plan.</p>
            <div className="mk-ctas">
              <Link to="/register" className="nubi-btn nubi-btn-primary nubi-btn-xl">Start free <ArrowRight size={16} strokeWidth={1.75} aria-hidden="true" /></Link>
              <Link to="/pricing" className="nubi-btn nubi-btn-secondary nubi-btn-xl">See pricing</Link>
              <Link to="/compare" className="nubi-btn nubi-btn-secondary nubi-btn-xl">Compare with Hex and Cube</Link>
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
