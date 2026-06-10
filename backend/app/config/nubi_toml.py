"""``nubi.toml`` — per-project managed-lakehouse optimizer configuration.

This is the declarative override surface for the auto partition/cluster manager
(MANAGED_LAKEHOUSE.md §4).  The optimizer is **automatic by default** (posture
C+A): it auto-detects a time partition key and high-selectivity cluster keys and
auto-builds rollups whose estimated savings clear a threshold.  ``nubi.toml`` is
where a project *opts out of* or *overrides* those defaults per table — never
where it has to opt in.

Design invariants
-----------------
* **Backward-compatible.** An absent ``nubi.toml`` (or an empty ``[optimize]``
  table) yields a :class:`ProjectConfig` of all-defaults — auto-optimize ON,
  no forced layout.  Nothing in core depends on the file existing.
* **Secrets by NAME only.** ``[secrets]`` maps a logical name → the *name* of an
  environment variable / secret-store key that holds the value.  The value is
  NEVER read or stored here (open-core invariant: "secrets never in synced
  files").  Resolution to an actual value happens elsewhere, at read time.
* **Typed surface.** Parsing produces frozen dataclasses, not raw dicts, so
  callers get a stable, documented shape regardless of TOML quirks.

Shape
-----
::

    [optimize]
    auto_optimize = "on"          # global default for tables without an override

    [optimize.events]             # per-table override (table name = "events")
    partition_by   = "ts"         # time column → day/month partitioning
    cluster_by     = ["org_id", "country"]
    materialize    = "auto"       # "auto" | "on" | "off"
    freshness      = "5m"         # serve-stale window for lambda freshness
    auto_optimize  = "off"        # disable auto-build for this table only

    [secrets]
    warehouse_dsn = "BIGQUERY_DSN_ENV"   # NAME of the env var — not its value

``load_project_config(path)`` is the single entry point.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Typed config surface
# ---------------------------------------------------------------------------

# ``materialize`` / ``auto_optimize`` accept a small closed vocabulary.  We keep
# them as plain strings (validated/normalised on parse) rather than enums so the
# config stays trivially JSON-serialisable for the UI override surface.
MaterializeMode = Literal["auto", "on", "off"]
ToggleMode = Literal["on", "off"]

_MATERIALIZE_VALUES: tuple[str, ...] = ("auto", "on", "off")
_TOGGLE_VALUES: tuple[str, ...] = ("on", "off")

# Defaults (posture C+A: automatic unless overridden).
DEFAULT_AUTO_OPTIMIZE: ToggleMode = "on"
DEFAULT_MATERIALIZE: MaterializeMode = "auto"
DEFAULT_FRESHNESS: str = "5m"


@dataclass(frozen=True)
class OptimizeTableConfig:
    """Per-table optimizer overrides (one ``[optimize.<table>]`` block).

    Every field is optional in the file; an omitted field means "use the
    project/auto default", represented here as ``None`` for the layout hints and
    the resolved global default for the modes.

    Attributes
    ----------
    table:
        The logical table name this block targets.
    partition_by:
        Name of the time column to partition by (``None`` → auto-detect).  A
        single column today; partitioning granularity (day/month) is chosen by
        the optimizer.
    cluster_by:
        Ordered cluster-key columns (high-selectivity filters).  Empty → the
        optimizer auto-detects from the query log.
    materialize:
        ``"auto"`` (build rollups when savings clear the threshold), ``"on"``
        (always materialize the mined shapes for this table), or ``"off"``
        (never).
    freshness:
        Serve-stale window for lambda freshness (e.g. ``"5m"``, ``"1h"``).  Kept
        as the raw string; duration parsing is the refresh scheduler's job.
    auto_optimize:
        ``"on"``/``"off"`` master switch for *this table*.  ``"off"`` pins the
        physical layout to whatever is declared here and disables auto-build.
    """

    table: str
    partition_by: str | None = None
    cluster_by: tuple[str, ...] = ()
    materialize: MaterializeMode = DEFAULT_MATERIALIZE
    freshness: str = DEFAULT_FRESHNESS
    auto_optimize: ToggleMode = DEFAULT_AUTO_OPTIMIZE

    @property
    def auto_optimize_enabled(self) -> bool:
        """``True`` when the optimizer may auto-build/maintain this table."""
        return self.auto_optimize == "on"

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "partition_by": self.partition_by,
            "cluster_by": list(self.cluster_by),
            "materialize": self.materialize,
            "freshness": self.freshness,
            "auto_optimize": self.auto_optimize,
        }


@dataclass(frozen=True)
class ProjectConfig:
    """Parsed ``nubi.toml`` (or all-defaults when absent).

    Attributes
    ----------
    auto_optimize:
        Project-wide default toggle, applied to tables without their own
        ``auto_optimize`` override.
    tables:
        ``{table_name: OptimizeTableConfig}`` for each ``[optimize.<table>]``
        block.
    secret_refs:
        ``{logical_name: env_var_or_secret_key_NAME}``.  Values are NEVER stored
        here — only the *name* of where to look the secret up.
    source_path:
        Absolute path the config was loaded from, or ``None`` when defaulted
        (no file present).
    raw:
        The raw parsed TOML dict (minus secret values, which are never values
        to begin with), kept for forward-compatible passthrough of keys this
        version does not yet model.
    """

    auto_optimize: ToggleMode = DEFAULT_AUTO_OPTIMIZE
    tables: dict[str, OptimizeTableConfig] = field(default_factory=dict)
    secret_refs: dict[str, str] = field(default_factory=dict)
    source_path: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    # ── Lookups ─────────────────────────────────────────────────────────────

    def for_table(self, table: str) -> OptimizeTableConfig:
        """Return the override config for *table*, or a defaulted one.

        When the table has no explicit block, a default
        :class:`OptimizeTableConfig` is synthesised that inherits the
        project-wide ``auto_optimize`` toggle — so callers can always ask
        ``config.for_table(t).auto_optimize_enabled`` without a None check.
        """
        existing = self.tables.get(table)
        if existing is not None:
            return existing
        return OptimizeTableConfig(table=table, auto_optimize=self.auto_optimize)

    def secret_ref(self, name: str) -> str | None:
        """Return the env/secret-store *name* for a logical secret, or ``None``.

        This deliberately returns the reference NAME, not a value — resolving it
        to an actual secret is the caller's job (open-core: secrets never live
        in synced config).
        """
        return self.secret_refs.get(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "auto_optimize": self.auto_optimize,
            "tables": {t: c.to_dict() for t, c in self.tables.items()},
            # Only the reference NAMES are surfaced — never values.
            "secret_refs": dict(self.secret_refs),
            "source_path": self.source_path,
        }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _normalise_toggle(value: Any, *, default: ToggleMode) -> ToggleMode:
    """Coerce a TOML value to ``"on"``/``"off"`` (booleans accepted too)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return "on" if value else "off"
    s = str(value).strip().lower()
    if s in ("on", "true", "yes", "enabled", "1"):
        return "on"
    if s in ("off", "false", "no", "disabled", "0"):
        return "off"
    return default


def _normalise_materialize(value: Any) -> MaterializeMode:
    """Coerce a TOML value to ``"auto"``/``"on"``/``"off"``."""
    if value is None:
        return DEFAULT_MATERIALIZE
    if isinstance(value, bool):
        return "on" if value else "off"
    s = str(value).strip().lower()
    if s in _MATERIALIZE_VALUES:
        return s  # type: ignore[return-value]
    # Treat truthy/falsy synonyms like the toggle, else fall back to auto.
    if s in ("true", "yes", "enabled", "1"):
        return "on"
    if s in ("false", "no", "disabled", "0"):
        return "off"
    return DEFAULT_MATERIALIZE


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    """Coerce a scalar-or-list TOML value into a tuple of column-name strings."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if str(v))
    return (str(value),)


def _parse_table_block(
    name: str,
    block: dict[str, Any],
    *,
    project_auto: ToggleMode,
) -> OptimizeTableConfig:
    """Build an :class:`OptimizeTableConfig` from one ``[optimize.<name>]`` block."""
    return OptimizeTableConfig(
        table=name,
        partition_by=(
            str(block["partition_by"])
            if block.get("partition_by") not in (None, "")
            else None
        ),
        cluster_by=_as_str_tuple(block.get("cluster_by")),
        materialize=_normalise_materialize(block.get("materialize")),
        freshness=str(block.get("freshness") or DEFAULT_FRESHNESS),
        # A table inherits the project default unless it sets its own toggle.
        auto_optimize=_normalise_toggle(
            block.get("auto_optimize"), default=project_auto
        ),
    )


def parse_project_config(
    data: dict[str, Any], *, source_path: str | None = None
) -> ProjectConfig:
    """Build a :class:`ProjectConfig` from an already-parsed TOML *data* dict.

    Split out from :func:`load_project_config` so callers (tests, the UI write
    path) can construct config from an in-memory mapping without touching disk.
    """
    optimize = data.get("optimize")
    optimize = optimize if isinstance(optimize, dict) else {}

    project_auto = _normalise_toggle(
        optimize.get("auto_optimize"), default=DEFAULT_AUTO_OPTIMIZE
    )

    # Sub-tables of [optimize] that are themselves tables are per-table blocks.
    # Scalar keys (like ``auto_optimize``) are project-wide and skipped here.
    tables: dict[str, OptimizeTableConfig] = {}
    for key, value in optimize.items():
        if isinstance(value, dict):
            tables[key] = _parse_table_block(key, value, project_auto=project_auto)

    # [secrets]: logical-name -> ENV/secret-store NAME.  Values are never read.
    secrets = data.get("secrets")
    secret_refs: dict[str, str] = {}
    if isinstance(secrets, dict):
        for logical, ref in secrets.items():
            # We only ever keep the *reference* (a name/string).  Anything that
            # is not a plain string is ignored rather than treated as a value.
            if isinstance(ref, str) and ref:
                secret_refs[str(logical)] = ref

    return ProjectConfig(
        auto_optimize=project_auto,
        tables=tables,
        secret_refs=secret_refs,
        source_path=source_path,
        raw=dict(data),
    )


def load_project_config(path: str | Path | None = None) -> ProjectConfig:
    """Load and parse a ``nubi.toml`` into a :class:`ProjectConfig`.

    Parameters
    ----------
    path:
        Path to ``nubi.toml`` **or** to a directory containing one.  When
        ``None`` or when the file does not exist, an **all-defaults**
        :class:`ProjectConfig` is returned (backward-compatible: no file ⇒
        auto-optimize on, no forced layout).

    Returns
    -------
    ProjectConfig
        Always a usable config; never raises on a missing file.  A malformed
        TOML file *does* raise ``tomllib.TOMLDecodeError`` so config typos are
        surfaced loudly rather than silently treated as "no config".
    """
    if path is None:
        return ProjectConfig()

    p = Path(path)
    if p.is_dir():
        p = p / "nubi.toml"

    if not p.is_file():
        # Absent file → defaults (record the path we looked for, for diagnostics).
        return replace(ProjectConfig(), source_path=str(p))

    with p.open("rb") as fh:
        data = tomllib.load(fh)

    return parse_project_config(data, source_path=str(p.resolve()))
