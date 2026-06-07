"""Application configuration loaded from environment variables via pydantic-settings."""

import os
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Minimum acceptable JWT secret length (bytes).  HS256 keys should be at least
# as long as the hash output (32 bytes = 256 bits) per RFC 7518 §3.2.
_JWT_SECRET_MIN_BYTES = 32

# Env file lives at the REPO ROOT (shared by Vite + backend). Select which one
# via ENV_FILE (e.g. .env, .env.dev, .env.main); defaults to <root>/.env.
# A relative ENV_FILE is resolved against the repo root. Real process-env vars
# always take precedence over the file, so tests/containers stay authoritative.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = os.getenv("ENV_FILE", ".env")
_ENV_FILE_PATH = _ENV_FILE if os.path.isabs(_ENV_FILE) else str(_REPO_ROOT / _ENV_FILE)


class Settings(BaseSettings):
    """All runtime configuration read from environment variables.

    Required variables must be present; optional ones have defaults.
    No secret values are ever logged or exposed in error responses.
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE_PATH,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # the shared root env file also holds VITE_* keys
    )

    # ── Database ────────────────────────────────────────────────────────────
    DATABASE_URL: str  # postgres://user:pass@host/db?sslmode=require

    # ── JWT ─────────────────────────────────────────────────────────────────
    JWT_SECRET: str
    JWT_ACCESS_TTL_MIN: int = 15  # access token lifetime in minutes

    @field_validator("JWT_SECRET")
    @classmethod
    def _jwt_secret_length(cls, value: str) -> str:
        """Reject secrets shorter than 32 bytes (256 bits) per RFC 7518 §3.2."""
        if len(value.encode()) < _JWT_SECRET_MIN_BYTES:
            raise ValueError(
                f"JWT_SECRET must be at least {_JWT_SECRET_MIN_BYTES} bytes "
                f"(got {len(value.encode())} bytes).  "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return value

    # ── Google OAuth ─────────────────────────────────────────────────────────
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str

    # ── Application URLs ─────────────────────────────────────────────────────
    FRONTEND_URL: str

    # ── Superuser / seed admin ───────────────────────────────────────────────
    # The bootstrap admin account created by the DB reset/seed flow. Set these
    # in the root env file. Optional defaults so existing env still validates.
    SUPERUSER_EMAIL: str = "admin@nubi.dev"
    SUPERUSER_PASSWORD: str = "nubi-admin-2026"
    SUPERUSER_NAME: str = "Nubi Admin"

    # Comma-separated string in env (e.g. "http://a.com,http://b.com").
    # Kept as a plain str so pydantic-settings does NOT attempt to JSON-decode it
    # (it pre-parses complex/List fields from env before validators run, which
    # crashes on a non-JSON value). Read the parsed list via ``cors_origins``.
    CORS_ORIGINS: str = ""

    # ── Cookie / Security ────────────────────────────────────────────────────
    COOKIE_SECURE: bool = True  # set False only in local dev (non-HTTPS)

    # ── Runtime environment ──────────────────────────────────────────────────
    ENV: str = "production"  # e.g. "development", "test", "production"

    # ── Jobs scheduler ───────────────────────────────────────────────────────
    # Set JOBS_SCHEDULER_ENABLED=true to activate the background tick that runs
    # due jobs on the interval below.  Defaults to False so tests and normal dev
    # runs do not spawn a background task inadvertently.
    JOBS_SCHEDULER_ENABLED: bool = False
    JOBS_SCHEDULER_INTERVAL_S: int = 30  # seconds between scheduler ticks

    # ── Flows worker ─────────────────────────────────────────────────────────
    # Set FLOWS_WORKER_ENABLED=true to activate the background tick that
    # materializes due scheduled flows and drains ready task_runs on the
    # interval below.  Defaults to False so tests and normal dev runs do not
    # spawn a background task inadvertently.  POST /flows/{id}/run executes
    # synchronously regardless of this setting.
    FLOWS_WORKER_ENABLED: bool = False
    FLOWS_WORKER_INTERVAL_S: int = 5  # seconds between flow-worker ticks

    # The flows engine is the single home for scheduled automation.  This is
    # the canonical switch for the background flow worker.  When left unset it
    # inherits its value from the (legacy) jobs scheduler / flows-worker flags
    # so a single environment variable can turn on all scheduled automation.
    FLOWS_SCHEDULER_ENABLED: bool | None = None

    # ── Flows tick (Cloud Run / Cloud Scheduler) ─────────────────────────────
    # Shared secret for the internal POST /flows/tick endpoint.  Google Cloud
    # Scheduler calls /flows/tick on cron with the header
    # ``X-Nubi-Tick-Secret: <FLOWS_TICK_SECRET>`` so the engine advances without
    # an always-on worker (Cloud Run throttles CPU off-request + scales to zero).
    # When empty the /flows/tick endpoint is disabled (returns 503).  This is
    # NOT a user JWT — it gates the internal scheduler webhook only.
    FLOWS_TICK_SECRET: str = ""

    @field_validator("FLOWS_SCHEDULER_ENABLED", mode="before")
    @classmethod
    def _coerce_flows_scheduler_enabled(cls, value: object) -> object:
        """Treat empty strings as 'unset' so the default-inheritance kicks in."""
        if value is None or value == "":
            return None
        return value

    # ── Kernel security ──────────────────────────────────────────────────────
    # Allow the local subprocess kernel in non-production environments (dev/test).
    # In production, set KERNEL_LOCAL_ENABLED=false and configure a sandboxed
    # remote runner (E2B/Modal).  The default is True so that local development
    # works out of the box without any extra configuration.
    KERNEL_LOCAL_ENABLED: bool = True

    # ── Remote kernel provider ────────────────────────────────────────────────
    # Set KERNEL_REMOTE_PROVIDER to 'e2b' or 'modal' to enable remote sandbox
    # execution.  When a remote provider is configured (provider name + API key
    # present), it is used in ALL environments including production.
    #
    # E2B (primary, fully tested):
    #   KERNEL_REMOTE_PROVIDER=e2b
    #   E2B_API_KEY=e2b-...
    #   pip install e2b-code-interpreter
    #
    # Modal (adapter, see remote_modal.py):
    #   KERNEL_REMOTE_PROVIDER=modal
    #   MODAL_TOKEN_ID=...
    #   MODAL_TOKEN_SECRET=...
    #   pip install modal
    KERNEL_REMOTE_PROVIDER: str = ""   # '' | 'e2b' | 'modal'
    E2B_API_KEY: str = ""              # E2B API key (e2b-code-interpreter)
    MODAL_TOKEN_ID: str = ""           # Modal token ID
    MODAL_TOKEN_SECRET: str = ""       # Modal token secret

    # ── Connector secret encryption ──────────────────────────────────────────
    # AES-256-GCM application-layer encryption for connector credentials.
    # The DB stores only ciphertext + nonce + key_version; the master key lives
    # exclusively in the application environment, NEVER in the database.
    #
    # Simple form (single active key):
    #   CONNECTOR_SECRET_KEY=<base64-encoded 32 bytes>
    #   CONNECTOR_SECRET_KEY_VERSION=1  (default)
    #
    # Extended form (multi-key rotation):
    #   CONNECTOR_SECRET_KEYS='{"1":"<b64>","2":"<b64>"}' — overrides simple form;
    #   the highest numeric version is treated as the current encryption key.
    CONNECTOR_SECRET_KEY: str = ""
    CONNECTOR_SECRET_KEY_VERSION: int = 1
    CONNECTOR_SECRET_KEYS: str = ""  # JSON map of version->b64key; overrides above when set

    # ── Chat webhook signing secrets ────────────────────────────────────────
    # When set, real HMAC-SHA256 signature verification is enforced on the
    # corresponding webhook endpoint.  Defaults to "" so that existing
    # deployments continue to start without changes; empty-string means
    # "not configured" (permissive in non-production, fail-closed in production).
    SLACK_SIGNING_SECRET: str = ""      # HMAC-SHA256 key for X-Slack-Signature
    WHATSAPP_APP_SECRET: str = ""       # HMAC-SHA256 key for X-Hub-Signature-256

    # ── Chat-over-channel org binding ─────────────────────────────────────────
    # Maps an inbound chat workspace/sender to a Nubi org so the agentic chat is
    # org-scoped (and only the allowlisted chat tools run — never arbitrary SQL).
    #
    # CHAT_DEFAULT_ORG_ID — fallback org for any inbound message when no
    #   workspace-specific binding matches.  Leave empty to require an explicit
    #   binding (unbound messages then run unscoped/denied by the tools' RLS).
    #
    # CHAT_ORG_BINDINGS — JSON object mapping a workspace/sender key to an
    #   org_id, e.g. '{"slack:T0123": "org-abc", "whatsapp:+27821234567":
    #   "org-xyz"}'.  Keys are matched as "<platform>:<workspace-or-sender>".
    CHAT_DEFAULT_ORG_ID: str = ""
    CHAT_ORG_BINDINGS: str = ""  # JSON map "<platform>:<key>" -> org_id

    # ── Slack integration (alerts + bot chat) ────────────────────────────────
    # All optional.  Set at least one of SLACK_ALERT_WEBHOOK or SLACK_BOT_TOKEN
    # to enable Slack alert delivery.  SLACK_BOT_TOKEN also enables chat.postMessage
    # and file uploads (chart PNGs).
    SLACK_BOT_TOKEN: str = ""           # xoxb-… Slack bot OAuth token
    SLACK_ALERT_WEBHOOK: str = ""       # Incoming Webhook URL for alerts
    SLACK_ALERT_CHANNEL: str = ""       # Default channel for chat.postMessage alerts

    # ── WhatsApp integration (alerts + send) ─────────────────────────────────
    # All optional.  WHATSAPP_SEND_TOKEN is the Graph API bearer token used for
    # outbound message delivery (distinct from WHATSAPP_APP_SECRET which is the
    # inbound webhook verification secret).
    WHATSAPP_SEND_TOKEN: str = ""       # Graph API bearer token for outbound messages
    WHATSAPP_PHONE_NUMBER_ID: str = ""  # Sender's WhatsApp Business phone number ID
    WHATSAPP_ALERT_RECIPIENT: str = ""  # Default alert recipient (E.164 phone number)

    # ── Flow-run alerts (Prefect-style) ──────────────────────────────────────
    # Org-level default for which flow-run terminal states fire an outbound
    # alert when a flow does not declare its own ``alerts`` block.  Comma-
    # separated states, e.g. "failed,success" or "failed,timed_out".  When
    # empty (default) only flows with an explicit ``spec.alerts``/``config.alerts``
    # block notify — so existing deployments stay silent until opted in.
    FLOW_ALERTS_DEFAULT_ON: str = ""

    # ── Email alert channel ──────────────────────────────────────────────────
    # Optional.  Set ALERT_EMAIL_RECIPIENT to receive alert emails.
    # Uses the NullSender (no real delivery) unless an SMTP/SES sender is wired.
    ALERT_EMAIL_RECIPIENT: str = ""     # Address to send alert emails to

    # ── Remote git push (M20-B) ──────────────────────────────────────────────
    # All optional — existing deployments continue to work with no changes.
    #
    # GitHub App (GIT_REMOTE_PROVIDER=github_app):
    #   GITHUB_APP_ID=<numeric app id>
    #   GITHUB_APP_PRIVATE_KEY=<PEM-encoded RSA private key>
    #   GITHUB_APP_INSTALLATION_ID=<installation id>
    #
    # GitLab token (GIT_REMOTE_PROVIDER=gitlab):
    #   GITLAB_TOKEN=<personal or project access token>
    #   GITLAB_HOST=gitlab.com  (override for self-hosted)
    #
    # No push / local only (default):
    #   GIT_REMOTE_PROVIDER=none  (or unset)
    GIT_REMOTE_PROVIDER: str = ""          # '' | 'none' | 'github_app' | 'gitlab'
    GITHUB_APP_ID: str = ""                # GitHub App numeric ID (as string)
    GITHUB_APP_PRIVATE_KEY: str = ""       # PEM RSA private key for the App
    GITHUB_APP_INSTALLATION_ID: str = ""   # Installation ID for the target org
    GITLAB_TOKEN: str = ""                 # GitLab access token
    GITLAB_HOST: str = "gitlab.com"        # GitLab host (override for self-hosted)

    @model_validator(mode="after")
    def _resolve_flows_scheduler_enabled(self) -> "Settings":
        """Inherit the flows-scheduler switch from the legacy flags when unset.

        Precedence when FLOWS_SCHEDULER_ENABLED is not explicitly set:
        FLOWS_WORKER_ENABLED (legacy alias) → JOBS_SCHEDULER_ENABLED.
        """
        if self.FLOWS_SCHEDULER_ENABLED is None:
            self.FLOWS_SCHEDULER_ENABLED = bool(
                self.FLOWS_WORKER_ENABLED or self.JOBS_SCHEDULER_ENABLED
            )
        return self

    @property
    def cors_origins(self) -> List[str]:
        """CORS_ORIGINS parsed into a list of origins (comma-separated)."""
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance.

    Using lru_cache means environment variables are read once at first call
    and the same object is returned on every subsequent call.  Call
    ``get_settings.cache_clear()`` in tests to reset.
    """
    return Settings()  # type: ignore[call-arg]
