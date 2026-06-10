"""Shared deterministic helpers for the demo dataset generators.

Everything is derived from a stable SHA-256 hash so every generator is fully
deterministic — two generations of the same dataset are identical.
"""

from __future__ import annotations

import hashlib
import math
from datetime import date

# All four datasets share the same 24-month window ending 2026-05 (the demo
# "current date" is 2026-06).
N_MONTHS = 24
END_YEAR = 2026
END_MONTH = 5


def noise(*parts: object) -> float:
    """Deterministic float in [0, 1) from a stable SHA-256 over *parts*."""
    key = "|".join(str(p) for p in parts).encode("utf-8")
    digest = hashlib.sha256(key).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def pick(items: list, *parts: object):
    """Deterministically pick one of *items* from the hash of *parts*."""
    return items[int(noise(*parts) * len(items)) % len(items)]


def weighted_pick(pairs: list[tuple], *parts: object):
    """Deterministic weighted choice from ``[(item, weight), ...]``."""
    total = sum(w for _, w in pairs)
    r = noise(*parts) * total
    acc = 0.0
    for item, w in pairs:
        acc += w
        if r < acc:
            return item
    return pairs[-1][0]


def iter_months() -> list[tuple[int, date, str]]:
    """Return ``[(month_index, first_of_month, 'YYYY-MM'), ...]`` ascending."""
    y, m = END_YEAR, END_MONTH
    stack: list[date] = []
    for _ in range(N_MONTHS):
        stack.append(date(y, m, 1))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    stack.reverse()
    return [(idx, d, f"{d.year:04d}-{d.month:02d}") for idx, d in enumerate(stack)]


def seasonality(month_num: int) -> float:
    """Smooth seasonal multiplier (~0.85–1.15) peaking mid-year and December."""
    summer = math.sin((month_num - 3) / 12.0 * 2 * math.pi)
    festive = math.cos((month_num - 12) / 12.0 * 2 * math.pi)
    return 1.0 + 0.09 * summer + 0.06 * festive
