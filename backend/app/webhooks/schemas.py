"""Pydantic request/response schemas for the webhook admin CRUD API."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.webhooks.events import ALL_EVENT_TYPES


class WebhookCreate(BaseModel):
    """Request body for ``POST /webhooks``."""

    name: str = Field("webhook", description="Human-readable label for this endpoint.")
    url: str = Field(..., description="Destination HTTPS URL the signed POST is delivered to.")
    secret: str = Field(
        ...,
        min_length=8,
        description="Signing secret (HMAC-SHA256 key). Stored encrypted; never returned.",
    )
    event_types: list[str] = Field(
        default_factory=list,
        description=f"Subscribed event types. Allowed: {', '.join(ALL_EVENT_TYPES)}.",
    )
    active: bool = Field(True, description="When false, the endpoint receives no deliveries.")


class WebhookUpdate(BaseModel):
    """Request body for ``PUT /webhooks/{endpoint_id}`` (all fields optional)."""

    name: str | None = None
    url: str | None = None
    secret: str | None = Field(
        None, min_length=8, description="New signing secret (rotates the old one)."
    )
    event_types: list[str] | None = None
    active: bool | None = None
