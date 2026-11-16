// Static markup for the landing page (authored here, no user data), mounted by LandingPage.
// The story copy renders from the scene modules (scenes/<id>/); this file holds the header, proof rows, the outro and overlays.
// The outro figures are driven by landingLive.js; the chrome (menu, anchors, ⌘K) by chrome.js.
//
// Every chapter follows one grammar, always in the same slots:
//   idx line / headline / one paragraph / facts row / code line / TRY line (+ its DOM controls) / spec link
import { TIERS } from '../../data/pricing'
import { getToolLogo, opticalBox } from '../../components/brand/registry'

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/** A mono logo cell: official mark (CSS mask of the registered SVG, optically sized) plus the vendor name. */
function logoCell(slug: string, name: string, href: string): string {
  const e = getToolLogo(slug)
  const mark = e
    ? (() => { const { w, h } = opticalBox(e, 24); return `<i class="lm" aria-hidden="true" style="--m:url(${e.file});width:${w}px;height:${h}px"></i>` })()
    : '<i class="lm" aria-hidden="true"></i>'
  return `<a class="lg" href="${href}" aria-label="${esc(name)} documentation">${mark}<span>${esc(name)}</span></a>`
}

const more = (href: string, text = 'Read the spec') => `<a class="more" href="${href}">${text} <span aria-hidden="true">→</span></a>`



const tierCells = TIERS.map(t => `
      <li class="tier${t.highlight ? ' hl' : ''}">
        <span class="label">${esc(t.name)}${t.highlight && t.badge ? ` <em class="tg">${esc(t.badge)}</em>` : ''}</span>
        <b class="num">${esc(t.price)}</b>
        <small class="num">${esc(t.cadence)}</small>
        <p>${esc(t.tagline)}</p>
      </li>`).join('')

/** Header: [mark]ubi lockup, nav, ⌘K, Sign in, Start free, or Open app when signed in (the drawer is the mobile menu). */
export const HEADER_MARKUP = `
<a class="skip" href="#main">Skip to content</a>
<header class="top gut"><div class="inner">
  <a class="brand" href="#top" aria-label="Nubi, back to the top"><i class="lockup" aria-hidden="true"></i></a>
  <nav class="main-nav" aria-label="Main"><a href="#ch-columns">Product</a><a href="#live">Live demo</a><a href="/docs">Docs</a><a href="/pricing">Pricing</a><a href="/compare">Compare</a></nav>
  <div class="r">
    <button class="kbtn" id="kbtn" type="button" aria-label="Search the page (Ctrl K)"><svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M11 11l4 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="square" fill="none"/></svg><span>Search or jump to…</span><kbd id="kmod">Ctrl K</kbd></button>
    <a class="ibtn gh" href="https://github.com/nu-bi/nubi" target="_blank" rel="noopener noreferrer" aria-label="Nubi on GitHub"><i class="gh-mark" aria-hidden="true"></i></a><a class="btn sm ghost signin guest" href="/login">Sign in</a><a class="btn sm pri guest" href="/register">Start free</a><a class="btn sm pri member" href="/home">Open app</a>
    <button class="menu-btn" id="menu-btn" type="button" aria-expanded="false" aria-controls="drawer" aria-label="Open menu"><svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><path class="m-open" d="M2 5h14M2 9h14M2 13h14" stroke="currentColor" stroke-width="1.5" stroke-linecap="square" fill="none"/><path class="m-close" d="M3 3l12 12M15 3L3 15" stroke="currentColor" stroke-width="1.5" stroke-linecap="square" fill="none"/></svg></button>
  </div>
</div>
<nav class="drawer" id="drawer" aria-label="Menu" hidden>
  <a href="#ch-columns">Product</a><a href="#live">Live demo</a><a href="#embed">Embed</a><a href="#agents">Agents</a><a href="/docs">Docs</a><a href="/pricing">Pricing</a><a href="/compare">Compare</a><a href="https://github.com/nu-bi/nubi" target="_blank" rel="noopener noreferrer">GitHub</a>
  <div class="dr-cta"><a class="btn guest" href="/login">Sign in</a><a class="btn pri guest" href="/register">Start free</a><a class="btn pri member" href="/home">Open app</a></div>
</nav>
</header>
`

/** Proof rows (built on · connects to · works with). Desktop: a one-row strip in the hero viewport; mobile: the 3-row band after the hero. */
export const PROOF_ROWS = {
  built: `${logoCell('duckdb', 'DuckDB', '/docs/architecture-and-economics')}${logoCell('apachearrow', 'Arrow', '/docs/architecture-and-economics')}${logoCell('apacheecharts', 'ECharts', '/docs/dashboards')}`,
  connects: `${logoCell('postgresql', 'PostgreSQL', '/docs/connectors')}${logoCell('snowflake', 'Snowflake', '/docs/connectors')}${logoCell('googlebigquery', 'BigQuery', '/docs/connectors')}${logoCell('databricks', 'Databricks', '/docs/connectors')}${logoCell('clickhouse', 'ClickHouse', '/docs/connectors')}${logoCell('mysql', 'MySQL', '/docs/connectors')}<a class="lg plus" href="/docs/connectors" aria-label="14 more connectors"><b class="num">+14</b><span>more</span></a>`,
  works: `${logoCell('claude', 'Claude', '/docs/ai-and-mcp')}${logoCell('openai', 'ChatGPT', '/docs/ai-and-mcp')}${logoCell('claudecode', 'Claude Code', '/docs/ai-and-mcp')}`,
}

export const PROOF_BAND_MARKUP = `<section class="proof gut" aria-label="What Nubi is built on and works with"><div class="inner">
    <div class="prow">
      <span class="label plabel">Built on</span>
      ${logoCell('duckdb', 'DuckDB', '/docs/architecture-and-economics')}${logoCell('apachearrow', 'Apache Arrow', '/docs/architecture-and-economics')}${logoCell('apacheecharts', 'Apache ECharts', '/docs/dashboards')}
      <p class="pnote">The engine, the wire format and the chart library are open-source projects you can read.</p>
    </div>
    <div class="prow">
      <span class="label plabel">Connects to</span>
      ${logoCell('postgresql', 'PostgreSQL', '/docs/connectors')}${logoCell('snowflake', 'Snowflake', '/docs/connectors')}${logoCell('googlebigquery', 'BigQuery', '/docs/connectors')}${logoCell('databricks', 'Databricks', '/docs/connectors')}${logoCell('clickhouse', 'ClickHouse', '/docs/connectors')}${logoCell('mysql', 'MySQL', '/docs/connectors')}<a class="lg plus" href="/docs/connectors" aria-label="14 more connectors"><b class="num">+14</b><span>more</span></a>
    </div>
    <div class="prow">
      <span class="label plabel">Works with</span>
      ${logoCell('claude', 'Claude', '/docs/ai-and-mcp')}${logoCell('openai', 'ChatGPT', '/docs/ai-and-mcp')}${logoCell('claudecode', 'Claude Code', '/docs/ai-and-mcp')}
      <p class="pnote">Over MCP and OAuth, under your own row policies.</p>
    </div>
  </div></section>`

export const PROOF_STRIP_MARKUP = `
  <div class="pgroup"><span class="label">Built on</span>${PROOF_ROWS.built}</div>
  <div class="pgroup"><span class="label">Connects to</span>${PROOF_ROWS.connects}</div>
  <div class="pgroup"><span class="label">Works with</span>${PROOF_ROWS.works}</div>`

/** "Works with" row for the agents chapter (mono marks, 20px). */
export const WORKS_WITH_MARKUP = `${logoCell('claude', 'Claude', '/docs/ai-and-mcp')}${logoCell('openai', 'ChatGPT', '/docs/ai-and-mcp')}${logoCell('claudecode', 'Claude Code', '/docs/ai-and-mcp')}${logoCell('modelcontextprotocol', 'MCP', '/docs/mcp')}`

/** The DOM outro after the story: 06 Live · 07 Embed · 08 What you get · 09 Open core · 10 Pricing · 11 Start. */
export const OUTRO_MARKUP = `
<div class="outro">
  <section class="gut" id="live" aria-labelledby="liveh"><div class="inner">
    <div class="sec-h"><div class="idx"><b>06</b><span>LIVE</span></div><div><h2 id="liveh">Run it in this tab.</h2><p>Over a million sample rows stream in as record batches and are totalled on your machine. Regroup them, filter them, run it again from the cache. The server does no work after the first query.</p>${more('/docs/architecture-and-economics')}</div></div>
    <div class="inst" id="instrument">
      <div class="inst-bar">
        <div class="ctl"><span class="label" id="gb-l">Group by</span>
          <div class="seg" role="group" aria-labelledby="gb-l" id="seg-dim"><button type="button" data-v="cty" aria-pressed="true">Country</button><button type="button" data-v="reg" aria-pressed="false">Region</button><button type="button" data-v="chan" aria-pressed="false">Channel</button><button type="button" data-v="cat" aria-pressed="false">Category</button></div></div>
        <div class="ctl"><span class="label" id="ms-l">Measure</span>
          <div class="seg" role="group" aria-labelledby="ms-l" id="seg-meas"><button type="button" data-v="rev" aria-pressed="true">Revenue</button><button type="button" data-v="cnt" aria-pressed="false">Orders</button></div></div>
        <div class="grow"></div>
        <div class="status" role="status"><i class="dot live" id="sdot"></i><span id="stext">Streaming from warehouse</span></div>
        <button class="btn sm" type="button" id="rerun">Run again</button>
        <button class="btn sm" type="button" id="purge">Clear cache</button>
      </div>
      <div class="pstage" id="pstage">
        <canvas id="pgl" role="img" aria-label="Halftone cloud raining data batches into a bar chart of the aggregated sample data"></canvas>
        <div class="fig"><span class="label">Fig. 6 · Live in this tab</span><span class="label">1,048,576 synthetic retail rows</span></div>
        <div class="legend"><span class="label" id="unit">■ = $0</span><span class="label">hover a bar · click to filter</span></div>
        <div class="cols" id="cols"></div>
        <div class="tip" id="tip" hidden></div>
      </div>
      <div class="readouts">
        <div class="ro"><span class="label">Rows in this tab</span><span class="v" id="r-rows">0</span><small id="r-rows-s">of 1,048,576 · 256 batches</small></div>
        <div class="ro"><span class="label">Arrow received</span><span class="v" id="r-bytes">0 MB</span><small>record batches, no JSON step</small></div>
        <div class="ro"><span class="label">Last aggregate</span><span class="v" id="r-agg">0.0 ms</span><small id="r-agg-s">measured in your browser</small></div>
        <div class="ro"><span class="label">Server compute</span><span class="v zero" id="r-srv">0 ms</span><small>for this view</small></div>
        <div class="ro"><span class="label">Warehouse queries</span><span class="v" id="r-wq">1</span><small id="r-wq-s">cold run</small></div>
      </div>
    </div>
  </div></section>

  <section class="gut" id="embed" aria-labelledby="embh"><div class="inner">
    <div class="sec-h"><div class="idx"><b>07</b><span>EMBEDDING</span></div><div><h2 id="embh">One tag in your product.</h2><p>Load the bundle, mount <code class="mono">&lt;nubi-dashboard&gt;</code> and hand it a function that returns a token. It fetches the board, runs each widget’s registered query, parses Arrow, renders, and refreshes the token before it expires.</p></div></div>
    <div class="emb">
      <div class="left">
        <div class="ctlrow">
          <div class="ctl"><span class="label" id="et-l">theme</span><div class="seg" role="group" aria-labelledby="et-l" id="seg-etheme"><button type="button" data-v="light" aria-pressed="true">light</button><button type="button" data-v="dark" aria-pressed="false">dark</button></div></div>
          <div class="ctl"><span class="label" id="eb-l">board</span><div class="seg" role="group" aria-labelledby="eb-l" id="seg-eboard"><button type="button" data-v="brd_sales" aria-pressed="true">sales</button><button type="button" data-v="brd_ops" aria-pressed="false">ops</button></div></div>
        </div>
        <div><div class="caprow"><span class="label">your-app/templates/reports.html</span><button class="cbtn" type="button" id="copy">Copy</button></div><div class="scrollx"><pre class="src" id="snippet"></pre></div></div>
        <div><div class="caprow"><span class="label">Events from the element</span><span class="label">composed · bubbles</span></div><div class="events" id="events" role="log" aria-live="polite"></div></div>
        <p class="after">${more('/docs/embed-api')}</p>
      </div>
      <div class="host" role="group" aria-label="Example host application with an embedded Nubi dashboard">
        <div class="host-bar"><b></b><b></b><b></b><span>app.your-product.com/reports</span></div>
        <div class="host-body">
          <div class="host-nav"><strong>Your product</strong><span>Overview</span><span style="color:var(--ink)">Reports</span><span>Customers</span><span>Settings</span></div>
          <div class="host-main"><h4 id="hosth">Sales report</h4><span style="font-size:12px;color:var(--muted)">Everything inside the dashed line is Nubi.</span><div class="nd light" id="nd"></div></div>
        </div>
      </div>
    </div>
  </div></section>

    <section class="gut" id="specs" aria-labelledby="spech"><div class="inner">
    <div class="sec-h"><div class="idx"><b>08</b><span>WHAT YOU GET</span></div><h2 id="spech">Everything a data team needs, in one open-source product.</h2></div>
    <div class="specs">
      <div class="spec"><span class="k">Engine</span><b>DuckDB-WASM in the tab</b><p>Server kernel only for large jobs and native libraries.</p></div>
      <div class="spec"><span class="k">Transport</span><b>Arrow IPC</b><p>One format from warehouse to chart.</p></div>
      <div class="spec"><span class="k">Cache</span><b>Content-hashed</b><p>Many viewers, one warehouse hit.</p></div>
      <div class="spec"><span class="k">Security</span><b>Row-level, in the plan</b><p>JWT claims become query predicates.</p></div>
      <div class="spec"><span class="k">Charts</span><b>17 types</b><p>Sankey, waterfall, forecast fan and more. WebGL past 20k rows.</p></div>
      <div class="spec"><span class="k">Agents</span><b>MCP server</b><p>Claude and other agents read and build boards over OAuth.</p></div>
      <div class="spec"><span class="k">Flows</span><b>SQL + Python DAGs</b><p>Schedules, backfills, data-quality checks.</p></div>
      <div class="spec"><span class="k">Sources</span><b>20+ connectors</b><p>Postgres to BigQuery, Snowflake, MySQL, ClickHouse, with a bridge for private networks.</p></div>
    </div>
    <div class="claims-row" aria-label="Agents">
      <div class="claim"><span class="label">Governed vocabulary</span><h3>Metrics, defined once.</h3><p>Revenue means the same thing to an agent, a dashboard and an export, because each one compiles the same metric definition to SQL.</p></div>
      <div class="claim"><span class="label">Bounded loop</span><h3>Caps on steps, tokens and time.</h3><p>The chat loop has a per-turn token budget, a step cap and a timeout, with its own rate-limit class.</p></div>
      <div class="claim"><span class="label">Same security</span><h3>The agent sees what you see.</h3><p>Tool calls carry your claims, so row policies apply to the agent exactly as they apply to you.</p></div>
    </div>
    <p class="after">${more('/docs', 'Read the docs')} ${more('/docs/mcp', 'Agents and MCP')}</p>
  </div></section>

  <section class="gut" id="selfhost" aria-labelledby="osh"><div class="inner">
    <div class="sec-h"><div class="idx"><b>09</b><span>OPEN CORE</span></div><div><h2 id="osh">Open source, full stop. Run it yourself.</h2><p>Nubi is Apache-2.0. Self-hosting gets you the whole product with billing switched off. Nubi Cloud is the same code, run for you.</p></div></div>
    <div class="sh">
      <div class="cmd">
        <div class="caprow"><span class="label">Self-host with Docker Compose</span><button class="cbtn" type="button" id="copy-sh">Copy</button></div>
        <div class="scrollx"><pre class="src" id="sh-cmd">git clone https://github.com/nu-bi/nubi.git
cd nubi
make up</pre></div>
        <p class="note">Needs Docker with the Compose plugin. Migrations run on start. There is no license key to buy: the billing code ships in the repo, switched off.</p>
      </div>
      <div class="cols2">
        <div class="col2"><span class="label">In the open core</span>
          <ul><li>The whole product, with no seat or usage limits</li><li>Editor, 17 chart types, embeds and row-level security</li><li>DuckDB-WASM in the tab, server kernels when you need them</li><li>20+ connectors and the bridge agent</li><li>Flows, watches and the MCP server</li><li>Runs on Postgres alone</li></ul></div>
        <div class="col2"><span class="label">In Nubi Cloud</span>
          <ul><li>The same code, hosted by us</li><li>Billing, the prepaid wallet and quota enforcement switched on</li><li>Metered embedded sessions, AI tokens and agent runs</li><li>Support plans, with an SLA and a dedicated CSM on Enterprise</li></ul></div>
      </div>
    </div>
    <p class="after">${more('/docs/open-core', 'Read the open-core split')} ${more('/docs/self-host', 'Self-hosting guide')}</p>
  </div></section>

  <section class="gut" id="pricing" aria-labelledby="prh"><div class="inner">
    <div class="sec-h"><div class="idx"><b>10</b><span>PRICING</span></div><div><h2 id="prh">Viewers are free on every plan.</h2><p>You pay for building and for server compute, never for people who only look.</p></div></div>
    <div class="price">
      <div class="big">$0<small>per extra viewer, on every plan</small></div>
      <ul>
        <li><span>Seats</span><span>Unlimited on every plan</span></li>
        <li><span>Embedding</span><span>One signed token per viewer</span></li>
        <li><span>Self-hosting</span><span>Apache-2.0, runs on Postgres alone</span></li>
        <li><span>Agents</span><span>MCP included</span></li>
      </ul>
    </div>
    <ol class="tiers" aria-label="Plans from the pricing page">${tierCells}
    </ol>
    <p class="after">${more('/pricing', 'See every plan and the usage rates')}</p>
  </div></section>

  <section class="gut" id="start" aria-labelledby="starth"><div class="inner">
    <div class="final">
      <div><div class="idx"><b>11</b><span>START</span></div><h2 id="starth">Point it at a warehouse and build your first board.</h2></div>
      <div class="ctas"><a class="btn pri" href="/register">Start free</a><a class="btn" href="/docs">Read the docs</a><a class="btn" href="https://github.com/nu-bi/nubi" target="_blank" rel="noopener">Read the code</a></div>
    </div>
    <div class="wordmark"><canvas id="wm" aria-hidden="true"></canvas></div>
  </div></section>
  <div class="foot-note gut"><div class="inner"><span>Figures on this page use synthetic sample data.</span><span id="kmod2">Ctrl K to search</span></div></div>
</div>
`

/** ⌘K palette and toast. */
export const OVERLAY_MARKUP = `
<div class="pal-bg" id="pal" hidden>
  <div class="pal" role="dialog" aria-modal="true" aria-label="Command palette">
    <input id="pal-q" type="text" role="combobox" aria-expanded="true" aria-autocomplete="list" aria-controls="pal-list" aria-label="Search the page" placeholder="Jump to a section or run an action…" autocomplete="off" spellcheck="false">
    <ul id="pal-list" role="listbox" aria-label="Results"></ul>
    <div class="pf"><span>↑↓ move</span><span>↵ open</span><span>esc close</span><span class="grow"></span><span id="pal-n" aria-live="polite"></span></div>
  </div>
</div>
<div class="toast" id="toast" role="status" hidden></div>
`
