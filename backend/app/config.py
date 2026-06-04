"""Application configuration loaded from environment variables via pydantic-settings."""

from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Minimum acceptable JWT secret length (bytes).  HS256 keys should be at least
# as long as the hash output (32 bytes = 256 bits) per RFC 7518 §3.2.
_JWT_SECRET_MIN_BYTES = 32


class Settings(BaseSettings):
    """All runtime configuration read from environment variables.

    Required variables must be present; optional ones have defaults.
    No secret values are ever logged or exposed in error responses.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
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
