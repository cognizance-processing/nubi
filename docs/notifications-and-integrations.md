# Notifications & Integrations

Status: implementation contract (agents follow this). Grounded in existing code.

Goal: one **connected integration** per org powers BOTH inbound chat
(`app/chat/gateway.py`) AND outbound alerts (`app/notify/*`). Add Google Chat +
Teams channels, real per-org persistence + a Settings UI, an in-app notification
feed, email alerts, and Web Push (VAPID). Alerts from across the system
(watches, flow runs, shares, …) flow through ONE dispatch path that writes an
in-app notification, sends Web Push, and fans out to the org's channels.

## 0. What already exists (reuse, do not reinvent)

- `app/notify/channels.py` — `Channel` protocol (`send(text, image_png)`),
  `NullChannel`, `SlackChannel`, `WhatsAppChannel`, `EmailChannel`,
  `get_channel(kind, config)`. **Add `GoogleChatChannel` + `TeamsChannel` here.**
- `app/notify/alerts.py` — `notify_alert(event)`, `format_alert_text`. Channels
  currently resolved from GLOBAL app settings (`_get_configured_channels`).
- `app/chat/notify.py` — `channels_for(config)`, `notify_flow_run(event)`;
  per-watch / per-flow alert config resolution.
- `app/chat/gateway.py` — inbound webhook verify + normalize (Slack/WhatsApp),
  `ChatTransport`, outbound `OutboundMessage`.
- `app/routes/integrations.py` — THIN: lists channel status from app settings,
  `POST /integrations/test`, best-effort `POST /integrations` (no real DB). **To
  be replaced with real per-org CRUD (Agent A).**
- `app/routes/watches.py` — dispatches breaches via `app/chat/notify` channels.
- Secret storage pattern — `app/connectors/secret_store.py` (`PgSecretStore`,
  AES-256-GCM via `app/security/crypto.py::encrypt_json/decrypt_json`, one blob
  per id). **Mirror this for `integration_secrets`.**
- Migration **0011_notifications.sql** (already written) — tables:
  `org_integrations`, `integration_secrets`, `notifications`,
  `notification_reads`, `push_subscriptions`. Use these table names verbatim.
- Auth deps — `app/auth/deps.py::current_user`, org resolution in
  `app/routes/_org.py` (`resolve_org_id`). Routes are registered in
  `backend/main.py` (e.g. `import app.routes.integrations`).
- Frontend shell — `src/layouts/AppShell.jsx` (mounts `AppRightRail`,
  `ChatPanelWrapper`, `GitPanelWrapper`), `src/components/app/AppTopbar.jsx`,
  settings pages in `src/pages/app/settings/` (+ `SettingsLayout.jsx`).

## 1. Data model (migration 0011 — DONE)

- `org_integrations(id, org_id, created_by, kind, name, config jsonb, enabled, …)`
  — kind ∈ slack | whatsapp | google_chat | teams | email | webhook. `config`
  is NON-SECRET only.
- `integration_secrets(integration_id PK, org_id, ciphertext, nonce, key_version, …)`
  — AES-GCM, mirrors connector_secrets.
- `notifications(id, org_id, user_id NULL, type, severity, title, body, link,
  metadata, read_at, created_at)` — user_id NULL ⇒ org broadcast.
- `notification_reads(notification_id, user_id, read_at)` — per-user read state
  for broadcasts.
- `push_subscriptions(id, user_id, org_id, endpoint UNIQUE, p256dh, auth,
  user_agent, …)`.

### Secret vs non-secret per kind

| kind         | non-secret `config`                         | secret (integration_secrets)        |
|--------------|---------------------------------------------|-------------------------------------|
| slack        | `channel`, `mode` (webhook\|bot)            | `webhook_url` OR `bot_token`        |
| whatsapp     | `phone_number_id`, `to`                     | `access_token`                      |
| google_chat  | `space` (label)                             | `webhook_url`                       |
| teams        | `name`                                      | `webhook_url`                       |
| email        | `recipients` (string[])                     | (none; uses app SMTP) OR `smtp_*`   |
| webhook      | `url_is_secret:false?`                      | `url` (treat as secret by default)  |

## 2. Module boundaries (so agents don't collide)

**Agent A owns** (integrations + channels):
- `app/notify/channels.py` — add `GoogleChatChannel`, `TeamsChannel`; register in `get_channel`.
- `app/notify/integrations.py` (NEW) — `IntegrationStore` (CRUD over
  `org_integrations` + `integration_secrets`, AES-GCM, mirrors
  `connectors/secret_store.py`) and **`channels_for_org(org_id) -> list[Channel]`**
  (build live Channel objects from every enabled integration, merging secret +
  config). This is the function Agent B's dispatcher calls.
- `app/routes/integrations.py` — REPLACE with real per-org CRUD:
  `GET /integrations`, `POST /integrations`, `GET/PUT/DELETE /integrations/{id}`,
  `POST /integrations/{id}/test`. Responses scrub secrets (return only
  `configured: true/false` + non-secret config). Reuse `current_user` +
  `resolve_org_id`.
- Wire `app/chat/notify.py` / `app/notify/alerts.py` channel resolution to ALSO
  include `channels_for_org(org_id)` (per-org integrations), keeping app-settings
  fallback. Wire `app/chat/gateway.py` outbound to resolve transport from the
  org's connected integration of the inbound platform.

**Agent B owns** (notifications feed + push + dispatch):
- `app/notify/notifications.py` (NEW) — `NotificationStore`: `create(...)`,
  `list_for_user(org_id, user_id, *, unread_only, limit)`,
  `mark_read(id, user_id)`, `mark_all_read(org_id, user_id)`, `unread_count(...)`.
  Handles broadcast (user_id NULL) read-state via `notification_reads`.
- `app/notify/push.py` (NEW) — VAPID Web Push via `pywebpush`; `send_push(sub,
  payload)`, key config from settings (`VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY`/
  `VAPID_SUBJECT`); dead-subscription pruning on 404/410.
- `app/notify/dispatch.py` (NEW) — **`notify_event(org_id, event, *, user_ids=None)`**:
  (1) write a `notifications` row (broadcast if user_ids None), (2) send Web Push
  to those users' subscriptions, (3) fan out to channels via Agent A's
  `channels_for_org(org_id)`. Best-effort: a channel/push failure never raises.
  This is the ONE path callers use (watches, flow runs, …).
- `app/routes/notifications.py` (NEW) — `GET /notifications` (feed, paginated,
  `?unread=1`), `GET /notifications/unread_count`, `POST /notifications/{id}/read`,
  `POST /notifications/read_all`.
- `app/routes/push.py` (NEW) — `GET /push/vapid_key` (public key),
  `POST /push/subscribe` (upsert by endpoint), `POST /push/unsubscribe`.
- Re-point `app/routes/watches.py` breach dispatch to call `notify_event(...)`
  so a watch breach also lands in the in-app feed + push (keep the channel send).

**Coordination seam:** Agent B's `dispatch.notify_event` imports Agent A's
`app.notify.integrations.channels_for_org`. Both exist as separate modules; the
only shared edits are `notify/__init__.py` exports (append, don't rewrite) and
`backend/main.py` router registration — the ORCHESTRATOR does main.py + final
`notify/__init__.py` reconciliation to avoid a merge conflict. Agents must NOT
edit `backend/main.py`.

**Frontend agent owns** all of `src/`:
- `Settings → Integrations` page (new `src/pages/app/settings/IntegrationsSettings.jsx`
  + nav entry in `SettingsLayout.jsx`): list/connect/edit/delete/test per kind
  (Slack/WhatsApp/Google Chat/Teams/Email), secret fields write-only.
- Notifications center: a bell in `AppTopbar` (or `AppRightRail`) with an unread
  badge (poll `GET /notifications/unread_count`) + a panel listing the feed with
  read/mark-all-read + deep links.
- Web Push: a service worker (`public/sw.js` or Vite PWA), opt-in toggle (e.g. in
  ProfileSettings or the notifications panel) that subscribes via
  `GET /push/vapid_key` → `pushManager.subscribe` → `POST /push/subscribe`.
- Wire the watches/alerts UI (`WatchesPage`) to pick from connected integrations
  instead of a free-form Slack channel string.
- A small `src/lib/notificationsApi.js` + `integrationsApi.js`.

## 3. Endpoints summary

Integrations (A): `GET /integrations`, `POST /integrations`,
`GET/PUT/DELETE /integrations/{id}`, `POST /integrations/{id}/test`.
Notifications (B): `GET /notifications`, `GET /notifications/unread_count`,
`POST /notifications/{id}/read`, `POST /notifications/read_all`.
Push (B): `GET /push/vapid_key`, `POST /push/subscribe`, `POST /push/unsubscribe`.

## 4. Config / deps

- Backend: add `pywebpython`→ **`pywebpush`** to `backend/requirements.txt`;
  VAPID keys via settings/env (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`,
  `VAPID_SUBJECT`). Degrade gracefully (push no-op + clear log) if unset.
- Frontend: no new hard deps required (use the native Service Worker + Push API).

## 5. Verification bar

- Backend: `cd backend && python -m pytest tests/ -k "integration or notif or push or channel or watch" -q` green; add tests for channel send (mocked httpx), integration CRUD + secret scrubbing + tenant isolation, notification feed incl. broadcast read-state, push subscribe/prune, and `notify_event` end-to-end (in-app + channels fan-out with a fake channel).
- Frontend: `npm run build` green; `npx eslint <changed files>` zero new errors (repo enforces `react-hooks/set-state-in-effect`).
