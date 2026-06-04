"""Compute placement router — M4-A.

``ComputePlacementRouter.place(cell)`` maps a cell descriptor to a compute
tier string.  This is a pure, deterministic, IO-free function: given the same
cell shape it always returns the same tier.

Tiers
-----
``"warehouse"``
    SQL cells — always routed to the data warehouse (DuckDB / Postgres).
``"browser"``
    Small Python cells whose libraries are all Pyodide-compatible and whose
    estimated row count is within the browser cap.
``"remote_kernel"``
    Python cells that need a native wheel, exceed the browser cap, or use
    a non-Pyodide lib — AND a remote runner is configured.
``"local_kernel"``
    Same as ``"remote_kernel"`` conditions, but the remote runner is not
    configured.  Falls back to the local subprocess runner.

Cell descriptor keys
--------------------
``kind``
    ``"sql"`` or ``"python"``.
``est_rows``
    Estimated row count of the result (default 0).
``libs``
    List of Python library names the code imports (default ``[]``).
``needs_native_wheel``
    ``True`` if the code requires a C-extension wheel that Pyodide cannot
    ship (e.g. ``torch``, ``lightgbm``, proprietary drivers).

Pyodide-compatible library set
-------------------------------
The set ``_PYODIDE_OK`` is a documented, conservative allowlist of packages
that are known to work in Pyodide (browser WebAssembly).  This list is NOT
exhaustive — it covers the most common data-science libraries that Pyodide
distributes.  Unknown libraries are treated as non-Pyodide-compatible.

References: https://pyodide.org/en/stable/usage/packages-in-pyodide.html
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Pyodide-compatible library allowlist (documented, conservative subset).
# Pure-Python or Pyodide-ported C-extension packages.
# ---------------------------------------------------------------------------
_PYODIDE_OK: frozenset[str] = frozenset(
    {
        # Core numerics
        "numpy",
        "scipy",
        # Data wrangling
        "pandas",
        "pyarrow",
        # Machine learning (Pyodide ships scikit-learn wheels)
        "sklearn",
        "scikit-learn",
        "scikit_learn",
        # Statistics
        "statsmodels",
        # Visualisation (pure Python or Pyodide-ported)
        "matplotlib",
        "bokeh",
        "altair",
        "vega_datasets",
        # Utilities
        "requests",      # vendored / pure fallback
        "dateutil",
        "python_dateutil",
        "pytz",
        "six",
        "attrs",
        "pydantic",
        "typing_extensions",
        "packaging",
        "pyparsing",
        "cycler",
        "kiwisolver",
        "Pillow",
        "pillow",
        "PIL",
        # Arrow / Parquet
        "parquet",
        "pyarrow",
        # JSON / data formats
        "simplejson",
        "orjson",
        "msgpack",
        # Math
        "sympy",
        "mpmath",
        # Other Pyodide-bundled packages
        "cryptography",
        "cffi",
        "regex",
        "lxml",
        "html5lib",
        "beautifulsoup4",
        "bs4",
        "soupsieve",
        "networkx",
        "imageio",
        "scikit_image",
        "skimage",
        "tqdm",
        "joblib",
        "threadpoolctl",
        "click",
        "rich",
        "tabulate",
        "openpyxl",
        "xlrd",
        "xlwt",
        "cachetools",
    }
)

# Default row cap below which results are safe to materialise in a browser.
_DEFAULT_BROWSER_ROW_CAP: int = 1_000_000


class ComputePlacementRouter:
    """Determine which compute tier should run a given cell.

    Parameters
    ----------
    remote_configured:
        ``True`` when a remote kernel (Modal/E2B) is available.  Affects
        whether Python cells that cannot run in the browser are routed to
        ``"remote_kernel"`` or ``"local_kernel"``.
    browser_row_cap:
        Maximum estimated row count that can be safely materialised in a
        browser.  Cells above this cap are routed off-browser.
    pyodide_ok:
        Set of library names that are known to work in Pyodide.  Defaults to
        the module-level :data:`_PYODIDE_OK` frozenset.
    """

    def __init__(
        self,
        remote_configured: bool = False,
        browser_row_cap: int = _DEFAULT_BROWSER_ROW_CAP,
        pyodide_ok: frozenset[str] | None = None,
    ) -> None:
        self.remote_configured = remote_configured
        self.browser_row_cap = browser_row_cap
        self.pyodide_ok: frozenset[str] = (
            pyodide_ok if pyodide_ok is not None else _PYODIDE_OK
        )

    def place(self, cell: dict[str, Any]) -> str:
        """Return the tier string for *cell*.

        Parameters
        ----------
        cell:
            Dict with keys:
            - ``kind`` (str): ``"sql"`` or ``"python"``.
            - ``est_rows`` (int, default 0): estimated result row count.
            - ``libs`` (list[str], default []): imported library names.
            - ``needs_native_wheel`` (bool, default False): requires a C wheel.

        Returns
        -------
        str
            One of ``"warehouse"``, ``"browser"``, ``"remote_kernel"``,
            ``"local_kernel"``.
        """
        kind: str = cell.get("kind", "python")
        est_rows: int = int(cell.get("est_rows", 0))
        libs: list[str] = list(cell.get("libs", []))
        needs_native_wheel: bool = bool(cell.get("needs_native_wheel", False))

        # ── SQL always goes to the warehouse ─────────────────────────────────
        if kind == "sql":
            return "warehouse"

        # ── Python path ───────────────────────────────────────────────────────
        # Check browser eligibility:
        # 1. No native wheel requirement.
        # 2. Row count within the browser cap.
        # 3. All imported libs are in the Pyodide allowlist.
        all_libs_pyodide_ok = all(lib in self.pyodide_ok for lib in libs)

        can_run_in_browser = (
            not needs_native_wheel
            and est_rows <= self.browser_row_cap
            and all_libs_pyodide_ok
        )

        if can_run_in_browser:
            return "browser"

        # Off-browser: prefer remote if configured, else local.
        if self.remote_configured:
            return "remote_kernel"
        return "local_kernel"
