/**
 * Competitor comparison data for Nubi's comparison page.
 *
 * Web-researched on 2026-06-04. Prices and features change frequently —
 * re-verify before publishing and at least once per quarter.
 *
 * Key sources consulted:
 *   Hex:      https://hex.tech/pricing/
 *   Cube:     https://cube.dev/pricing  |  https://cube.dev/docs/product/administration/pricing
 *   Metabase: https://www.metabase.com/pricing/
 *   Looker:   https://cloud.google.com/looker/pricing  |  https://cloud.google.com/looker
 *   Sigma:    https://www.sigmacomputing.com/product/architecture
 *             https://qrvey.com/blog/sigma-pricing/
 *   Tableau:  https://www.tableau.com/pricing  (403'd — data from secondary sources)
 *             https://www.toucantoco.com/en/blog/tableau-pricing
 *             https://www.usedatabrain.com/blog/tableau-embedded-analytics-pricing
 *   Power BI: https://www.microsoft.com/en-us/power-platform/products/power-bi/pricing
 *             https://azure.microsoft.com/en-us/pricing/details/power-bi-embedded/
 *             https://powerbiconsulting.com/blog/power-bi-pricing-licensing-guide-2026
 *   Preset:   https://preset.io/pricing/
 */

// ---------------------------------------------------------------------------
// NUBI — sourced from ROADMAP.md §1-2
// ---------------------------------------------------------------------------
export const NUBI = {
  name: "Nubi",
  tagline: "Browser-first analytics kernel — marginal cost per dashboard view ≈ $0",
  kernel:
    "Pyodide (Python) + DuckDB-WASM in the browser by default; on-demand server kernel (E2B/Modal, scale-to-zero) only for workloads that need it. Cost driver: connector throughput + embed views + AI calls + kernel-seconds.",
  transport:
    "Arrow IPC over WebSocket — columnar buffers land directly in the browser; viz reads buffers without re-serialisation.",
  viz:
    "WebGL/WebGPU on Arrow buffers via regl; <nubi-chart> auto-upgrades to WebGL above a configurable row threshold. 1M+ point scatter at 60 fps. LLM-authorable HTML/CSS dashboards with sanitised custom elements (<nubi-kpi>, <nubi-table>, <nubi-chart>).",
  caching:
    "Content-hashed edge cache keyed on (serialised plan + RLS-affecting JWT claims) — N viewers of the same dashboard collapse to 1 warehouse hit. Automatic pre-aggregations mined from query log (rollup suggester + routing).",
  embedding:
    "Core product surface: read-only <nubi-dashboard> (iframe + web component, JWT-scoped, CSS-var theming) → cell-level <nubi-widget> → embedded <nubi-editor> → headless PNG/PDF render → bring-your-own-frontend (engine as library). Auth-as-code: host publishes JWKS, implements getToken(), mounts component. No separate embed SDK required.",
  modeling:
    "Low — point at a warehouse and go. No hand-written semantic model required to start. Auth policies live as TypeScript/SQL in the repo (diffable, PR-reviewable). SQL-first connector SDK; Python connector SDK for arbitrary Arrow-returning sources.",
  ai:
    "Lineage-indexed retrieval + LLM generation grounded on real catalog. POST /ai/ask. MCP server (4 tools): agents author dashboards via HTML/CSS + <nubi-*> custom elements. AI calls metered per-token (cost passed through at margin). LLM-authorable dashboard output is HTML/CSS sanitised by DOMPurify.",
  pricing:
    "Connector throughput (bytes/queries) + embed views (per-thousand) + AI calls (per-token) + on-demand kernel time (per-second, scale-to-zero) + scheduled jobs (separate SKU). Generous free tier structurally viable (browser compute is free to Nubi). Billed in ZAR via Paystack.",
  selfHost:
    "Yes — fully self-hosted (VPC, regulated) planned in M10 (Docker Compose stack). Intermediate: hosted control plane + self-hosted connector so warehouse creds never leave customer network.",
  strength:
    "Near-zero marginal cost per embedded view (compute is the user's browser). Arrow IPC + WebGL enables 1M+ point rendering. Auto pre-aggregations match Cube's core weapon without requiring hand-written semantic model. Auth-as-code with JWT/JWKS is simpler than bolt-on embed SDKs.",
  limitation:
    "Browser memory cap (~4GB) requires aggressive pushdown. Pyodide native-wheel gaps mean on-demand kernel is a launch requirement, not optional. NoSQL deliberately out of scope. M10 self-host stack not yet shipped.",
};

// ---------------------------------------------------------------------------
// COMPETITORS
// ---------------------------------------------------------------------------
export const COMPETITORS = [
  {
    name: "Hex",
    tagline: "Collaborative data notebooks + published apps",
    kernel:
      "Python kernel per session, running in Hex's cloud. Cold starts 10–30 s on free/team tiers. Compute billed per-minute of active execution: Large 16 GB $0.32/hr, XL 32 GB $0.65/hr, 2XL 64 GB $1.29/hr, GPU (A10G) $4.06/hr. Medium compute included on all paid plans.",
    transport:
      "JSON responses via pandas DataFrames. No native Arrow IPC path for dashboard viewers.",
    viz:
      "Plotly/SVG-based charts. Performance degrades past ~50k rows in the browser. No native WebGL path. Custom themes per plan (1 on Professional, 5+ on Team).",
    caching:
      "Per-session result caching; weak cross-user cache sharing. No automatic pre-aggregation layer. Results are re-computed per new session by default.",
    embedding:
      "Available as Enterprise add-on only; not a core product surface. Embed customers must contact sales. Bolt-on auth rather than JWKS-native. Noted as expensive relative to core seat pricing.",
    modeling:
      "Medium — notebook cells are the modeling unit. Team tier adds a Semantic Model agent (AI-assisted). No mandatory schema-definition step, but no formal semantic layer either.",
    ai:
      "Magic AI (LLM query suggestions grounded in schema + query history). Notebook Agent (Professional+), Threads Agent (Team+), Semantic Model Agent (Team+). Monthly credit grants per paid seat for AI features.",
    pricing:
      "Per-seat: Community free (up to 5 notebooks); Professional $36/editor/month; Team $75/editor/month; Enterprise custom. Pay-as-you-go compute add-on for Team/Enterprise. Embedding requires Enterprise tier.",
    selfHost: "No — Hex is cloud-only (SaaS). No self-host option.",
    strength:
      "Best-in-class collaborative Python notebook UX; strong AI-assisted analysis; broad data source connectivity.",
    limitation:
      "Per-session cloud kernel is the main cost driver — scales linearly with concurrent users. Embedding is expensive and bolt-on. SVG viz chokes past ~50k rows. No self-host option.",
    sourceUrls: [
      "https://hex.tech/pricing/",
      "https://checkthat.ai/brands/hex/pricing",
      "https://www.vendr.com/marketplace/hex-technologies",
    ],
  },

  {
    name: "Cube",
    tagline: "Headless semantic layer + API for embedded analytics",
    kernel:
      "No compute kernel in the notebook sense — data plane runs in the customer's warehouse + Cube Store (materialised pre-aggregations). Cube Cloud infra billed hourly: Dedicated deployment $0.60–$1.20/hr; additional API instances $0.15–$0.30/hr; Cube Store caching worker $0.15–$0.30/hr; Multi-cluster $1.20/hr per cluster.",
    transport:
      "JSON / REST + GraphQL SQL API. No native Arrow IPC. Results are JSON rows; consumers must serialise for their own frontends.",
    viz:
      "Bring-your-own frontend — Cube is headless. No built-in viz layer. Consumers use React, Recharts, D3, etc. No row ceiling imposed by Cube itself (warehouse and downstream frontend set limits).",
    caching:
      "Pre-aggregations in Cube Store (materialised rollup tables, up to 150 GB on Starter). Strong, explicit pre-agg model — but requires hand-written cube schema to define them. In-memory cache layer also available.",
    embedding:
      "Core strength: headless API means any frontend can embed. Embedded analytics chat and embedded dashboards on Premium+. Security context: JWT → SQL filters for row-level security. Viewer seats $20/user/month (Premium+), Explorer $40/user/month.",
    modeling:
      "High — must define cube schema (data models) before querying. Proprietary schema language (JavaScript/YAML). Semantic Layer Sync with BI tools (1 tool on Starter, unlimited on Enterprise). Cube Copilot assists model authoring.",
    ai:
      "Cube Copilot (model design assistant). Text-to-semantic-layer query via LLM API. AI token costs passed through from provider at no markup. Enterprise: Bring Your Own LLM.",
    pricing:
      "Free tier (hobbyists). Starter: $40/developer/month. Premium: $80/developer/month + Explorer $40/user + Viewer $20/user. Enterprise: custom. Infra billed separately per-hour on top of seats. Consumption-based infra costs can be significant at scale.",
    selfHost:
      "Yes — Cube Core is open source (MIT license). Self-hosted requires Redis, Cube Store cluster, API instances. Cube Cloud adds managed HA, advanced caching, observability.",
    strength:
      "Gold-standard pre-aggregation engine; headless architecture suits any frontend; strong JWT/RLS security model; open-source core.",
    limitation:
      "High modeling tax — must write cube schema before any query works. Headless = no built-in viz or authoring. Infra billing on top of seats can surprise. JSON transport only.",
    sourceUrls: [
      "https://cube.dev/pricing",
      "https://cube.dev/docs/product/administration/pricing",
      "https://cube.dev/product/cube-core",
      "https://github.com/cube-js/cube",
    ],
  },

  {
    name: "Metabase",
    tagline: "Simple self-serve BI for non-technical users",
    kernel:
      "Queries pushed to the connected warehouse/database. Metabase server executes SQL against the source; no in-browser compute. Transform runs metered (1k included on Starter, then $0.01/run).",
    transport:
      "JSON rows from server to browser. No Arrow IPC. Results loaded into browser memory via standard HTTP responses.",
    viz:
      "40+ chart types via SVG/Canvas. No WebGL path. Row limits: browser performance degrades past tens of thousands of rows in a single chart. Data Studio (v59, March 2026) adds a semantic layer workbench.",
    caching:
      "Basic query/model caching on all tiers; granular result caching and duration control on Pro/Enterprise. Preemptive caching added in v53 (Feb 2025). No automatic pre-aggregation — manual model persistence only.",
    embedding:
      "Open Source and Starter: static embedding with 'Powered by Metabase' branding. Pro/Enterprise: white-label, modular embedding SDK (React), full-app embedding, AI in embeds. Every authenticated viewer counts as a paid seat ($12/user/month on Pro).",
    modeling:
      "Low-to-medium — point-and-click question builder for non-technical users; SQL editor for power users. Data Studio (2026) adds curated semantic layer. Models are SQL-defined views with metadata.",
    ai:
      "Metabot: natural-language → SQL → viz. Available all tiers. AI SQL generation in Open Source since March 2026. AI-powered semantic search on Pro/Enterprise. Bring-your-own API key (free); Metabase AI service $3.75/1M tokens (Cloud only, first 1M complimentary/month). MCP server: users pay their AI provider.",
    pricing:
      "Open Source: free (self-host only, AGPL v3). Starter: $100/month + $6/user/month (5 users included). Pro: $575/month + $12/user/month (10 users included). Enterprise: from $20k/year. No viewer-only tier — every user is full seat. 1,000 embedded viewers = ~$149k/year at Pro rates.",
    selfHost:
      "Yes — Open Source (AGPL v3) is free to self-host. Pro self-hosted carries same license fee as cloud Pro plus your own infra ($100–$200/month typical). Enterprise self-hosted available.",
    strength:
      "Lowest barrier to entry for non-technical users; strong open-source community; free self-host; new Data Studio semantic layer (2026).",
    limitation:
      "No viewer-only pricing tier — every embedded viewer is a full paid seat, making large-scale embedding very expensive. No WebGL or Arrow path. Pre-aggregation is manual. Open Source requires AGPL compliance.",
    sourceUrls: [
      "https://www.metabase.com/pricing/",
      "https://www.metabase.com/features/semantic-layer",
      "https://coefficient.io/metabase-pricing",
    ],
  },

  {
    name: "Looker",
    tagline: "Enterprise semantic layer + governed BI (Google Cloud)",
    kernel:
      "All queries pushed to the connected warehouse (BigQuery-native, but supports many). Looker server compiles LookML → SQL. No in-browser compute. BigQuery compute billed separately at $5/TB processed (often $50k–$200k/year additional). AI (Conversational Analytics) measured in Data Tokens: $3/1M input, $20/1M output (billing starts Oct 2026; free until then).",
    transport:
      "JSON rows via Looker API / iframes. No native Arrow IPC. Large result sets transported as paginated JSON.",
    viz:
      "Extensive chart library (SVG/Canvas). No WebGL. Row limits per chart: practical UI degradation past ~100k rows. Visualization Assistant (Gemini) helps customize charts via natural language.",
    caching:
      "PDT (Persistent Derived Tables) for pre-computed aggregates, defined in LookML. Caching tied to LookML layer — requires semantic model to leverage effectively. Result caching per query fingerprint.",
    embedding:
      "Embed edition is a distinct product SKU (500k query-based API calls/month, 100k admin calls). iFrame + signed URLs for embedded dashboards. Strong JWT-based user attribute → row-level security via LookML access_filter. No public pricing — sales-negotiated. Known entry point ~$60k/year for Standard.",
    modeling:
      "Very high — LookML is a proprietary modeling language requiring dedicated engineering. Organisations spend est. 40–60% of total Looker investment on LookML dev/maintenance. LookML Assistant (Gemini) helps generate model code. Knowledge Catalog (2026) adds semantic graph for AI agents.",
    ai:
      "Gemini in Looker: Conversational Analytics (follow-up questions, forecasting, anomaly detection), LookML Assistant (code gen), Visualization Assistant, Code Interpreter (Python). Knowledge Catalog for agentic BI (Next '26). AI tokens billed separately after Oct 2026.",
    pricing:
      "No published pricing — contact sales. Analyst estimates: Standard $60k/year (10 Standard + 2 Developer users); Enterprise higher. Per-user add-ons: Developer ~$1,665/year, Standard ~$400/year, Viewer ~$200/year (analyst estimates, unverified). BigQuery compute billed separately. Annual commitment required.",
    selfHost:
      "No — Looker is fully cloud-hosted on Google Cloud (since 2019 acquisition). No on-premise option. Managed by Google.",
    strength:
      "Gold-standard enterprise semantic layer (LookML); deep Google Cloud / BigQuery integration; strong governance and access control; Gemini AI native.",
    limitation:
      "Extremely high LookML modeling tax; no published pricing (opaque sales process); BigQuery compute costs stack on top of license; no self-host; no Arrow IPC; embedding is a separate expensive SKU.",
    sourceUrls: [
      "https://cloud.google.com/looker/pricing",
      "https://cloud.google.com/looker",
      "https://www.shearwaterdata.com/blog/looker-pricing-total-cost-breakdown-explained",
      "https://www.holistics.io/blog/looker-pricing/",
      "https://cloud.google.com/blog/products/business-intelligence/looker-updates-for-agentic-bi-at-next26",
    ],
  },

  {
    name: "Sigma Computing",
    tagline: "Spreadsheet-UX live query BI on top of your warehouse",
    kernel:
      "All compute pushes to the connected warehouse (Snowflake-first, also BigQuery, Databricks, Redshift). Every spreadsheet operation (pivot, filter, formula) compiles to warehouse-native SQL. Hybrid query engine evaluates cheapest execution path: browser cache → query ID cache → warehouse result cache → materialization → optimized pushdown.",
    transport:
      "Warehouse results delivered to browser via Sigma's cloud as JSON rows. Paginated — 10,000 rows dispatched per page for browser viewing. No Arrow IPC.",
    viz:
      "Spreadsheet-style workbook UI; 30+ chart types. No WebGL renderer — SVG/Canvas. Billion-row datasets queryable at warehouse (no Sigma-side row ceiling), but browser rendering limited to paginated 10k rows. No GPU-accelerated viz.",
    caching:
      "Multi-tier: browser session cache → query ID fingerprint cache → Snowflake/BigQuery native result cache (~24 hrs) → Sigma Materialization (pre-computed warehouse tables) → Sigma Alpha Query optimised pushdown. Materialization is manual (workbook-triggered), not automatic query-log mining.",
    embedding:
      "Embedded analytics available as add-on (Essential, Business, Enterprise tiers). Can double or triple base contract value. Custom pricing negotiated with sales. JWT-based authentication for embedded views. No public pricing.",
    modeling:
      "Low-to-medium — Data Models provide reusable tables, relationships, metrics, role-based permissions, version tagging, all in no-code UI. No proprietary schema language. Compiles to warehouse SQL automatically.",
    ai:
      "AI features for text-to-SQL, chart narration. Warehouse-native AI function invocation (Snowflake Cortex, Databricks AI, BigQuery ML) without data extraction. Specific AI product name/pricing not independently verified — unverified.",
    pricing:
      "No published pricing — negotiated with sales. Sigma introduced 4-tier license model March 2025: View, Act, Analyze, Build. Median annual contract: $61,158 (range $17.5k–$131k, 117 contracts per Vendr). Essentials from ~$300/month (unlimited users). Creator/Build licenses est. $2,000–$3,500/user/year. Embedding significantly increases contract value.",
    selfHost:
      "No — Sigma is fully cloud-hosted SaaS. No self-host option.",
    strength:
      "Familiar spreadsheet UX removes BI learning curve; live warehouse queries with no data copies; strong Snowflake/Databricks integration; no per-seat ceiling for viewer counts on some tiers.",
    limitation:
      "All compute costs land on customer's warehouse bill (live query model = warehouse spend driver). Embedding pricing opaque and reportedly expensive. No Arrow IPC; browser limited to 10k rows per page. No self-host. AI features details unverified.",
    sourceUrls: [
      "https://www.sigmacomputing.com/product/architecture",
      "https://qrvey.com/blog/sigma-pricing/",
      "https://checkthat.ai/brands/sigma-computing/pricing",
      "https://help.sigmacomputing.com/docs/caching-and-data-freshness",
    ],
  },

  {
    name: "Tableau",
    tagline: "Industry-standard visual analytics with deep Salesforce integration",
    kernel:
      "Queries run against live connection or Tableau Extracts (.hyper format, columnar in-memory engine). Extract engine is proprietary Hyper — not the warehouse. Live connections push SQL to warehouse. Tableau Next (2026) eliminates consumption-based pricing for data queries; Cloud+ adds Tableau Agent (agentic BI).",
    transport:
      "Proprietary Hyper/VizQL protocol (not Arrow IPC). JSON for API interactions. Results rendered server-side or in Tableau's browser runtime. No open Arrow path.",
    viz:
      "Best-in-class drag-and-drop chart authoring; 40+ chart types; SVG/Canvas rendering. Performance degrades past ~1–2M rows in extract. No WebGL GPU path. Tableau Agent (Cloud+) generates and explains viz via natural language.",
    caching:
      "Hyper extract caching (in-memory columnar). Live connections rely on warehouse caching. No automatic pre-aggregation in the Cube/Nubi sense — manual extract refresh scheduling.",
    embedding:
      "Embedded analytics via iframes + Tableau Embedding API v3. Cloud Standard: Viewer $15, Explorer $42, Creator $75 /user/month. Enterprise: Viewer $35, Explorer $70, Creator $115. OEM/SaaS embedding: custom-quoted, year-1 floor ~$60k–$150k. Every viewer is a paid seat.",
    modeling:
      "Medium — drag-and-drop calculated fields, LOD expressions; no proprietary schema language required. Published data sources act as a light semantic layer. No formal semantic layer comparable to LookML/Cube.",
    ai:
      "Tableau Agent (Cloud+ and Tableau+ Bundle): dashboard narratives, agentic analytics, Pulse (metric monitoring with NL explanations). Tableau Next: role-based pricing, no consumption charges for queries/transforms/agentic calls. Standard/Enterprise: no Tableau Agent. AI tier (Cloud+/Next) requires sales contact.",
    pricing:
      "Tableau Cloud Standard: Viewer $15, Explorer $42, Creator $75 /user/month (annual). Enterprise: Viewer $35, Explorer $70, Creator $115. Tableau Next: Creator $40/user/month (role-based, no consumption). Cloud+ (agentic, Tableau Agent): contact sales. OEM embedding: from $60k–$150k/year. Free tier: Tableau Public (public data only, no private data).",
    selfHost:
      "Yes — Tableau Server (on-premise or self-managed cloud). Server pricing: Creator $70/user/month. Requires dedicated server infrastructure. Salesforce is pushing customers toward Tableau Cloud.",
    strength:
      "Unmatched visualization breadth and polish; massive user community and training ecosystem; Hyper engine fast for extracts; Salesforce CRM integration native.",
    limitation:
      "Every viewer is a paid seat — no viewer-only pricing for embeds below Enterprise OEM. Proprietary VizQL/Hyper not open. High cost at scale. No Arrow IPC. No automatic pre-aggregation. Cloud+ AI tier is opaque/contact-sales.",
    sourceUrls: [
      "https://www.toucantoco.com/en/blog/tableau-pricing",
      "https://www.usedatabrain.com/blog/tableau-embedded-analytics-pricing",
      "https://qrvey.com/blog/tableau-pricing/",
      "https://redresscompliance.com/tableau-pricing-2026-creator-explorer-viewer",
    ],
  },

  {
    name: "Power BI",
    tagline: "Microsoft's BI platform — deep Office/Azure integration, Copilot AI",
    kernel:
      "Import mode: data loaded into Power BI's in-memory VertiPaq engine (columnar compression). DirectQuery mode: pushes DAX → SQL to connected source. Fabric capacity (F-SKUs) adds Spark, notebooks, lakehouses. Copilot requires F2+ capacity (~$262/month). Compute driver: Fabric F-SKU capacity tier (not per-user for viewers in app-owns-data embedding).",
    transport:
      "Proprietary VertiPaq binary + DAX query protocol. JSON for REST API. No Arrow IPC. Reports streamed as rendered tiles or DAX results.",
    viz:
      "100+ built-in visuals; AppSource marketplace for custom visuals. Canvas rendering (no WebGL GPU path). No hard row ceiling on Import (limited by RAM/capacity), DirectQuery performance degrades at very large cardinalities. Copilot auto-generates visuals from NL.",
    caching:
      "VertiPaq in-memory columnar compression (Import mode) — very fast repeat queries. Fabric F-SKUs add intelligent caching. Refresh: Free/Pro 8×/day, Premium 48×/day. No automatic pre-aggregation in the warehouse-side sense.",
    embedding:
      "App-owns-data embedding via Fabric F-SKUs — no per-viewer license needed at any F-tier. F-SKU capacity covers viewers. A-SKU legacy still available ($1.01–$32.25/hr, pause-on-idle). P-SKU (Premium) being retired (new sales ended July 2024). F4/F8 supports ~100 concurrent users (~$400–$800/month PAYG); F16 ~500 concurrent (~$1,300/month).",
    modeling:
      "Medium — DAX + Power Query M; familiar to Excel users. No proprietary schema language beyond DAX. Calculated tables, measures, row-level security defined in the model. Relatively low learning curve vs LookML.",
    ai:
      "Copilot for Power BI: DAX formula generation, visual creation, report summarization, NL narratives (GPT-4 architecture via Azure OpenAI). Included in Fabric capacity at no extra charge (currently). Q&A feature retiring Dec 2026, replaced by Copilot. Copilot available from F2 (~$262/month) since April 2025 (previously required F64).",
    pricing:
      "Free: create/view only, no sharing. Pro: $14/user/month (raised from $10 in April 2025). Premium Per User (PPU): $24/user/month. Fabric F-SKU (capacity, covers embedding): F2 ~$262/month to F128 ~$16,768/month (PAYG). A-SKU legacy embedding: $1.01–$32.25/hour. Microsoft 365 E5 includes Power BI Pro.",
    selfHost:
      "Power BI Report Server: on-premise/self-hosted, included with Power BI Premium or SQL Server EE with SA. Feature lag vs cloud (typically 3–6 months behind).",
    strength:
      "Best price-performance for Microsoft shops (included in M365 E5); Copilot AI at no extra charge; capacity-based embedding removes per-viewer cost; huge connector library; Excel familiarity.",
    limitation:
      "Lock-in to Microsoft/Azure ecosystem. Import mode requires scheduled refresh (data staleness). VertiPaq not open; no Arrow IPC. Copilot requires F-SKU capacity (not available on Pro). A-SKU and P-SKU retirement causes migration complexity. Q&A retiring Dec 2026.",
    sourceUrls: [
      "https://www.microsoft.com/en-us/power-platform/products/power-bi/pricing",
      "https://azure.microsoft.com/en-us/pricing/details/power-bi-embedded/",
      "https://powerbiconsulting.com/blog/power-bi-pricing-licensing-guide-2026",
      "https://datatako.com/blog/power-bi-embedded-complete-2026-guide",
      "https://learn.microsoft.com/en-us/fabric/fundamentals/copilot-fabric-overview",
    ],
  },

  {
    name: "Preset / Apache Superset",
    tagline: "Open-source BI (Apache Superset) with a managed cloud option (Preset)",
    kernel:
      "Queries executed server-side against connected databases/warehouses. No in-browser compute engine. Superset server pushes SQL to the source. No separate kernel billing — infra cost is the user's own server or Preset's cloud subscription.",
    transport:
      "JSON rows from server to browser via HTTP. No Arrow IPC. Chart data fetched as JSON; browser renders with ECharts/D3.",
    viz:
      "40+ chart types via ECharts (Canvas/SVG). No WebGL GPU path for scatter at scale. Cross-filtering supported. Viz row limits: practical browser degradation past tens of thousands of rows per chart.",
    caching:
      "Query result caching via Redis (configurable TTL). Dashboard-level caching. No automatic pre-aggregation layer — requires external dbt/Cube for pre-agg. Superset Jinja templating for dynamic SQL filters.",
    embedding:
      "Embedded via iframe + Guest Token (server-signed JWT). Embedded SDK available. Row-level security via RLS rules in Superset. Preset: embedded viewer licenses from $500/month for 50 viewers. SSO integration supported.",
    modeling:
      "Low — virtual datasets (SQL-defined views) + metrics. No formal semantic layer language. Datasets act as a light modeling layer. No LookML equivalent.",
    ai:
      "Superset's NL-to-SQL (Text-to-Viz) via optional LLM integration. Preset cloud adds AI features on higher tiers. Open Source: community plugins for AI; no native GA AI as of 2025. Mostly unverified — Preset AI roadmap not publicly detailed.",
    pricing:
      "Apache Superset: 100% free and open source (Apache 2.0 license). Preset Cloud: Starter free forever (up to 5 users); Professional $20/user/month (billed annually) or $25/month; Enterprise: custom. Embedded viewer licenses: from $500/month for 50 viewers (Preset Professional+). Self-managed Preset Certified Superset and Managed Private Cloud on Enterprise.",
    selfHost:
      "Yes — Apache Superset is free to self-host. Docker Compose and Kubernetes Helm charts available. Preset offers a managed self-hosted 'Certified Superset' option on Enterprise.",
    strength:
      "Fully open source (no license cost); Apache 2.0 license (no AGPL compliance burden); large community; low barrier to start; ECharts viz library is capable.",
    limitation:
      "No Arrow IPC; no WebGL GPU rendering; no automatic pre-aggregation; no formal semantic layer; embedded viewer pricing on Preset can add up quickly; AI features limited/unverified; self-hosting requires significant DevOps investment.",
    sourceUrls: [
      "https://preset.io/pricing/",
      "https://superset.apache.org/",
      "https://www.metabase.com/blog/vs-superset",
      "https://embeddable.com/blog/metabase-pricing",
    ],
  },
];

// ---------------------------------------------------------------------------
// COMPARE_DIMENSIONS — the rows of the comparison matrix
// ---------------------------------------------------------------------------
export const COMPARE_DIMENSIONS = [
  {
    key: "kernel",
    label: "Compute Kernel",
    description:
      "Where analytics compute runs and what drives cost. Browser-side = near-zero marginal cost per view; cloud kernel = billed per session/second/CU.",
  },
  {
    key: "transport",
    label: "Result Transport",
    description:
      "Wire format used to move query results to the frontend. Arrow IPC is columnar and zero-copy; JSON adds serialisation overhead and memory pressure at scale.",
  },
  {
    key: "viz",
    label: "Viz Capability & Row Ceiling",
    description:
      "Rendering technology and practical row limit before performance degrades. WebGL GPU rendering handles 1M+ points; SVG/Canvas degrades past ~50k–100k rows.",
  },
  {
    key: "caching",
    label: "Caching & Pre-aggregation",
    description:
      "How repeated queries are served without hitting the warehouse every time. Auto pre-agg vs manual; edge cache vs server cache; cross-user vs per-session.",
  },
  {
    key: "embedding",
    label: "Embedding Model",
    description:
      "How dashboards or analytics are embedded into third-party products. Core surface vs add-on; viewer pricing model; auth mechanism (JWT/JWKS vs bolt-on SDK).",
  },
  {
    key: "modeling",
    label: "Semantic Layer / Modeling Tax",
    description:
      "How much upfront schema or model definition is required before analysts can query data. High = LookML/cube schema required; low = point-and-go.",
  },
  {
    key: "ai",
    label: "AI Features",
    description:
      "Native AI/LLM capabilities: text-to-SQL, NL query, dashboard generation, agentic BI. Grounded AI (lineage-aware) vs generic SQL generation.",
  },
  {
    key: "pricing",
    label: "Pricing Model",
    description:
      "Cost drivers: per-seat vs capacity vs usage-based. Existence and generosity of free tier. Viewer pricing for embedded deployments.",
  },
  {
    key: "selfHost",
    label: "Self-Host Option",
    description:
      "Whether the tool can be fully self-hosted in a customer's own infrastructure, and the cost/complexity of doing so.",
  },
];

// ---------------------------------------------------------------------------
// MATRIX — compact per-dimension cell values for Nubi + each competitor
// Short strings suitable for table cells. "unverified" = not independently confirmed.
// ---------------------------------------------------------------------------
export const MATRIX = {
  // Rows = dimension keys; columns = tool names
  kernel: {
    Nubi:    "Pyodide+DuckDB-WASM in browser; on-demand server (E2B/Modal) only when needed",
    Hex:     "Python kernel per session, Hex cloud; 10–30s cold starts; per-minute billing",
    Cube:    "No kernel; warehouse + Cube Store for pre-aggs; hourly infra billing",
    Metabase:"Server-side SQL push to warehouse; no in-browser compute",
    Looker:  "Warehouse pushdown; Looker cloud compiles LookML → SQL; no in-browser compute",
    Sigma:   "Warehouse pushdown (Snowflake-first); hybrid browser/warehouse exec path",
    Tableau: "Hyper extract engine (in-memory) or live warehouse pushdown (DirectQuery)",
    "Power BI": "VertiPaq in-memory (Import) or DirectQuery; Fabric F-SKU capacity",
    "Preset / Superset": "Server-side SQL push to warehouse; no in-browser compute",
  },
  transport: {
    Nubi:    "Arrow IPC over WebSocket (columnar, zero-copy to browser viz)",
    Hex:     "JSON via pandas; no Arrow path",
    Cube:    "JSON / REST+GraphQL SQL API; no Arrow IPC",
    Metabase:"JSON rows over HTTP",
    Looker:  "JSON via Looker API / iFrame; no Arrow IPC",
    Sigma:   "JSON rows, paginated 10k at a time; no Arrow IPC",
    Tableau: "Proprietary VizQL/Hyper protocol; JSON for API; no Arrow",
    "Power BI": "Proprietary VertiPaq/DAX protocol; JSON REST API; no Arrow IPC",
    "Preset / Superset": "JSON rows over HTTP; no Arrow IPC",
  },
  viz: {
    Nubi:    "WebGL/WebGPU (regl) on Arrow buffers; 1M+ pts at 60fps; auto-upgrade above row threshold",
    Hex:     "Plotly/SVG; degrades past ~50k rows; no WebGL",
    Cube:    "Bring-your-own frontend; no built-in viz",
    Metabase:"40+ charts SVG/Canvas; no WebGL; degrades past tens-of-thousands rows",
    Looker:  "Extensive chart library SVG/Canvas; Viz Assistant (Gemini); no WebGL",
    Sigma:   "Spreadsheet + 30+ charts SVG/Canvas; browser limited to 10k rows/page; no WebGL",
    Tableau: "40+ chart types SVG/Canvas; Hyper fast for extracts; no WebGL GPU path",
    "Power BI": "100+ visuals Canvas; AppSource custom visuals; no WebGL GPU path",
    "Preset / Superset": "40+ ECharts SVG/Canvas; no WebGL; community custom charts",
  },
  caching: {
    Nubi:    "Content-hashed edge cache (plan+RLS claims key); automatic pre-agg from query log",
    Hex:     "Per-session result cache; weak cross-user sharing; no auto pre-agg",
    Cube:    "Pre-aggregations in Cube Store (hand-written schema required); in-memory cache",
    Metabase:"Query/model cache all tiers; granular caching Pro+; preemptive caching v53; no auto pre-agg",
    Looker:  "PDT (LookML-defined pre-computed tables); query result cache; requires LookML model",
    Sigma:   "6-tier hybrid: browser → query ID → warehouse cache → materialization → pushdown; manual materialization",
    Tableau: "Hyper in-memory extract cache; live connections use warehouse cache; no auto pre-agg",
    "Power BI": "VertiPaq in-memory (Import mode); Fabric intelligent cache; refresh 8–48×/day",
    "Preset / Superset": "Redis query result cache (configurable TTL); no auto pre-agg; manual dbt/Cube needed",
  },
  embedding: {
    Nubi:    "Core surface: <nubi-dashboard> → <nubi-widget> → <nubi-editor>; JWKS-native; no separate SDK",
    Hex:     "Enterprise add-on only; bolt-on auth; expensive; not a core surface",
    Cube:    "Core strength (headless); JWT→SQL RLS; Viewer $20/user/month (Premium+)",
    Metabase:"Static embed (free/Starter w/ branding); white-label on Pro ($575+/month); every viewer = paid seat",
    Looker:  "Separate Embed edition SKU; iFrame + signed URLs; strong JWT RLS; no public price (~$60k+ entry)",
    Sigma:   "Add-on; doubles/triples contract; JWT auth; no public price; custom-quoted",
    Tableau: "Embedding API v3; every viewer = paid seat; OEM SaaS from $60k–$150k/year",
    "Power BI": "App-owns-data via Fabric F-SKU; no per-viewer license; F4 ~$400/month covers ~100 concurrent users",
    "Preset / Superset": "iframe + Guest Token; Preset viewer licenses from $500/month for 50 viewers",
  },
  modeling: {
    Nubi:    "Low — point at warehouse, go; auth-as-code (TypeScript/SQL in repo); no mandatory schema",
    Hex:     "Medium — notebook cells; AI Semantic Model agent (Team+); no formal semantic layer",
    Cube:    "High — must write cube schema (JS/YAML) before any query works; proprietary language",
    Metabase:"Low-medium — point-and-click + SQL; Data Studio semantic layer (v59, 2026)",
    Looker:  "Very high — LookML proprietary language; est. 40–60% of total investment in LookML dev",
    Sigma:   "Low-medium — no-code Data Models (tables, relationships, metrics, RLS) in spreadsheet UI",
    Tableau: "Medium — DAX/LOD expressions; published data sources as light semantic layer; no LookML",
    "Power BI": "Medium — DAX + Power Query M; familiar to Excel users; model-defined RLS",
    "Preset / Superset": "Low — virtual datasets (SQL views) + metrics; no formal semantic layer",
  },
  ai: {
    Nubi:    "Lineage-grounded LLM (catalog-anchored); /ai/ask; MCP server; LLM-authorable HTML/CSS dashboards",
    Hex:     "Magic AI (schema-grounded SQL); Notebook/Threads/Semantic agents; credit-based",
    Cube:    "Cube Copilot (model assist); text-to-semantic-layer; BYOLLM (Enterprise); token cost pass-through",
    Metabase:"Metabot NL→SQL (all tiers); AI SQL gen in OSS since March 2026; $3.75/1M tokens (managed)",
    Looker:  "Gemini: Conversational Analytics, LookML Assistant, Viz Assistant, Code Interpreter; Data Tokens $3–$20/1M",
    Sigma:   "Text-to-SQL; warehouse-native AI (Cortex/Databricks AI/BigQuery ML); details unverified",
    Tableau: "Tableau Agent (Cloud+/Next only): narratives, agentic BI, Pulse; Standard/Enterprise: no Agent",
    "Power BI": "Copilot (GPT-4/Azure OpenAI): DAX gen, visual creation, NL narratives; included in F2+ capacity",
    "Preset / Superset": "NL-to-SQL via optional LLM plugin; Preset AI roadmap unverified; limited GA AI",
  },
  pricing: {
    Nubi:    "Usage-based: connector bytes + embed views + AI tokens + kernel-seconds; generous free tier",
    Hex:     "Per-seat: Community free; Professional $36/editor/month; Team $75/editor/month; compute add-on",
    Cube:    "Per-developer + hourly infra: Free hobbyist; Starter $40/dev/month; Premium $80/dev/month",
    Metabase:"Tiered: OSS free; Starter $100+$6/user/month; Pro $575+$12/user/month; Enterprise $20k+/year",
    Looker:  "No public pricing — contact sales; est. from $60k/year; per-user add-ons; BigQuery compute separate",
    Sigma:   "No public pricing — negotiated; median contract $61k/year; Essentials from ~$300/month",
    Tableau: "Per-seat: Standard Viewer $15, Explorer $42, Creator $75/month; Enterprise higher; Next $40/Creator; OEM from $60k–$150k",
    "Power BI": "Pro $14/user/month; PPU $24/user/month; Fabric F-SKU $262–$16,768/month; Free: create/view only",
    "Preset / Superset": "Superset: free OSS; Preset: Starter free (5 users); Pro $20/user/month; embed viewers from $500/month/50",
  },
  selfHost: {
    Nubi:    "Planned (M10 Docker Compose); intermediate: hosted control plane + self-hosted connector",
    Hex:     "No — cloud-only SaaS",
    Cube:    "Yes — Cube Core open source (MIT); production requires Redis + Cube Store cluster",
    Metabase:"Yes — OSS free (AGPL v3); Pro self-hosted same license fee as cloud",
    Looker:  "No — Google Cloud hosted only (since 2019)",
    Sigma:   "No — cloud-only SaaS",
    Tableau: "Yes — Tableau Server (on-premise/self-managed cloud); Creator $70/user/month",
    "Power BI": "Yes — Power BI Report Server (included with Premium or SQL Server EE with SA); feature lag",
    "Preset / Superset": "Yes — Apache Superset free self-host; Preset Certified Superset (managed self-host) on Enterprise",
  },
};
