/**
 * Compare page data — the hand-curated tables behind /compare.
 *
 * Every figure comes from a competitor's public pricing page or an independent
 * analyst (June 2026). `entryEst: true` marks an estimate where no public price
 * exists. The long-form profiles live in src/content/compare/*.md and load
 * through registry.ts; this file holds the structured rows.
 */

export interface Competitor {
  name: string
  isNubi: boolean
  category: string
  pricingModel: string
  entryPrice: string
  entryEst: boolean
  seatModel: string
  embedding: string
  selfHost: string
  strengths: string
  weaknesses: string
  sourceUrl: string
}

export const ALL_COMPETITORS: Competitor[] = [
  /* ── NUBI ── */
  {
    name: 'Nubi',
    isNubi: true,
    category: 'Embedded BI + Flows',
    pricingModel: 'Usage-based flat tiers',
    entryPrice: '$0 free / $9/mo Starter',
    entryEst: false,
    seatModel: 'Unlimited users & viewers at every tier — no per-seat charge',
    embedding: 'Core surface: JWKS-native JWT RLS; unlimited viewers all paid tiers',
    selfHost: 'Connector self-host (cloud control plane); full self-host planned',
    strengths: 'Unlimited seats; browser compute; transparent pricing',
    weaknesses: 'Newer product; smaller ecosystem; full self-host not yet shipped',
    sourceUrl: 'https://nubi.io/pricing',
  },
  /* ── GENERAL BI ── */
  {
    name: 'Metabase',
    isNubi: false,
    category: 'General BI',
    pricingModel: 'Per-seat tiered',
    entryPrice: 'OSS free; Pro $575/mo + $12/viewer',
    entryEst: false,
    seatModel: 'Every embedded viewer = full paid seat (~$150k/yr for 1k viewers on Pro)',
    embedding: 'White-label on Pro ($575+/mo); per-viewer seat cost at scale is prohibitive',
    selfHost: 'Yes — AGPL OSS free; Pro self-hosted same fee as cloud',
    strengths: 'Largest open-source community; lowest barrier for non-technical users; free AGPL self-host; Data Studio semantic layer (v59, 2026)',
    weaknesses: 'Per-viewer seat penalty; AGPL compliance burden for SaaS; no Arrow path',
    sourceUrl: 'https://www.metabase.com/pricing/',
  },
  {
    name: 'Hex',
    isNubi: false,
    category: 'Notebooks + Apps',
    pricingModel: 'Per-editor seat + compute add-on',
    entryPrice: 'Community free; Team $50/editor/mo',
    entryEst: false,
    seatModel: 'Per-editor; compute billed separately by kernel-minute',
    embedding: 'Enterprise add-on only; not a core surface; expensive',
    selfHost: 'No — cloud-only SaaS',
    strengths: 'Best-in-class collaborative Python notebook UX; strong AI (Magic AI, agents); broad connectivity',
    weaknesses: 'Per-session cloud kernel scales linearly with concurrent users; embedding expensive and bolt-on; no self-host; SVG viz ceiling ~50k rows',
    sourceUrl: 'https://hex.tech/pricing/',
  },
  {
    name: 'Cube Cloud',
    isNubi: false,
    category: 'Headless Semantic Layer',
    pricingModel: 'Per-developer + hourly CCU infra',
    entryPrice: 'Free hobbyist; Starter ~$40/dev/mo',
    entryEst: false,
    seatModel: 'Developer seats + Cube Consumption Units (CCU) for infra; viewer $20/user/mo (Premium+)',
    embedding: 'Core strength (headless only); JWT→SQL RLS; no built-in viz — bring your own frontend',
    selfHost: 'Yes — Cube Core open source (MIT); production needs Redis + Cube Store cluster',
    strengths: 'Gold-standard headless semantic layer; strong JWT-driven RLS; MIT open core; warehouse-native pre-aggregations',
    weaknesses: 'No built-in viz — must build full frontend; high schema upfront cost (JS/YAML required before any query); CCU billing unpredictable at scale',
    sourceUrl: 'https://cube.dev/pricing',
  },
  {
    name: 'Looker',
    isNubi: false,
    category: 'Enterprise BI (LookML)',
    pricingModel: 'Sales-only (no public price)',
    entryPrice: 'Est. from $60k/year',
    entryEst: true,
    seatModel: 'Per-user; BigQuery compute billed separately on Google Cloud',
    embedding: 'Separate Embed SKU; iFrame + signed URLs; strong JWT RLS; est. $60k+ entry',
    selfHost: 'No — Google Cloud hosted only (since 2019)',
    strengths: 'Strongest governance and audit trail; best LookML ecosystem; Gemini AI (Conversational Analytics, LookML Assistant, Viz Assistant); Google Cloud integrations',
    weaknesses: 'No public pricing; very high LookML modeling tax (est. 40-60% of total investment); cloud-only since 2019; expensive for SMB',
    sourceUrl: 'https://cloud.google.com/looker/pricing',
  },
  {
    name: 'Tableau',
    isNubi: false,
    category: 'Viz Industry Standard',
    pricingModel: 'Per-seat tiered',
    entryPrice: 'Standard Viewer $15/user/mo; Creator $75/user/mo',
    entryEst: false,
    seatModel: 'Every viewer = paid seat; OEM SaaS embedding from $60k–$150k/year',
    embedding: 'Embedding API v3; per-viewer seat cost; OEM from $60k–$150k/year',
    selfHost: 'Yes — Tableau Server; Creator $70/user/mo for on-prem',
    strengths: 'Largest ecosystem; 40+ viz types; Hyper extract for fast in-memory queries; Tableau Pulse automated insights; industry recognition (Gartner MQ leader)',
    weaknesses: 'Per-viewer seat cost makes SaaS embedding expensive; high total contract value; slower innovation pace vs newer tools; no browser compute',
    sourceUrl: 'https://www.tableau.com/pricing/teams-orgs',
  },
  {
    name: 'Power BI',
    isNubi: false,
    category: 'Microsoft Ecosystem BI',
    pricingModel: 'Per-seat or Fabric F-SKU capacity',
    entryPrice: 'Pro $14/user/mo; Fabric F4 ~$400/mo',
    entryEst: false,
    seatModel: 'Pro per-seat ($14/user/mo) or Fabric F-SKU capacity (no per-viewer for F-SKU)',
    embedding: 'App-owns-data via Fabric F-SKU; F4 ~$400/mo covers ~100 concurrent embedded users — good value for Microsoft shops',
    selfHost: 'Yes — Power BI Report Server (requires Premium/SQL Server EE with SA); feature lag vs cloud',
    strengths: 'Deepest Microsoft 365/Teams/Excel integration; Copilot AI (DAX gen, narratives) on F2+ capacity; strong DAX + Power Query M ecosystem; 100+ custom visuals on AppSource; F-SKU removes per-viewer penalty',
    weaknesses: 'Significant value degradation outside Microsoft ecosystem; DAX/M learning curve; feature lag in Report Server vs cloud; complex licensing with multiple tiers',
    sourceUrl: 'https://powerbi.microsoft.com/en-us/pricing/',
  },
  {
    name: 'Apache Superset / Preset',
    isNubi: false,
    category: 'Open-Source BI',
    pricingModel: 'Free OSS; Preset managed cloud per-seat',
    entryPrice: 'Superset OSS free; Preset Pro $20/user/mo',
    entryEst: false,
    seatModel: 'Superset: unlimited (self-manage infra cost); Preset: per-seat cloud; embed add-on $500/mo for 50 viewers',
    embedding: 'iframe + Guest Token (OSS); Preset embed viewer add-on $500/mo for 50 viewers — penalises scale',
    selfHost: 'Yes — Apache Superset is free open-source (Apache 2.0); Preset Enterprise offers managed private cloud',
    strengths: 'Apache 2.0 licence (no AGPL compliance burden); large active community; broad connector support; Preset removes operational burden; no seat limit on self-hosted Superset',
    weaknesses: 'Superset DevOps overhead significant (Redis, Celery, Postgres); Preset embed per-viewer pricing penalises SaaS scale; no in-browser compute; limited AI maturity',
    sourceUrl: 'https://preset.io/pricing/',
  },
  {
    name: 'Count',
    isNubi: false,
    category: 'Data Canvas / Narrative BI',
    pricingModel: 'Per-editor; viewers always free',
    entryPrice: 'Free (3 editors); Pro $49/editor/mo',
    entryEst: false,
    seatModel: 'Editors pay; viewers are always free at every tier — closest to Nubi seat philosophy',
    embedding: 'Limited; Enterprise-only; not a core product surface',
    selfHost: 'No — cloud-only SaaS',
    strengths: 'Viewers genuinely free at every tier (aligns with no-viewer-seat philosophy); distinctive canvas layout for narrative analytics; strong SQL + Python + dbt integration',
    weaknesses: 'Scale tier requires minimum 15 editors ($1,035/mo minimum); embedding is limited and Enterprise-only; cloud-only; no Arrow path',
    sourceUrl: 'https://count.co/pricing',
  },
  /* ── EMBEDDED ANALYTICS SPECIALISTS ── */
  {
    name: 'Embeddable',
    isNubi: false,
    category: 'Embedded Analytics SDK',
    pricingModel: 'Session-based flat tiers',
    entryPrice: 'Free (200 sessions/mo); Lite $499/mo (1k sessions)',
    entryEst: false,
    seatModel: 'Unlimited end-users; pricing by dashboard sessions — not per viewer',
    embedding: 'Core product; React/Vue SDK; $499/mo for 1,000 sessions; $200 per additional 500 sessions overage',
    selfHost: 'No — cloud-only SaaS',
    strengths: 'Developer-first SDK; purpose-built for embedding; session-based model is predictable; strong React/Vue component library; good CI/CD workflow support on Premium',
    weaknesses: '$499/mo for only 1,000 sessions is expensive vs Nubi Starter ($9/mo for 1,000 sessions) or Team ($49/mo for 5,000 sessions); steep overage at $200/500 additional sessions; no open-source core; no in-browser compute',
    sourceUrl: 'https://embeddable.com/pricing',
  },
  {
    name: 'Holistics',
    isNubi: false,
    category: 'Embedded Analytics Platform',
    pricingModel: 'Flat platform fee, unlimited viewers',
    entryPrice: '$800/mo annual (Entry); $1,000/mo annual (Standard)',
    entryEst: false,
    seatModel: 'Flat fee includes 10 seats; unlimited embedded viewers at all tiers; extra seats $15–$18/seat/mo',
    embedding: 'Unlimited embedded viewers on flat fee; RLS on SCS tier ($2,000/mo annual); SAML/SCIM',
    selfHost: 'No — cloud-only SaaS',
    strengths: 'Genuinely unlimited embedded viewers at flat fee — the most viewer-generous model in this range; strong RLS passthrough auth; mature ISV track record; unlimited reports on Standard+',
    weaknesses: '$800/mo annual entry is 16× Nubi Team ($49/mo) or 89× Nubi Starter ($9/mo); cloud-only; no in-browser compute; limited AI maturity; no self-host',
    sourceUrl: 'https://www.holistics.io/pricing/',
  },
  {
    name: 'Luzmo',
    isNubi: false,
    category: 'Embedded Analytics (MAU)',
    pricingModel: 'Monthly active users (MAU), EUR',
    entryPrice: '€495/mo (~$540) annual Starter',
    entryEst: false,
    seatModel: 'Unlimited registered users; billing based on monthly active users (MAU)',
    embedding: 'Core product; React SDK; white-label on Premium (€1,995/mo annual); AI NL queries (30/user/day on Premium)',
    selfHost: 'No — cloud-only SaaS',
    strengths: 'MAU-based model economical for apps with many registered but low-activity users; strong React SDK and component library; AI NL queries on Premium; good documentation',
    weaknesses: 'EUR-only pricing; MAU model can be unpredictable with engagement spikes; Premium at ~$2,175/mo is expensive; no open-source core; no self-host',
    sourceUrl: 'https://www.luzmo.com/pricing',
  },
  {
    name: 'Omni Analytics',
    isNubi: false,
    category: 'Full-Stack BI + Embed (via Explo)',
    pricingModel: 'Sales-only (no public price)',
    entryPrice: 'Est. ~$70/creator/mo; est. ~$420/viewer/yr',
    entryEst: true,
    seatModel: 'Per-creator for internal BI; per-viewer for embedded (estimates, not public)',
    embedding: 'Via Explo acquisition; embedded viewer pricing est. ~$420/user/year; Explo customers migrating',
    selfHost: 'No — cloud-only SaaS',
    strengths: 'Full-stack BI + embedded in one platform; strong SQL + no-code modeling; acquired Explo\'s embedded capabilities and customer base',
    weaknesses: 'No public pricing — requires sales call; per-viewer cost model (est.); Explo customers face migration uncertainty; relatively new combined product; no self-host',
    sourceUrl: 'https://aws.amazon.com/marketplace/pp/prodview-6ohhb7zzk5brq',
  },
  {
    name: 'GoodData',
    isNubi: false,
    category: 'Enterprise Analytics Platform',
    pricingModel: 'Sales-only, workspace-based (no public price)',
    entryPrice: 'Est. ~$1,500+/mo platform fee',
    entryEst: true,
    seatModel: 'Unlimited users; pricing by workspace count; contract-negotiated',
    embedding: 'Core strength; headless InsightView React components; strong multi-tenant; workspace-based; no public price',
    selfHost: 'Yes — GoodData Cloud Native (Kubernetes); commercial license required',
    strengths: 'Longest track record in embedded analytics (founded 2008); HIPAA and FedRAMP compliant; Kubernetes-native for on-prem enterprise; Analytics Lake for federated data; mature multi-tenant architecture',
    weaknesses: 'No public pricing; high implementation complexity; not accessible for SMB/early-stage SaaS; primarily enterprise-focused; pricing requires sales cycle',
    sourceUrl: 'https://www.gooddata.ai/pricing/',
  },
]

export const PRIMARY_ROWS: { label: string; hex: string; cube: string; nubi: string }[] = [
  {
    label: 'Shape',
    hex:  'Notebook + published apps',
    cube: 'Headless semantic layer + API',
    nubi: 'Batteries-included BI + embed — authoring and output included',
  },
  {
    label: 'Compute kernel',
    hex:  'Python per session, Hex cloud (10–30 s cold, per-minute billing)',
    cube: 'n/a — warehouse + Cube Store; hourly infra billing',
    nubi: 'DuckDB-WASM (SQL) in browser by default; on-demand server (E2B/Modal, scale-to-zero) for Python and heavy workloads',
  },
  {
    label: 'Result transport',
    hex:  'JSON via pandas — no Arrow path',
    cube: 'JSON / SQL API — no Arrow IPC',
    nubi: 'Arrow IPC over WebSocket — columnar, zero-copy to viz',
  },
  {
    label: 'Viz ceiling',
    hex:  'Plotly/SVG — chokes past ~50 k rows',
    cube: 'Bring-your-own frontend; no built-in viz',
    nubi: 'Apache ECharts (canvas) on Arrow buffers — fast on large result sets',
  },
  {
    label: 'Caching',
    hex:  'Per-session; weak cross-user sharing; no auto pre-agg',
    cube: 'Pre-aggs in Cube Store (hand-written schema required)',
    nubi: 'Content-hashed edge cache + automatic pre-aggregations mined from query log',
  },
  {
    label: 'Modeling tax',
    hex:  'Medium — notebook cells; no formal semantic layer',
    cube: 'High — must define cube schema (JS/YAML) before any query works',
    nubi: 'Low — point at a warehouse and go; auth-as-code in repo',
  },
  {
    label: 'Embedding',
    hex:  'Enterprise add-on only; bolt-on auth; not a core surface',
    cube: 'Core strength (headless only); JWT→SQL RLS; viewer seats $20+/month',
    nubi: 'Core product surface: <nubi-dashboard> plus cell-level <nubi-kpi>/<nubi-table>/<nubi-chart>; JWKS-native; no separate SDK',
  },
  {
    label: 'Pricing',
    hex:  'Per-seat + compute add-on (kernels cost real money)',
    cube: 'Per-developer + hourly infra (on top of seats)',
    nubi: 'No per-seat pricing at any tier: Starter $9/mo | Team $49/mo | Pro $149/mo | Enterprise from $1,000/mo. Pay for compute, storage, AI calls, and embed sessions — never for users.',
  },
]

/** Categories shown under "General BI tools". */
export const GENERAL_BI = [
  'General BI', 'Notebooks + Apps', 'Headless Semantic Layer', 'Enterprise BI (LookML)',
  'Viz Industry Standard', 'Microsoft Ecosystem BI', 'Open-Source BI', 'Data Canvas / Narrative BI',
]
/** Categories shown under "Embedded analytics specialists". */
export const EMBEDDED = [
  'Embedded Analytics SDK', 'Embedded Analytics Platform', 'Embedded Analytics (MAU)',
  'Full-Stack BI + Embed (via Explo)', 'Enterprise Analytics Platform', 'Embedded BI + Flows',
]

/** Matrix dimensions: a one-line statement of the Nubi edge for each. */
export const DIMENSION_EDGE: Record<string, { label: string; edge: string }> = {
  kernel:    { label: 'Compute kernel',    edge: 'DuckDB-WASM runs in the viewer’s browser' },
  transport: { label: 'Result transport',  edge: 'Arrow IPC over WebSocket, zero-copy to charts' },
  viz:       { label: 'Viz and row ceiling', edge: 'ECharts on canvas, straight off Arrow buffers' },
  caching:   { label: 'Caching and pre-agg', edge: 'Edge cache plus automatic pre-aggregations from the query log' },
  embedding: { label: 'Embedding model',   edge: 'Core surface: <nubi-dashboard>, JWKS-native' },
  modeling:  { label: 'Modeling tax',      edge: 'Point at a warehouse and go; auth as code' },
  ai:        { label: 'AI features',       edge: 'Lineage-grounded LLM and an MCP server' },
  pricing:   { label: 'Pricing model',     edge: 'Usage-based, unlimited seats' },
  selfHost:  { label: 'Self-host',         edge: 'Docker Compose stack and a self-host connector' },
}
export const DIMENSION_ORDER = ['kernel', 'transport', 'viz', 'caching', 'embedding', 'modeling', 'ai', 'pricing', 'selfHost']

/** Orchestration comparison: Nubi Flows against four orchestrators. */
export const ORCH_COLUMNS = [
  { key: 'Prefect', subtitle: 'Python decorators' },
  { key: 'Apache Airflow', subtitle: 'DAG operators' },
  { key: 'Dagster', subtitle: 'asset-centric' },
  { key: 'n8n', subtitle: 'visual automation' },
]

export const ORCH_ROWS: Record<string, string>[] = [
{
  label: 'DAG definition',
  nubi: 'Declarative JSON FlowSpec; visual React Flow canvas; LLM can author flows in NL',
  Prefect: '@flow/@task Python decorators; code-only; no visual builder',
  'Apache Airflow': 'Python DAG files with Operators; global-scope instantiation; no visual builder',
  Dagster: '@asset + @job Python; Software-Defined Assets; Dagit UI (read-only graph)',
  n8n: 'Visual drag-and-drop node canvas; 400+ pre-built integration nodes',
},
{
  label: 'Execution infra',
  nubi: 'Postgres SKIP LOCKED — no Redis, no Celery, no K8s required',
  Prefect: 'Postgres metadata DB + customer-managed workers (Docker, K8s, cloud VMs)',
  'Apache Airflow': 'Postgres + Redis/RabbitMQ (Celery) or Kubernetes; significant DevOps overhead',
  Dagster: 'Postgres + dagster-daemon + Dagit; no Redis; Dagster+ Serverless $0.01/min',
  n8n: 'Node.js + Postgres or SQLite; no broker; execution capped by cloud plan tier',
},
{
  label: 'RLS / multi-tenant',
  nubi: 'JWT claims flow to every query/agent task; org-scoped execution; cross-org 404',
  Prefect: 'None — flows run as a service account; no per-user data isolation',
  'Apache Airflow': 'None — single execution context; UI RBAC only',
  Dagster: 'None natively; Dagster+ RBAC per deployment; no JWT-scoped task execution',
  n8n: 'None — single service credential; Projects for workspace separation only',
},
{
  label: 'LLM / agent tasks',
  nubi: 'Native agent task kind; AI tools create/run/generate flows in NL; MCP-compatible',
  Prefect: 'No native kind; PythonTask can call any LLM API; no built-in MCP',
  'Apache Airflow': 'No native kind; PythonOperator + community LLM providers; no built-in MCP',
  Dagster: 'No native kind; @asset can call LLM APIs; community OpenAI/Anthropic resources',
  n8n: 'First-class AI Agent + LLM Chain nodes; 12+ LLM providers; AI Workflow Builder',
},
{
  label: 'Self-host infra',
  nubi: 'Runs inside Nubi (uses existing Postgres); no additional broker',
  Prefect: 'Prefect Server: Postgres only (Apache 2.0). Workers: customer-managed.',
  'Apache Airflow': 'Postgres + Redis (Celery) or Kubernetes; managed options from ~$300–$1,400/month',
  Dagster: 'Postgres + dagster-daemon + Dagit (no Redis); Dagster+ cloud available',
  n8n: 'Docker + Postgres/SQLite; fair-code license; Business license needed for SSO/Git',
},
{
  label: 'Pricing',
  nubi: 'Included in Nubi usage-based pricing; no separate orchestration SKU',
  Prefect: 'Hobby free; paid from ~$75–$100/month (seat-based); compute separate',
  'Apache Airflow': 'OSS free; managed from ~$100/month (Astronomer) to $1,400/month (MWAA large)',
  Dagster: 'OSS free; Dagster+ Solo $10/month + $0.040/credit; Starter $100/month + $0.035/credit',
  n8n: 'Community free (self-host); Cloud Starter €20/month (2,500 exec); Pro €50/month (10k exec)',
},
]
