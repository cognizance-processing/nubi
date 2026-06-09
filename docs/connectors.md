# Connectors — connecting your data

A **connector** is a saved link to one of your data sources — a Postgres database, a BigQuery project, a Snowflake warehouse, a Parquet file in S3, or any of 20+ supported types. Once you add a connector, you can browse its tables, run queries against it, build dashboards on it, and reference it in flows. Nubi does not copy your data: it queries your source on demand and caches results.

Connectors live on the **Connectors** page (`/connectors` in the app sidebar). Credentials you enter are encrypted at rest with AES-256-GCM and are never shown again after you save them.

---

## The Connectors page

Open **Connectors** from the sidebar. You'll see:

- A header with a **Refresh** button and an **Add connector** button (the Add button only appears if you have writer/admin permissions in the org).
- A list of connector cards — one per data source you've added.
- A built-in **Demo data** connector at the top of the list (see [Demo data](#demo-data-connector)).

Each connector card shows:

| Element | What it tells you |
|---------|-------------------|
| Logo + name | The connector's brand logo and the name you gave it. |
| Type badge | The connector type (e.g. *PostgreSQL*, *Snowflake*). |
| **Built-in** badge | Present only on system connectors like Demo data. |
| Network badge | Shows `bridge` when the connector reaches your database through a private-network agent. |
| Summary line | A quick read of the config — usually `host:port · database · user`. |
| Test result pill | Appears after you click **Test** (green ✓ or red ✗). |

The card's action buttons are **View data**, **Test**, **Edit** (pencil), and **Delete** (trash). Edit and Delete only appear if you can write to the org; system connectors hide the Edit button.

If you have no connectors yet, an empty state invites you to *Add your first connector*. If you're a read-only member, it tells you to ask an admin instead.

---

## Supported connector types

The **Add connector** picker groups types into six categories. The full catalogue:

### Relational databases
| Type | Notes |
|------|-------|
| **PostgreSQL** | Host/port/database/user/password + SSL mode. |
| **MySQL** | Standard host/port/database/user/password. |
| **MariaDB** | MySQL-compatible; same fields. |
| **Microsoft SQL Server** | T-SQL; adds an *Encrypt connection* toggle. |
| **Oracle Database** | Host/port + **Service name / SID** instead of a database name. |
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
| **Snowflake** | Account, user, password, plus optional warehouse/database/schema/role. |
| **Amazon Redshift** | Postgres wire; SSL mode included. |
| **Databricks** | Server hostname, HTTP path, access token, optional catalog/schema. |
| **ClickHouse** | Host/port/database/user/password + a TLS (secure) toggle. |
| **Azure Synapse** | T-SQL analytics pool. |

### Query engines
| Type | Notes |
|------|-------|
| **Amazon Athena** | Serverless SQL over S3; needs a region and an S3 staging dir. |
| **Trino** | Distributed SQL engine; coordinator host + catalog + schema. |
| **Presto** | Open-source distributed SQL engine; same shape as Trino. |

### Lakehouse & files
| Type | Notes |
|------|-------|
| **Object storage (Parquet / DuckDB)** | Query a Parquet or DuckDB file by `s3://`, `gs://`, or `https://` URL. |
| **Demo data** | The built-in sample dataset (no setup). See below. |

### APIs & custom
| Type | Notes |
|------|-------|
| **HTTP / JSON API** | Any REST API that returns JSON. Base URL + optional bearer token + extra headers. |
| **JDBC (custom driver)** | Connect any JDBC source with a driver JAR (JDBC URL + driver class + JAR path). |

> Some warehouse drivers (BigQuery, Snowflake, JDBC, etc.) are optional and load on first use. If a driver isn't installed in your deployment, the connector saves fine but raises a `driver_unavailable` error with install guidance when first queried — ask your administrator to add the driver.

---

## Adding a connector

You need writer or admin permission in the org. Then:

1. On the **Connectors** page, click **Add connector**. A slide-over panel opens from the right.
2. **Pick a type.** The picker shows all types grouped by category. Use the **search box** at the top to filter by name or description (e.g. type `post` to find PostgreSQL). Click a type tile to continue.
3. **Name the connector.** Give it a clear name like `prod-postgres` or `analytics-bigquery`. This is what appears on the card and in the connector picker when you write queries.
4. **Fill in the connection details.** The form is generated from the type you chose — only the relevant fields appear. Required fields are marked; optional ones say *(optional)*. Sensible defaults (ports, SSL mode, etc.) are pre-filled.
5. **Enter credentials.** Secret fields (passwords, tokens, service-account keys) are clearly marked with a note: *Encrypted at rest with AES-256-GCM — never shown after save*. See [Entering credentials](#entering-credentials-secrets).
6. Click **Add connector**. On success a toast confirms *Connector added* and the panel closes. The new card appears in the list.

To change the type before filling in details, click **Change type** in the form header to go back to the picker.

If something is wrong (e.g. a missing required field, or a server-side validation error), an inline red error appears at the bottom of the form. Fix it and resubmit.

### Field reference by example

A **PostgreSQL** connector, for instance, asks for:

- **Host** (required) and **Port** (defaults to `5432`)
- **Database** (required) and **User** (defaults to `postgres`)
- **Password** (secret)
- **SSL mode** — one of `disable`, `allow`, `prefer` (default), `require`, `verify-ca`, `verify-full`
- **Network mode** — leave blank for direct, or choose `bridge` (see [Private networks & bridges](#private-networks--bridges))

A **BigQuery** connector instead asks for a **GCP Project ID** and a **Service account JSON** key (upload a `.json` file or paste it). Leave the key blank to use Application Default Credentials.

An **Object storage** connector asks for a single **File URL** (`s3://…`, `gs://…`, or `https://…` pointing at a Parquet or DuckDB file), plus optional endpoint/region and access keys for private buckets.

---

## Entering credentials (secrets)

Nubi separates **non-secret config** (host, port, database name, region…) from **secret credentials** (passwords, tokens, service-account keys). They are stored differently:

- Non-secret config is saved on the connector record in plain form so the card can show a summary.
- Secret fields are sent to the encrypted secret store, which encrypts them with **AES-256-GCM** before writing them to the database.

**Secrets are never returned by the API after you save.** That's why secret fields in the **Edit** form come up blank — the app cannot show you the existing value. Leaving a secret field blank on edit keeps the current credential; typing a new value rotates it (see [Editing](#editing-a-connector)).

The secret-bearing fields, depending on type, are: `password`, `service_account_json` (BigQuery), `token` (HTTP/JSON), `access_token` (Databricks), and `aws_secret_access_key` (Athena / object storage). For the HTTP/JSON connector, put auth tokens in the **Bearer token** field — not in the *Extra headers* box, which is for non-secret headers only.

> **Tip — service-account keys.** For BigQuery, the *Service account JSON* control lets you either upload a `.json` key file or paste the JSON directly. Either way it's stored as an encrypted secret.

---

## Testing a connection

After saving (or any time), click **Test** on a connector card. A spinner shows *Testing…*, then a result pill appears:

- **Green ✓** — the connector's config and encrypted secret were both resolved. A small `config:✓ secret:✓` indicator shows which layers passed.
- **Red ✗** — one of the layers is missing (for example, the secret couldn't be decrypted).

A toast also confirms the result (*Connection verified successfully* or a failure message).

> **What Test checks.** Test is a fast **structural** check: it confirms the connector record exists and that its encrypted secret can be retrieved and decrypted. It does **not** open a network socket to your database. To verify the source is actually reachable and the credentials work end-to-end, use **View data** (below) — listing tables exercises a real connection.

The Demo data connector always tests green (it has no secret and runs in-process).

---

## Browsing a connector's data

Click **View data** on any connector card to open the **Data Browser** (`/connectors/:id/data`). This lets you explore the source without writing any SQL:

- The **left rail** lists the connector's tables (searchable, with row counts where available). The first table is auto-selected.
- The **right panel** previews the selected table — columns with their types, and up to 500 rows in a grid.
- A **Refresh** button re-reads the table list and current preview; **Back** returns to the Connectors page.

Because the Data Browser introspects the live source and reads a sample of rows, it's also the best way to confirm a freshly added connector actually works.

Row-level security still applies: you only see rows your access policies permit.

---

## Using a connector in queries

When you write a query in the **Query Workspace** (or a SQL cell in a flow notebook), a **Connector** picker sits in the toolbar:

1. Choose your connector from the dropdown. The default is **Demo data (built-in)**; every connector you've added is listed by name.
2. Write your SQL against that source and run it. The SQL dialect is auto-detected from the connector type (and is overridable).
3. When you save the query, the chosen connector is **bound** to it (stored as the query's `datastore_id`). Dashboards and scheduled reports that use the query then run against that source automatically.

If you have no connectors, the picker shows *No connectors yet — using demo data*, and queries run against the built-in demo dataset.

### Selecting a connector via the API

If you're calling the query API directly, pass the connector's id as `datastore_id`:

```json
POST /api/v1/query
{
  "query_id":     "revenue_by_month",
  "datastore_id": "ds-uuid"
}
```

A registered query can carry its own bound connector, so a dashboard widget can send just `{ "query_id": "…" }` and still hit the right source. When both are present, the request body's `datastore_id` wins.

---

## Using a connector in flows

In a [flow](/docs/flows), each SQL cell has a **Run against** picker in the task inspector — the same connector dropdown you see in the Query Workspace. Pick your connector there to run the cell against a real warehouse instead of in-memory DuckDB.

For spec-based flow authoring, set `datastore_id` in the cell's `config`. Omit it to fall back to the demo DuckDB dataset:

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

1. The form pre-fills with your saved **non-secret** config (host, port, database, etc.). You can change the connector's name and any non-secret field.
2. **Secret fields come up blank** — Nubi can't display the stored value. Leave a secret blank to keep the current credential. Type a new value to **rotate** it (the old encrypted secret is replaced).
3. Click **Save changes**. A toast confirms *Connector updated*.

You cannot change a connector's type — delete it and add a new one if you need a different source type. System connectors (Demo data) have no Edit button because they have no configurable fields.

---

## Deleting a connector

Click the **trash** icon and confirm in the dialog.

- For a normal connector, this **permanently deletes** the record and its encrypted credentials. This can't be undone. Queries bound to it will fall back to demo data or fail until rebound.
- For the **Demo data** connector, *Delete* just **removes it from this workspace's list**. You can add it back any time from **Add connector** — the dialog says *Remove* rather than *Delete* to make this clear.

---

## Demo data connector

Every workspace starts with a built-in **Demo data** connector — a small sample dataset you can query immediately with no setup. It's marked **Built-in** and is read-only (no Edit). Use it to explore Nubi's query, dashboard, and flow features before you connect a real source.

If you remove it, you can re-add it from the picker; it always reappears at the top of the list when present.

---

## Private networks & bridges

If your database lives inside a private network (a VPC with no public ingress), most connectors expose a **Network mode** field:

- **direct** (default) — Nubi connects straight to the host you entered.
- **bridge** — traffic is routed through a Nubi **bridge agent** running inside your network. The connector card then shows a `bridge` badge.

To use bridge mode you first deploy a bridge agent and link it to the connector. See [Bridges](/docs/bridges) for the full setup guide.

---

## Permissions

- **Listing, testing, and browsing** connectors is available to any member of the org.
- **Adding, editing, and deleting** connectors requires writer or admin permission. Read-only members see the cards but not the Add/Edit/Delete controls.
- All connectors are **org-scoped** — you only ever see your own org's connectors. A connector belonging to another org is treated as not found.

---

## Security summary

- Credentials are encrypted at rest with **AES-256-GCM** and are never returned by the API after save.
- Non-secret config and secret credentials are stored separately; secret keys are rejected if they're submitted as plain config.
- **Test** verifies your config and secret are resolvable without opening a network socket.
- Row-level security policies are enforced on every read, including the Data Browser preview.

For the deeper security model — encryption, key rotation, and network modes — see [Connector Security](/docs/connector-security).
