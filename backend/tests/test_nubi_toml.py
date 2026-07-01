"""Tests for ``app.config.nubi_toml`` — the ``nubi.toml`` optimizer config parser.

Strategy
--------
Pure parsing / dataclass module — no network or cloud I/O.  Disk I/O is
exercised through ``tmp_path`` fixtures only (writing small ``nubi.toml``
files), never touching the real filesystem outside pytest's sandbox.

Coverage
--------
1. ``load_project_config(None)`` -> all-defaults ``ProjectConfig``.
2. ``load_project_config(<missing path>)`` -> defaults, records source_path.
3. ``load_project_config(<dir with no nubi.toml>)`` -> defaults.
4. ``load_project_config(<dir containing nubi.toml>)`` -> resolves ``dir/nubi.toml``.
5. Full valid file: project-wide ``auto_optimize``, per-table overrides
   (partition_by, cluster_by list, materialize, freshness, auto_optimize),
   ``[secrets]`` name-only refs.
6. cluster_by as a single scalar string (not a list) -> 1-tuple.
7. Malformed TOML -> raises ``tomllib.TOMLDecodeError`` (loud failure, not
   silently treated as absent).
8. ``parse_project_config({})`` -> defaults (no [optimize]/[secrets] blocks).
9. Non-dict ``[optimize]`` / ``[secrets]`` blocks are ignored gracefully.
10. Table block without its own ``auto_optimize`` inherits the project default.
11. ``OptimizeTableConfig.auto_optimize_enabled`` reflects on/off.
12. ``ProjectConfig.for_table`` — existing block vs synthesized default that
    inherits the project toggle.
13. ``ProjectConfig.secret_ref`` — hit / miss; non-string secret values ignored.
14. ``to_dict()`` on both dataclasses.
15. ``_normalise_toggle`` / ``_normalise_materialize`` / ``_as_str_tuple``
    edge cases (bools, synonyms, unknown values, None, list/scalar coercion).
16. ``partition_by`` empty-string / missing normalises to ``None``.
17. ``freshness`` missing/empty falls back to the module default.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from app.config.nubi_toml import (
    DEFAULT_AUTO_OPTIMIZE,
    DEFAULT_FRESHNESS,
    DEFAULT_MATERIALIZE,
    OptimizeTableConfig,
    ProjectConfig,
    _as_str_tuple,
    _normalise_materialize,
    _normalise_toggle,
    load_project_config,
    parse_project_config,
)


# ---------------------------------------------------------------------------
# load_project_config — absent / missing file
# ---------------------------------------------------------------------------


def test_load_project_config_none_returns_defaults():
    cfg = load_project_config(None)
    assert cfg == ProjectConfig()
    assert cfg.auto_optimize == DEFAULT_AUTO_OPTIMIZE
    assert cfg.tables == {}
    assert cfg.secret_refs == {}
    assert cfg.source_path is None


def test_load_project_config_missing_file_returns_defaults(tmp_path: Path):
    missing = tmp_path / "does-not-exist" / "nubi.toml"
    cfg = load_project_config(missing)
    assert cfg.auto_optimize == DEFAULT_AUTO_OPTIMIZE
    assert cfg.tables == {}
    assert cfg.source_path == str(missing)


def test_load_project_config_dir_without_file_returns_defaults(tmp_path: Path):
    cfg = load_project_config(tmp_path)
    assert cfg.auto_optimize == DEFAULT_AUTO_OPTIMIZE
    assert cfg.source_path == str(tmp_path / "nubi.toml")


def test_load_project_config_dir_with_file_resolves_nubi_toml(tmp_path: Path):
    (tmp_path / "nubi.toml").write_text('[optimize]\nauto_optimize = "off"\n')
    cfg = load_project_config(tmp_path)
    assert cfg.auto_optimize == "off"
    assert cfg.source_path == str((tmp_path / "nubi.toml").resolve())


def test_load_project_config_accepts_str_path(tmp_path: Path):
    (tmp_path / "nubi.toml").write_text('[optimize]\nauto_optimize = "off"\n')
    cfg = load_project_config(str(tmp_path / "nubi.toml"))
    assert cfg.auto_optimize == "off"


# ---------------------------------------------------------------------------
# load_project_config — full valid file
# ---------------------------------------------------------------------------


_FULL_TOML = """
[optimize]
auto_optimize = "on"

[optimize.events]
partition_by = "ts"
cluster_by = ["org_id", "country"]
materialize = "on"
freshness = "10m"
auto_optimize = "off"

[optimize.logs]
cluster_by = "region"

[secrets]
warehouse_dsn = "BIGQUERY_DSN_ENV"
api_key = ""
bad_ref = 123
"""


def test_load_project_config_full_file(tmp_path: Path):
    p = tmp_path / "nubi.toml"
    p.write_text(_FULL_TOML)
    cfg = load_project_config(p)

    assert cfg.auto_optimize == "on"
    assert set(cfg.tables) == {"events", "logs"}

    events = cfg.tables["events"]
    assert events.table == "events"
    assert events.partition_by == "ts"
    assert events.cluster_by == ("org_id", "country")
    assert events.materialize == "on"
    assert events.freshness == "10m"
    assert events.auto_optimize == "off"
    assert events.auto_optimize_enabled is False

    logs = cfg.tables["logs"]
    # cluster_by given as a bare scalar string coerces to a 1-tuple.
    assert logs.cluster_by == ("region",)
    # No explicit auto_optimize -> inherits the project-wide default ("on").
    assert logs.auto_optimize == "on"
    assert logs.auto_optimize_enabled is True
    # partition_by omitted -> None; defaults for materialize/freshness apply.
    assert logs.partition_by is None
    assert logs.materialize == DEFAULT_MATERIALIZE
    assert logs.freshness == DEFAULT_FRESHNESS

    # Secrets: only non-empty string refs are kept; empty string and non-str
    # values (int) are dropped rather than surfaced as bogus refs.
    assert cfg.secret_refs == {"warehouse_dsn": "BIGQUERY_DSN_ENV"}
    assert cfg.secret_ref("warehouse_dsn") == "BIGQUERY_DSN_ENV"
    assert cfg.secret_ref("api_key") is None
    assert cfg.secret_ref("bad_ref") is None
    assert cfg.secret_ref("nonexistent") is None


def test_load_project_config_malformed_toml_raises(tmp_path: Path):
    p = tmp_path / "nubi.toml"
    p.write_text("this is not [valid toml")
    with pytest.raises(tomllib.TOMLDecodeError):
        load_project_config(p)


# ---------------------------------------------------------------------------
# parse_project_config — in-memory dict entry point
# ---------------------------------------------------------------------------


def test_parse_project_config_empty_dict_is_defaults():
    cfg = parse_project_config({})
    assert cfg.auto_optimize == DEFAULT_AUTO_OPTIMIZE
    assert cfg.tables == {}
    assert cfg.secret_refs == {}
    assert cfg.source_path is None
    assert cfg.raw == {}


def test_parse_project_config_non_dict_optimize_block_ignored():
    cfg = parse_project_config({"optimize": "not-a-dict", "secrets": ["not", "a", "dict"]})
    assert cfg.auto_optimize == DEFAULT_AUTO_OPTIMIZE
    assert cfg.tables == {}
    assert cfg.secret_refs == {}


def test_parse_project_config_scalar_keys_are_not_treated_as_tables():
    # Only sub-dict values of [optimize] become table blocks; scalar keys
    # (like the project-wide auto_optimize toggle) must be skipped.
    cfg = parse_project_config({"optimize": {"auto_optimize": "off", "some_scalar": 42}})
    assert cfg.auto_optimize == "off"
    assert cfg.tables == {}


def test_parse_project_config_records_source_path():
    cfg = parse_project_config({}, source_path="/tmp/nubi.toml")
    assert cfg.source_path == "/tmp/nubi.toml"


def test_parse_project_config_keeps_raw_passthrough():
    data = {"optimize": {"auto_optimize": "on"}, "future_key": {"x": 1}}
    cfg = parse_project_config(data)
    assert cfg.raw == data


# ---------------------------------------------------------------------------
# ProjectConfig / OptimizeTableConfig behaviour
# ---------------------------------------------------------------------------


def test_for_table_returns_existing_block():
    cfg = parse_project_config({"optimize": {"events": {"partition_by": "ts"}}})
    tc = cfg.for_table("events")
    assert tc.partition_by == "ts"


def test_for_table_synthesizes_default_inheriting_project_toggle():
    cfg = parse_project_config({"optimize": {"auto_optimize": "off"}})
    tc = cfg.for_table("never_declared")
    assert tc.table == "never_declared"
    assert tc.auto_optimize == "off"
    assert tc.auto_optimize_enabled is False
    assert tc.cluster_by == ()
    assert tc.partition_by is None


def test_optimize_table_config_to_dict():
    tc = OptimizeTableConfig(
        table="events",
        partition_by="ts",
        cluster_by=("a", "b"),
        materialize="on",
        freshness="1h",
        auto_optimize="on",
    )
    assert tc.to_dict() == {
        "table": "events",
        "partition_by": "ts",
        "cluster_by": ["a", "b"],
        "materialize": "on",
        "freshness": "1h",
        "auto_optimize": "on",
    }


def test_project_config_to_dict_only_surfaces_secret_names():
    cfg = parse_project_config(
        {
            "optimize": {"auto_optimize": "on", "events": {"partition_by": "ts"}},
            "secrets": {"dsn": "ENV_NAME"},
        },
        source_path="/x/nubi.toml",
    )
    d = cfg.to_dict()
    assert d["auto_optimize"] == "on"
    assert d["tables"]["events"]["partition_by"] == "ts"
    assert d["secret_refs"] == {"dsn": "ENV_NAME"}
    assert d["source_path"] == "/x/nubi.toml"
    # Never a "secret_values" or similar key — only reference NAMES surface.
    assert "secret_values" not in d


# ---------------------------------------------------------------------------
# Normalisation helpers (pure functions) — edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "on"),  # default passed through when value is None
        (True, "on"),
        (False, "off"),
        ("on", "on"),
        ("ON", "on"),
        ("true", "on"),
        ("yes", "on"),
        ("enabled", "on"),
        ("1", "on"),
        ("off", "off"),
        ("false", "off"),
        ("no", "off"),
        ("disabled", "off"),
        ("0", "off"),
        ("garbage", "on"),  # unknown -> falls back to the supplied default
    ],
)
def test_normalise_toggle(value, expected):
    assert _normalise_toggle(value, default="on") == expected


def test_normalise_toggle_unknown_uses_given_default_off():
    assert _normalise_toggle("garbage", default="off") == "off"


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, DEFAULT_MATERIALIZE),
        (True, "on"),
        (False, "off"),
        ("auto", "auto"),
        ("on", "on"),
        ("off", "off"),
        ("true", "on"),
        ("false", "off"),
        ("garbage", DEFAULT_MATERIALIZE),
    ],
)
def test_normalise_materialize(value, expected):
    assert _normalise_materialize(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, ()),
        ("", ()),
        ("region", ("region",)),
        (["a", "b"], ("a", "b")),
        (("a", "b"), ("a", "b")),
        ([], ()),
        (["a", "", "b"], ("a", "b")),  # falsy entries dropped
        (42, ("42",)),
    ],
)
def test_as_str_tuple(value, expected):
    assert _as_str_tuple(value) == expected


def test_partition_by_empty_string_normalises_to_none():
    cfg = parse_project_config({"optimize": {"events": {"partition_by": ""}}})
    assert cfg.tables["events"].partition_by is None


def test_freshness_missing_falls_back_to_default():
    cfg = parse_project_config({"optimize": {"events": {}}})
    assert cfg.tables["events"].freshness == DEFAULT_FRESHNESS
