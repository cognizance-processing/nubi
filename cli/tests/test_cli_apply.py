"""Tests for ``nubi apply`` / ``nubi plan`` (declarative resource-bundle provisioning).

Strategy
--------
- ``typer.testing.CliRunner`` drives the app.
- HTTP is prevented by monkeypatching ``nubi_cli.client.post`` with a fake.
- Real filesystem bundles are created via ``tmp_path``.
- No actual network connections are made in any test.

Coverage
--------
1. Happy-path apply: bundle loads and resources apply (created, updated).
2. Idempotent re-apply: same bundle → ``unchanged`` (no writes).
3. Dry-run / plan: validates and shows plan, makes NO writes.
4. Partial failure: one resource fails → reported, rest succeed; exit code 1.
5. Bundle load errors: missing bundle.yaml, invalid YAML, missing slug.
6. Empty bundle: no resources → no-op exit 0.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_TOKEN = "test-jwt-token"


def _make_response(json_data: Any = None, status_code: int = 200) -> MagicMock:
    """Build a minimal mock httpx.Response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.is_success = 200 <= status_code < 300
    mock.text = json.dumps(json_data) if json_data is not None else ""
    mock.json.return_value = json_data or {}
    return mock


def _bundle_dir(tmp_path: Path, *, org: str = "test-org", version: str = "1") -> Path:
    """Create a minimal bundle directory with bundle.yaml."""
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


def _write_metric(bundle: Path, slug: str, name: str | None = None) -> Path:
    """Write a metric YAML into the bundle's metrics/ directory."""
    md = bundle / "metrics"
    md.mkdir(exist_ok=True)
    metric_data = {
        "slug": slug,
        "name": name or slug.replace("_", " ").title(),
        "measure": {"name": slug, "agg": "sum", "expr": "amount", "type": "additive"},
        "base_table": "orders",
        "description": f"Test metric {slug}",
    }
    path = md / f"{slug}.yaml"
    path.write_text(yaml.dump(metric_data))
    return path


def _write_datastore(bundle: Path, connector_name: str, connector_id: str | None = None) -> None:
    """Write a datastores.yaml with one connector envelope."""
    entry: dict[str, Any] = {
        "kind": "connector",
        "apiVersion": "nubi/v1",
        "metadata": {"name": connector_name},
        "spec": {
            "connector_type": "postgres",
            "host": "db.example.com",
            "database": "prod",
        },
    }
    if connector_id:
        entry["metadata"]["id"] = connector_id
    (bundle / "datastores.yaml").write_text(yaml.dump([entry]))


def _write_dashboard(bundle: Path, dashboard_name: str, dashboard_id: str | None = None) -> None:
    """Write a dashboard JSON into the bundle's dashboards/ directory."""
    dd = bundle / "dashboards"
    dd.mkdir(exist_ok=True)
    env: dict[str, Any] = {
        "kind": "dashboard",
        "apiVersion": "nubi/v1",
        "metadata": {"name": dashboard_name},
        "spec": {"title": dashboard_name, "panels": []},
    }
    if dashboard_id:
        env["metadata"]["id"] = dashboard_id
    safe = dashboard_name.replace(" ", "_").lower()
    (dd / f"{safe}.json").write_text(json.dumps(env))


def _apply_success_response(
    resources: list[dict[str, Any]],
    dry_run: bool = False,
    action: str = "created",
) -> dict[str, Any]:
    """Build a mock /apply response."""
    results = []
    for env in resources:
        kind = env.get("kind", "query")
        meta = env.get("metadata") or {}
        results.append(
            {
                "kind": kind,
                "id": meta.get("id") or f"new-{kind}-id",
                "name": meta.get("name") or "unnamed",
                "action": "create" if dry_run else action,
            }
        )
    summary = {k: 0 for k in ("created", "updated", "unchanged", "failed")}
    if not dry_run:
        for r in results:
            a = r["action"]
            if a in summary:
                summary[a] += 1
    return {"results": results, "summary": summary, "dry_run": dry_run}


# ---------------------------------------------------------------------------
# 1. Happy-path apply
# ---------------------------------------------------------------------------


class TestApplyHappyPath:
    """Bundle applies cleanly; resources are created."""

    def test_apply_creates_metric_and_dashboard(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A bundle with a metric + dashboard issues POST /apply and reports created."""
        bundle = _bundle_dir(tmp_path)
        _write_metric(bundle, "revenue")
        _write_dashboard(bundle, "Main Dashboard")

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)

        # Build the expected envelopes the CLI will send to /apply.
        captured_payloads: list[dict] = []

        def _fake_post(path: str, json: dict | None = None, **_: Any):
            if path == "apply" and json:
                captured_payloads.append(json)
                resources = json.get("resources", [])
                return _make_response(_apply_success_response(resources, action="created"))
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)

        result = runner.invoke(app, ["apply", str(bundle)])

        assert result.exit_code == 0, result.output
        assert len(captured_payloads) == 1
        payload = captured_payloads[0]
        assert payload["dry_run"] is False
        assert payload["org"] == "test-org"
        resources = payload["resources"]
        # One metric (kind=query with metric block) + one dashboard.
        kinds = {r.get("kind") for r in resources}
        assert "query" in kinds    # metric converted to query+metric-block
        assert "dashboard" in kinds
        assert "CREATED" in result.output or "created" in result.output.lower()

    def test_apply_with_datastore(self, tmp_path: Path, monkeypatch) -> None:
        """A bundle with a datastore is sent to /apply and reported as created."""
        bundle = _bundle_dir(tmp_path)
        _write_datastore(bundle, "Production DB")

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)

        def _fake_post(path: str, json: dict | None = None, **_: Any):
            if path == "apply":
                resources = (json or {}).get("resources", [])
                return _make_response(_apply_success_response(resources, action="created"))
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)

        result = runner.invoke(app, ["apply", str(bundle)])

        assert result.exit_code == 0, result.output
        assert "connector" in result.output.lower() or "CREATED" in result.output

    def test_apply_org_override(self, tmp_path: Path, monkeypatch) -> None:
        """--org flag overrides the bundle.yaml metadata.org."""
        bundle = _bundle_dir(tmp_path, org="original-org")
        _write_metric(bundle, "churn")

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        captured: list[dict] = []

        def _fake_post(path: str, json: dict | None = None, **_: Any):
            if path == "apply":
                captured.append(json or {})
                resources = (json or {}).get("resources", [])
                return _make_response(_apply_success_response(resources, action="created"))
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)

        result = runner.invoke(app, ["apply", str(bundle), "--org", "override-org"])

        assert result.exit_code == 0, result.output
        assert captured[0]["org"] == "override-org"


# ---------------------------------------------------------------------------
# 2. Idempotency: second apply is a no-op (unchanged)
# ---------------------------------------------------------------------------


class TestApplyIdempotent:
    """Applying the same bundle twice reports unchanged — true idempotency."""

    def test_second_apply_is_unchanged(self, tmp_path: Path, monkeypatch) -> None:
        """A second POST /apply with unchanged resources → all unchanged."""
        bundle = _bundle_dir(tmp_path)
        _write_metric(bundle, "revenue")
        _write_dashboard(bundle, "Dashboard")

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        call_count = 0

        def _fake_post(path: str, json: dict | None = None, **_: Any):
            nonlocal call_count
            if path == "apply":
                call_count += 1
                resources = (json or {}).get("resources", [])
                if call_count == 1:
                    # First apply: created.
                    return _make_response(_apply_success_response(resources, action="created"))
                # Second apply: unchanged.
                results = [
                    {
                        "kind": r.get("kind", "query"),
                        "id": (r.get("metadata") or {}).get("id") or "some-id",
                        "name": (r.get("metadata") or {}).get("name") or "unnamed",
                        "action": "unchanged",
                    }
                    for r in resources
                ]
                summary = {"created": 0, "updated": 0, "unchanged": len(results), "failed": 0}
                return _make_response({"results": results, "summary": summary, "dry_run": False})
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)

        # First apply.
        r1 = runner.invoke(app, ["apply", str(bundle)])
        assert r1.exit_code == 0, r1.output

        # Second apply — idempotent: no failures.
        r2 = runner.invoke(app, ["apply", str(bundle)])
        assert r2.exit_code == 0, r2.output
        assert "unchanged" in r2.output.lower() or "UNCHANGED" in r2.output

    def test_apply_called_twice_makes_two_api_calls(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Each ``nubi apply`` invocation issues exactly one POST /apply call."""
        bundle = _bundle_dir(tmp_path)
        _write_metric(bundle, "revenue")

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        post_calls: list[str] = []

        def _fake_post(path: str, json: dict | None = None, **_: Any):
            post_calls.append(path)
            resources = (json or {}).get("resources", [])
            return _make_response(_apply_success_response(resources, action="created"))

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)

        runner.invoke(app, ["apply", str(bundle)])
        runner.invoke(app, ["apply", str(bundle)])

        apply_calls = [p for p in post_calls if p == "apply"]
        assert apply_calls == ["apply", "apply"]


# ---------------------------------------------------------------------------
# 3. Dry-run / plan
# ---------------------------------------------------------------------------


class TestApplyDryRun:
    """--dry-run and ``nubi plan`` must NOT write; they show the plan."""

    def test_dry_run_does_not_write(self, tmp_path: Path, monkeypatch) -> None:
        """--dry-run: payload has dry_run=True; response shows planned actions."""
        bundle = _bundle_dir(tmp_path)
        _write_metric(bundle, "revenue")
        _write_dashboard(bundle, "Revenue Dashboard")

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        payloads: list[dict] = []

        def _fake_post(path: str, json: dict | None = None, **_: Any):
            if path == "apply":
                payloads.append(json or {})
                resources = (json or {}).get("resources", [])
                return _make_response(
                    _apply_success_response(resources, dry_run=True)
                )
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)

        result = runner.invoke(app, ["apply", str(bundle), "--dry-run"])

        assert result.exit_code == 0, result.output
        assert payloads[0]["dry_run"] is True
        # Plan output is shown.
        assert "plan" in result.output.lower() or "dry" in result.output.lower()

    def test_plan_command_sends_dry_run(self, tmp_path: Path, monkeypatch) -> None:
        """``nubi plan`` always sends dry_run=True to /apply."""
        bundle = _bundle_dir(tmp_path)
        _write_metric(bundle, "churn")

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        payloads: list[dict] = []

        def _fake_post(path: str, json: dict | None = None, **_: Any):
            if path == "apply":
                payloads.append(json or {})
                resources = (json or {}).get("resources", [])
                return _make_response(_apply_success_response(resources, dry_run=True))
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)

        result = runner.invoke(app, ["plan", str(bundle)])

        assert result.exit_code == 0, result.output
        assert payloads, "No POST /apply was issued by nubi plan"
        assert payloads[0].get("dry_run") is True

    def test_dry_run_makes_no_put_or_delete(self, tmp_path: Path, monkeypatch) -> None:
        """In dry-run mode only POST /apply (with dry_run=True) is ever called."""
        bundle = _bundle_dir(tmp_path)
        _write_metric(bundle, "revenue")

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)

        def _fake_put(*_a: Any, **_k: Any) -> None:
            raise AssertionError("PUT must not be called in dry-run")

        def _fake_delete(*_a: Any, **_k: Any) -> None:
            raise AssertionError("DELETE must not be called in dry-run")

        def _fake_post(path: str, json: dict | None = None, **_: Any):
            if path == "apply":
                resources = (json or {}).get("resources", [])
                return _make_response(_apply_success_response(resources, dry_run=True))
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)
        monkeypatch.setattr("nubi_cli.client.put", _fake_put)
        monkeypatch.setattr("nubi_cli.client.delete", _fake_delete)

        result = runner.invoke(app, ["apply", str(bundle), "--dry-run"])
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# 4. Partial failure
# ---------------------------------------------------------------------------


class TestApplyPartialFailure:
    """One resource fails → CLI exits 1; others succeed; error is reported."""

    def test_partial_failure_exits_1(self, tmp_path: Path, monkeypatch) -> None:
        """When any resource in the response has action=failed exit code is 1."""
        bundle = _bundle_dir(tmp_path)
        _write_metric(bundle, "revenue")
        _write_dashboard(bundle, "Broken Dashboard")

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)

        def _fake_post(path: str, json: dict | None = None, **_: Any):
            if path == "apply":
                resources = (json or {}).get("resources", [])
                results = []
                for i, r in enumerate(resources):
                    kind = r.get("kind", "query")
                    meta = r.get("metadata") or {}
                    if i == 0:
                        results.append(
                            {
                                "kind": kind,
                                "id": "new-id",
                                "name": meta.get("name") or "metric",
                                "action": "created",
                            }
                        )
                    else:
                        results.append(
                            {
                                "kind": kind,
                                "id": None,
                                "name": meta.get("name") or "dashboard",
                                "action": "failed",
                                "error": "Spec validation failed: panels is required.",
                            }
                        )
                summary = {"created": 1, "updated": 0, "unchanged": 0, "failed": 1}
                return _make_response({"results": results, "summary": summary, "dry_run": False})
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)

        result = runner.invoke(app, ["apply", str(bundle)])

        assert result.exit_code == 1, result.output
        # The failure is reported.
        combined = result.output + (result.stderr or "")
        assert "FAILED" in combined or "failed" in combined.lower()
        # The success is also shown.
        assert "CREATED" in result.output or "created" in result.output.lower()

    def test_partial_failure_continues_applying_remaining(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Even when a resource fails, the rest of the bundle is applied."""
        bundle = _bundle_dir(tmp_path)
        _write_metric(bundle, "revenue")
        _write_metric(bundle, "churn")

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)

        def _fake_post(path: str, json: dict | None = None, **_: Any):
            if path == "apply":
                resources = (json or {}).get("resources", [])
                results = []
                for i, r in enumerate(resources):
                    meta = r.get("metadata") or {}
                    results.append(
                        {
                            "kind": r.get("kind", "query"),
                            "id": f"id-{i}",
                            "name": meta.get("name") or f"resource-{i}",
                            "action": "failed" if i == 0 else "created",
                            **({"error": "DB error"} if i == 0 else {}),
                        }
                    )
                summary = {"created": 1, "updated": 0, "unchanged": 0, "failed": 1}
                return _make_response({"results": results, "summary": summary, "dry_run": False})
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)

        result = runner.invoke(app, ["apply", str(bundle)])

        # Exit 1 (partial failure).
        assert result.exit_code == 1, result.output
        # Both resources appear in the output.
        assert "CREATED" in result.output or "created" in result.output.lower()
        assert "FAILED" in result.output or "failed" in result.output.lower()


# ---------------------------------------------------------------------------
# 5. Bundle load errors
# ---------------------------------------------------------------------------


class TestBundleLoadErrors:
    """Invalid or missing bundles are rejected before any API call."""

    def _no_http(self, monkeypatch) -> None:
        """Patch HTTP so any call raises."""
        monkeypatch.setattr(
            "nubi_cli.client.post",
            MagicMock(side_effect=AssertionError("POST must not be called")),
        )

    def test_missing_bundle_yaml(self, tmp_path: Path, monkeypatch) -> None:
        """A directory without bundle.yaml → exit 1 before any API call."""
        d = tmp_path / "no_bundle"
        d.mkdir()
        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        self._no_http(monkeypatch)

        result = runner.invoke(app, ["apply", str(d)])

        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        assert "bundle.yaml" in combined

    def test_bundle_yaml_missing_org(self, tmp_path: Path, monkeypatch) -> None:
        """bundle.yaml without metadata.org → exit 1."""
        d = tmp_path / "b"
        d.mkdir()
        (d / "bundle.yaml").write_text(
            yaml.dump(
                {
                    "apiVersion": "nubi/v1",
                    "kind": "bundle",
                    "metadata": {"version": "1"},  # no org
                }
            )
        )
        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        self._no_http(monkeypatch)

        result = runner.invoke(app, ["apply", str(d)])

        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        assert "org" in combined.lower()

    def test_metric_missing_slug(self, tmp_path: Path, monkeypatch) -> None:
        """A metric YAML without a slug is reported as a load error."""
        bundle = _bundle_dir(tmp_path)
        md = bundle / "metrics"
        md.mkdir()
        (md / "bad.yaml").write_text(
            yaml.dump(
                {
                    "name": "No Slug Metric",
                    "measure": {"name": "m", "agg": "sum", "expr": "x", "type": "additive"},
                    "base_table": "orders",
                }
            )
        )

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)

        # The only resource has a load error — the CLI should exit 1 without
        # calling the API (nothing left to apply).
        api_called = []

        def _fake_post(path: str, **_: Any):
            api_called.append(path)
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)

        result = runner.invoke(app, ["apply", str(bundle)])

        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        assert "slug" in combined.lower()

    def test_metric_missing_base(self, tmp_path: Path, monkeypatch) -> None:
        """A metric without base_table or base_sql is a load error."""
        bundle = _bundle_dir(tmp_path)
        md = bundle / "metrics"
        md.mkdir()
        (md / "bad.yaml").write_text(
            yaml.dump(
                {
                    "slug": "bad_metric",
                    "name": "Bad",
                    "measure": {"name": "m", "agg": "sum", "expr": "x", "type": "additive"},
                    # neither base_table nor base_sql
                }
            )
        )

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        api_called: list[str] = []

        def _fake_post(path: str, **_: Any):
            api_called.append(path)
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)

        result = runner.invoke(app, ["apply", str(bundle)])

        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        assert "base_table" in combined or "base_sql" in combined


# ---------------------------------------------------------------------------
# 6. Empty bundle
# ---------------------------------------------------------------------------


class TestEmptyBundle:
    """A bundle with no resources is a no-op."""

    def test_empty_bundle_no_api_call(self, tmp_path: Path, monkeypatch) -> None:
        """A valid bundle with no resource files → exit 0, no API call."""
        bundle = _bundle_dir(tmp_path)

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        monkeypatch.setattr(
            "nubi_cli.client.post",
            MagicMock(side_effect=AssertionError("POST must not be called for empty bundle")),
        )

        result = runner.invoke(app, ["apply", str(bundle)])

        assert result.exit_code == 0, result.output
        assert "empty" in result.output.lower()

    def test_plan_empty_bundle(self, tmp_path: Path, monkeypatch) -> None:
        """nubi plan on an empty bundle → exit 0, no API call."""
        bundle = _bundle_dir(tmp_path)

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        monkeypatch.setattr(
            "nubi_cli.client.post",
            MagicMock(side_effect=AssertionError("POST must not be called for empty plan")),
        )

        result = runner.invoke(app, ["plan", str(bundle)])

        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# 7. Metric → envelope conversion
# ---------------------------------------------------------------------------


class TestMetricEnvelopeConversion:
    """Verify the metric YAML → portability envelope conversion in bundle.py."""

    def test_metric_slug_becomes_spec_metric_slug(self, tmp_path: Path, monkeypatch) -> None:
        """The metric slug is included in spec.metric.slug in the envelope."""
        bundle = _bundle_dir(tmp_path)
        _write_metric(bundle, "monthly_revenue")

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        captured: list[dict] = []

        def _fake_post(path: str, json: dict | None = None, **_: Any):
            if path == "apply":
                captured.append(json or {})
                resources = (json or {}).get("resources", [])
                return _make_response(_apply_success_response(resources, action="created"))
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)
        runner.invoke(app, ["apply", str(bundle)])

        assert captured
        resources = captured[0].get("resources", [])
        # Find the metric envelope (kind=query).
        metrics = [r for r in resources if r.get("kind") == "query"]
        assert metrics, f"No query envelope found. resources={resources}"
        spec = metrics[0].get("spec") or {}
        metric_block = spec.get("metric") or {}
        assert metric_block.get("slug") == "monthly_revenue"

    def test_metric_base_table_produces_sql(self, tmp_path: Path, monkeypatch) -> None:
        """base_table is converted to a SELECT * SQL in the spec."""
        bundle = _bundle_dir(tmp_path)
        _write_metric(bundle, "revenue")  # uses base_table=orders

        monkeypatch.setenv("NUBI_TOKEN", _DEFAULT_TOKEN)
        captured: list[dict] = []

        def _fake_post(path: str, json: dict | None = None, **_: Any):
            if path == "apply":
                captured.append(json or {})
                resources = (json or {}).get("resources", [])
                return _make_response(_apply_success_response(resources, action="created"))
            return _make_response({})

        monkeypatch.setattr("nubi_cli.client.post", _fake_post)
        runner.invoke(app, ["apply", str(bundle)])

        resources = (captured[0] if captured else {}).get("resources", [])
        metrics = [r for r in resources if r.get("kind") == "query"]
        assert metrics
        spec = metrics[0].get("spec") or {}
        assert "sql" in spec
        assert "orders" in spec["sql"]
