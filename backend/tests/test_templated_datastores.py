"""Tests for claim-templated datastores and the pluggable secret resolver.

Coverage
--------
1. test_template_expansion_from_claim
   Basic {{ claims.org }} expands correctly.

2. test_template_injection_rejected
   Claim values containing SQL injection characters raise TemplateSecurityError.

3. test_template_unknown_claim_name_rejected
   Placeholder with an out-of-allowlist name raises TemplateSecurityError.

4. test_encrypted_resolver_is_default
   Factory returns EncryptedStoreResolver when no secret_resolver field.

5. test_external_resolver_double
   ExternalSecretResolver calls the provided callable and returns creds.

6. test_resolver_failure_fails_closed
   If the resolver raises, the exception propagates (no fallback).

7. test_org_a_cannot_resolve_org_b
   With org="orgA" claim, template resolves to orgA's DB, not orgB's.

8. test_existing_datastore_no_template_unchanged
   When template_config is None, _build_connector_for_plan behaves as before
   (mock the existing path).
"""

from __future__ import annotations

import base64
import os
import secrets

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Test-key helpers (mirrors test_connector_secrets.py pattern)
# ---------------------------------------------------------------------------


def _random_b64_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def _set_single_key(b64_key: str, version: int = 1) -> None:
    from app.security.crypto import reset_keys_for_tests

    os.environ["CONNECTOR_SECRET_KEY"] = b64_key
    os.environ["CONNECTOR_SECRET_KEY_VERSION"] = str(version)
    os.environ.pop("CONNECTOR_SECRET_KEYS", None)
    reset_keys_for_tests()


def _clear_keys() -> None:
    from app.security.crypto import reset_keys_for_tests

    os.environ.pop("CONNECTOR_SECRET_KEY", None)
    os.environ.pop("CONNECTOR_SECRET_KEY_VERSION", None)
    os.environ.pop("CONNECTOR_SECRET_KEYS", None)
    reset_keys_for_tests()


@pytest.fixture(autouse=True)
def _reset_crypto():
    _clear_keys()
    yield
    _clear_keys()


# ---------------------------------------------------------------------------
# 1. test_template_expansion_from_claim
# ---------------------------------------------------------------------------


def test_template_expansion_from_claim():
    """Basic {{ claims.org }} placeholder expands to the claim value."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()
    result = resolver.resolve_template("ks_{{ claims.org }}", {"org": "acme"})
    assert result == "ks_acme"


def test_template_expansion_multiple_claims():
    """Multiple distinct placeholders are all resolved."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()
    result = resolver.resolve_template(
        "{{ claims.org }}_{{ claims.environment }}_db",
        {"org": "myorg", "environment": "prod"},
    )
    assert result == "myorg_prod_db"


def test_template_expansion_no_placeholders():
    """Strings without placeholders are returned unchanged."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()
    result = resolver.resolve_template("static_database", {"org": "anything"})
    assert result == "static_database"


def test_template_expansion_whitespace_variants():
    """Optional whitespace inside {{ }} is handled correctly."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()
    # tight
    assert resolver.resolve_template("{{claims.org}}", {"org": "tight"}) == "tight"
    # extra spaces
    assert resolver.resolve_template("{{   claims.org   }}", {"org": "spaced"}) == "spaced"


# ---------------------------------------------------------------------------
# 2. test_template_injection_rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value",
    [
        "acme'; DROP TABLE users;--",
        "acme' OR '1'='1",
        "../../etc/passwd",
        "acme/subdir",
        "acme\\backslash",
        "acme space",
        "acme\nnewline",
        "a" * 129,  # 129 chars — exceeds 128-char cap
        "",          # empty string
    ],
)
def test_template_injection_rejected(bad_value: str):
    """Claim values with injection characters raise TemplateSecurityError."""
    from app.connectors.claim_template import TemplateResolver, TemplateSecurityError

    resolver = TemplateResolver()
    with pytest.raises(TemplateSecurityError):
        resolver.resolve_template("db_{{ claims.org }}", {"org": bad_value})


# ---------------------------------------------------------------------------
# 3. test_template_unknown_claim_name_rejected
# ---------------------------------------------------------------------------


def test_template_unknown_claim_name_rejected():
    """Placeholder with a name not in the allowlist raises TemplateSecurityError."""
    from app.connectors.claim_template import TemplateResolver, TemplateSecurityError

    resolver = TemplateResolver()
    with pytest.raises(TemplateSecurityError, match="not in the claim allowlist"):
        resolver.resolve_template(
            "db_{{ claims.arbitrary_field }}", {"arbitrary_field": "value"}
        )


def test_template_unknown_claim_name_not_in_allowlist_many():
    """Multiple disallowed names all raise TemplateSecurityError (checked in phase 1)."""
    from app.connectors.claim_template import TemplateResolver, TemplateSecurityError

    resolver = TemplateResolver()
    # Even with a valid claim mixed in, the disallowed one is caught first.
    with pytest.raises(TemplateSecurityError):
        resolver.resolve_template(
            "{{ claims.org }}_{{ claims.__class__ }}",
            {"org": "acme"},
        )


# ---------------------------------------------------------------------------
# 4. test_encrypted_resolver_is_default
# ---------------------------------------------------------------------------


def test_encrypted_resolver_is_default():
    """get_resolver() returns EncryptedStoreResolver when no secret_resolver field."""
    from app.connectors.secret_resolver import (
        EncryptedStoreResolver,
        get_resolver,
        set_external_resolver_factory,
    )

    # Clean up any lingering external factory from other tests.
    set_external_resolver_factory(None)

    resolver = get_resolver({})
    assert isinstance(resolver, EncryptedStoreResolver)


def test_encrypted_resolver_is_default_explicit():
    """Explicit secret_resolver='encrypted_store' also returns EncryptedStoreResolver."""
    from app.connectors.secret_resolver import EncryptedStoreResolver, get_resolver

    resolver = get_resolver({"secret_resolver": "encrypted_store"})
    assert isinstance(resolver, EncryptedStoreResolver)


# ---------------------------------------------------------------------------
# 5. test_external_resolver_double
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_external_resolver_double():
    """ExternalSecretResolver calls the provided callable and returns creds."""
    from app.connectors.secret_resolver import ExternalSecretResolver

    expected_creds = {"password": "vault-secret-pw", "host": "db.internal"}

    def _vault_callable(datastore_id: str, claims: dict) -> dict:
        # In production this would call HashiCorp Vault / AWS Secrets Manager etc.
        return expected_creds

    resolver = ExternalSecretResolver(_vault_callable)
    result = await resolver.resolve("ds-uuid-1234", {"org": "acme"})
    assert result == expected_creds


@pytest.mark.asyncio
async def test_external_resolver_async_callable():
    """ExternalSecretResolver also accepts async callables."""
    from app.connectors.secret_resolver import ExternalSecretResolver

    expected = {"api_key": "tok_abc123"}

    async def _async_vault(datastore_id: str, claims: dict) -> dict:
        return expected

    resolver = ExternalSecretResolver(_async_vault)
    result = await resolver.resolve("ds-uuid-5678", {"org": "acme"})
    assert result == expected


# ---------------------------------------------------------------------------
# 6. test_resolver_failure_fails_closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolver_failure_fails_closed():
    """If the resolver raises, the exception propagates — no fallback."""
    from app.connectors.secret_resolver import ExternalSecretResolver

    class VaultUnavailableError(RuntimeError):
        pass

    def _broken_vault(datastore_id: str, claims: dict) -> dict:
        raise VaultUnavailableError("vault is unreachable")

    resolver = ExternalSecretResolver(_broken_vault)

    with pytest.raises(VaultUnavailableError, match="vault is unreachable"):
        await resolver.resolve("ds-uuid-fail", {"org": "acme"})


@pytest.mark.asyncio
async def test_encrypted_resolver_org_mismatch_returns_empty():
    """EncryptedStoreResolver returns {} (not another org's secret) on org mismatch."""
    from app.connectors.secret_resolver import EncryptedStoreResolver
    from app.connectors.secret_store import InMemorySecretStore, set_secret_store_for_tests

    _set_single_key(_random_b64_key())
    store = InMemorySecretStore()
    set_secret_store_for_tests(store)

    ds_id = "aaaaaaaa-0000-0000-0000-000000000001"
    org_a = "org_A"
    org_b = "org_B"

    await store.put(ds_id, org_a, {"password": "secret_for_org_a"})

    # Resolver scoped to org_b — must not return org_a's secret.
    resolver = EncryptedStoreResolver(org_id=org_b)
    result = await resolver.resolve(ds_id, {"org": org_b})
    # InMemorySecretStore returns None for wrong org; resolver returns {}.
    assert result == {}

    set_secret_store_for_tests(None)


# ---------------------------------------------------------------------------
# 7. test_org_a_cannot_resolve_org_b
# ---------------------------------------------------------------------------


def test_org_a_cannot_resolve_org_b():
    """With org='orgA' claim, template resolves to orgA's DB, not orgB's."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()

    claims_a = {"org": "orgA"}
    claims_b = {"org": "orgB"}

    db_a = resolver.resolve_template("ks_{{ claims.org }}", claims_a)
    db_b = resolver.resolve_template("ks_{{ claims.org }}", claims_b)

    assert db_a == "ks_orgA"
    assert db_b == "ks_orgB"
    # Cross-contamination check — they must not be equal.
    assert db_a != db_b


def test_org_a_cannot_resolve_org_b_via_config():
    """resolve_config scopes each field to the claims in the token."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()

    template_config = {
        "database": "ks_{{ claims.org }}",
        "schema": "{{ claims.org }}_raw",
    }

    resolved_a = resolver.resolve_config(template_config, {"org": "orgA"})
    resolved_b = resolver.resolve_config(template_config, {"org": "orgB"})

    assert resolved_a["database"] == "ks_orgA"
    assert resolved_a["schema"] == "orgA_raw"
    assert resolved_b["database"] == "ks_orgB"
    assert resolved_b["schema"] == "orgB_raw"
    # Cross-contamination: neither org sees the other's DB.
    assert resolved_a["database"] != resolved_b["database"]
    assert resolved_a["schema"] != resolved_b["schema"]


# ---------------------------------------------------------------------------
# 8. test_existing_datastore_no_template_unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_datastore_no_template_unchanged():
    """When template_config is None, _build_connector_for_plan behaves as before.

    We mock the repo, secret store, connector registry, and network resolution
    to verify that a datastore WITHOUT template_config goes through the same
    code path as today — the new template block is skipped entirely.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.connectors.plan import PhysicalPlan

    # A minimal physical plan with no rls claims and a stable cache_key.
    plan = PhysicalPlan(
        sql="SELECT 1",
        cache_key="a" * 64,
        rls_claims={},
    )

    datastore_id = "dddddddd-0000-0000-0000-000000000001"
    org_id = "eeeeeeee-0000-0000-0000-000000000001"

    # Datastore config WITHOUT template_config — existing connector shape.
    ds_row = {
        "id": datastore_id,
        "org_id": org_id,
        "config": {
            "connector_type": "http_json",
            "base_url": "https://api.example.com",
            # No template_config key at all.
        },
    }

    fake_repo = AsyncMock()
    fake_repo.get = AsyncMock(return_value=ds_row)

    # The secret resolver (EncryptedStoreResolver) will call secret_store.get.
    _set_single_key(_random_b64_key())
    from app.connectors.secret_store import InMemorySecretStore, set_secret_store_for_tests

    store = InMemorySecretStore()
    # Existing connector has no secret stored — returns None (empty creds).
    set_secret_store_for_tests(store)

    # Mock the HttpJsonConnector factory so we never open a real HTTP connection.
    mock_connector = MagicMock()
    mock_connector.capabilities.return_value = {
        "native_arrow": False,
        "predicate_pushdown": False,
        "projection_pushdown": False,
        "partition_pushdown": False,
        "predicate_rls": False,
        "column_masking": False,
        "streaming_cdc": False,
    }

    with (
        patch(
            "app.routes.query.get_connector_registry",
        ) as mock_registry_fn,
        patch(
            "app.connectors.network.resolve_network",
            return_value=None,
        ),
    ):
        mock_registry = MagicMock()
        mock_registry.get.return_value = lambda cfg: mock_connector
        mock_registry_fn.return_value = mock_registry

        from app.routes.query import _build_connector_for_plan

        connector, conn_kind, cleanup = await _build_connector_for_plan(
            plan,
            datastore_id,
            org_id,
            None,
            fake_repo,
        )

    assert connector is mock_connector
    assert conn_kind == "http_json"
    # No template was applied — verify the repo.get was called once with the
    # correct org/datastore (proves org-scoping was preserved).
    fake_repo.get.assert_called_once_with("datastores", org_id, datastore_id)

    set_secret_store_for_tests(None)


# ---------------------------------------------------------------------------
# Additional: allowlist enumeration
# ---------------------------------------------------------------------------


def test_claim_allowlist_contents():
    """CLAIM_ALLOWLIST contains exactly the documented safe names."""
    from app.connectors.claim_template import CLAIM_ALLOWLIST

    expected = {"org", "sub", "email", "tenant", "workspace", "environment", "region"}
    assert CLAIM_ALLOWLIST == expected


def test_resolve_config_all_allowlisted_claims():
    """Each allowlisted claim name resolves without error."""
    from app.connectors.claim_template import CLAIM_ALLOWLIST, TemplateResolver

    resolver = TemplateResolver()
    claims = {name: f"value-{name}" for name in CLAIM_ALLOWLIST}
    # Hyphens are valid in resolved values.
    for name in CLAIM_ALLOWLIST:
        result = resolver.resolve_template(f"prefix_{{{{ claims.{name} }}}}", claims)
        assert result == f"prefix_value-{name}"
