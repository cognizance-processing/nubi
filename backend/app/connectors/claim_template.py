"""Claim-templated datastore field resolver.

Allows a single datastore definition to serve many tenants by resolving
target fields (``database``, ``schema``, ``host``) from JWT claims at
request time.

Template syntax
---------------
Fields in ``template_config`` may contain ``{{ claims.<name> }}`` placeholders
(with optional surrounding whitespace).  Example::

    template_config:
      database: "ks_{{ claims.org }}"
      schema:   "{{ claims.tenant }}_data"

Security model
--------------
1. Placeholder names must appear in the CLAIM_ALLOWLIST — any other name
   raises :class:`TemplateSecurityError` before substitution begins.
2. Resolved values are validated against a strict character allowlist
   (``^[a-zA-Z0-9_-]{1,128}$``) — SQL/shell injection characters (single
   quotes, semicolons, slashes, …) are rejected.
3. Substitution is done with ``re.sub`` / string manipulation — no ``eval``,
   no ``exec``, no Jinja2 with auto-escape disabled, no ``str.format`` with
   caller-controlled keys.

Public API
----------
``TemplateSecurityError``
    Raised when a placeholder name is not in the allowlist OR when a resolved
    value does not match the strict character pattern.

``TemplateResolver``
    Main resolver class.  Instantiate once; call ``resolve_template(tmpl, claims)``
    or ``resolve_config(template_config, claims)`` to get resolved dicts.
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Allowlist and patterns
# ---------------------------------------------------------------------------

#: Names that may appear in ``{{ claims.<name> }}`` placeholders.
#: Any other name is rejected with :class:`TemplateSecurityError`.
CLAIM_ALLOWLIST: frozenset[str] = frozenset(
    {
        "org",
        "sub",
        "email",
        "tenant",
        "workspace",
        "environment",
        "region",
    }
)

# Matches {{ claims.<name> }} with optional whitespace around the expression.
# Capture group 1 = claim name (identifier characters only — no expressions).
_PLACEHOLDER_RE = re.compile(
    r"\{\{\s*claims\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}"
)

# Resolved values must match this pattern (strict identifier-style allowlist).
# Rejects SQL injection chars (single quotes, semicolons, slashes, spaces, …).
_SAFE_VALUE_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class TemplateSecurityError(ValueError):
    """Raised when template resolution is refused for security reasons.

    Scenarios
    ---------
    - A placeholder references a claim name NOT in :data:`CLAIM_ALLOWLIST`.
    - A resolved value contains characters outside ``[a-zA-Z0-9_-]`` or
      exceeds 128 characters.
    """


# ---------------------------------------------------------------------------
# TemplateResolver
# ---------------------------------------------------------------------------


class TemplateResolver:
    """Resolve ``{{ claims.<name> }}`` placeholders from verified JWT claims.

    Usage::

        resolver = TemplateResolver()
        db_name = resolver.resolve_template("ks_{{ claims.org }}", {"org": "acme"})
        # -> "ks_acme"

    The resolver is stateless and thread-safe; a single module-level instance
    is sufficient for the lifetime of the process.

    Parameters
    ----------
    allowlist:
        Override the default :data:`CLAIM_ALLOWLIST`.  Useful for tests that
        need to inject additional claim names; the default should be used in
        production.
    """

    def __init__(
        self,
        allowlist: frozenset[str] | None = None,
    ) -> None:
        self._allowlist: frozenset[str] = (
            allowlist if allowlist is not None else CLAIM_ALLOWLIST
        )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def resolve_template(self, template_str: str, claims: dict[str, Any]) -> str:
        """Resolve all ``{{ claims.<name> }}`` placeholders in *template_str*.

        Parameters
        ----------
        template_str:
            A string that may contain zero or more ``{{ claims.<name> }}``
            placeholders.  Literal text outside placeholders is preserved as-is.
        claims:
            A ``dict`` of verified JWT claims (e.g. from
            :class:`~app.auth.verify.VerifiedIdentity`).  Only keys in the
            allowlist are ever accessed.

        Returns
        -------
        str
            The fully-resolved string.

        Raises
        ------
        TemplateSecurityError
            If any placeholder name is not in the allowlist, if a claim value
            is missing, or if a resolved value fails the strict character
            validation.
        """
        # --- Phase 1: validate all placeholder names before any substitution ---
        placeholder_names: list[str] = _PLACEHOLDER_RE.findall(template_str)
        for name in placeholder_names:
            if name not in self._allowlist:
                raise TemplateSecurityError(
                    f"Template placeholder 'claims.{name}' is not in the "
                    f"claim allowlist.  Allowed names: {sorted(self._allowlist)!r}"
                )

        # --- Phase 2: substitute and validate each resolved value ---
        def _replacer(match: re.Match) -> str:  # type: ignore[type-arg]
            name = match.group(1)
            # Allowlist already verified in phase 1.
            raw = claims.get(name)
            if raw is None:
                raise TemplateSecurityError(
                    f"Template placeholder 'claims.{name}' references a claim "
                    "that is not present in the verified token."
                )
            value = str(raw)
            if not _SAFE_VALUE_RE.match(value):
                raise TemplateSecurityError(
                    f"Resolved value for 'claims.{name}' contains characters "
                    "outside the safe pattern ^[a-zA-Z0-9_-]{{1,128}}$.  "
                    f"Received: {value!r}"
                )
            return value

        return _PLACEHOLDER_RE.sub(_replacer, template_str)

    def resolve_config(
        self,
        template_config: dict[str, str],
        claims: dict[str, Any],
    ) -> dict[str, str]:
        """Resolve all template strings in *template_config*.

        Parameters
        ----------
        template_config:
            A ``{field_name: template_string}`` dict, e.g.::

                {"database": "ks_{{ claims.org }}", "schema": "{{ claims.tenant }}"}

        claims:
            Verified JWT claims.

        Returns
        -------
        dict[str, str]
            ``{field_name: resolved_value}`` with all placeholders substituted.

        Raises
        ------
        TemplateSecurityError
            Propagated from :meth:`resolve_template` on the first failing field.
        """
        return {
            field: self.resolve_template(tmpl, claims)
            for field, tmpl in template_config.items()
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_resolver: TemplateResolver | None = None


def get_template_resolver() -> TemplateResolver:
    """Return the process-level singleton :class:`TemplateResolver`."""
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = TemplateResolver()
    return _default_resolver
