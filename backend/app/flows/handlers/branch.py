"""Branch (conditional routing) handler for the Flows engine.

Implements the ``'branch'`` task kind.  The handler evaluates an ordered list
of ``{when, next}`` conditions against the resolved template namespace and
returns the first matching condition's ``next`` task keys in a sentinel field
that the runtime reads to activate/deactivate downstream task_runs.

Public API
----------
handle_branch(config, ctx, claims) -> dict
    Evaluate ``config['conditions']`` in order; return
    ``{"branch_taken": label, "branch_index": i, "__branch_next__": [...]}``.

``when`` expression format
--------------------------
Two formats are supported:

1. **Full-expression inside braces** (preferred):
   ``"{{ inputs.classify.label == 'high_value' }}"``
   The content inside ``{{ }}`` is not a dot-path but an arbitrary Python
   boolean expression evaluated against a namespace containing the special
   locals ``inputs``, ``params``, and ``secrets`` directly.

   Example: ``{{ inputs.classify.label == 'high_value' }}``
   resolves to: ``eval("inputs.classify.label == 'high_value'", {inputs: ...})``
   (dict access, not attribute — so the expression must use ``['key']``
   notation or we pre-expand the top-level names for convenience).

2. **Template substitution then literal/eval**:
   ``"{{ inputs.classify.label }} == 'high_value'"``
   The ``{{ ... }}`` dot-paths are substituted with their native values
   (wrapped as Python repr) so the resulting expression can be safely eval'd.

Resolved Decisions (from task prompt)
--------------------------------------
Q1:  ``else_`` / ``default`` is OPTIONAL.  If no condition matches and
     ``default`` is omitted (empty list), a ``ValueError`` is raised so the
     engine marks the branch ``'failed'`` and all downstream tasks receive
     ``'upstream_failed'``.  This prevents silent hangs.

Security note on ``eval``
--------------------------
``when`` expressions are authored by the flow author (an authenticated user),
not by end-users.  The expression is evaluated with ``__builtins__`` removed
and only ``inputs``, ``params``, and ``secrets`` in scope.  This is the same
trust boundary as the existing ``'python'`` task kind which executes arbitrary
subprocess code.

A sandboxed evaluator (e.g. ``simpleeval``) can replace ``eval`` as a
hardening step but is not required for the initial implementation.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.flows.executor import TaskContext

# Matches ``{{ some content }}`` — content may include non-greedy anything.
_BRACE_RE = re.compile(r"\{\{(.+?)\}\}")

# Matches a pure ``{{ dot.path.expr }}`` expression (only word chars and dots).
_PURE_PATH_RE = re.compile(r"^\{\{\s*([\w.]+)\s*\}\}$")


def _resolve_native_value(path: str, ctx: "TaskContext") -> Any:
    """Resolve a dotted path expression to its native Python value.

    Parameters
    ----------
    path:
        A dotted namespace path such as ``"inputs.classify.label"`` or
        ``"params.region_code"``.
    ctx:
        Task execution context.

    Returns
    -------
    Any
        The native Python value (str, int, list, dict, …).
        Returns ``""`` for unknown namespaces or missing keys.
    """
    parts = path.strip().split(".")
    if not parts:
        return ""

    namespace = parts[0]
    rest = parts[1:]

    if namespace == "params":
        if not rest:
            return ctx.flow_params
        val: Any = ctx.flow_params.get(rest[0])
        for key in rest[1:]:
            if isinstance(val, dict):
                val = val.get(key)
            else:
                return val
        return val

    if namespace == "inputs":
        if not rest:
            return ctx.inputs
        task_key = rest[0]
        val = ctx.inputs.get(task_key, {})
        for key in rest[1:]:
            if isinstance(val, dict):
                val = val.get(key)
            else:
                return val
        return val

    if namespace == "secrets":
        if not rest:
            return ctx.secrets
        secret_name = rest[0]
        return ctx.secrets.get(secret_name, "")

    return ""


def _eval_when(when_expr: str, ctx: "TaskContext", condition_index: int) -> bool:
    """Evaluate a ``when`` expression against the task context.

    Supports two formats:

    1. Pure dotted-path template: ``"{{ inputs.x.label }}"``
       Resolved to the native value; truthy check applied.

    2. Template with surrounding operators: ``"{{ inputs.x.label }} == 'high'"``
       Each ``{{ path }}`` token is replaced with ``repr(native_value)`` so
       the expression can be safely ``eval``'d as a Python comparison.

    3. Full Python expression in braces: ``"{{ inputs.x.label == 'high' }}"``
       The content is eval'd against ``{"inputs": ctx.inputs, "params": ...,
       "secrets": ...}`` directly.

    Parameters
    ----------
    when_expr:
        The condition expression string.
    ctx:
        Task execution context.
    condition_index:
        Used only for error messages.

    Returns
    -------
    bool
        ``True`` if the condition is satisfied.

    Raises
    ------
    ValueError
        If the expression cannot be evaluated.
    """
    import ast  # noqa: PLC0415

    when_expr = when_expr.strip()
    if not when_expr:
        return False

    # Safe builtins available in all eval calls.
    safe_builtins: dict[str, Any] = {
        "__builtins__": {},
        "int": int,
        "float": float,
        "str": str,
        "bool": bool,
        "len": len,
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "list": list,
        "dict": dict,
        "tuple": tuple,
        "set": set,
    }

    def _make_ns() -> dict[str, Any]:
        """Build eval namespace: safe builtins + ctx top-level names."""
        ns = dict(safe_builtins)
        ns.update({
            "inputs": ctx.inputs,
            "params": ctx.flow_params,
            "secrets": ctx.secrets,
        })
        return ns

    # Fast path: plain Python literal with no template markers at all.
    if "{{" not in when_expr:
        try:
            return bool(ast.literal_eval(when_expr))
        except (ValueError, SyntaxError):
            pass
        try:
            return bool(eval(when_expr, _make_ns(), {}))  # noqa: S307
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"branch handler: failed to evaluate condition[{condition_index}] "
                f"when={when_expr!r}: {exc}"
            ) from exc

    # Check if this is a PURE single-path template: ``{{ a.b.c }}``
    m = _PURE_PATH_RE.match(when_expr)
    if m:
        native = _resolve_native_value(m.group(1), ctx)
        # If the native value is a string, try to interpret it as a Python
        # literal (e.g. "True", "False", "0", "1") before the raw bool test.
        if isinstance(native, str):
            try:
                return bool(ast.literal_eval(native.strip()))
            except (ValueError, SyntaxError):
                pass
        return bool(native)

    # Count the number of {{ }} blocks.
    braces = _BRACE_RE.findall(when_expr)

    # Check if the WHOLE string is one single {{ ... }} block (possibly a
    # full Python expression inside, not just a dot-path).
    if len(braces) == 1 and when_expr.strip().startswith("{{") and when_expr.strip().endswith("}}"):
        inner = braces[0].strip()
        # Is the inner content a simple dot-path?  If so, return truthiness.
        if re.match(r'^[\w.]+$', inner):
            return bool(_resolve_native_value(inner, ctx))
        # Otherwise evaluate as a full Python expression with ctx namespaces.
        try:
            return bool(eval(inner, _make_ns(), {}))  # noqa: S307
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"branch handler: failed to evaluate condition[{condition_index}] "
                f"when={when_expr!r} (inner={inner!r}): {exc}"
            ) from exc

    # Mixed template + operators: ``{{ path }} == 'value'``
    # Replace each {{ path }} with repr(native_value) so the full expression
    # is a valid Python literal comparison.
    def _substitute(match: re.Match) -> str:  # type: ignore[type-arg]
        path = match.group(1).strip()
        # If it looks like a plain dot-path, resolve and repr it.
        if re.match(r'^[\w.]+$', path):
            native = _resolve_native_value(path, ctx)
            return repr(native)
        # Otherwise leave the brace content as-is (will eval with namespace).
        return match.group(0)

    substituted = _BRACE_RE.sub(_substitute, when_expr)

    # Now try as a literal first, then as a Python expression.
    try:
        return bool(ast.literal_eval(substituted.strip()))
    except (ValueError, SyntaxError):
        pass

    try:
        return bool(eval(substituted, _make_ns(), {}))  # noqa: S307
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"branch handler: failed to evaluate condition[{condition_index}] "
            f"when={when_expr!r} (substituted={substituted!r}): {exc}"
        ) from exc


def handle_branch(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate branch conditions and return the routing result.

    Conditions are evaluated in order; the first matching condition wins.
    If no condition matches and ``default`` is set, the default tasks are
    activated.  If neither condition matches nor default exists, a
    ``ValueError`` is raised (engine marks the task ``'failed'``).

    Parameters
    ----------
    config:
        Resolved task config dict.  Must contain ``'conditions'`` (list of
        ``{when, next}`` dicts).  Optional: ``'default'`` (list[str]).
    ctx:
        Task execution context (provides ``flow_params``, ``inputs``,
        ``secrets`` for template resolution).
    claims:
        Caller's auth claims (not used by this handler directly).

    Returns
    -------
    dict
        ``{
            "branch_taken":    "condition_0" | "condition_1" | … | "default",
            "branch_index":    <int, -1 for default>,
            "__branch_next__": [task_key, ...]
        }``

        The runtime reads ``__branch_next__`` and activates those tasks;
        all other downstream tasks that depend on this branch node are
        marked ``'upstream_failed'``.

    Raises
    ------
    ValueError
        If no condition matches and ``default`` is empty/absent (Q1: optional
        else_).
    """
    conditions: list[dict[str, Any]] = config.get("conditions", [])
    default_next: list[str] = config.get("default") or []

    for i, cond in enumerate(conditions):
        when_expr: str = cond.get("when", "")
        if not when_expr:
            continue

        matched = _eval_when(when_expr, ctx, condition_index=i)

        if matched:
            next_keys: list[str] = cond.get("next") or []
            return {
                "branch_taken": f"condition_{i}",
                "branch_index": i,
                "__branch_next__": next_keys,
            }

    # No condition matched — fall back to default.
    if default_next:
        return {
            "branch_taken": "default",
            "branch_index": -1,
            "__branch_next__": default_next,
        }

    # No match, no default (Q1: else_ is optional; absence → fail).
    raise ValueError(
        "branch handler: no condition matched and no 'default' is configured. "
        "All downstream tasks that depend on this branch will be marked "
        "'upstream_failed'.  Add a 'default' list or ensure conditions are "
        "exhaustive."
    )
