"""Unit tests for ComputePlacementRouter (M4-A).

Each test calls ``place()`` with a specific cell descriptor and asserts the
expected tier string.  No IO — the router is pure logic.

Coverage
--------
- sql → warehouse
- small pure Python, all Pyodide libs → browser
- needs_native_wheel=True, remote off → local_kernel
- needs_native_wheel=True, remote on → remote_kernel
- est_rows > browser_row_cap, remote off → local_kernel
- est_rows > browser_row_cap, remote on → remote_kernel
- unknown lib, remote off → local_kernel
- unknown lib, remote on → remote_kernel
- est_rows exactly at cap → browser (boundary)
- est_rows one above cap → local_kernel / remote_kernel
- empty libs list → browser (no lib restriction)
"""

from __future__ import annotations

import pytest

from app.compute.router import ComputePlacementRouter, _DEFAULT_BROWSER_ROW_CAP


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def router(remote: bool = False) -> ComputePlacementRouter:
    return ComputePlacementRouter(remote_configured=remote)


# ===========================================================================
# SQL → warehouse
# ===========================================================================


def test_sql_always_warehouse_remote_off():
    assert router(remote=False).place({"kind": "sql"}) == "warehouse"


def test_sql_always_warehouse_remote_on():
    assert router(remote=True).place({"kind": "sql"}) == "warehouse"


def test_sql_with_large_rows_still_warehouse():
    assert router().place({"kind": "sql", "est_rows": 10_000_000}) == "warehouse"


# ===========================================================================
# Small pure-Python → browser
# ===========================================================================


def test_small_pure_python_no_libs_browser():
    cell = {"kind": "python", "est_rows": 100, "libs": []}
    assert router().place(cell) == "browser"


def test_small_pure_python_pyodide_libs_browser():
    cell = {
        "kind": "python",
        "est_rows": 500,
        "libs": ["numpy", "pandas"],
        "needs_native_wheel": False,
    }
    assert router().place(cell) == "browser"


def test_all_pyodide_libs_at_row_cap_browser():
    """Row count exactly at the cap should still route to browser."""
    cell = {
        "kind": "python",
        "est_rows": _DEFAULT_BROWSER_ROW_CAP,
        "libs": ["scipy"],
    }
    assert router().place(cell) == "browser"


def test_zero_rows_browser():
    cell = {"kind": "python", "est_rows": 0, "libs": ["pyarrow"]}
    assert router().place(cell) == "browser"


# ===========================================================================
# needs_native_wheel → off-browser
# ===========================================================================


def test_needs_native_wheel_remote_off_local_kernel():
    cell = {"kind": "python", "needs_native_wheel": True, "est_rows": 10}
    assert router(remote=False).place(cell) == "local_kernel"


def test_needs_native_wheel_remote_on_remote_kernel():
    cell = {"kind": "python", "needs_native_wheel": True, "est_rows": 10}
    assert router(remote=True).place(cell) == "remote_kernel"


# ===========================================================================
# est_rows > browser_row_cap → off-browser
# ===========================================================================


def test_rows_above_cap_remote_off():
    cell = {"kind": "python", "est_rows": _DEFAULT_BROWSER_ROW_CAP + 1}
    assert router(remote=False).place(cell) == "local_kernel"


def test_rows_above_cap_remote_on():
    cell = {"kind": "python", "est_rows": _DEFAULT_BROWSER_ROW_CAP + 1}
    assert router(remote=True).place(cell) == "remote_kernel"


def test_large_rows_remote_off():
    cell = {"kind": "python", "est_rows": 50_000_000, "libs": ["numpy"]}
    assert router(remote=False).place(cell) == "local_kernel"


# ===========================================================================
# Unknown lib (not in Pyodide allowlist) → off-browser
# ===========================================================================


def test_unknown_lib_remote_off():
    cell = {"kind": "python", "libs": ["torch"], "est_rows": 10}
    assert router(remote=False).place(cell) == "local_kernel"


def test_unknown_lib_remote_on():
    cell = {"kind": "python", "libs": ["torch"], "est_rows": 10}
    assert router(remote=True).place(cell) == "remote_kernel"


def test_mix_known_unknown_libs_remote_off():
    """A single unknown lib among known ones → off-browser."""
    cell = {
        "kind": "python",
        "libs": ["numpy", "lightgbm"],  # lightgbm not in Pyodide
        "est_rows": 100,
    }
    assert router(remote=False).place(cell) == "local_kernel"


# ===========================================================================
# Custom browser_row_cap
# ===========================================================================


def test_custom_browser_row_cap():
    r = ComputePlacementRouter(remote_configured=False, browser_row_cap=100)
    assert r.place({"kind": "python", "est_rows": 100}) == "browser"
    assert r.place({"kind": "python", "est_rows": 101}) == "local_kernel"


# ===========================================================================
# Default values (missing keys in cell dict)
# ===========================================================================


def test_missing_est_rows_defaults_zero():
    """Missing est_rows defaults to 0 → browser (if no other disqualifier)."""
    assert router().place({"kind": "python"}) == "browser"


def test_missing_needs_native_wheel_defaults_false():
    """Missing needs_native_wheel defaults to False."""
    assert router().place({"kind": "python", "libs": ["pandas"]}) == "browser"
