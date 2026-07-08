-- 0027_notifications_email_only.sql
--
-- Trims org_integrations to EMAIL ONLY. Nubi is embedded BI, not a chat-ops
-- platform: Slack / WhatsApp / Google Chat / Teams / generic-webhook
-- connectors are maintenance a small team doesn't need — the embedding host
-- owns its own notification/chat integrations. Email (scheduled reports +
-- alerts) is the one outbound channel Nubi ships.
--
-- This ALTERs the CHECK constraint added in 0011_notifications.sql. Any
-- existing rows with a now-unsupported kind are left in place (this migration
-- does not delete data) but are inert: app.notify.integrations.VALID_KINDS and
-- app.notify.channels.get_channel() no longer recognise them, so
-- channels_for_org() skips them (same treatment as an incomplete/unknown
-- kind). Operators should delete any such legacy rows via
-- DELETE FROM org_integrations WHERE kind <> 'email'; once confirmed unused.

ALTER TABLE org_integrations DROP CONSTRAINT IF EXISTS org_integrations_kind_check;

ALTER TABLE org_integrations ADD CONSTRAINT org_integrations_kind_check
    CHECK (kind IN ('email'));
