# Connectors

![Connect any data source — SQL databases, cloud warehouses, file storage, and custom APIs](illustration:ConnectorSdk)

A **connector** links Nubi to one of your data sources — a Postgres database, a BigQuery project, a Snowflake warehouse, a Parquet file in S3, or any of 20+ supported types. Once added, you can browse its tables, run queries against it, build dashboards, and reference it in flows. Nubi queries your source on demand and caches results; it does not copy your data.

Connectors live on the **Connectors** page (`/connectors` in the app sidebar). Credentials you enter are encrypted at rest with AES-256-GCM and are never shown again after you save them.

---

## The Connectors page

![The Connectors page — one card per source, with View data and Test actions and at-rest encryption noted in the header](/docs/screenshots/connectors.png)

Open **Connectors** from the sidebar. The page shows:

- A header with a **Refresh** button and an **Add connector** button (visible to writers and admins only).
- A list of connector cards — one per data source you have added.
- A built-in **Demo data** card at the top of the list when you are in the default project (see [Demo data connector](#demo-data-connector)).

Each connector card shows:

| Element | What it tells you |
|---------|-------------------|
| Logo + name | The connector's brand logo and the name you gave it. |
| Type badge | The connector type (e.g. *PostgreSQL*, *Snowflake*). |
| **Built-in** badge | Present only on the Demo data connector. |
| Network badge | Shows `bridge` when traffic routes through a private-network agent. |
| Summary line | A quick read of the config — usually `host:port · database · user`. |
| Test result pill | Appears after you click **Test** (green ✓ or red ✗). |

The card's action buttons are **View data**, **Test**, **Edit** (pencil), and **Delete** (trash). Edit and Delete require write permission; the Demo data connector has no Edit button because it has no configurable fields.

If you have no connectors yet, an empty state invites you to *Add your first connector*. Read-only members see a message to ask an admin.

---

## Supported connector types

The **Add connector** picker groups types into six categories.

### Relational databases

| Type | Notes |
|------|-------|
| **PostgreSQL** | Host / port / database / user / password + SSL mode. |
| **MySQL** | Standard host / port / database / user / password. |
| **MariaDB** | MySQL-compatible; same fields. |
| **Microsoft SQL Server** | T-SQL; adds an *Encrypt connection* toggle. |
| **Oracle Database** | Host / port + **Service name / SID** instead of a database name. |
| **CockroachDB** | Postgres wire-compatible; SSL mode included. |

### Cloud-managed SQL

| Type | Notes |
|------|-------|
| **Google Cloud SQL** | Managed Postgres on GCP (Postgres wire). |
| **Azure SQL Database** | Managed SQL Server; *Server* is the `*.database.windows.net` host. |

### Cloud warehouses

| Type | Notes |
|------|-------|
| **Google BigQuery** | GCP Project ID + a service-account JSON key (or Application Default Credentials). |
| **Snowflake** | Account, user, password; optional warehouse / database / schema / role. |
| **Amazon Redshift** | Postgres wire; default port 5439; SSL mode included. |
| **Databricks** | Server hostname, HTTP path, access token; optional catalog / schema. |
| **ClickHouse** | Host / port / database / user / password + TLS toggle. Default port 8443. |
| **Azure Synapse** | T-SQL analytics pool. |

### Query engines

| Type | Notes |
|------|-------|
| **Amazon Athena** | Serverless SQL over S3; needs a region and an S3 staging directory. |
| **Trino** | Distributed SQL engine; coordinator host + catalog + schema. |
| **Presto** | Open-source distributed SQL; same shape as Trino. |

### Lakehouse & files

| Type | Notes |
|------|-------|
| **Object storage (Parquet / DuckDB)** | Query a Parquet or DuckDB file by `s3://`, `gs://`, or `https://` URL. |
| **Demo data** | Built-in sample dataset (no setup); see below. |

### APIs & custom

| Type | Notes |
|------|-------|
| **HTTP / JSON API** | Any REST API returning JSON. Base URL + optional bearer token + extra headers. |
| **JDBC (custom driver)** | Any JDBC source with a driver JAR (JDBC URL + driver class + JAR path). |

> Some warehouse drivers (BigQuery, Snowflake, JDBC, etc.) are loaded on first use. If a driver is not installed in your deployment, the connector saves fine but raises a `driver_unavailable` error the first time it is queried. Ask your administrator to add the missing driver.

---

## Adding a connector

You need writer or admin permission in the org.

1. On the **Connectors** page, click **Add connector**. A slide-over panel opens from the right.
2. **Pick a type.** The picker shows all types grouped by category. Use the **search box** to filter by name or description (e.g. type `post` to find PostgreSQL). Click a type tile to continue.
3. **Name the connector.** Give it a clear name such as `prod-postgres` or `analytics-bigquery`. This name appears on the card and in the connector picker when you write queries.
4. **Fill in the connection details.** The form is generated from the type you chose — only the relevant fields appear. Required fields are marked; optional ones say *(optional)*. Sensible defaults (ports, SSL mode, etc.) are pre-filled.
5. **Enter credentials.** Secret fields are clearly marked with a note about AES-256-GCM encryption. See [Entering credentials](#entering-credentials-secrets).
6. Click **Add connector**. A toast confirms *Connector added* and the panel closes.

To change the type before filling in details, click **Change type** in the form header to go back to the picker.

If something is missing or fails server-side validation, an inline red error appears at the bottom of the form.

### Field reference by example

A **PostgreSQL** connector asks for:

- **Host** (required) and **Port** (defaults to `5432`)
- **Database** (required) and **User** (defaults to `postgres`)
- **Password** (secret, encrypted)
- **SSL mode** — `disable`, `allow`, `prefer` (default), `require`, `verify-ca`, or `verify-full`
- **Network mode** — leave blank for direct; set to `bridge` for VPC access (see [Private networks & bridges](#private-networks--bridges))

A **BigQuery** connector asks for a **GCP Project ID** and a **Service account JSON** key (upload a `.json` file or paste it). Leave the key blank to use Application Default Credentials.

An **Object storage** connector asks for a single **File URL** (`s3://…`, `gs://…`, or `https://…` pointing at a Parquet or DuckDB file), plus optional endpoint / region and access keys for private buckets.

---

## Entering credentials (secrets)

Nubi separates **non-secret config** (host, port, database name, region…) from **secret credentials** (passwords, tokens, keys). They are stored differently:

- Non-secret config is saved on the connector record so the card can display a summary.
- Secret fields are forwarded to the encrypted secret store, which encrypts them with **AES-256-GCM** before writing to the database.

**Secrets are never returned by the API after you save.** That is why secret fields in the **Edit** form come up blank — the app cannot show you the existing value. Leaving a secret field blank on edit keeps the current credential; typing a new value rotates it.

Secret-bearing fields by connector type:

| Field | Used by |
|-------|---------|
| `password` | PostgreSQL, MySQL, MariaDB, SQL Server, Oracle, CockroachDB, Redshift, Snowflake, ClickHouse, Trino, Presto, Azure SQL, Azure Synapse, Cloud SQL, JDBC |
| `service_account_json` | BigQuery |
| `token` | HTTP / JSON API (bearer token) |
| `access_token` | Databricks |
| `aws_secret_access_key` | Amazon Athena, Object storage |

For the HTTP / JSON connector put auth tokens in the **Bearer token** field — not in *Extra headers*, which is for non-secret headers only.

> **BigQuery tip.** The *Service account JSON* control lets you upload a `.json` key file or paste the JSON directly. Either way it is stored as an encrypted secret.

---

## Testing a connection

After saving (or at any time), click **Test** on a connector card. A spinner shows *Testing…*, then a result pill appears:

- **Green ✓** — the connector record was found and its encrypted secret was retrieved and decrypted. The `config:✓ secret:✓` indicator confirms which layers passed.
- **Red ✗** — one layer is missing or decryption failed. The indicator shows which layer failed.

A toast also confirms the result.

> **What Test checks.** Test is a **structural** check only: it confirms the connector record exists and its encrypted secret can be decrypted. It does **not** open a network socket to your database. To verify the source is actually reachable and credentials work end-to-end, use **View data** — listing tables exercises a real connection.

The Demo data connector always tests green (it has no secret and runs in-process).

---

## Browsing a connector's data

![The Data Browser — searchable table list with row counts on the left, a 50-row preview grid on the right](/docs/screenshots/data-browser.png)

Click **View data** on any connector card to open the **Data Browser** (`/connectors/:id/data`). This lets you explore the source without writing any SQL:

- The **left rail** lists the connector's tables (searchable, with row counts where available). The first table is auto-selected on load.
- The **right panel** previews the selected table — columns with their types, and the first 50 rows in a grid.
- A **Refresh** button re-reads the table list and current preview. **Back** (the arrow icon) returns to the Connectors page.

Because the Data Browser introspects the live source and reads a sample of rows, it is also the fastest way to confirm a freshly added connector actually works.

Row-level security still applies: you see only rows your access policies permit.

---

## Using a connector in queries

When writing SQL in the **Query Workspace** (or a SQL cell in a flow notebook), a **Connector** picker sits in the toolbar:

1. Choose your connector from the dropdown. The default is **Demo data (built-in)**; every connector you have added is listed by name.
2. Write your SQL against that source and run it. The SQL dialect is auto-detected from the connector type.
3. When you save the query, the chosen connector is **bound** to it (stored as the query's `datastore_id`). Dashboards and scheduled reports that use the query then run against that source automatically.

If you have no connectors, the picker shows *No connectors yet — using demo data*, and queries run against the built-in demo dataset.

### Selecting a connector via the API

Pass the connector's id as `datastore_id`:

```json
POST /api/v1/query
{
  "query_id":     "revenue_by_month",
  "datastore_id": "ds-uuid"
}
```

A saved query can carry its own bound connector, so a dashboard widget can send just `{ "query_id": "…" }` and still hit the right source. When both are present, the request-body `datastore_id` wins.

---

## Using a connector in flows

In a [flow](/docs/flows), each SQL cell has a **Run against** picker in the task inspector — the same connector dropdown as the Query Workspace. Pick your connector there to run the cell against a real warehouse instead of in-memory DuckDB.

For spec-based flow authoring, set `datastore_id` in the cell's `config`:

```json
{
  "key": "pull",
  "kind": "query",
  "needs": [],
  "config": {
    "sql": "SELECT * FROM revenue_by_region",
    "datastore_id": "ds-uuid"
  }
}
```

The same row-level security and SQL planning that apply to interactive queries apply here too. See [Flows](/docs/flows) for the full cell reference.

---

## Editing a connector

Click the **pencil** icon on a connector card to open the Edit panel.

1. The form pre-fills with your saved **non-secret** config (host, port, database, etc.). You can change the connector name and any non-secret field.
2. **Secret fields come up blank.** Leave a secret blank to keep the current credential. Type a new value to rotate it (the old encrypted secret is replaced).
3. Click **Save changes**. A toast confirms *Connector updated*.

You cannot change a connector's type — delete it and add a new one if you need a different source type. The Demo data connector has no Edit button because it has no configurable fields.

---

## Deleting a connector

Click the **trash** icon and confirm in the dialog.

- For a normal connector, this **permanently deletes** the record and its encrypted credentials. This cannot be undone. Queries bound to it will fall back to demo data or fail until rebound.
- For the **Demo data** connector, the action **removes it from this project's list** without deleting anything permanently. The dialog says *Remove* rather than *Delete* to make this clear. You can add it back any time from the **Add connector** picker.

---

## Demo data connector

The **Demo data** connector is a built-in dataset available in the default project. It covers four domains across 17 tables and requires no setup:

| Dataset | Tables |
|---------|--------|
| **Retail sales** | `dim_regions`, `dim_products`, `dim_customers`, `sales`, `budget`, `targets` |
| **SaaS metrics** | `saas_plans`, `saas_accounts`, `saas_subscriptions`, `saas_subscription_events`, `saas_invoices` |
| **Web analytics** | `web_sessions`, `web_pageviews` |
| **Finance ops** | `fin_invoices`, `fin_payments`, `fin_expenses`, `fin_headcount` |

The demo connector is marked **Built-in** and appears only in the default (demo) project; other projects you create start empty and require a real connector. It is read-only — there is no Edit button.

If you remove it, you can re-add it from **Add connector** → *Demo data* in the Lakehouse & files category. It reappears at the top of the list.

---

## Private networks & bridges

If your database lives inside a private network (a VPC with no public ingress), most connectors expose a **Network mode** field:

- **direct** (default) — Nubi connects straight to the host you entered.
- **bridge** — traffic routes through a Nubi bridge agent running inside your network. The connector card then shows a `bridge` badge.

To use bridge mode, deploy a bridge agent and link it to the connector. See [Bridges](/docs/bridges) for the full setup guide.

---

## Python connector SDK

The SDK lets you wrap **any Python callable** as a first-class Nubi connector. This is the primary extension point for non-SQL sources such as REST APIs, Python functions, in-memory data, or mock fixtures.

### FunctionConnector

`FunctionConnector` is the entry point. Provide a callable `fn(plan) -> pyarrow.Table` plus the capability dict:

```python
import pyarrow as pa
from app.connectors.sdk import FunctionConnector

def my_source(plan):
    # Return all rows as a raw Arrow table — do NOT filter here.
    return pa.table({
        "tenant_id": ["acme", "acme", "globex"],
        "value":     [1,      2,      3],
    })

conn = FunctionConnector(
    fn=my_source,
    capabilities={
        "native_arrow":        True,
        "predicate_pushdown":  False,
        "projection_pushdown": False,
        "partition_pushdown":  False,
        "predicate_rls":       True,
        "column_masking":      False,
        "streaming_cdc":       False,
    },
)
```

After calling `fn`, `FunctionConnector.execute()` automatically applies three post-fetch guards in order:

1. **RLS guard** — when `predicate_pushdown=False` and `predicate_rls=True`, filters rows to those matching the RLS policies from the request JWT. Fail-closed: if a policy references a column absent from the table, the call raises a 403 error rather than returning unfiltered data.
2. **Projection guard** — when `projection_pushdown=False`, narrows the column set to the plan's requested columns (intersection semantics).
3. **Limit guard** — best-effort row cap extracted from the plan's `LIMIT` clause.

### Capability flags

All seven flags must be present in the dict; the planner raises `KeyError` if any are missing.

| Flag | Meaning |
|------|---------|
| `native_arrow` | Connector returns Arrow IPC natively (no row-by-row conversion). |
| `predicate_pushdown` | WHERE predicates can be pushed to the source. |
| `projection_pushdown` | Column selection can be pushed to the source. |
| `partition_pushdown` | Queries can be routed to specific partitions / shards. |
| `predicate_rls` | Row-level security is enforced (either by the source or post-fetch). |
| `column_masking` | Column values can be masked / redacted before leaving the connector. |
| `streaming_cdc` | Connector can stream Change-Data-Capture events. |

When `predicate_pushdown=True`, you assert that your `fn` already filtered the data (the planner encoded RLS predicates into the plan). No post-fetch RLS is applied in that case.

### Subclassing Connector directly

For full control, subclass `Connector` from `app.connectors.base`:

```python
from app.connectors.base import Connector
from app.connectors.plan import PhysicalPlan
import pyarrow as pa

class MyConnector(Connector):
    def capabilities(self) -> dict[str, bool]:
        return {
            "native_arrow":        True,
            "predicate_pushdown":  True,
            "projection_pushdown": True,
            "partition_pushdown":  False,
            "predicate_rls":       True,
            "column_masking":      False,
            "streaming_cdc":       False,
        }

    def execute(self, plan: PhysicalPlan) -> pa.Table:
        # plan.sql is ready to run verbatim; do NOT rewrite it.
        ...

    def execute_stream(self, plan: PhysicalPlan):
        yield from self.execute(plan).to_batches()
```

Then register it:

```python
from app.connectors.registry import get_connector_registry

get_connector_registry().register("my_source", MyConnector)
```

Connectors are stateless with respect to individual queries; connection pools may live in instance state. The planner calls `capabilities()` to decide which push-downs are safe and then calls `execute()` or `execute_stream()` with a fully-baked `PhysicalPlan`.

### Optional pre-run estimate

A connector may implement an optional `estimate(plan)` hook that returns a best-effort, pre-run cost/scan estimate (a `QueryEstimate`) for a `PhysicalPlan`:

```python
def estimate(self, plan: PhysicalPlan) -> "QueryEstimate | None":
    ...
```

Key properties:

- **Opt-in, default unsupported.** The base `Connector.estimate` returns `None`. A connector that cannot dry-run or `EXPLAIN` simply inherits this; `None` means "estimate unsupported" (distinct from an estimate of zero). It is deliberately *not* an eighth `capabilities()` flag — `capabilities()` asserts exactly seven keys.
- **Estimates the RLS-rewritten plan, never raw SQL.** An override must estimate `plan.sql`, which is already RLS-rewritten, so the estimate can never reveal rows outside the caller's scope.
- **Advisory only — never blocks a run.** Any engine error is swallowed and reported as `None` rather than raised. An estimate is informational; it never gates execution.
- **No user-facing route yet.** There is currently **no HTTP endpoint and no UI** for estimates — the hook exists on the connector interface only.

Two built-in connectors implement it:

| Connector | Mechanism (`QueryEstimate.mechanism`) | What it reports | `exact` |
|---|---|---|---|
| **BigQuery** | `bigquery_dry_run` | Exact `est_bytes_scanned` via a free, synchronous dry-run job (no execution, no cost). | `True` |
| **DuckDB** | `duckdb_explain` | Approximate `est_rows` from `EXPLAIN` cardinality (`~<n> rows`); plans only, executes nothing. | `False` |

`QueryEstimate` fields (`est_bytes_scanned`, `est_rows`, `est_cost`, `mechanism`, `exact`) are all best-effort and may be `None`. `est_cost` is an engine-native optimiser cost, not a currency value; the UI should prefix non-`exact` figures with `~`.

---

## Permissions

- **Listing, testing, and browsing** connectors is available to any member of the org.
- **Adding, editing, and deleting** connectors requires writer or admin permission. Read-only members see the cards but not the Add / Edit / Delete controls.
- All connectors are **org-scoped** — you only see your own org's connectors.

---

## Security summary

- Credentials are encrypted at rest with **AES-256-GCM** and are never returned by the API after save.
- Non-secret config and secret credentials are stored separately; secret keys are rejected if submitted as plain config.
- **Test** verifies your config and secret are resolvable without opening a network socket.
- RLS predicates are injected as AST predicates by the query planner — never string-concatenated — and are enforced server-side; the browser never filters rows.
- Post-fetch RLS in `FunctionConnector` fails closed: a missing policy column returns 403 rather than unfiltered data.

For the deeper security model — encryption, key rotation, and network modes — see [Connector Security](/docs/connector-security).
