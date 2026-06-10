"""Safe boolean expression evaluator for cell ``run_when`` gates.

A cell (sql / python / markdown) may carry a ``config.run_when`` string — a
boolean expression over ``inputs`` / ``params`` / ``secrets``.  When it
evaluates to ``False`` the cell is SKIPPED (task_run state ``'skipped'``).
Empty / absent ⇒ the cell always runs.

This is the "decision" half of the old ``branch`` kind reframed as a per-cell
setting: a Python cell returns a value, and downstream cells reference it in
their ``run_when`` expressions, e.g.::

    run_when = "inputs.classify.label == 'high_value'"

Safety (PINNED)
---------------
Unlike ``branch._eval_when`` (which uses ``eval`` with empty builtins), this
evaluator uses a RESTRICTED AST walk — there is NO ``eval`` / ``exec`` and no
access to builtins or attributes of arbitrary objects.  Only a fixed grammar of
node types is permitted:

- ``Expression``
- ``BoolOp`` (``and`` / ``or``), ``UnaryOp`` (``not`` / unary ``-`` / ``+``)
- ``BinOp`` (``+ - * / %``)
- ``Compare`` (``== != < <= > >= in not in is is not``)
- ``Name``, ``Attribute``, ``Subscript`` (both dict-get)
- ``Constant``, ``List``, ``Tuple``, ``Dict``, ``Set``
- ``Index`` / ``Slice`` (subscript support on older ASTs)
- ``Call`` restricted to a fixed table ``{len,str,int,float,bool,abs,min,max}``

Any other node (arbitrary ``Call``, ``Lambda``, comprehensions, ``Starred``,
attribute writes, etc.) raises ``ValueError``.

Names resolve ONLY against ``{inputs, params, secrets, True, False, None}``.
``obj.x`` and ``obj['x']`` both do a soft dict ``.get('x')`` — an unknown key
yields ``None`` (so a not-yet-run upstream cell never raises).  A malformed
expression (parse error or disallowed node) raises ``ValueError`` so the gate
FAILS LOUDLY rather than silently skipping the cell.

Public API
----------
evaluate_run_when(expr, ctx) -> bool
    Evaluate *expr* (a ``run_when`` string) against the task context's
    ``inputs`` / ``flow_params`` / ``secrets`` and return its truthiness.
"""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.flows.executor import TaskContext


# Fixed table of permitted call targets (never sourced from builtins dict).
_SAFE_CALLS: dict[str, Any] = {
    "len": len,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "abs": abs,
    "min": min,
    "max": max,
}

# Strips ``{{ ... }}`` braces around a PURE dot-path so the namespace form is
# canonical: ``{{ inputs.x.label }}`` → ``inputs.x.label``.  Only applied when
# the whole expression is a single brace block wrapping a bare dot-path.
_PURE_BRACE_PATH_RE = re.compile(r"^\{\{\s*([\w.]+)\s*\}\}$")


def _strip_braces(expr: str) -> str:
    """Strip ``{{ }}`` around a pure dot-path; pass everything else through.

    The namespace form (``inputs.x == 'y'``) is canonical.  We only unwrap the
    template form when the ENTIRE expression is ``{{ <dot.path> }}`` — mixed
    template + operator strings are left intact so the AST parser sees a valid
    Python expression (callers should author the namespace form).
    """
    s = expr.strip()
    m = _PURE_BRACE_PATH_RE.match(s)
    if m:
        return m.group(1)
    return s


def _dict_get(obj: Any, key: str) -> Any:
    """Soft dict-get: ``obj['key']`` / ``obj.key`` ⇒ ``obj.get(key)``.

    Unknown key ⇒ ``None`` so a not-yet-run upstream cell (absent from
    ``inputs``) never raises.  Non-dict objects also yield ``None``.
    """
    if isinstance(obj, dict):
        return obj.get(key)
    return None


def evaluate_run_when(expr: Any, ctx: "TaskContext") -> bool:
    """Return the truthiness of the ``run_when`` expression *expr*.

    Parameters
    ----------
    expr:
        The ``run_when`` string (boolean expression over ``inputs`` /
        ``params`` / ``secrets``).  Empty / ``None`` ⇒ ``True`` (always runs).
    ctx:
        The task execution context (provides ``inputs`` / ``flow_params`` /
        ``secrets``).

    Returns
    -------
    bool
        ``True`` when the cell should run, ``False`` when it should be skipped.

    Raises
    ------
    ValueError
        If *expr* fails to parse or contains a disallowed node — the gate must
        fail loudly, never silently skip.
    """
    if expr is None:
        return True
    text = _strip_braces(str(expr))
    if not text:
        return True

    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ValueError(
            f"run_when: could not parse expression {expr!r}: {exc}"
        ) from exc

    names: dict[str, Any] = {
        "inputs": ctx.inputs,
        "params": ctx.flow_params,
        "secrets": ctx.secrets,
        "True": True,
        "False": False,
        "None": None,
    }

    try:
        value = _eval_node(tree.body, names)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 — surface any eval error as ValueError
        raise ValueError(
            f"run_when: failed to evaluate expression {expr!r}: {exc}"
        ) from exc

    return bool(value)


def _eval_node(node: ast.AST, names: dict[str, Any]) -> Any:
    """Recursively evaluate a permitted AST node; raise on anything else."""
    # ── Literals ──────────────────────────────────────────────────────────
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.List):
        return [_eval_node(e, names) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(e, names) for e in node.elts)
    if isinstance(node, ast.Set):
        return {_eval_node(e, names) for e in node.elts}
    if isinstance(node, ast.Dict):
        return {
            _eval_node(k, names): _eval_node(v, names)
            for k, v in zip(node.keys, node.values)
            if k is not None
        }

    # ── Name resolution ───────────────────────────────────────────────────
    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        raise ValueError(
            f"run_when: unknown name {node.id!r} "
            "(only inputs/params/secrets/True/False/None are allowed)."
        )

    # ── Attribute / Subscript → soft dict-get ─────────────────────────────
    if isinstance(node, ast.Attribute):
        obj = _eval_node(node.value, names)
        return _dict_get(obj, node.attr)

    if isinstance(node, ast.Subscript):
        obj = _eval_node(node.value, names)
        key = _eval_subscript_key(node.slice, names)
        if isinstance(key, str):
            return _dict_get(obj, key)
        # Numeric / slice indexing into a list / tuple.
        try:
            return obj[key]  # type: ignore[index]
        except (TypeError, KeyError, IndexError):
            return None

    # ── Boolean / unary / binary / comparison ─────────────────────────────
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result: Any = True
            for v in node.values:
                result = _eval_node(v, names)
                if not result:
                    return result
            return result
        if isinstance(node.op, ast.Or):
            result = False
            for v in node.values:
                result = _eval_node(v, names)
                if result:
                    return result
            return result
        raise ValueError("run_when: unsupported boolean operator.")

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, names)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return +operand
        raise ValueError("run_when: unsupported unary operator.")

    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, names)
        right = _eval_node(node.right, names)
        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            return left / right
        if isinstance(op, ast.Mod):
            return left % right
        raise ValueError("run_when: unsupported binary operator.")

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, names)
        result = True
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, names)
            if not _apply_compare(op, left, right):
                return False
            left = right
        return result

    # ── Restricted Call (fixed safe table) ────────────────────────────────
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_CALLS:
            raise ValueError(
                "run_when: function calls are restricted to "
                f"{sorted(_SAFE_CALLS)}."
            )
        if node.keywords:
            raise ValueError("run_when: keyword arguments are not allowed.")
        func = _SAFE_CALLS[node.func.id]
        args = [_eval_node(a, names) for a in node.args]
        return func(*args)

    raise ValueError(
        f"run_when: disallowed expression node {type(node).__name__!r}."
    )


def _eval_subscript_key(node: ast.AST, names: dict[str, Any]) -> Any:
    """Resolve a subscript key, tolerating the legacy ``ast.Index`` wrapper."""
    # Python <3.9 wraps the key in ast.Index; unwrap it for compatibility.
    inner = getattr(ast, "Index", None)
    if inner is not None and isinstance(node, inner):  # pragma: no cover
        node = node.value  # type: ignore[attr-defined]
    if isinstance(node, ast.Slice):
        lower = _eval_node(node.lower, names) if node.lower else None
        upper = _eval_node(node.upper, names) if node.upper else None
        step = _eval_node(node.step, names) if node.step else None
        return slice(lower, upper, step)
    return _eval_node(node, names)


def _apply_compare(op: ast.cmpop, left: Any, right: Any) -> bool:
    """Apply a single comparison operator (permitted subset only)."""
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    if isinstance(op, ast.In):
        return left in right
    if isinstance(op, ast.NotIn):
        return left not in right
    if isinstance(op, ast.Is):
        return left is right
    if isinstance(op, ast.IsNot):
        return left is not right
    raise ValueError("run_when: unsupported comparison operator.")
