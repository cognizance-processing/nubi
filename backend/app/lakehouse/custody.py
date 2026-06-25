"""Custody tier — config helpers + feature registration (OSS core, opt-in).

The **data-custody tier** is an opt-in capability set for deployers who must own
their storage + keys and pin data to a region (POPIA / GDPR residency).  It ships
in OSS core but is OFF by default and packaged as the paid ``custody`` tier.

This module is the single source of truth for "is the custody tier on, and how is
it configured?".  Every custody capability — the dedicated-bucket provider, the
versioned write/ingest API, bulk export, CMEK, region pinning, cache encryption —
reads its switches from here so the gate logic lives in exactly one place.

Authoritative gate
------------------
The deployment switch is the ``NUBI_CUSTODY_ENABLED`` setting, surfaced by
:func:`custody_enabled`.  Routes call :func:`assert_custody_enabled` to fail
closed (403 ``custody_disabled``) when the tier is off.  We also register a
``custody`` feature checker so :func:`app.features.feature_enabled` reflects the
switch for surfacing, but the config flag is the authority (feature-gate state is
reset between tests; the config flag is not).
"""

from __future__ import annotations

from app.config import get_settings

#: Feature identifier for the custody tier (surfacing only — config is authority).
CUSTODY_FEATURE = "custody"

_VALID_PROVIDERS = {"prefix", "dedicated"}
_VALID_CMEK_MODES = {"none", "kms", "client"}


def custody_enabled() -> bool:
    """Return ``True`` when the data-custody tier is enabled on this deployment."""
    return bool(get_settings().NUBI_CUSTODY_ENABLED)


def assert_custody_enabled() -> None:
    """Raise ``AppError("custody_disabled", …, 403)`` when the tier is off.

    The fail-closed guard every custody route calls before doing any work.
    """
    if not custody_enabled():
        from app.errors import AppError  # noqa: PLC0415 — avoid import cycle at load

        raise AppError(
            "custody_disabled",
            "The data-custody tier is not enabled on this deployment.",
            403,
        )


def lakehouse_provider_kind() -> str:
    """Return the configured managed-lakehouse provider: ``prefix`` | ``dedicated``.

    Falls back to ``prefix`` for any unrecognised value (safe default).  The
    ``dedicated`` provider only takes effect when the custody tier is enabled.
    """
    kind = (get_settings().NUBI_LAKEHOUSE_PROVIDER or "prefix").strip().lower()
    if not custody_enabled():
        return "prefix"
    return kind if kind in _VALID_PROVIDERS else "prefix"


def lake_region() -> str:
    """Return the configured region pin (e.g. ``africa-south1``), or ``""``.

    Empty string means "no pinning".  Region enforcement is only meaningful when
    the custody tier is enabled.
    """
    if not custody_enabled():
        return ""
    return (get_settings().NUBI_LAKE_REGION or "").strip()


def cmek_mode() -> str:
    """Return the CMEK mode: ``none`` | ``kms`` | ``client``.

    ``none`` whenever the tier is off or the value is unrecognised (safe default).
    """
    if not custody_enabled():
        return "none"
    mode = (get_settings().NUBI_CMEK_MODE or "none").strip().lower()
    return mode if mode in _VALID_CMEK_MODES else "none"


def cmek_key_uri() -> str:
    """Return the cloud-KMS key uri/resource id (CMEK mode ``kms``)."""
    return (get_settings().NUBI_CMEK_KEY_URI or "").strip()


def cmek_key_material() -> str:
    """Return the base64 deployer-held key material (CMEK mode ``client``)."""
    return (get_settings().NUBI_CMEK_KEY_MATERIAL or "").strip()


def cache_encryption_key() -> str:
    """Return the base64 cache-encryption key, or ``""`` when cache stays plaintext."""
    if not custody_enabled():
        return ""
    return (get_settings().NUBI_CACHE_ENCRYPTION_KEY or "").strip()


def custody_status() -> dict[str, object]:
    """Return a redacted summary of the custody configuration (for surfacing).

    Never includes key material — only modes / flags / the configured region so
    an admin endpoint can show "what custody posture is this deployment in".
    """
    return {
        "enabled": custody_enabled(),
        "provider": lakehouse_provider_kind(),
        "region": lake_region(),
        "cmek_mode": cmek_mode(),
        "cmek_configured": bool(
            cmek_key_uri() if cmek_mode() == "kms" else cmek_key_material()
        )
        if cmek_mode() != "none"
        else False,
        "cache_encrypted": bool(cache_encryption_key()),
    }


def _register_feature() -> None:
    """Register the ``custody`` feature checker (surfacing; config is authority)."""
    try:
        from app.features import register_feature  # noqa: PLC0415

        register_feature(CUSTODY_FEATURE, custody_enabled)
    except Exception:  # noqa: BLE001 — surfacing only; never break import
        pass


_register_feature()
