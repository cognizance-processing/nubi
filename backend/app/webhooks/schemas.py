"""Pydantic request/response schemas for the webhook admin CRUD API."""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from app.webhooks.events import ALL_EVENT_TYPES


def _validate_webhook_url(value: str) -> str:
    """Reject non-http(s) schemes and obviously malformed URLs at schema time.

    This is a cheap, early defence that rejects garbage before the URL ever
    reaches the SSRF guard.  The SSRF guard (guard_url / resolve_and_pin)
    remains the authoritative check; this validator is not a substitute.

    Raises
    ------
    ValueError
        If the scheme is not ``http`` or ``https``, or if the URL has no host.
    """
    try:
        parts = urlsplit(value)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Malformed URL: {exc}") from exc

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(
            f"Webhook URL scheme {scheme!r} is not allowed; "
            "only http:// and https:// URLs are accepted."
        )
    if not parts.hostname:
        raise ValueError("Webhook URL must include a host component.")
    return value


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

    @field_validator("url")
    @classmethod
    def url_must_be_http_or_https(cls, v: str) -> str:
        return _validate_webhook_url(v)


class WebhookUpdate(BaseModel):
    """Request body for ``PUT /webhooks/{endpoint_id}`` (all fields optional)."""

    name: str | None = None
    url: str | None = None
    secret: str | None = Field(
        None, min_length=8, description="New signing secret (rotates the old one)."
    )
    event_types: list[str] | None = None
    active: bool | None = None

    @field_validator("url")
    @classmethod
    def url_must_be_http_or_https(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_webhook_url(v)
