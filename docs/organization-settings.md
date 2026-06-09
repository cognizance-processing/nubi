# Organization & settings

Everything that controls who can use your workspace, what they can do in it, and how it connects to the outside world lives under **Settings**. This page is a tour of those screens: your profile, your organization (members, roles, branding), your projects, the security configuration for embedding, and where secrets and billing live.

Open **Settings** from the bottom of the left sidebar. It lands on a two-column layout: a sidebar on the left (grouped by scope) and the active section on the right.

The sidebar groups are:

| Group | Nav item | Route | What it controls |
|---|---|---|---|
| **Account** | Profile | `/settings/profile` | Your display name and avatar |
| **Organization** | General | `/settings/organization` | Org name, branding, deletion |
| **Organization** | Members | `/settings/members` | Invites, roles, removals |
| **Organization** | Security | `/settings/security` | Embed JWT issuers (host-signed token verification) |
| **Organization** | Billing *(Cloud/EE only)* | `/billing` | Plan, usage, invoices |
| **Project** | General | `/settings/project` | Project name, Git sync, deletion |

Two related things live just outside the Settings screen:

- **Secrets** are flow-scoped and live under the Flows workspace at **Flows → Secrets**, not in Settings.
- **Billing** is a separate top-level page (`/billing`) and only appears on Nubi Cloud / EE builds where billing is enabled. See [Where billing lives](#where-billing-lives).

---

## Your profile

**Settings → Profile** manages how you appear to other members. It is account-wide — it follows you across every organization you belong to.

You can change:

- **Avatar** — defaults to your Google profile picture. Set a custom image URL or upload a file to override it.
- **Display name** — the name teammates see in the member list and elsewhere.

Your **email address is read-only** here; it comes from how you signed in and cannot be changed on this screen.

**To update your profile:**

1. Open **Settings → Profile**.
2. Edit your avatar and/or display name.
3. Click **Save profile**. A green **Saved** confirmation appears for a few seconds.

---

## Organization settings

**Settings → General** (under the Organization group) is where you manage the org itself: its identity, and (if you must) its deletion. Changes here affect **all members**.

Members are managed on their own dedicated page at **Settings → Members**.

> The **Personal** workspace is a special case — it cannot be renamed or deleted, and it has no members. The branding and danger-zone controls described below only appear for real organizations.

### Name and branding

Owners and admins can set:

- **Organization avatar** — a logo/image shown for the org.
- **Organization name** — the display name shown in the org switcher and to all members.

Edit the fields and click **Save changes**. If you only have **member** or **viewer** access, this section is read-only and you'll see a note saying so.

### Members and roles

Go to **Settings → Members** to manage who's in the organization. The page has two main cards: the invite form at the top (for owners/admins), and the members list below it.

Nubi has four organization roles, in descending order of privilege:

| Role | Can do |
|---|---|
| **owner** | Everything, including managing members, billing, and deleting the org |
| **admin** | Manage members, branding, security, and all project content |
| **member** | Read and **write** project content (queries, dashboards, flows, secrets) |
| **viewer** | **Read-only** — can view but not change anything |

Write access is the line that matters day-to-day: **viewer is read-only; owner, admin, and member can all write.** Many screens (project settings, secrets, Git) hide their edit controls entirely for viewers.

**Only owners and admins can manage members.** If you're a member or viewer, the members card shows a "View only — ask an owner or admin to manage members" note instead of editing controls.

#### Invite a teammate

The **Invite a teammate** card appears at the top of the Members page (owners/admins only).

1. Enter the teammate's **email address**.
2. Choose the **role** they'll get when they accept. (Only owners can offer the **owner** role.)
3. Click **Invite**.

An invite link is generated for you to share. An email is sent automatically **only if email delivery is configured** for your deployment — otherwise, copy the link and send it yourself.

Pending invites appear in a **Pending invites** card once at least one invite exists, each showing the email and role. From there you can:

- **Copy link** — copies the shareable invite URL to your clipboard.
- **Revoke** (trash icon) — cancels the invite so the link stops working.

#### Change a member's role

1. In the **Members** card, find the person.
2. Pick a new role from the dropdown next to their name.

The change saves immediately. Two safeguards apply: the **last remaining owner cannot be demoted or removed** (the controls are disabled), and only an owner can grant the **owner** role.

#### Remove a member

Click the trash icon on the member's row. The same last-owner protection applies.

### Deleting an organization (danger zone)

The **Danger zone** is owner/admin only and permanently removes the organization and everything in it. **This cannot be undone.**

Nubi blocks the delete if the org still has projects. In that case you'll see a message like "This organisation has N projects. Delete all projects first" and the **Delete organisation** button is disabled.

Once the org has no projects:

1. Click **Delete organisation**.
2. A confirmation dialog shows the impact and asks you to **type the organization's exact name** to confirm.
3. Confirm. Nubi deletes the org and switches you to one of your remaining organizations.

---

## Project settings

**Settings → General** (under the Project group) configures the **currently active project**. If you have multiple projects, a project picker in the page header lets you switch which project you're editing without leaving settings. The page rolls three things into one place: renaming, Git sync, and deletion. Everything here requires **write access**; viewers see a read-only notice and the edit controls are hidden.

### Rename a project

1. Edit the **Project name** field.
2. Click **Save changes**.

### Git sync

The **Git sync** section embeds the Git panel directly into project settings so all project configuration lives together. Use it to connect the project to a GitHub or GitLab repository and commit your queries and dashboards as code. For the full workflow, see [Git Sync](/docs/git-sync).

### Deleting a project (danger zone)

Deleting a project permanently removes it and **everything inside it** — dashboards, queries, flows, connectors, secrets, and automations. **This cannot be undone.**

The danger zone shows a precise impact breakdown (e.g. how many dashboards, queries, and flows will be deleted) so you know exactly what you're about to lose.

1. Click **Delete project**.
2. In the confirmation dialog, review the impact list and **type the project's exact name**.
3. Confirm. Nubi deletes the project and switches you to a remaining one.

---

## The security dial — embed authentication

**Settings → Security** is the org-wide control for **embed authentication**. When you embed a Nubi dashboard inside your own application, your backend mints a short-lived signed JWT that says which org/project the viewer belongs to and what they're allowed to see. Nubi must verify that token's signature before granting access — and this page is where you register the public keys to verify it against.

Think of it as a dial: with **no issuers configured**, host-signed embedding is effectively off. Each **enabled** issuer you add opens the door a little wider, allowing tokens signed by that specific key and matching that specific `iss` claim. Only **enabled** issuers are consulted at verification time, so you can dial access up or down without deleting configuration.

### What an issuer entry contains

| Field | Meaning |
|---|---|
| **Name** | A human label, e.g. `My App Production` (required) |
| **Issuer** | The `iss` claim value your tokens carry, e.g. `https://myapp.example.com` (required) |
| **JWKS URL** | A JWKS endpoint Nubi fetches and refreshes automatically — **recommended** |
| **Inline PEM / JWK** | A pasted public key, as an alternative to a JWKS URL |
| **Algorithms** | One or more of `RS256`, `RS384`, `RS512`, `ES256`, `ES384`, `ES512` (required) |
| **Audience** | Optional expected `aud` claim |
| **Enabled** | Toggle an issuer on/off without deleting it |

Prefer a **JWKS URL** over an inline key: Nubi caches and rotates the keys automatically, so key rotation on your side doesn't require touching Nubi.

### Add an issuer

1. Open **Settings → Security** and click **Add issuer** (or **Add your first issuer** from the empty state).
2. Enter a **Name** and the **Issuer** (`iss`) value.
3. Provide your signing key one of two ways:
   - Paste a **JWKS URL** such as `https://myapp.example.com/.well-known/jwks.json`, or
   - Click **Or paste inline PEM / JWK** and paste the public key:

     ```text
     -----BEGIN PUBLIC KEY-----
     MIIBIjANBgkqhkiG9w0BAQEFA...
     -----END PUBLIC KEY-----
     ```
4. Select the **Algorithms** your tokens are signed with (tap the chips to toggle them).
5. Optionally set an **Audience** (`aud`).
6. Leave **Enabled** on, then click **Add issuer**.

A short **Saved** confirmation appears and the issuer joins the list.

The token your backend mints might look like this once decoded:

```json
{
  "iss": "https://myapp.example.com",
  "aud": "nubi-embed",
  "sub": "viewer-42",
  "policies": [{ "table": "orders", "column": "tenant_id", "value": "acme" }],
  "exp": 1717920000
}
```

### Edit, disable, or delete an issuer

- **Edit** — opens the form inline on that row to change any field.
- **Disable** — turn off the **Enabled** toggle and save; the key stays registered but is ignored at verification time.
- **Delete** — removes the issuer entirely. You'll be warned that **embed tokens signed by it will stop working**.

For the full embedding flow — minting tokens, row-level security, and mounting `<nubi-dashboard>` — see [Embedding](/docs/embedding).

---

## Secrets management

Secrets are encrypted credentials (API keys, access tokens, connection passwords) that your **flow tasks** reference by name. They are **org-scoped** and live under the Flows workspace at **Flows → Secrets** — not in the Settings screen.

A secret is never displayed after it's saved. The list shows only the **name** and the **date added**; the value is masked (`••••••••`) and is **never returned by the API**. Values are **encrypted at rest with AES-256-GCM**. Store your value somewhere safe when you create it, because Nubi cannot show it to you again.

### Reference a secret in a flow

In task configuration, reference a secret by name with the `{{ secrets.NAME }}` template:

```
{{ secrets.S3_ACCESS_KEY }}
```

Nubi resolves it server-side at run time — the value is injected only inside the secured execution environment.

### Add a secret

1. Go to **Flows → Secrets** and click **Add secret**.
2. Enter a **Name**. It must start with a letter and contain only letters, digits, underscores, and hyphens (e.g. `S3_ACCESS_KEY`).
3. Paste the **Value** (use the eye icon to reveal what you typed).
4. Click **Save secret**.

### Delete a secret

Click the trash icon on the secret's row and confirm. Any flow task referencing it will stop being able to use it. **This cannot be undone.**

> **Permissions:** adding and deleting secrets requires write access. Viewers see the list but no edit controls. See [Secrets](/docs/secrets) and [Connector Security](/docs/connector-security) for the full security model.

---

## Where billing lives

Billing is **not** part of the Settings screen. On Nubi Cloud and Enterprise (EE) builds where billing is enabled, it's a separate top-level page at **`/billing`**. There you manage your plan, usage, and (where applicable) auto top-up.

On open-source / self-hosted builds without the billing module, the `/billing` route simply doesn't render — there's nothing to manage because there's no metered billing. For pricing tiers and usage details, see [Billing & Usage](/docs/billing-and-usage).

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
| Add / delete secrets | ✓ | ✓ | ✓ | — |
| View dashboards, queries, flows | ✓ | ✓ | ✓ | ✓ |

The last owner of an organization can never be demoted or removed — promote someone else to owner first.
