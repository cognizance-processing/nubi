"""Tests for ``nubi apply`` with ``watches/*.yaml`` files in the bundle.

Strategy
--------
- ``typer.testing.CliRunner`` drives the CLI.
- HTTP is mocked via monkeypatching ``nubi_cli.client.post``.
- Real filesystem bundles are created via ``tmp_path``.
- Validates that watch YAML files are parsed + included in the POST /apply payload.

Coverage
--------
1. Bundle with watches/*.yaml → watch envelopes included in payload (kind=watch).
2. Watch envelope schema: name, metric_id, config, labels all forwarded.
3. Watch missing name → load error, CLI exit 1.
4. Watch missing metric_id → load error, CLI exit 1.
5. Watch missing breach rule → load error, CLI exit 1.
6. Watches co-exist with metrics and dashboards in one bundle.
7. Dry-run with watch envelopes → dry_run=True in payload.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from typer.testing import CliRunner

from nubi_cli.main import app

runner = CliRunner()

_DEFAULT_TOKEN = "test-jwt-token"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(json_data: Any = None, status_code: int = 200) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    mock.is_success = 200 <= status_code < 300
    mock.text = json.dumps(json_data) if json_data is not None else ""
    mock.json.return_value = json_data or {}
    return mock


def _bundle_dir(tmp_path: Path, *, org: str = "test-org", version: str = "1") -> Path:
    d = tmp_path / "bundle"
    d.mkdir()
    (d / "bundle.yaml").write_text(
        yaml.dump(
            {
                "apiVersion": "nubi/v1",
                "kind": "bundle",
                "metadata": {"org": org, "version": version},
            }
        )
    )
    return d


def _write_watch(
    bundle: Path,
    name: str,
    *,
    metric_id: str = "demo_revenue",
    threshold: dict | None = None,
    config: dict | None = None,
    labels: dict | None = None,
    filename: str | None = None,
) -> Path:
    """Write a watch YAML into the bundle's watches/ directory."""
    wd = bundle / "watches"
    wd.mkdir(exist_ok=True)
    if threshold is None:
        threshold = {"op": ">", "value": 10}
    data: dict[str, Any] = {
        "name": name,
        "metric_id": metric_id,
        "threshold": threshold,
    }
    if config:
        data["config"] = config
    if labels:
        data["labels"] = labels
    fname = filename or (name.replace(" ", "_").lower() + ".yaml")
    path = wd / fname
    path.write_text(yaml.dump(data))
    return path


def _success_response(resources: list[dict], action: str = "created") -> dict:
    results = [
        {
            "kind": r.get("kind", "watch"),
            "id": (r.get("metadata") or {}).get("id") or f"id-{i}",
            "name": (r.get("metadata") or {}).get("name") or f"resource-{i}",
            "action": action,
        }
        for i, r in enumerate(resources)
    ]
    summary = {k: 0 for k in ("created", "updated", "unchanged", "failed")}
    for r in results:
        a = r["action"]
        if a in summary:
            summary[a] += 1
    return {"results": results, "summary": summary, "dry_run": False}


# ---------------------------------------------------------------------------
# 1. watches/*.yaml → watch envelopes included in payload
# ---------------------------------------------------------------------------


class TestWatchEnvelopeInPayload:
    def test_apply_watch_yaml_includes_watch_envelope(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A watches/*.yaml file produces a kind='watch' envelope in the payload."""
        bundle = _bundle_dir(tmp_path)
        _write_watch(bundle, "Revenue Alert")

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        captured: list[dict] = []

        def _fake_post(path: str, json: dict | None = None, **_: Any):
            if path == "apply":
                captured.append(json or {})
                resources = (json or {}).get("resources", [])
                return _make_response(_success_response(resources))
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)

        result = runner.invoke(app, ["apply", str(bundle)])

        assert result.exit_code == 0, result.output
        assert captured, "POST /apply was not called"
        resources = captured[0].get("resources", [])
        watch_envs = [r for r in resources if r.get("kind") == "watch"]
        assert len(watch_envs) == 1, f"Expected 1 watch envelope, got {watch_envs}"
        assert watch_envs[0]["metadata"]["name"] == "Revenue Alert"

    def test_apply_multiple_watch_yamls(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Multiple watches/*.yaml files → multiple watch envelopes."""
        bundle = _bundle_dir(tmp_path)
        _write_watch(bundle, "Watch A")
        _write_watch(bundle, "Watch B")
        _write_watch(bundle, "Watch C")

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        captured: list[dict] = []

        def _fake_post(path: str, json: dict | None = None, **_: Any):
            if path == "apply":
                captured.append(json or {})
                resources = (json or {}).get("resources", [])
                return _make_response(_success_response(resources))
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)
        result = runner.invoke(app, ["apply", str(bundle)])

        assert result.exit_code == 0, result.output
        resources = captured[0].get("resources", [])
        watch_envs = [r for r in resources if r.get("kind") == "watch"]
        assert len(watch_envs) == 3


# ---------------------------------------------------------------------------
# 2. Watch envelope schema
# ---------------------------------------------------------------------------


class TestWatchEnvelopeSchema:
    def test_watch_spec_name_metric_config_labels_forwarded(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Watch spec fields (name, metric_id, config, labels) appear in the envelope."""
        bundle = _bundle_dir(tmp_path)
        _write_watch(
            bundle,
            "Detailed Watch",
            metric_id="my_metric",
            threshold={"op": ">=", "value": 100},
            config={"dimensions": ["region"], "time_grain": "week"},
            labels={"team": "analytics", "env": "prod"},
        )

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        captured: list[dict] = []

        def _fake_post(path: str, json: dict | None = None, **_: Any):
            if path == "apply":
                captured.append(json or {})
                resources = (json or {}).get("resources", [])
                return _make_response(_success_response(resources))
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)
        runner.invoke(app, ["apply", str(bundle)])

        resources = captured[0].get("resources", [])
        watch = next(r for r in resources if r.get("kind") == "watch")
        spec = watch.get("spec") or {}

        assert spec["name"] == "Detailed Watch"
        assert spec["metric_id"] == "my_metric"
        # Top-level threshold is folded into config on the backend side,
        # but the CLI carries it as a top-level key in the spec.
        cfg = spec.get("config") or {}
        # Either top-level threshold in spec OR in config is fine.
        threshold = spec.get("threshold") or cfg.get("threshold")
        assert threshold is not None, f"Threshold missing from spec: {spec}"
        assert threshold.get("value") == 100
        assert spec.get("labels") == {"team": "analytics", "env": "prod"}


# ---------------------------------------------------------------------------
# 3-5. Validation errors → load error, exit 1
# ---------------------------------------------------------------------------


class TestWatchLoadErrors:
    def _no_http(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "nubi_cli.client.post",
            MagicMock(side_effect=AssertionError("POST must not be called")),
        )

    def test_watch_missing_name_load_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A watch YAML without a name → load error, exit 1."""
        bundle = _bundle_dir(tmp_path)
        wd = bundle / "watches"
        wd.mkdir()
        (wd / "bad.yaml").write_text(
            yaml.dump(
                {
                    "metric_id": "demo_revenue",
                    "threshold": {"op": ">", "value": 10},
                }
            )
        )

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        self._no_http(monkeypatch)

        result = runner.invoke(app, ["apply", str(bundle)])

        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        assert "name" in combined.lower()

    def test_watch_missing_metric_id_load_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A watch YAML without metric_id → load error, exit 1."""
        bundle = _bundle_dir(tmp_path)
        wd = bundle / "watches"
        wd.mkdir()
        (wd / "bad.yaml").write_text(
            yaml.dump(
                {
                    "name": "No Metric Watch",
                    "threshold": {"op": ">", "value": 10},
                }
            )
        )

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        self._no_http(monkeypatch)

        result = runner.invoke(app, ["apply", str(bundle)])

        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        assert "metric_id" in combined.lower()

    def test_watch_missing_rule_load_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A watch YAML with no breach rule → load error, exit 1."""
        bundle = _bundle_dir(tmp_path)
        wd = bundle / "watches"
        wd.mkdir()
        (wd / "bad.yaml").write_text(
            yaml.dump(
                {
                    "name": "No Rule Watch",
                    "metric_id": "demo_revenue",
                    # no threshold / comparison / change
                }
            )
        )

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        self._no_http(monkeypatch)

        result = runner.invoke(app, ["apply", str(bundle)])

        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        assert "threshold" in combined.lower() or "rule" in combined.lower()


# ---------------------------------------------------------------------------
# 6. Watches co-exist with metrics and dashboards
# ---------------------------------------------------------------------------


class TestWatchesWithOtherResources:
    def test_watches_and_metrics_in_same_bundle(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A bundle with metrics and watches sends envelopes for both kinds."""
        bundle = _bundle_dir(tmp_path)

        # Add a metric.
        md = bundle / "metrics"
        md.mkdir()
        (md / "revenue.yaml").write_text(
            yaml.dump(
                {
                    "slug": "revenue",
                    "name": "Revenue",
                    "measure": {"name": "revenue", "agg": "sum", "expr": "amount", "type": "additive"},
                    "base_table": "orders",
                }
            )
        )

        # Add a watch.
        _write_watch(bundle, "Revenue Watch")

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        captured: list[dict] = []

        def _fake_post(path: str, json: dict | None = None, **_: Any):
            if path == "apply":
                captured.append(json or {})
                resources = (json or {}).get("resources", [])
                return _make_response(_success_response(resources))
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)

        result = runner.invoke(app, ["apply", str(bundle)])

        assert result.exit_code == 0, result.output
        resources = captured[0].get("resources", [])
        kinds = {r.get("kind") for r in resources}
        assert "query" in kinds, f"Expected metric (query) in kinds: {kinds}"
        assert "watch" in kinds, f"Expected watch in kinds: {kinds}"


# ---------------------------------------------------------------------------
# 7. Dry-run with watches
# ---------------------------------------------------------------------------


class TestWatchDryRun:
    def test_dry_run_includes_watches_with_dry_run_true(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """--dry-run sends dry_run=True and watch envelopes are included."""
        bundle = _bundle_dir(tmp_path)
        _write_watch(bundle, "Dry Run Watch")

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        payloads: list[dict] = []

        def _fake_post(path: str, json: dict | None = None, **_: Any):
            if path == "apply":
                payloads.append(json or {})
                resources = (json or {}).get("resources", [])
                results = [
                    {
                        "kind": r.get("kind", "watch"),
                        "id": None,
                        "name": (r.get("metadata") or {}).get("name"),
                        "action": "create",
                    }
                    for r in resources
                ]
                summary = {k: 0 for k in ("created", "updated", "unchanged", "failed")}
                return _make_response({"results": results, "summary": summary, "dry_run": True})
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)

        result = runner.invoke(app, ["apply", str(bundle), "--dry-run"])

        assert result.exit_code == 0, result.output
        assert payloads, "POST /apply was not called"
        payload = payloads[0]
        assert payload.get("dry_run") is True
        watch_envs = [r for r in payload.get("resources", []) if r.get("kind") == "watch"]
        assert len(watch_envs) == 1
