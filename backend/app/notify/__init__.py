"""Notification system for Nubi.

Provides multi-channel alert delivery (Slack, WhatsApp, Email, Null) and
flow-failure listener registration.

Public API
----------
from app.notify.channels import (
    Channel, NullChannel, SlackChannel, WhatsAppChannel, EmailChannel,
    get_channel,
)
from app.notify.alerts import notify_alert
"""
