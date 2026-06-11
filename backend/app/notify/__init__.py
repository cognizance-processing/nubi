"""Notification system for Nubi.

Provides multi-channel alert delivery (Slack, WhatsApp, Email, Null) and
flow-failure listener registration.

Public API
----------
from app.notify.channels import (
    Channel, NullChannel, SlackChannel, WhatsAppChannel, EmailChannel,
    GoogleChatChannel, TeamsChannel, get_channel,
)
from app.notify.alerts import notify_alert
from app.notify.integrations import (
    IntegrationStore, get_integration_store, channels_for_org,
)
"""

# Per-org connected integrations (Agent A). Re-exported so callers can do
# ``from app.notify import channels_for_org`` — the seam Agent B's dispatcher
# uses. Import is lazy-safe (the module does no I/O at import time).
from app.notify.integrations import (  # noqa: E402,F401
    IntegrationStore,
    channels_for_org,
    get_integration_store,
    set_integration_store_for_tests,
)

# In-app feed + Web Push + the unified dispatch path (Agent B). ``notify_event``
# is the ONE call alert sources use to land an event in the feed, send Web Push,
# and fan out to the org's channels.
from app.notify.dispatch import notify_event  # noqa: E402,F401
from app.notify.notifications import (  # noqa: E402,F401
    NotificationStore,
    get_notification_store,
    set_notification_store_for_tests,
)
from app.notify.push import (  # noqa: E402,F401
    PushStore,
    get_push_store,
    send_push,
    set_push_store_for_tests,
)
