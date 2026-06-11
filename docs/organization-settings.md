# Organization & settings

Everything that controls who can use your workspace, what they can do in it, and how Nubi authenticates embedded viewers lives under **Settings**. Open it from the bottom of the left sidebar.

Settings use a two-column layout: a sticky sidebar on the left, grouped by scope, and the active section on the right.

![Settings — grouped sidebar with Account, Organization, and Project scopes; the Organization → General section active](/docs/screenshots/settings-organization.png)

| Group | Nav item | Route | What it controls |
|---|---|---|---|
| **Account** | Profile | `/settings/profile` | Your display name and avatar |
| **Organization** | General | `/settings/organization` | Org name, avatar, deletion |
| **Organization** | Members | `/settings/members` | Invites, roles, removals |
| **Organization** | Integrations | `/settings/integrations` | Slack / WhatsApp / Google Chat / Teams / Email channels |
| **Organization** | Security | `/settings/security` | Embed JWT issuers (host-signed token trust) |
| **Organization** | Usage | `/settings/usage` | Read-only usage metering for the org |
| **Organization** | Billing *(Cloud/EE only)* | `/billing` | Plan, wallet, invoices — link-out, shown only when billing is enabled |
| **Project** | General | `/settings/project` | Project name, Git sync, deletion |

Two notes on what lives where:

- **Secrets** are managed under **Flows → Secrets**, not here.
- **Billing** is a separate top-level page (`/billing`) and only appears on Nubi Cloud / EE builds — the sidebar shows it as a link-out when the billing feature is enabled. **Usage**, by contrast, is open-core and lives right here in Settings; the old standalone `/usage` page redirects to `/settings/usage`. See [Billing & Usage](/docs/billing-and-usage).

---

## Account — your profile

**Settings → Profile** manages how you appear to teammates. It is account-wide and follows you across every organization you belong to.

You can change:

- **Avatar** — defaults to your Google profile picture. Set a custom URL or upload a file to override it.
- **Display name** — shown in the member list and across the app.

Your **email address is read-only** here; it comes from how you signed in.

To update: open **Settings → Profile**, edit the fields, and click **Save profile**. A green **Saved** badge appears briefly to confirm.

---

## Organization — General

**Settings → Organization → General** manages the org's identity and (if needed) its deletion. Changes here affect all members.

> The **Personal** workspace is a special case — it cannot be renamed or deleted and has no member management. The controls below appear only for real organizations.

### Name and avatar

Owners and admins can update:

- **Organization avatar** — logo or image shown in the org switcher.
- **Organization name** — the display name visible to all members.

Edit the fields and click **Save changes**. Members and viewers see a read-only notice instead.

### Deleting an organization

The **Danger zone** is visible only to owners and admins.

Nubi blocks deletion while the org still has projects. The Delete button is disabled and you'll see: "This organisation has N projects. Delete all projects first." Once the org is empty:

1. Click **Delete organisation**.
2. A confirmation dialog shows the full impact. Type the org's **exact name** to confirm.
3. Confirm. Nubi deletes the org and switches you to one of your remaining organizations.

This cannot be undone.

---

## Organization — Members

**Settings → Members** is a dedicated page for managing who's in the org. Owners and admins see two cards: the invite form at the top and the member list below. Members and viewers see the list but no edit controls ("View only — ask an owner or admin to manage members").

![Settings — Members: invite form, pending invites, and the member list with role dropdowns](/docs/screenshots/settings-members.png)

### Roles

Nubi has four org roles in descending order of privilege:

| Role | Permissions |
|---|---|
| **owner** | Everything: manage members, branding, security, billing, delete org |
| **admin** | Manage members, branding, security, and all project content |
| **member** | Read and write project content (queries, dashboards, flows, secrets) |
| **viewer** | Read-only — cannot change anything |

The key line for daily work: **viewer is read-only; owner, admin, and member can all write.** Edit controls are hidden entirely for viewers on many screens.

### Invite a teammate

1. Enter the teammate's **email address**.
2. Choose the **role** they'll receive when they accept. Only owners can offer the **owner** role.
3. Click **Invite**.

An invite link is generated for you to copy and share. Email delivery happens automatically only if it is configured for your deployment — otherwise copy the link manually.

Pending invites appear in a **Pending invites** card. From there you can:

- **Copy link** — copies the shareable invite URL to your clipboard.
- **Revoke** (trash icon) — cancels the invite immediately.

### Change a member's role

On the member's row, pick a new role from the dropdown. The change saves immediately.

Constraints: the **last remaining owner cannot be demoted or removed** (controls are disabled). Only an owner can grant the **owner** role.

### Remove a member

Click the trash icon on the member's row. The last-owner protection applies here too.

---

## Organization — Integrations

**Settings → Integrations** connects the org's notification channels. One connected integration powers **both** inbound chat and outbound alerts (watches, flow runs, shares), so you configure each channel once.

![Settings — Integrations: connected channels with Test / Enable / Edit / Delete controls, and the connect-a-channel picker](/docs/screenshots/settings-integrations.png)

Five channel kinds are available:

| Channel | How it sends | Fields |
|---|---|---|
| **Slack** | Incoming webhook | Channel, webhook URL *(secret)* |
| **WhatsApp** | WhatsApp Business Cloud API | Phone number ID, recipient number, access token *(secret)* |
| **Google Chat** | Incoming webhook | Space label, webhook URL *(secret)* |
| **Microsoft Teams** | Incoming webhook connector | Connector name, webhook URL *(secret)* |
| **Email** | App SMTP | Recipients (comma- or newline-separated) |

### Connect a channel

1. In the **Connect a channel** card, pick a kind — the form opens with the right fields for it.
2. Give it a **Name**, fill in the fields, and click **Connect**. New integrations are enabled immediately.

### Secrets are write-only

Webhook URLs and access tokens are stored encrypted and **never shown again after saving** — not even when editing. When you edit a configured integration, the secret field shows `•••••••• (leave blank to keep)`: leave it empty to keep the stored secret, or type a new value to replace it.

### Manage a connected integration

Each connected integration is a row with:

- **Test** — sends a test message and shows the result inline (success or the error). Available to everyone, including viewers.
- **Enable / Disable** — pause a channel without deleting its configuration.
- **Edit** (pencil) — change the name or any field.
- **Delete** (trash) — asks for an inline **Confirm** before removing.

A **Not configured** badge appears on integrations whose secret hasn't been set yet.

Connecting, editing, enabling/disabling, and deleting require **write access** (owner, admin, or member). Viewers see the list and can send tests, but the edit controls are hidden.

See [Notifications & Integrations](/docs/notifications-and-integrations) for how alerts use these channels.

---

## Organization — Security

**Settings → Security** registers the public keys Nubi uses to verify **host-signed embed JWTs**. When you embed a Nubi dashboard in your own application, your backend signs a short-lived RS256 or ES256 JWT. Nubi verifies the signature against the public keys registered here before granting the viewer access.

With no issuers configured, host-signed embedding is off. Each enabled issuer you add trusts tokens that carry a matching `iss` claim and are signed with a key from that issuer's JWKS. Disable an issuer to pause trust without deleting the configuration.

Write access (owner, admin, or member) is required to create, edit, or delete issuers. Viewers can see the list but not modify it.

### What an issuer entry contains

| Field | Required | Notes |
|---|:---:|---|
| **Name** | Yes | Human label, e.g. `My App Production` |
| **Issuer** (`iss`) | Yes | The `iss` claim value your tokens carry, e.g. `https://myapp.example.com` |
| **JWKS URL** | — | A JWKS endpoint Nubi fetches and refreshes automatically — **recommended** |
| **Inline PEM / JWK** | — | Pasted public key; alternative to a JWKS URL |
| **Algorithms** | Yes | One or more of `RS256`, `RS384`, `RS512`, `ES256`, `ES384`, `ES512` |
| **Audience** (`aud`) | — | Optional expected `aud` claim value |
| **Enabled** | — | Toggle on/off without deleting |

Prefer a **JWKS URL** over an inline key — Nubi caches and rotates keys automatically, so key rotation on your side never requires touching Nubi.

### Add an issuer

1. Open **Settings → Security** and click **Add issuer** (or **Add your first issuer** from the empty state).
2. Enter a **Name** and the **Issuer** (`iss`) value.
3. Provide the signing key:
   - Paste a **JWKS URL** such as `https://myapp.example.com/.well-known/jwks.json`, or
   - Click **Or paste inline PEM / JWK** and paste the public key:

     ```
     -----BEGIN PUBLIC KEY-----
     MIIBIjANBgkqhkiG9w0BAQEFA...
     -----END PUBLIC KEY-----
     ```

4. Select the **Algorithms** your tokens use (tap the chips to toggle).
5. Optionally set an **Audience** (`aud`).
6. Leave **Enabled** on and click **Add issuer**.

A brief **Saved** confirmation appears and the issuer joins the list.

A token your backend mints, once decoded, looks like this:

```json
{
  "iss": "https://myapp.example.com",
  "aud": "nubi-embed",
  "sub": "viewer-42",
  "org": "my-org-id",
  "policies": { "tenant_id": "acme" },
  "exp": 1717920000
}
```

The `policies` object is a flat key/value map that Nubi injects as AST-level `WHERE` predicates — never string-concatenated into SQL.

### Edit, disable, or delete an issuer

- **Edit** — opens the form inline on that row to change any field.
- **Disable** — uncheck **Enabled** and save; the key stays registered but is ignored at verification time.
- **Delete** — removes the issuer entirely. You'll be warned that embed tokens signed by it will stop working immediately.

For the full embedding workflow — minting tokens, row-level security, and mounting `<nubi-dashboard>` — see [Embedding](/docs/embedding).

---

## Organization — Usage

**Settings → Usage** is read-only visibility into what the active org has consumed this period. It used to be a standalone page; the old `/usage` route now redirects here.

![Settings — Usage: per-metric cards with used / limit and a time-series chart for the selected metric](/docs/screenshots/settings-usage.png)

Usage metering is **open-core** — every Nubi deployment has this page. It shows one card per metric (queries run, compute units, bytes scanned, flow runs, AI usage, embedded sessions, storage), a period selector (**Today / 7 days / Month**), and a time-series chart for whichever metric card you select.

This page is deliberately **billing-free**: it never charges, blocks, or implies a hard cap. Soft plan limits (the "used / limit / %" progress bars) only appear when an EE tier configures them; in core everything shows as *unlimited*. Plans, the wallet, and invoices live on the EE-only **Billing** page.

The full metric-by-metric breakdown is in [Billing & Usage](/docs/billing-and-usage).

---

## Project — General

**Settings → Project → General** configures the **currently active project**. If you have multiple projects, a project picker in the page header lets you switch which project you're editing without leaving settings.

Everything on this page requires **write access** (owner, admin, or member). Viewers see a read-only notice and the edit controls are hidden.

### Rename a project

Edit the **Project name** field and click **Save changes**.

### Git sync

The **Git sync** card is embedded directly in project settings so all project configuration stays in one place. Use it to connect the project to a GitHub or GitLab repository and version your queries and dashboards as code. See [Git Sync](/docs/git-sync) for the full workflow.

### Deleting a project

Deleting a project permanently removes it and everything inside it — dashboards, queries, flows, connectors, and secrets. This cannot be undone.

The danger zone shows a precise impact breakdown (e.g. "3 dashboards, 12 queries, 2 flows") so you know exactly what will be removed.

1. Click **Delete project**.
2. Review the impact list in the confirmation dialog and type the project's **exact name**.
3. Confirm. Nubi deletes the project and switches you to a remaining one.

---

## Who can do what — quick reference

| Action | owner | admin | member | viewer |
|---|:---:|:---:|:---:|:---:|
| Edit own profile | ✓ | ✓ | ✓ | ✓ |
| Rename org / set branding | ✓ | ✓ | — | — |
| Invite / remove members, change roles | ✓ | ✓ | — | — |
| Grant the **owner** role | ✓ | — | — | — |
| Delete the organization | ✓ | ✓ | — | — |
| Rename project / configure Git / delete project | ✓ | ✓ | ✓ | — |
| Manage embed JWT issuers (Security) | ✓ | ✓ | ✓ | — |
| Connect / edit / delete integrations | ✓ | ✓ | ✓ | — |
| Send a test message on an integration | ✓ | ✓ | ✓ | ✓ |
| View usage metrics | ✓ | ✓ | ✓ | ✓ |
| Add / delete secrets | ✓ | ✓ | ✓ | — |
| View dashboards, queries, flows | ✓ | ✓ | ✓ | ✓ |

The last owner of an organization can never be demoted or removed — promote someone else to owner first.

---

## Related

- [Embedding](/docs/embedding) — full embed flow: minting JWTs, RLS policies, SDK
- [Notifications & Integrations](/docs/notifications-and-integrations) — what the connected channels deliver
- [Secrets](/docs/secrets) — encrypted credentials for flow tasks
- [Git Sync](/docs/git-sync) — version dashboards and queries as code
- [Billing & Usage](/docs/billing-and-usage) — usage metering (open-core) and billing (Cloud/EE)
