# Notifications & Integrations

Nubi is embedded BI, not a chat-ops platform: the one outbound **notify
channel** it ships is **email** (scheduled reports + alerts). Slack, WhatsApp,
Microsoft Teams, and Google Chat integrations are explicitly out of scope —
the embedding host owns any chat/Slack-style notifications it wants to layer
on top. Alongside email, Nubi ships an in-app **notifications feed** and
**Web Push**, both fed by a single dispatch path.

## What ships today

- **Per-org email integration** — one connected integration per org
  (`org_integrations`, kind constrained to `email`), with secret material
  (e.g. custom SMTP credentials) AES-256-GCM encrypted at rest in
  `integration_secrets`, mirroring the connector secret store.
- **In-app notifications feed** — a per-user/org feed (`notifications` table)
  with read/unread state, including org-wide broadcasts (`notification_reads`
  tracks per-user read state for a broadcast row).
- **Web Push** — VAPID-based browser push (`push_subscriptions`), with
  dead-subscription pruning on delivery failure.
- **One dispatch path** — alerts raised anywhere in the system (watch
  breaches, flow-run completion/failure, shares, …) funnel through a single
  `notify_event(...)` call that (1) writes an in-app notification, (2) sends
  Web Push to subscribed devices, and (3) sends email via the org's connected
  integration, if configured. A channel or push failure is best-effort and
  never raises.

## Why not Slack / WhatsApp / Teams / Google Chat

A small embedded-BI team doesn't carry the maintenance surface of four
chat-platform integrations (webhook formats, rate limits, OAuth flows, per-
platform message formatting). Every one of those platforms already has a
first-class way to receive alerts from the **host application** — so the host
is the right owner. Nubi stays inside its lane: govern the data, raise the
alert, deliver it by email, and hand the host a webhook (see
[Outbound webhooks](semantic-and-data-apps.md#outbound-webhooks)) so it can
fan the same event out to its own Slack/Teams/whatever if it wants to.

An earlier iteration of this feature did add `SlackChannel`, `WhatsAppChannel`,
a `GoogleChatChannel`, and a `TeamsChannel`, plus an inbound chat gateway that
verified Slack/WhatsApp webhooks. All of that was removed; the `org_integrations`
table's `kind` check constraint is now `CHECK (kind IN ('email'))`
(migration `0027_notifications_email_only.sql`). Legacy rows with an
unsupported kind are left in place (not deleted) but are inert — the
integration factory treats an unsupported kind exactly like an incomplete one
and skips it.

## Endpoints

**Integrations** (email only):

| Method / path | Purpose |
|---|---|
| `GET /integrations` | List the org's connected integrations. Secrets are scrubbed; each row carries `configured: bool`. |
| `POST /integrations` | Create an integration. Secret fields are split out and encrypted. |
| `GET /integrations/{id}` | Fetch one (secrets scrubbed). |
| `PUT /integrations/{id}` | Update non-secret config and/or rotate the secret. |
| `DELETE /integrations/{id}` | Delete the integration and its secret blob. |
| `POST /integrations/{id}/test` | Build the live channel and send a test message. |

**Notifications feed:**

| Method / path | Purpose |
|---|---|
| `GET /notifications` | Paginated feed; `?unread=1` filters to unread. |
| `GET /notifications/unread_count` | Badge count for the current user. |
| `POST /notifications/{id}/read` | Mark one notification read. |
| `POST /notifications/read_all` | Mark the whole feed read. |

**Web Push:**

| Method / path | Purpose |
|---|---|
| `GET /push/vapid_key` | Public VAPID key for `pushManager.subscribe`. |
| `POST /push/subscribe` | Upsert a push subscription by endpoint. |
| `POST /push/unsubscribe` | Remove a push subscription. |

All routes are org-scoped via the standard `current_user` + `resolve_org_id`
dependencies; a row belonging to a different org is treated as not-found.

## Security

- Secret fields (e.g. custom SMTP credentials) are never stored in the
  non-secret `config` jsonb and never returned by any read endpoint — listings
  carry only non-secret config plus a `configured` boolean.
- Secrets are AES-256-GCM encrypted (`app.security.crypto`); the DB holds only
  ciphertext, nonce, and key version.
- Every dispatch is best-effort per channel: a failed email send, push
  delivery, or notification write is isolated and logged — it never fails the
  triggering request (a watch breach, a flow-run completion, …).

## Settings UI

`Settings → Integrations` lists the org's email integration (connect / edit /
delete / send test), and the notifications bell in the app topbar shows the
unread badge and the feed panel with mark-read / mark-all-read actions. Users
can opt in to Web Push from the notifications panel or their profile settings.

## See also

- [Outbound webhooks](semantic-and-data-apps.md#outbound-webhooks) — the
  event catalog, HMAC signing, and per-org endpoint management a host uses to
  receive the same events (watch breaches, flow completion, …) and fan them
  out to its own Slack/Teams/whatever.
- [Exports & scheduled reports](exports-and-jobs.md) — email delivery for
  scheduled dashboard/report sends.
