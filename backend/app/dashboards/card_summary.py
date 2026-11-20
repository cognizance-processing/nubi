"""Slim board configs for list views (``GET /boards?view=card``).

The boards gallery draws each card from the spec's widget geometry (see the
frontend's ``specMiniature.ts``) and shows a widget count and folder. It does
not need widget SQL params, styles, HTML, encodings or filter variables, which
are most of a board's bytes (~460 KB for a 48-board project).

A summarised row carries ``config_partial: true``. It must never be written
back: ``PUT /boards/{id}`` replaces the whole config, so a client that wants to
change one field re-reads the full row first.
"""

from __future__ import annotations

from typing import Any

from app.errors import AppError

#: Set inside every summarised config so a write of one can be refused.
CARD_SUMMARY_MARKER = "_card_summary"

# Widget fields the card needs: identity, kind, geometry, tab and placement.
_WIDGET_KEYS = ("id", "type", "chart_type", "pos", "tab_id", "placement", "drawer", "hidden")

# Spec fields dropped from cards (large, and not drawn).
_SPEC_DROP = ("widgets", "variables")


def card_summary_config(config: Any) -> Any:
    """Return *config* with the spec reduced to what a board card draws."""
    if not isinstance(config, dict):
        return config
    out = dict(config)
    out[CARD_SUMMARY_MARKER] = True
    if isinstance(out.get("html"), str) and out["html"]:
        # Cards only ask "is this an HTML board?"; the markup can be large.
        out["html"] = True
    spec = out.get("spec")
    if isinstance(spec, dict):
        slim = {k: v for k, v in spec.items() if k not in _SPEC_DROP}
        widgets = spec.get("widgets")
        if isinstance(widgets, list):
            slim["widgets"] = [
                {k: w[k] for k in _WIDGET_KEYS if k in w} if isinstance(w, dict) else w
                for w in widgets
            ]
        out["spec"] = slim
    return out


def card_summary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarise every row's config and mark it partial."""
    out = []
    for row in rows:
        r = dict(row)
        r["config"] = card_summary_config(r.get("config"))
        r["config_partial"] = True
        out.append(r)
    return out


def reject_card_summary(config: Any) -> None:
    """Refuse to store a card summary as a board's config (it would drop data)."""
    if isinstance(config, dict) and config.get(CARD_SUMMARY_MARKER):
        raise AppError(
            "partial_config",
            "This board config is a list summary, not the full board. "
            "Load the board and save its full config instead.",
            409,
        )
