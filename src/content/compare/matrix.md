---
title: Full Feature Matrix
subtitle: "All tools, side by side. Scroll horizontally to compare every dimension. Nubi column is highlighted."
dimensions:
  - key: kernel
    label: Compute Kernel
    description: "Where analytics compute runs and what drives cost. Browser-side = near-zero marginal cost per view; cloud kernel = billed per session/second/CU."
  - key: transport
    label: Result Transport
    description: "Wire format used to move query results to the frontend. Arrow IPC is columnar and zero-copy; JSON adds serialisation overhead and memory pressure at scale."
  - key: viz
    label: "Viz Capability & Row Ceiling"
    description: "Rendering technology and practical row limit before performance degrades. WebGL GPU rendering handles 1M+ points; SVG/Canvas degrades past ~50k–100k rows."
  - key: caching
    label: Caching & Pre-aggregation
    description: "How repeated queries are served without hitting the warehouse every time. Auto pre-agg vs manual; edge cache vs server cache; cross-user vs per-session."
  - key: embedding
    label: Embedding Model
    description: "How dashboards or analytics are embedded into third-party products. Core surface vs add-on; viewer pricing model; auth mechanism (JWT/JWKS vs bolt-on SDK)."
  - key: modeling
    label: "Semantic Layer / Modeling Tax"
    description: "How much upfront schema or model definition is required before analysts can query data. High = LookML/cube schema required; low = point-and-go."
  - key: ai
    label: AI Features
    description: "Native AI/LLM capabilities: text-to-SQL, NL query, dashboard generation, agentic BI. Grounded AI (lineage-aware) vs generic SQL generation."
  - key: pricing
    label: Pricing Model
    description: "Cost drivers: per-seat vs capacity vs usage-based. Existence and generosity of free tier. Viewer pricing for embedded deployments."
  - key: selfHost
    label: Self-Host Option
    description: "Whether the tool can be fully self-hosted in a customer's own infrastructure, and the cost/complexity of doing so."
matrix:
  kernel:
    Nubi: "Pyodide+DuckDB-WASM in browser by default; on-demand server (E2B/Modal, scale-to-zero) for ~10% of workloads"
    Hex: "Python kernel per session, Hex cloud; 10–30 s cold starts; per-minute billing"
    Cube: "No kernel; warehouse + Cube Store for pre-aggs; hourly infra billing"
    Metabase: "Server-side SQL push to warehouse; no in-browser compute"
    Looker: "Warehouse pushdown; Looker cloud compiles LookML → SQL; no in-browser compute"
    Sigma: "Warehouse pushdown (Snowflake-first); hybrid browser/warehouse exec path"
    Tableau: "Hyper extract engine (in-memory) or live warehouse pushdown (DirectQuery)"
    PowerBI: "VertiPaq in-memory (Import) or DirectQuery; Fabric F-SKU capacity"
    Superset: "Server-side SQL push to warehouse; no in-browser compute"
  transport:
    Nubi: "Arrow IPC over WebSocket (columnar, zero-copy to browser viz)"
    Hex: "JSON via pandas; no Arrow path"
    Cube: "JSON / REST+GraphQL SQL API; no Arrow IPC"
    Metabase: "JSON rows over HTTP"
    Looker: "JSON via Looker API / iFrame; no Arrow IPC"
    Sigma: "JSON rows, paginated 10 k at a time; no Arrow IPC"
    Tableau: "Proprietary VizQL/Hyper protocol; JSON for API; no Arrow"
    PowerBI: "Proprietary VertiPaq/DAX protocol; JSON REST API; no Arrow IPC"
    Superset: "JSON rows over HTTP; no Arrow IPC"
  viz:
    Nubi: "WebGL/WebGPU (regl) on Arrow buffers; 1M+ pts at 60 fps; auto-upgrade above row threshold"
    Hex: "Plotly/SVG; degrades past ~50 k rows; no WebGL"
    Cube: "Bring-your-own frontend; no built-in viz"
    Metabase: "40+ charts SVG/Canvas; no WebGL; degrades past tens-of-thousands rows"
    Looker: "Extensive chart library SVG/Canvas; Viz Assistant (Gemini); no WebGL"
    Sigma: "Spreadsheet + 30+ charts SVG/Canvas; browser limited to 10 k rows/page; no WebGL"
    Tableau: "40+ chart types SVG/Canvas; Hyper fast for extracts; no WebGL GPU path"
    PowerBI: "100+ visuals Canvas; AppSource custom visuals; no WebGL GPU path"
    Superset: "40+ ECharts SVG/Canvas; no WebGL; community custom charts"
  caching:
    Nubi: "Content-hashed edge cache (plan+RLS claims key); automatic pre-agg mined from query log — extends advantage to diverse-slice workloads"
    Hex: "Per-session result cache; weak cross-user sharing; no auto pre-agg"
    Cube: "Pre-aggregations in Cube Store (hand-written schema required); in-memory cache"
    Metabase: "Query/model cache all tiers; granular caching Pro+; preemptive caching v53; no auto pre-agg"
    Looker: "PDT (LookML-defined pre-computed tables); query result cache; requires LookML model"
    Sigma: "6-tier hybrid: browser → query ID → warehouse cache → materialization → pushdown; manual materialization"
    Tableau: "Hyper in-memory extract cache; live connections use warehouse cache; no auto pre-agg"
    PowerBI: "VertiPaq in-memory (Import mode); Fabric intelligent cache; refresh 8–48×/day"
    Superset: "Redis query result cache (configurable TTL); no auto pre-agg; manual dbt/Cube needed"
  embedding:
    Nubi: "<nubi-dashboard> → <nubi-widget> → <nubi-editor>; JWKS-native; no separate SDK"
    Hex: "Enterprise add-on only; bolt-on auth; expensive; not a core surface"
    Cube: "Core strength (headless); JWT→SQL RLS; Viewer $20/user/month (Premium+)"
    Metabase: "Static embed (free/Starter w/ branding); white-label on Pro ($575+/month); every viewer = paid seat"
    Looker: "Separate Embed edition SKU; iFrame + signed URLs; strong JWT RLS; no public price (~$60 k+ entry)"
    Sigma: "Add-on; doubles/triples contract; JWT auth; no public price; custom-quoted"
    Tableau: "Embedding API v3; every viewer = paid seat; OEM SaaS from $60 k–$150 k/year"
    PowerBI: "App-owns-data via Fabric F-SKU; no per-viewer license; F4 ~$400/month covers ~100 concurrent users"
    Superset: "iframe + Guest Token; Preset viewer licenses from $500/month for 50 viewers"
  modeling:
    Nubi: "Low — point at warehouse, go; auth-as-code (TypeScript/SQL in repo); no mandatory schema; NoSQL deliberately out of scope"
    Hex: "Medium — notebook cells; AI Semantic Model agent (Team+); no formal semantic layer"
    Cube: "High — must write cube schema (JS/YAML) before any query works; proprietary language"
    Metabase: "Low-medium — point-and-click + SQL; Data Studio semantic layer (v59, 2026)"
    Looker: "Very high — LookML proprietary language; est. 40–60% of total investment in LookML dev"
    Sigma: "Low-medium — no-code Data Models (tables, relationships, metrics, RLS) in spreadsheet UI"
    Tableau: "Medium — DAX/LOD expressions; published data sources as light semantic layer; no LookML"
    PowerBI: "Medium — DAX + Power Query M; familiar to Excel users; model-defined RLS"
    Superset: "Low — virtual datasets (SQL views) + metrics; no formal semantic layer"
  ai:
    Nubi: "Lineage-grounded LLM (catalog-anchored); /ai/ask; MCP server; LLM-authorable HTML/CSS dashboards"
    Hex: "Magic AI (schema-grounded SQL); Notebook/Threads/Semantic agents; credit-based"
    Cube: "Cube Copilot (model assist); text-to-semantic-layer; BYOLLM (Enterprise); token cost pass-through"
    Metabase: "Metabot NL→SQL (all tiers); AI SQL gen in OSS since March 2026; $3.75/1M tokens (managed)"
    Looker: "Gemini: Conversational Analytics, LookML Assistant, Viz Assistant, Code Interpreter; Data Tokens $3–$20/1M"
    Sigma: "Text-to-SQL; warehouse-native AI (Cortex/Databricks AI/BigQuery ML); details unverified (est.)"
    Tableau: "Tableau Agent (Cloud+/Next only): narratives, agentic BI, Pulse; Standard/Enterprise: no Agent"
    PowerBI: "Copilot (GPT-4/Azure OpenAI): DAX gen, visual creation, NL narratives; included in F2+ capacity"
    Superset: "NL-to-SQL via optional LLM plugin; Preset AI roadmap unverified (est.); limited GA AI"
  pricing:
    Nubi: "Usage-based: connector bytes + embed views/1k + AI tokens + kernel-seconds; genuine free tier (browser compute is free to Nubi); billed ZAR via Paystack"
    Hex: "Per-seat: Community free; Professional $36/editor/month; Team $75/editor/month; compute add-on"
    Cube: "Per-developer + hourly infra: Free hobbyist; Starter $40/dev/month; Premium $80/dev/month"
    Metabase: "Tiered: OSS free; Starter $100+$6/user/month; Pro $575+$12/user/month; Enterprise $20 k+/year"
    Looker: "No public pricing — contact sales; est. from $60 k/year; per-user add-ons; BigQuery compute separate"
    Sigma: "No public pricing — negotiated; median contract $61 k/year; Essentials from ~$300/month"
    Tableau: "Per-seat: Standard Viewer $15, Explorer $42, Creator $75/month; Enterprise higher; Next $40/Creator; OEM from $60 k–$150 k"
    PowerBI: "Pro $14/user/month; PPU $24/user/month; Fabric F-SKU $262–$16,768/month; Free: create/view only"
    Superset: "Superset: free OSS; Preset: Starter free (5 users); Pro $20/user/month; embed viewers from $500/month/50"
  selfHost:
    Nubi: "Planned — M10 Docker Compose stack not yet shipped; intermediate: hosted control plane + self-hosted connector (warehouse creds never leave customer network)"
    Hex: "No — cloud-only SaaS"
    Cube: "Yes — Cube Core open source (MIT); production requires Redis + Cube Store cluster"
    Metabase: "Yes — OSS free (AGPL v3); Pro self-hosted same license fee as cloud"
    Looker: "No — Google Cloud hosted only (since 2019)"
    Sigma: "No — cloud-only SaaS"
    Tableau: "Yes — Tableau Server (on-premise/self-managed cloud); Creator $70/user/month"
    PowerBI: "Yes — Power BI Report Server (included with Premium or SQL Server EE with SA); feature lag"
    Superset: "Yes — Apache Superset free self-host; Preset Certified Superset (managed self-host) on Enterprise"
---

All tools compared across every dimension.
Scroll horizontally to see the full matrix.
Nubi column is highlighted with the brand gradient.
Data researched June 2026 — features and pricing change frequently. Verify before publishing.
