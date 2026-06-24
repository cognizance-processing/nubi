"""Comprehensive edge-case tests for claim_template and secret_resolver.

Existing coverage lives in test_templated_datastores.py.
This file adds targeted edge-case and parametrized tests for:
- Every allowlisted claim name resolves correctly
- Rejection of each injection class
- Non-allowlisted placeholder names
- Valid resolved values (boundary/character set)
- Default resolver selection (case-insensitive)
- External resolver factory registration and cleanup
- Unknown secret_resolver values
- Resolver failure propagation (fail-closed)
- Async callable in ExternalSecretResolver
- ExternalSecretResolver non-dict return types
- Cross-org non-contamination
- resolve_config partial/full resolution
- Missing claim raises TemplateSecurityError (not KeyError)
- Phase-1 catches bad placeholder name before substitution
- Allowlist override in constructor
- get_template_resolver singleton
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# 1. Every allowlisted claim name resolves correctly (parametrize)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "claim_name",
    ["org", "sub", "email", "tenant", "workspace", "environment", "region"],
)
def test_every_allowlisted_claim_resolves(claim_name: str) -> None:
    """Each name in CLAIM_ALLOWLIST successfully resolves to its value."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()
    template = f"prefix_{{{{ claims.{claim_name} }}}}_suffix"
    # Use a value that is within the safe pattern (alphanumeric + hyphen)
    safe_value = "safe-val1"
    result = resolver.resolve_template(template, {claim_name: safe_value})
    assert result == f"prefix_{safe_value}_suffix"


# ---------------------------------------------------------------------------
# 2. Rejection of each injection class (parametrize)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value,desc",
    [
        ("acme'; DROP TABLE--", "sql_injection_quote_semicolon"),
        ("acme; SELECT *", "semicolon_injection"),
        ("-- comment", "sql_comment_dash"),
        ("/* comment */", "block_comment"),
        ("../../etc/passwd", "path_traversal_dots"),
        ("/etc/passwd", "path_traversal_slash"),
        ("acme\\backslash", "backslash"),
        ("acme space", "space_char"),
        ("acme\tnewline", "tab_char"),
        ("acme\x00null", "null_byte"),
        ("a" * 129, "overlength_129"),
        ("a" * 200, "overlength_200"),
        ("", "empty_string"),
        ("{{ claims.org }}", "nested_braces"),
        ("{nested}", "single_braces"),
        ("acmeа", "unicode_homoglyph_cyrillic_a"),  # Cyrillic а (U+0430)
    ],
)
def test_injection_class_rejected(bad_value: str, desc: str) -> None:
    """Each injection-class value raises TemplateSecurityError."""
    from app.connectors.claim_template import TemplateResolver, TemplateSecurityError

    resolver = TemplateResolver()
    with pytest.raises(TemplateSecurityError, match="Resolved value for 'claims.org' contains characters outside the safe pattern"):
        resolver.resolve_template("prefix_{{ claims.org }}", {"org": bad_value})


# ---------------------------------------------------------------------------
# 3. Non-allowlisted placeholder names (parametrize)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    [
        "__class__",
        "__dict__",
        "role",
        "user_id",
        "permissions",
        "admin",
        "password",
        "secret",
        "arbitrary_field",
    ],
)
def test_non_allowlisted_name_rejected(bad_name: str) -> None:
    """Placeholder names outside CLAIM_ALLOWLIST raise TemplateSecurityError."""
    from app.connectors.claim_template import TemplateResolver, TemplateSecurityError

    resolver = TemplateResolver()
    template = f"db_{{{{ claims.{bad_name} }}}}"
    with pytest.raises(TemplateSecurityError, match="not in the claim allowlist"):
        resolver.resolve_template(template, {bad_name: "somevalue"})


# ---------------------------------------------------------------------------
# 4. Valid resolved values — should NOT raise (parametrize)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valid_value",
    [
        "a",              # single char
        "A" * 128,       # exactly 128 chars (max allowed)
        "abc123",         # alphanumeric
        "my-org",         # hyphen allowed
        "my_tenant",      # underscore allowed
        "ORG-001",        # upper + digits + hyphen
        "1234",           # all digits
        "UPPER",          # all uppercase
    ],
)
def test_valid_value_resolves(valid_value: str) -> None:
    """Values within the safe character set resolve without error."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()
    result = resolver.resolve_template("prefix_{{ claims.org }}", {"org": valid_value})
    assert result == f"prefix_{valid_value}"


# ---------------------------------------------------------------------------
# 5. Default resolver selection
# ---------------------------------------------------------------------------


def test_get_resolver_empty_config_returns_encrypted_store() -> None:
    """get_resolver({}) returns EncryptedStoreResolver."""
    from app.connectors.secret_resolver import (
        EncryptedStoreResolver,
        get_resolver,
        set_external_resolver_factory,
    )

    set_external_resolver_factory(None)
    resolver = get_resolver({})
    assert isinstance(resolver, EncryptedStoreResolver)


def test_get_resolver_explicit_encrypted_store() -> None:
    """get_resolver({'secret_resolver': 'encrypted_store'}) returns EncryptedStoreResolver."""
    from app.connectors.secret_resolver import EncryptedStoreResolver, get_resolver

    resolver = get_resolver({"secret_resolver": "encrypted_store"})
    assert isinstance(resolver, EncryptedStoreResolver)


def test_get_resolver_encrypted_store_case_insensitive() -> None:
    """secret_resolver value is matched case-insensitively."""
    from app.connectors.secret_resolver import EncryptedStoreResolver, get_resolver

    for variant in ("ENCRYPTED_STORE", "Encrypted_Store", "ENCRYPTED_store"):
        resolver = get_resolver({"secret_resolver": variant})
        assert isinstance(resolver, EncryptedStoreResolver), f"failed for {variant!r}"


def test_get_resolver_none_value_defaults_to_encrypted_store() -> None:
    """Explicit None for secret_resolver key falls back to EncryptedStoreResolver."""
    from app.connectors.secret_resolver import EncryptedStoreResolver, get_resolver

    resolver = get_resolver({"secret_resolver": None})
    assert isinstance(resolver, EncryptedStoreResolver)


# ---------------------------------------------------------------------------
# 6. External resolver requires factory registration
# ---------------------------------------------------------------------------


def test_external_resolver_no_factory_raises_runtime_error() -> None:
    """get_resolver('external') with no factory raises RuntimeError."""
    from app.connectors.secret_resolver import get_resolver, set_external_resolver_factory

    set_external_resolver_factory(None)
    with pytest.raises(RuntimeError, match="no external resolver factory has been registered"):
        get_resolver({"secret_resolver": "external"})


def test_external_resolver_factory_registration_returns_external_resolver() -> None:
    """After set_external_resolver_factory, get_resolver returns ExternalSecretResolver."""
    from app.connectors.secret_resolver import (
        ExternalSecretResolver,
        get_resolver,
        set_external_resolver_factory,
    )

    def _my_factory() -> ExternalSecretResolver:
        return ExternalSecretResolver(lambda ds_id, claims: {"key": "val"})

    try:
        set_external_resolver_factory(_my_factory)
        resolver = get_resolver({"secret_resolver": "external"})
        assert isinstance(resolver, ExternalSecretResolver)
    finally:
        set_external_resolver_factory(None)


# ---------------------------------------------------------------------------
# 7. Unknown secret_resolver value raises ValueError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "unknown_value",
    ["vault_api", "hashicorp", "aws_sm", "gcp_sm", "redis", "EXTERNAL_UNKNOWN"],
)
def test_unknown_secret_resolver_raises_value_error(unknown_value: str) -> None:
    """An unknown secret_resolver value raises ValueError."""
    from app.connectors.secret_resolver import get_resolver

    with pytest.raises(ValueError, match="Unknown secret_resolver value"):
        get_resolver({"secret_resolver": unknown_value})


# ---------------------------------------------------------------------------
# 8. Resolver failure fails closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_external_resolver_exception_propagates() -> None:
    """ExternalSecretResolver with a failing callable propagates the error."""
    from app.connectors.secret_resolver import ExternalSecretResolver

    class CustomVaultError(Exception):
        pass

    def _broken(ds_id: str, claims: dict) -> dict:
        raise CustomVaultError("vault is down")

    resolver = ExternalSecretResolver(_broken)
    with pytest.raises(CustomVaultError, match="vault is down"):
        await resolver.resolve("ds-1", {"org": "acme"})


@pytest.mark.asyncio
async def test_external_resolver_async_exception_propagates() -> None:
    """Async callable raising an exception also propagates without swallowing."""
    from app.connectors.secret_resolver import ExternalSecretResolver

    class AsyncVaultError(RuntimeError):
        pass

    async def _broken_async(ds_id: str, claims: dict) -> dict:
        raise AsyncVaultError("async vault unreachable")

    resolver = ExternalSecretResolver(_broken_async)
    with pytest.raises(AsyncVaultError, match="async vault unreachable"):
        await resolver.resolve("ds-2", {"org": "acme"})


# ---------------------------------------------------------------------------
# 9. Async callable in ExternalSecretResolver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_external_resolver_with_async_callable() -> None:
    """ExternalSecretResolver correctly awaits an async coroutine callable."""
    from app.connectors.secret_resolver import ExternalSecretResolver

    expected = {"host": "vault-db.internal", "password": "tok_xyz"}

    async def _async_vault(ds_id: str, claims: dict) -> dict:
        # Simulate async I/O
        return expected

    resolver = ExternalSecretResolver(_async_vault)
    result = await resolver.resolve("ds-async", {"org": "myorg"})
    assert result == expected


@pytest.mark.asyncio
async def test_external_resolver_async_receives_correct_args() -> None:
    """Async callable is called with the correct datastore_id and claims."""
    from app.connectors.secret_resolver import ExternalSecretResolver

    received: list = []

    async def _recording_vault(ds_id: str, claims: dict) -> dict:
        received.append((ds_id, claims))
        return {"ok": True}

    resolver = ExternalSecretResolver(_recording_vault)
    await resolver.resolve("ds-uuid-abc", {"org": "orgX", "sub": "user1"})

    assert len(received) == 1
    assert received[0][0] == "ds-uuid-abc"
    assert received[0][1] == {"org": "orgX", "sub": "user1"}


# ---------------------------------------------------------------------------
# 10. ExternalSecretResolver returns non-dict → TypeError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_return,desc",
    [
        ([], "list"),
        ("credentials", "string"),
        (None, "none"),
        (42, "int"),
        (("a", "b"), "tuple"),
    ],
)
@pytest.mark.asyncio
async def test_external_resolver_non_dict_return_raises_type_error(
    bad_return: object, desc: str
) -> None:
    """ExternalSecretResolver raises TypeError when callable returns non-dict."""
    from app.connectors.secret_resolver import ExternalSecretResolver

    def _bad_callable(ds_id: str, claims: dict):
        return bad_return

    resolver = ExternalSecretResolver(_bad_callable)
    with pytest.raises(TypeError, match="expected dict"):
        await resolver.resolve("ds-bad", {"org": "acme"})


# ---------------------------------------------------------------------------
# 11. Cross-org non-resolution (no cross-contamination)
# ---------------------------------------------------------------------------


def test_cross_org_template_resolution_distinct() -> None:
    """Claims for org_a and org_b resolve to distinct values — no cross-contamination."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()
    template = "ks_{{ claims.org }}"

    result_a = resolver.resolve_template(template, {"org": "org-alpha"})
    result_b = resolver.resolve_template(template, {"org": "org-beta"})

    assert result_a == "ks_org-alpha"
    assert result_b == "ks_org-beta"
    assert result_a != result_b


def test_cross_org_resolve_config_distinct() -> None:
    """resolve_config with different org claims yields distinct field values."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()
    template_config = {
        "database": "db_{{ claims.org }}",
        "schema": "{{ claims.tenant }}_raw",
    }

    resolved_a = resolver.resolve_config(
        template_config, {"org": "orgA", "tenant": "tenantA"}
    )
    resolved_b = resolver.resolve_config(
        template_config, {"org": "orgB", "tenant": "tenantB"}
    )

    assert resolved_a["database"] == "db_orgA"
    assert resolved_b["database"] == "db_orgB"
    assert resolved_a["schema"] == "tenantA_raw"
    assert resolved_b["schema"] == "tenantB_raw"
    # Explicit cross-contamination check
    assert resolved_a["database"] != resolved_b["database"]
    assert resolved_a["schema"] != resolved_b["schema"]


# ---------------------------------------------------------------------------
# 12. resolve_config: all fields resolved / partial claim presence
# ---------------------------------------------------------------------------


def test_resolve_config_three_fields_all_resolved() -> None:
    """resolve_config with 3 placeholder fields resolves all of them."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()
    template_config = {
        "database": "ks_{{ claims.org }}",
        "schema": "{{ claims.tenant }}",
        "host": "{{ claims.region }}.db.internal",
    }
    claims = {"org": "myorg", "tenant": "mytenant", "region": "eu-west"}
    result = resolver.resolve_config(template_config, claims)

    assert result["database"] == "ks_myorg"
    assert result["schema"] == "mytenant"
    assert result["host"] == "eu-west.db.internal"


def test_resolve_config_partial_placeholders() -> None:
    """Fields with no placeholder pass through unchanged; fields with placeholder resolve."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()
    template_config = {
        "database": "ks_{{ claims.org }}",  # has placeholder
        "port": "5432",                     # static — no placeholder
        "ssl": "true",                      # static — no placeholder
    }
    result = resolver.resolve_config(template_config, {"org": "acme"})

    assert result["database"] == "ks_acme"
    assert result["port"] == "5432"
    assert result["ssl"] == "true"


def test_resolve_config_empty_template_config() -> None:
    """resolve_config with an empty dict returns an empty dict."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()
    result = resolver.resolve_config({}, {"org": "acme"})
    assert result == {}


# ---------------------------------------------------------------------------
# 13. Missing claim in template raises TemplateSecurityError (not KeyError)
# ---------------------------------------------------------------------------


def test_missing_claim_raises_template_security_error_not_key_error() -> None:
    """Template references {{ claims.org }} but 'org' is absent from claims dict."""
    from app.connectors.claim_template import TemplateResolver, TemplateSecurityError

    resolver = TemplateResolver()
    with pytest.raises(TemplateSecurityError):
        resolver.resolve_template("db_{{ claims.org }}", {})  # empty claims


def test_missing_claim_is_not_key_error() -> None:
    """Verify the raised exception is TemplateSecurityError, NOT a plain KeyError."""
    from app.connectors.claim_template import TemplateResolver, TemplateSecurityError

    resolver = TemplateResolver()
    try:
        resolver.resolve_template("db_{{ claims.tenant }}", {"org": "present-but-not-tenant"})
        pytest.fail("Expected TemplateSecurityError was not raised")
    except TemplateSecurityError:
        pass  # correct
    except KeyError:
        pytest.fail("Got KeyError instead of TemplateSecurityError — bad error handling")


def test_missing_claim_error_message_names_the_field() -> None:
    """TemplateSecurityError message references the missing claim name."""
    from app.connectors.claim_template import TemplateResolver, TemplateSecurityError

    resolver = TemplateResolver()
    with pytest.raises(TemplateSecurityError, match="claims.org"):
        resolver.resolve_template("db_{{ claims.org }}", {"tenant": "has-tenant-not-org"})


# ---------------------------------------------------------------------------
# 14. Phase-1 catches bad placeholder name before substitution
# ---------------------------------------------------------------------------


def test_bad_placeholder_name_caught_before_substitution() -> None:
    """Phase-1 rejects a bad claim name even if it appears before a valid one."""
    from app.connectors.claim_template import TemplateResolver, TemplateSecurityError

    resolver = TemplateResolver()
    # "badname" is not in allowlist — must be caught before any value is read
    template = "{{ claims.badname }}_{{ claims.org }}"
    with pytest.raises(TemplateSecurityError, match="not in the claim allowlist"):
        resolver.resolve_template(template, {"badname": "anything", "org": "acme"})


def test_phase1_rejects_disallowed_name_even_with_valid_value() -> None:
    """Phase-1 rejects based on name, not value — even if the value would pass _SAFE_VALUE_RE."""
    from app.connectors.claim_template import TemplateResolver, TemplateSecurityError

    resolver = TemplateResolver()
    with pytest.raises(TemplateSecurityError):
        # "role" is not allowlisted; "admin" is a perfectly safe value
        resolver.resolve_template("{{ claims.role }}", {"role": "admin"})


def test_phase1_bad_name_mixed_with_valid_name() -> None:
    """Bad name after a valid name is still caught in phase 1."""
    from app.connectors.claim_template import TemplateResolver, TemplateSecurityError

    resolver = TemplateResolver()
    template = "{{ claims.org }}_{{ claims.__class__ }}"
    with pytest.raises(TemplateSecurityError, match="not in the claim allowlist"):
        resolver.resolve_template(template, {"org": "acme"})


# ---------------------------------------------------------------------------
# 15. Allowlist override in TemplateResolver constructor
# ---------------------------------------------------------------------------


def test_custom_allowlist_allows_custom_field() -> None:
    """TemplateResolver with custom allowlist accepts a custom claim name."""
    from app.connectors.claim_template import TemplateResolver

    custom = frozenset({"myfield"})
    resolver = TemplateResolver(allowlist=custom)
    result = resolver.resolve_template("db_{{ claims.myfield }}", {"myfield": "val123"})
    assert result == "db_val123"


def test_custom_allowlist_rejects_default_names() -> None:
    """Custom allowlist that excludes default names rejects them."""
    from app.connectors.claim_template import TemplateResolver, TemplateSecurityError

    custom = frozenset({"myfield"})
    resolver = TemplateResolver(allowlist=custom)
    # "org" is in the default allowlist but NOT in custom — should be rejected
    with pytest.raises(TemplateSecurityError, match="not in the claim allowlist"):
        resolver.resolve_template("db_{{ claims.org }}", {"org": "acme"})


def test_custom_allowlist_none_uses_default() -> None:
    """Passing allowlist=None to constructor uses the default CLAIM_ALLOWLIST."""
    from app.connectors.claim_template import CLAIM_ALLOWLIST, TemplateResolver

    resolver = TemplateResolver(allowlist=None)
    assert resolver._allowlist == CLAIM_ALLOWLIST


def test_empty_custom_allowlist_rejects_all_names() -> None:
    """An empty custom allowlist rejects all placeholder names."""
    from app.connectors.claim_template import TemplateResolver, TemplateSecurityError

    resolver = TemplateResolver(allowlist=frozenset())
    with pytest.raises(TemplateSecurityError):
        resolver.resolve_template("db_{{ claims.org }}", {"org": "acme"})


# ---------------------------------------------------------------------------
# 16. get_template_resolver() returns singleton
# ---------------------------------------------------------------------------


def test_get_template_resolver_singleton() -> None:
    """Two calls to get_template_resolver() return the same object."""
    import app.connectors.claim_template as ct_module
    from app.connectors.claim_template import get_template_resolver

    # Reset the module-level singleton to ensure a fresh state for this test.
    original = ct_module._default_resolver
    ct_module._default_resolver = None

    try:
        r1 = get_template_resolver()
        r2 = get_template_resolver()
        assert r1 is r2
    finally:
        ct_module._default_resolver = original


def test_get_template_resolver_is_template_resolver_instance() -> None:
    """get_template_resolver() returns a TemplateResolver instance."""
    from app.connectors.claim_template import TemplateResolver, get_template_resolver

    resolver = get_template_resolver()
    assert isinstance(resolver, TemplateResolver)


def test_get_template_resolver_singleton_after_first_init() -> None:
    """Calling get_template_resolver() 5 times always returns the same object."""
    from app.connectors.claim_template import get_template_resolver

    first = get_template_resolver()
    for _ in range(4):
        assert get_template_resolver() is first


# ---------------------------------------------------------------------------
# 17. Cleanup: restore external resolver factory after use
# ---------------------------------------------------------------------------


def test_set_external_resolver_factory_cleanup() -> None:
    """After registering and clearing a factory, external resolver raises RuntimeError again."""
    from app.connectors.secret_resolver import get_resolver, set_external_resolver_factory

    # Start clean
    set_external_resolver_factory(None)

    # Register a factory
    def _dummy_factory():
        from app.connectors.secret_resolver import ExternalSecretResolver

        return ExternalSecretResolver(lambda ds, c: {"k": "v"})

    set_external_resolver_factory(_dummy_factory)

    # Verify it works
    from app.connectors.secret_resolver import ExternalSecretResolver

    resolver = get_resolver({"secret_resolver": "external"})
    assert isinstance(resolver, ExternalSecretResolver)

    # Clean up
    set_external_resolver_factory(None)

    # Now it must raise again
    with pytest.raises(RuntimeError):
        get_resolver({"secret_resolver": "external"})


def test_set_external_resolver_factory_can_be_called_multiple_times() -> None:
    """set_external_resolver_factory can overwrite a previous factory safely."""
    from app.connectors.secret_resolver import (
        ExternalSecretResolver,
        get_resolver,
        set_external_resolver_factory,
    )

    counter: list[int] = [0]

    def _factory_v1():
        counter[0] = 1
        return ExternalSecretResolver(lambda ds, c: {"v": 1})

    def _factory_v2():
        counter[0] = 2
        return ExternalSecretResolver(lambda ds, c: {"v": 2})

    try:
        set_external_resolver_factory(_factory_v1)
        get_resolver({"secret_resolver": "external"})
        assert counter[0] == 1

        set_external_resolver_factory(_factory_v2)
        get_resolver({"secret_resolver": "external"})
        assert counter[0] == 2
    finally:
        set_external_resolver_factory(None)


# ---------------------------------------------------------------------------
# 18. EncryptedStoreResolver: no org_id and no org claim raises ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_encrypted_store_resolver_no_org_raises_value_error() -> None:
    """EncryptedStoreResolver with no org_id and no 'org' claim raises ValueError."""
    from app.connectors.secret_resolver import EncryptedStoreResolver

    resolver = EncryptedStoreResolver(org_id=None)
    with pytest.raises(ValueError, match="no org_id available"):
        await resolver.resolve("ds-1234", {})  # empty claims, no org_id


# ---------------------------------------------------------------------------
# 19. Whitespace variants in template placeholders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template,expected",
    [
        ("{{claims.org}}", "acme"),                  # no spaces
        ("{{ claims.org}}", "acme"),                 # trailing space only
        ("{{claims.org }}", "acme"),                 # leading space only
        ("{{  claims.org  }}", "acme"),              # double spaces each side
        ("prefix_{{ claims.org }}_suffix", "prefix_acme_suffix"),
    ],
)
def test_whitespace_variants_resolve(template: str, expected: str) -> None:
    """Template placeholders with varying whitespace all resolve correctly."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()
    result = resolver.resolve_template(template, {"org": "acme"})
    assert result == expected


# ---------------------------------------------------------------------------
# 20. Repeated placeholder for same claim in one template
# ---------------------------------------------------------------------------


def test_repeated_placeholder_same_claim() -> None:
    """A claim referenced multiple times in the same template resolves all occurrences."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()
    template = "{{ claims.org }}_schema_{{ claims.org }}"
    result = resolver.resolve_template(template, {"org": "acme"})
    assert result == "acme_schema_acme"


# ---------------------------------------------------------------------------
# 21. Boundary values for allowed length (127, 128, 129 chars)
# ---------------------------------------------------------------------------


def test_value_exactly_128_chars_is_allowed() -> None:
    """A value of exactly 128 characters resolves successfully."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()
    val = "a" * 128
    result = resolver.resolve_template("{{ claims.org }}", {"org": val})
    assert result == val


def test_value_127_chars_is_allowed() -> None:
    """A value of 127 characters resolves successfully."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()
    val = "b" * 127
    result = resolver.resolve_template("{{ claims.org }}", {"org": val})
    assert result == val


def test_value_129_chars_is_rejected() -> None:
    """A value of 129 characters raises TemplateSecurityError."""
    from app.connectors.claim_template import TemplateResolver, TemplateSecurityError

    resolver = TemplateResolver()
    with pytest.raises(TemplateSecurityError):
        resolver.resolve_template("{{ claims.org }}", {"org": "c" * 129})


# ---------------------------------------------------------------------------
# 22. get_resolver passes org_id to EncryptedStoreResolver
# ---------------------------------------------------------------------------


def test_get_resolver_passes_org_id_to_encrypted_store() -> None:
    """org_id kwarg is forwarded to EncryptedStoreResolver."""
    from app.connectors.secret_resolver import EncryptedStoreResolver, get_resolver

    resolver = get_resolver({}, org_id="my-org-uuid")
    assert isinstance(resolver, EncryptedStoreResolver)
    assert resolver._org_id == "my-org-uuid"


def test_get_resolver_explicit_encrypted_store_passes_org_id() -> None:
    """Explicit encrypted_store config also forwards org_id."""
    from app.connectors.secret_resolver import EncryptedStoreResolver, get_resolver

    resolver = get_resolver({"secret_resolver": "encrypted_store"}, org_id="uuid-abc")
    assert isinstance(resolver, EncryptedStoreResolver)
    assert resolver._org_id == "uuid-abc"


# ---------------------------------------------------------------------------
# 23. TemplateSecurityError is a ValueError subclass
# ---------------------------------------------------------------------------


def test_template_security_error_is_value_error_subclass() -> None:
    """TemplateSecurityError is a subclass of ValueError."""
    from app.connectors.claim_template import TemplateSecurityError

    assert issubclass(TemplateSecurityError, ValueError)


def test_template_security_error_can_be_caught_as_value_error() -> None:
    """TemplateSecurityError can be caught with 'except ValueError'."""
    from app.connectors.claim_template import TemplateResolver

    resolver = TemplateResolver()
    caught_as_value_error = False
    try:
        resolver.resolve_template("{{ claims.hacked }}", {"hacked": "x"})
    except ValueError:
        caught_as_value_error = True

    assert caught_as_value_error


# ---------------------------------------------------------------------------
# 24. ExternalSecretResolver sync callable receives correct args
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_external_resolver_sync_callable_receives_correct_args() -> None:
    """Sync callable receives datastore_id and claims as positional args."""
    from app.connectors.secret_resolver import ExternalSecretResolver

    call_log: list = []

    def _spy(ds_id: str, claims: dict) -> dict:
        call_log.append({"ds_id": ds_id, "claims": claims})
        return {"password": "secret"}

    resolver = ExternalSecretResolver(_spy)
    result = await resolver.resolve("ds-spy-001", {"org": "spy-org", "sub": "u1"})

    assert result == {"password": "secret"}
    assert len(call_log) == 1
    assert call_log[0]["ds_id"] == "ds-spy-001"
    assert call_log[0]["claims"]["org"] == "spy-org"
