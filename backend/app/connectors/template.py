"""Jinja2-powered safe SQL template engine (replaces the trivial ``{{name}}`` regex).

Design
------
We use a ``jinja2.sandbox.SandboxedEnvironment`` so that template expressions
cannot call arbitrary Python builtins or access object internals (e.g.
``{{ ''.__class__ }}`` is blocked by the sandbox).

Every VALUE output via ``{{ expr }}`` is intercepted by a custom ``finalize``
hook that:

1. Appends the value to a per-render param list.
2. Returns the dialect-specific positional placeholder (``$1``, ``$2``, …)
   instead of the value text.

This means ALL output expressions are bound parameters — the final rendered
SQL string contains ONLY placeholders, never raw user-supplied data.  SQL
structure (``{% if %}``, ``{% for %}``, comparisons, ``{% set %}``) is
evaluated on raw Python values BEFORE ``finalize`` is called, so control flow
can branch on values without ever embedding them in the SQL text.

Deduplication of repeated simple variable references
----------------------------------------------------
For backward compatibility with the old regex engine, a simple ``{{ name }}``
that appears multiple times in the SQL is mapped to a SINGLE ``$N`` slot (the
value appears once in the params list, the placeholder ``$N`` appears multiple
times in the rendered SQL).  This is achieved by a pre-pass that wraps
already-seen context values in a ``_SlotRef`` sentinel before Jinja2 renders
the template.  Complex expressions (e.g. ``{{ x + 1 }}``) or filter chains
(``{{ x | upper }}``) are NOT deduplicated — each occurrence gets its own slot.

Supported template features
----------------------------
- ``{{ varname }}``              — binds value, emits ``$N``
- ``{% if cond %}…{% endif %}``  — conditional SQL block (Go ``if`` equivalent)
- ``{% for x in list %}…{% endfor %}`` — loop (e.g. to build expressions)
- ``{% set x = expr %}``         — local variable assignment
- Jinja2 filters (``| upper``, ``| default(…)``, etc.)
- ``{{ ids | inclause }}``       — binds a list and emits ``($1, $2, $3, …)``
  for use with SQL ``IN`` operators.  Each element is bound separately.
- ``{{ val | sqlsafe }}``        — UNSAFE raw interpolation escape hatch.
  The value is embedded directly in the SQL text (no binding, no escaping).
  ONLY use for trusted, server-controlled values (e.g. dynamically chosen
  column/table identifiers from an allowlist).  NEVER pass user input through
  this filter.

Backward compatibility
-----------------------
Plain ``{{name}}`` (Jinja2's default variable syntax) still works exactly as
before — the finalize hook binds it the same way, so all existing registered
queries using ``{{region}}``-style placeholders continue to function without
any change.  Repeated occurrences of the same simple name map to the same $N
slot (matching the old regex deduplication behaviour).

Public API
----------
``render_sql_template(sql, context, dialect='postgres') -> (rendered_sql, params)``

    Render *sql* as a Jinja2 template with *context* values.  Returns the
    rewritten SQL (containing only dialect placeholders) and the ordered list
    of bound parameter values.

Dialect placeholder mapping
----------------------------
- ``'postgres'``: ``$1``, ``$2``, …  (asyncpg / psycopg2 / DuckDB)
- ``'duckdb'``:   ``$1``, ``$2``, …  (DuckDB also accepts dollar-style)
- ``'mysql'``:    ``%s`` (placeholder repeated per position, positional)
- ``'sqlite'``:   ``?``
- anything else: ``?``  (generic DBAPI2 positional)

Security notes
--------------
- The ``SandboxedEnvironment`` blocks attribute access to ``__class__``,
  ``__globals__``, ``__builtins__`` etc.  Any attempt raises
  ``jinja2.exceptions.SecurityError`` (which we allow to propagate).
- ``undefined=StrictUndefined`` means a missing context key raises
  ``jinja2.exceptions.UndefinedError`` (equivalent to the current ``KeyError``
  raised by the regex approach when a placeholder has no value).
- The ``finalize`` function is the ONLY way a value reaches the SQL string;
  it always emits a placeholder, never the raw value.
- The ``sqlsafe`` filter explicitly bypasses binding and should be treated like
  ``| safe`` in HTML templates — only for trusted, server-controlled content.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Callable

import jinja2
from jinja2.sandbox import SandboxedEnvironment

# ---------------------------------------------------------------------------
# Dialect placeholder helpers
# ---------------------------------------------------------------------------

_POSITIONAL_DOLLAR = "dollar"   # $1, $2, …  (postgres, duckdb)
_POSITIONAL_QMARK = "qmark"     # ?           (sqlite, generic DBAPI2)
_POSITIONAL_PYFORMAT = "pyformat"  # %s        (mysql)

_DIALECT_STYLE: dict[str, str] = {
    "postgres": _POSITIONAL_DOLLAR,
    "postgresql": _POSITIONAL_DOLLAR,
    "duckdb": _POSITIONAL_DOLLAR,
    "mysql": _POSITIONAL_PYFORMAT,
    "sqlite": _POSITIONAL_QMARK,
}


def _placeholder(style: str, n: int) -> str:
    """Return the correct placeholder string for slot *n* (1-based)."""
    if style == _POSITIONAL_DOLLAR:
        return f"${n}"
    if style == _POSITIONAL_PYFORMAT:
        return "%s"
    return "?"


# ---------------------------------------------------------------------------
# Sentinel types
# ---------------------------------------------------------------------------


class _RawSQL:
    """Wrapper that marks a string as explicitly-unsafe raw SQL.

    Produced by the ``sqlsafe`` filter.  The ``finalize`` hook detects this
    wrapper and emits the raw string without binding it as a parameter.

    This is intentionally UNSAFE — treat it like ``| safe`` in Jinja2 HTML
    templates.  Only use with trusted, server-controlled content.
    """

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = str(value)

    def __str__(self) -> str:
        return self.value


class _SlotRef:
    """Reference to an already-assigned positional slot.

    Used for deduplication: when a simple ``{{ name }}`` is seen a second time
    we hand back a ``_SlotRef`` that already knows its placeholder so the
    finalize hook can re-emit the same ``$N`` without adding a second param
    entry.
    """

    __slots__ = ("placeholder",)

    def __init__(self, placeholder: str) -> None:
        self.placeholder = placeholder

    def __str__(self) -> str:
        return self.placeholder


# ---------------------------------------------------------------------------
# Thread-local render state
# ---------------------------------------------------------------------------


class _RenderState(threading.local):
    """Thread-local accumulator used during a single ``render_sql_template`` call.

    Attributes
    ----------
    params : list
        Accumulated bound parameter values (in emission order).
    style : str
        The placeholder style for the current render (dollar / qmark / pyformat).
    name_to_slot : dict[str, _SlotRef]
        Maps simple variable name → its assigned ``_SlotRef`` for dedup.
    active : bool
        Whether a render is currently in progress on this thread.
    """

    def __init__(self) -> None:
        self.params: list[Any] = []
        self.style: str = _POSITIONAL_DOLLAR
        self.name_to_slot: dict[str, _SlotRef] = {}
        self.active: bool = False


_state = _RenderState()


# ---------------------------------------------------------------------------
# Custom filters
# ---------------------------------------------------------------------------


def _filter_inclause(value: Any) -> _RawSQL:
    """Bind each element of *value* and emit ``($1, $2, …)`` for SQL IN clauses.

    The filter returns a ``_RawSQL`` sentinel so that the ``finalize`` hook
    does NOT try to bind the placeholder string itself as a parameter — the
    individual elements have already been bound during filter execution.

    Parameters
    ----------
    value:
        An iterable of scalar values to be bound.  Strings, ints, floats, etc.
        are all accepted.  Each element becomes a separate bound parameter.

    Returns
    -------
    _RawSQL
        A ``_RawSQL`` wrapper containing the parenthesised, comma-separated
        list of positional placeholders, e.g. ``($1, $2, $3)``.
        Suitable for ``WHERE col IN {{ ids | inclause }}``.

    Raises
    ------
    TypeError
        If *value* is not iterable.
    ValueError
        If *value* is empty (an empty IN clause is invalid SQL).
    """
    items = list(value)
    if not items:
        raise ValueError("inclause filter received an empty list; IN () is invalid SQL.")

    placeholders: list[str] = []
    for item in items:
        _state.params.append(item)
        n = len(_state.params)
        placeholders.append(_placeholder(_state.style, n))

    # Return as _RawSQL so finalize emits the placeholder string directly
    # without trying to bind it as yet another parameter value.
    return _RawSQL("(" + ", ".join(placeholders) + ")")


def _filter_sqlsafe(value: Any) -> _RawSQL:
    """Emit *value* as raw SQL WITHOUT binding it as a parameter.

    This is an UNSAFE escape hatch.  Use ONLY for trusted, server-controlled
    identifiers (e.g. a column name chosen from an explicit allowlist).
    NEVER pass user-supplied input through this filter.

    Example
    -------
    ::

        col_name = "revenue"  # trusted server-side value
        render_sql_template("SELECT {{ col | sqlsafe }} FROM t", {"col": col_name})
        # → "SELECT revenue FROM t", params=[]

    """
    return _RawSQL(value)


# ---------------------------------------------------------------------------
# Finalize hook — the heart of safe binding
# ---------------------------------------------------------------------------


def _make_finalize(style: str) -> Callable[[Any], str]:
    """Return a ``finalize`` function bound to *style*.

    The finalize hook is called by Jinja2 immediately before a ``{{ expr }}``
    result is converted to a string for output.  We intercept it here to:

    1. If the value is a ``_RawSQL`` instance: return its raw string directly
       (``sqlsafe`` escape hatch).
    2. If the value is a ``_SlotRef`` sentinel (set by the dedup pre-pass):
       return the already-assigned placeholder directly (re-use same $N).
    3. Otherwise: append the value to ``_state.params``, compute ``$N``, and
       return the placeholder string.

    Control-flow blocks (``{% if %}``, ``{% for %}``, ``{% set %}``,
    comparisons) do NOT pass through ``finalize`` — they operate on raw Python
    values and only affect which SQL fragments are included in the output.
    This is what gives us both full template power AND guaranteed safe binding.
    """

    def finalize(value: Any) -> str:
        if isinstance(value, _RawSQL):
            # Raw SQL escape hatch — emit directly without binding.
            return value.value

        if isinstance(value, _SlotRef):
            # Already-bound slot reference — re-use the same $N placeholder.
            return value.placeholder

        # Bind the value and emit a positional placeholder.
        _state.params.append(value)
        n = len(_state.params)
        return _placeholder(style, n)

    return finalize


# ---------------------------------------------------------------------------
# Environment factory
# ---------------------------------------------------------------------------


def _build_env(style: str) -> SandboxedEnvironment:
    """Build a fresh ``SandboxedEnvironment`` for *style*.

    We re-create the environment per render so the finalize closure is always
    freshly bound.  Environments are lightweight (no file I/O), so this is fine.
    """
    env = SandboxedEnvironment(
        # Block {{ }} values being treated as Markup; we want plain strings.
        autoescape=False,
        # Strict undefined: missing context key → UndefinedError (like KeyError
        # in the old regex, preserving backward-compatible error behaviour).
        undefined=jinja2.StrictUndefined,
        # The finalize hook: intercept every {{ expr }} output and bind/replace.
        finalize=_make_finalize(style),
        # Keep newlines; don't strip whitespace aggressively.
        keep_trailing_newline=True,
    )

    # Register custom filters.
    env.filters["inclause"] = _filter_inclause
    env.filters["bind_in"] = _filter_inclause  # alias for discoverability
    env.filters["sqlsafe"] = _filter_sqlsafe

    return env


# ---------------------------------------------------------------------------
# Deduplication: simple-name pre-pass
# ---------------------------------------------------------------------------

# Matches {{ varname }} or {{varname}} (with optional internal whitespace).
# Capture group 1: the bare identifier.  We only deduplicate simple name
# references, not filter chains or expressions.
_SIMPLE_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _build_dedup_context(
    sql: str,
    context: dict[str, Any],
    style: str,
) -> dict[str, Any]:
    """Build a context dict that wraps repeated simple-name references as _SlotRef.

    For each simple ``{{ name }}`` that appears more than once in *sql* and
    resolves to a value in *context*, we:

    1. Leave the FIRST occurrence as-is (the finalize hook will bind it).
    2. Replace the context value with a ``_SlotRef`` after the first bind so
       that subsequent occurrences re-emit the same placeholder.

    To do this without actually running the template, we scan *sql* for simple
    variable references, track which names appear more than once, and build a
    wrapper context where those names start with their raw value (first emit
    binds them) and are subsequently replaced by a ``_SlotRef``.

    Implementation: we use a ``ContextProxy`` class that wraps the original
    context and replaces each simple-name value with a ``_FirstOnceProxy``
    that binds on first access and returns a ``_SlotRef`` on subsequent accesses.
    But this is complex with Jinja2's variable lookup model.

    Simpler approach: pre-count occurrences.  For names that appear exactly
    once, no change needed.  For names that appear multiple times, we inject a
    ``{% set __dedup_name = name %}`` preamble and replace subsequent
    ``{{ name }}`` occurrences (2nd onward) with ``{{ __dedup_name_slot }}``.
    But modifying the SQL template is fragile.

    Cleanest approach: use a custom context mapping that tracks how many times
    each simple name has been accessed during the render.  Jinja2's variable
    lookup calls ``context[name]`` (via ``Environment.getitem`` or
    ``context.__getitem__``).  We override the context dict to return a
    ``_SlotRef`` on the second and subsequent accesses to the same name.

    This last approach is what we implement here: return a ``_DeduplicatingDict``
    that wraps *context*.
    """
    # Find all simple-name references in the SQL template.
    names_seen: set[str] = set()
    multi_names: set[str] = set()
    for m in _SIMPLE_VAR_RE.finditer(sql):
        name = m.group(1)
        if name in names_seen:
            multi_names.add(name)
        names_seen.add(name)

    if not multi_names:
        # No duplicates — return context as-is (no wrapping needed).
        return dict(context)

    # For names that appear more than once, wrap the value in a FirstOnce proxy.
    wrapped: dict[str, Any] = dict(context)
    for name in multi_names:
        if name in context:
            wrapped[name] = _FirstOnceValue(context[name], style)

    return wrapped


class _FirstOnceValue:
    """On first finalize call, bind the value and record the placeholder.

    On subsequent finalize calls, return a ``_SlotRef`` pointing to the
    already-assigned placeholder.

    Since Jinja2 evaluates ``{{ name }}`` by looking up ``name`` in the context
    dict and THEN calling ``finalize(value)``, we intercept at finalize time by
    using this wrapper class: finalize checks ``isinstance(value, _FirstOnceValue)``
    and delegates accordingly.
    """

    __slots__ = ("_value", "_style", "_slot_ref", "_bound")

    def __init__(self, value: Any, style: str) -> None:
        self._value = value
        self._style = style
        self._slot_ref: _SlotRef | None = None
        self._bound: bool = False

    def bind_once(self) -> str:
        """Bind the value and return its placeholder (called on first emit)."""
        _state.params.append(self._value)
        n = len(_state.params)
        ph = _placeholder(self._style, n)
        self._slot_ref = _SlotRef(ph)
        self._bound = True
        return ph

    def reuse(self) -> str:
        """Return the already-bound placeholder (called on subsequent emits)."""
        assert self._slot_ref is not None
        return self._slot_ref.placeholder


# ---------------------------------------------------------------------------
# Updated finalize to handle _FirstOnceValue
# ---------------------------------------------------------------------------


def _make_finalize_v2(style: str) -> Callable[[Any], str]:
    """Finalize hook that handles deduplication via _FirstOnceValue.

    Extends _make_finalize to also handle:
    - ``_FirstOnceValue``: bind on first call, re-use placeholder on subsequent.
    """

    def finalize(value: Any) -> str:
        if isinstance(value, _RawSQL):
            return value.value

        if isinstance(value, _SlotRef):
            return value.placeholder

        if isinstance(value, _FirstOnceValue):
            if value._bound:
                return value.reuse()
            else:
                return value.bind_once()

        # Detect Jinja2 Undefined objects — they reach finalize when
        # StrictUndefined is set because finalize is called before the
        # Undefined's __str__ raises.  We must raise here explicitly.
        if isinstance(value, jinja2.Undefined):
            # Calling _fail_with_undefined_error triggers StrictUndefined's
            # UndefinedError.  We call __str__ on it which raises for Strict.
            value._fail_with_undefined_error()  # always raises UndefinedError

        # Normal value: bind and return placeholder.
        _state.params.append(value)
        n = len(_state.params)
        return _placeholder(style, n)

    return finalize


# ---------------------------------------------------------------------------
# Public render function
# ---------------------------------------------------------------------------


def render_sql_template(
    sql: str,
    context: dict[str, Any],
    dialect: str = "postgres",
) -> tuple[str, list[Any]]:
    """Render *sql* as a Jinja2 SQL template, binding all output values.

    This is the main entry point for the template engine.

    Parameters
    ----------
    sql:
        A SQL string that may contain Jinja2 template syntax:
        - ``{{ varname }}`` — binds the value and emits a positional placeholder.
        - ``{% if cond %}…{% endif %}`` — conditional SQL block.
        - ``{% for x in items %}…{% endfor %}`` — loop.
        - ``{{ ids | inclause }}`` — bind a list as an IN clause.
        - ``{{ val | sqlsafe }}`` — raw SQL (UNSAFE — trusted values only).
        Plain ``{{name}}`` (no spaces) also works (standard Jinja2 syntax).
    context:
        Mapping of template variable names to their resolved values.  All
        names referenced via ``{{ }}`` in *sql* MUST be present; a missing
        name raises ``jinja2.UndefinedError``.
    dialect:
        Target SQL dialect for placeholder style:
        ``'postgres'`` / ``'duckdb'`` → ``$N`` ;
        ``'mysql'`` → ``%s`` ;
        ``'sqlite'`` → ``?`` ;
        anything else → ``?``.

    Returns
    -------
    tuple[str, list[Any]]
        ``(rendered_sql, params)`` where *rendered_sql* contains only
        positional placeholders (no raw values) and *params* is the ordered
        list of bound values.

    Raises
    ------
    jinja2.UndefinedError
        If a ``{{ varname }}`` references a name not in *context*.
    jinja2.TemplateSyntaxError
        If *sql* contains invalid Jinja2 syntax.
    jinja2.exceptions.SecurityError
        If the template attempts to access blocked attributes (sandbox
        protection, e.g. ``{{ ''.__class__ }}``).
    ValueError
        If the ``inclause`` filter receives an empty list.
    """
    style = _DIALECT_STYLE.get(dialect.lower(), _POSITIONAL_QMARK)

    # Reset thread-local state for this render.
    _state.params = []
    _state.style = style
    _state.name_to_slot = {}
    _state.active = True

    # Build a dedup-aware context for repeated simple-name references.
    dedup_context = _build_dedup_context(sql, context, style)

    try:
        # Build an env with the v2 finalize (handles _FirstOnceValue).
        env = SandboxedEnvironment(
            autoescape=False,
            undefined=jinja2.StrictUndefined,
            finalize=_make_finalize_v2(style),
            keep_trailing_newline=True,
        )
        env.filters["inclause"] = _filter_inclause
        env.filters["bind_in"] = _filter_inclause
        env.filters["sqlsafe"] = _filter_sqlsafe

        tmpl = env.from_string(sql)
        rendered = tmpl.render(**dedup_context)
    finally:
        _state.active = False

    return rendered, list(_state.params)
