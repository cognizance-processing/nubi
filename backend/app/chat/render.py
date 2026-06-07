"""Server-side chart rendering for the Nubi chat gateway (M22-A).

``render_chart_png`` converts a minimal chart spec + tabular data into PNG bytes
using matplotlib (Agg backend, no display required).

Design contract
---------------
The interface is intentionally slim: a plain dict spec and a list of row dicts
go in; raw PNG bytes come out.  The matplotlib implementation is a stand-in;
a headless-ECharts (or regl) renderer can replace it later by providing the
same signature.

Spec format (minimal, not strict)
----------------------------------
::

    {
        "type": "bar" | "line" | "scatter" | "pie",   # default: "bar"
        "title": "Revenue by Region",                  # optional
        "x": "region",                                 # column name for x-axis
        "y": "revenue"                                 # column name for y-axis
    }

If *x* or *y* are absent the renderer falls back to the first two column names
found in the first row.

Raises
------
AppError("chart_render_failed", ..., 500)
    If matplotlib is not installed or rendering fails unexpectedly.
"""

from __future__ import annotations

import io
from typing import Any

__all__ = ["render_chart_png"]


def render_chart_png(
    chart: dict[str, Any],
    table_or_rows: list[dict[str, Any]] | Any,
) -> bytes:
    """Render *chart* spec + *table_or_rows* data to PNG bytes.

    Parameters
    ----------
    chart:
        Minimal chart descriptor:
        ``{type?, title?, x?, y?}``.
        *type* must be one of ``"bar"``, ``"line"``, ``"scatter"``, ``"pie"``.
        Defaults to ``"bar"`` when absent or unrecognised.
    table_or_rows:
        An iterable of row dicts (``[{col: value, ...}, ...]``).
        If a pyarrow Table is passed its ``to_pylist()`` method is called
        automatically.

    Returns
    -------
    bytes
        Raw PNG bytes starting with ``b'\\x89PNG'``.

    Raises
    ------
    AppError("chart_render_failed", ..., 500)
        If matplotlib is unavailable or rendering fails.
    """
    # ── Lazy import — keeps the module importable even if matplotlib is absent ──
    try:
        import matplotlib  # noqa: PLC0415
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # noqa: PLC0415
    except ImportError as exc:
        from app.errors import AppError  # noqa: PLC0415
        raise AppError(
            "chart_render_failed",
            "matplotlib is required for chart rendering. "
            "Install it with: pip install matplotlib",
            500,
        ) from exc

    # ── Normalise rows ────────────────────────────────────────────────────────
    if hasattr(table_or_rows, "to_pylist"):
        rows: list[dict[str, Any]] = table_or_rows.to_pylist()
    else:
        rows = list(table_or_rows)

    # ── Resolve spec fields with sensible defaults ────────────────────────────
    chart_type: str = (chart.get("type") or "bar").lower()
    title: str = chart.get("title") or ""

    # Determine x/y column names from the spec; fall back to first two columns.
    all_cols: list[str] = list(rows[0].keys()) if rows else []
    x_col: str = chart.get("x") or (all_cols[0] if all_cols else "x")
    y_col: str = chart.get("y") or (all_cols[1] if len(all_cols) > 1 else "y")

    # Extract vectors (missing values become 0 / empty string).
    x_vals: list[Any] = [row.get(x_col, "") for row in rows]
    y_vals: list[Any] = []
    for row in rows:
        raw = row.get(y_col, 0)
        try:
            y_vals.append(float(raw))
        except (TypeError, ValueError):
            y_vals.append(0.0)

    # ── Draw ─────────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(6, 4))

    try:
        if chart_type == "pie":
            # For pie charts use y values as sizes and x labels as labels.
            ax.pie(y_vals, labels=[str(v) for v in x_vals], autopct="%1.0f%%")
        elif chart_type == "scatter":
            x_numeric: list[float] = []
            for v in x_vals:
                try:
                    x_numeric.append(float(v))
                except (TypeError, ValueError):
                    x_numeric.append(0.0)
            ax.scatter(x_numeric, y_vals)
            ax.set_xlabel(x_col)
            ax.set_ylabel(y_col)
        elif chart_type == "line":
            ax.plot(range(len(x_vals)), y_vals, marker="o")
            step = max(1, len(x_vals) // 10)
            ax.set_xticks(range(0, len(x_vals), step))
            ax.set_xticklabels(
                [str(x_vals[i]) for i in range(0, len(x_vals), step)],
                rotation=30,
                ha="right",
            )
            ax.set_xlabel(x_col)
            ax.set_ylabel(y_col)
        else:
            # Default: bar chart
            ax.bar(range(len(x_vals)), y_vals)
            step = max(1, len(x_vals) // 10)
            ax.set_xticks(range(0, len(x_vals), step))
            ax.set_xticklabels(
                [str(x_vals[i]) for i in range(0, len(x_vals), step)],
                rotation=30,
                ha="right",
            )
            ax.set_xlabel(x_col)
            ax.set_ylabel(y_col)

        if title:
            ax.set_title(title)

        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        buf.seek(0)
        png_bytes = buf.read()
    except Exception as exc:  # pragma: no cover
        from app.errors import AppError  # noqa: PLC0415
        raise AppError(
            "chart_render_failed",
            f"Chart rendering failed: {exc}",
            500,
        ) from exc
    finally:
        plt.close(fig)

    return png_bytes
