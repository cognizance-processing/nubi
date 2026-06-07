"""Tests for scripts.classify_connectors.

All tests are hermetic: no network, no real DB.  The --apply path is not
exercised (we mock or skip it).  The InMemorySecretStore is used for any
crypto-path tests.

Coverage
--------
- Private-IP MySQL → bridge/private_vpc
- Public Postgres → direct/public_db
- BigQuery (API type) → direct/api
- sslMode / sslmode normalisation
- Nested connectionDetails flattening
- Secret extraction separates password from host/port/db/user
- plan_backfill summary counts
- plan_backfill returns rotation_required=True for every connector
- Dry-run output redacts all secret values
- _parse_sql_dump: best-effort SQL INSERT parsing
- Various private hostname heuristics (.internal, .local, single-label)
- API type with empty host
"""

from __future__ import annotations

import base64
import json
import os
import secrets

import pytest


# ---------------------------------------------------------------------------
# Set a test encryption key before importing any crypto-dependent modules.
# ---------------------------------------------------------------------------

def _set_test_key() -> None:
    from app.security.crypto import reset_keys_for_tests

    os.environ["CONNECTOR_SECRET_KEY"] = base64.b64encode(secrets.token_bytes(32)).decode()
    os.environ["CONNECTOR_SECRET_KEY_VERSION"] = "1"
    os.environ.pop("CONNECTOR_SECRET_KEYS", None)
    reset_keys_for_tests()


@pytest.fixture(autouse=True)
def _crypto_key():
    """Ensure a test encryption key is always configured."""
    _set_test_key()
    yield
    from app.security.crypto import reset_keys_for_tests

    os.environ.pop("CONNECTOR_SECRET_KEY", None)
    os.environ.pop("CONNECTOR_SECRET_KEY_VERSION", None)
    reset_keys_for_tests()


# ---------------------------------------------------------------------------
# Import the module under test (after key is set).
# ---------------------------------------------------------------------------

from scripts.classify_connectors import (  # noqa: E402
    _flatten,
    _is_private_hostname,
    _is_private_ip,
    _parse_sql_dump,
    _redact_plan,
    classify,
    plan_backfill,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mysql_private_row(**kwargs) -> dict:
    base = {
        "id": "aaa00000-0000-0000-0000-000000000001",
        "org_id": "bbb00000-0000-0000-0000-000000000001",
        "name": "prod-mysql",
        "type": "mysql",
        "host": "10.132.0.15",
        "port": 3306,
        "database": "analytics",
        "user": "readonly",
        "sslmode": "require",
        "password": "super-secret-pw",
    }
    base.update(kwargs)
    return base


def _postgres_public_row(**kwargs) -> dict:
    base = {
        "id": "aaa00000-0000-0000-0000-000000000002",
        "org_id": "bbb00000-0000-0000-0000-000000000001",
        "name": "analytics-pg",
        "type": "postgres",
        "host": "41.21.218.123",
        "port": 5432,
        "database": "analytics",
        "user": "reader",
        "sslmode": "require",
        "password": "pg-pass",
    }
    base.update(kwargs)
    return base


def _bigquery_row(**kwargs) -> dict:
    base = {
        "id": "aaa00000-0000-0000-0000-000000000003",
        "org_id": "bbb00000-0000-0000-0000-000000000001",
        "name": "prod-bigquery",
        "type": "bigquery",
        "project_id": "my-gcp-project",
        "dataset": "analytics",
        "service_account_json": '{"type":"service_account","project_id":"my-gcp-project"}',
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# 1. Private-IP MySQL → bridge / private_vpc
# ---------------------------------------------------------------------------

class TestPrivateVpcClassification:
    def test_rfc1918_10_block_is_bridge(self):
        row = _mysql_private_row(host="10.132.0.15")
        result = classify(row)
        assert result["network_mode"] == "bridge"
        assert result["reachability_class"] == "private_vpc"

    def test_rfc1918_172_16_block_is_bridge(self):
        row = _mysql_private_row(host="172.16.0.1")
        result = classify(row)
        assert result["network_mode"] == "bridge"
        assert result["reachability_class"] == "private_vpc"

    def test_rfc1918_172_31_block_is_bridge(self):
        row = _mysql_private_row(host="172.31.255.254")
        result = classify(row)
        assert result["network_mode"] == "bridge"
        assert result["reachability_class"] == "private_vpc"

    def test_rfc1918_192_168_block_is_bridge(self):
        row = _mysql_private_row(host="192.168.1.100")
        result = classify(row)
        assert result["network_mode"] == "bridge"
        assert result["reachability_class"] == "private_vpc"

    def test_loopback_127_is_bridge(self):
        row = _mysql_private_row(host="127.0.0.1")
        result = classify(row)
        assert result["network_mode"] == "bridge"
        assert result["reachability_class"] == "private_vpc"

    def test_dot_internal_hostname_is_bridge(self):
        row = _mysql_private_row(host="db.prod.internal")
        result = classify(row)
        assert result["network_mode"] == "bridge"
        assert result["reachability_class"] == "private_vpc"

    def test_dot_local_hostname_is_bridge(self):
        row = _mysql_private_row(host="postgres.local")
        result = classify(row)
        assert result["network_mode"] == "bridge"
        assert result["reachability_class"] == "private_vpc"

    def test_single_label_hostname_is_bridge(self):
        # k8s service names like "postgres" or "mysql" have no dot
        row = _mysql_private_row(host="postgres")
        result = classify(row)
        assert result["network_mode"] == "bridge"
        assert result["reachability_class"] == "private_vpc"

    def test_reason_mentions_bridge(self):
        row = _mysql_private_row(host="10.0.0.5")
        result = classify(row)
        assert "bridge" in result["reason"].lower() or "private" in result["reason"].lower()


# ---------------------------------------------------------------------------
# 2. Public Postgres → direct / public_db
# ---------------------------------------------------------------------------

class TestPublicDbClassification:
    def test_public_ip_is_direct(self):
        row = _postgres_public_row(host="41.21.218.123")
        result = classify(row)
        assert result["network_mode"] == "direct"
        assert result["reachability_class"] == "public_db"

    def test_public_hostname_is_direct(self):
        row = _postgres_public_row(host="db.example.com")
        result = classify(row)
        assert result["network_mode"] == "direct"
        assert result["reachability_class"] == "public_db"

    def test_reason_mentions_allowlist_or_egress(self):
        row = _postgres_public_row(host="db.example.com")
        result = classify(row)
        lower_reason = result["reason"].lower()
        assert "allowlist" in lower_reason or "egress" in lower_reason or "routable" in lower_reason


# ---------------------------------------------------------------------------
# 3. BigQuery / API types → direct / api
# ---------------------------------------------------------------------------

class TestApiClassification:
    def test_bigquery_is_api(self):
        row = _bigquery_row()
        result = classify(row)
        assert result["network_mode"] == "direct"
        assert result["reachability_class"] == "api"

    def test_bq_alias_is_api(self):
        row = _bigquery_row(type="bq")
        result = classify(row)
        assert result["reachability_class"] == "api"

    def test_google_bigquery_is_api(self):
        row = _bigquery_row(type="google_bigquery")
        result = classify(row)
        assert result["reachability_class"] == "api"

    def test_salesforce_is_api(self):
        row = {
            "id": "aaa-004",
            "org_id": "bbb-001",
            "name": "sf",
            "type": "salesforce",
            "api_key": "tok_xyz",
        }
        result = classify(row)
        assert result["network_mode"] == "direct"
        assert result["reachability_class"] == "api"

    def test_api_type_with_no_host_is_api(self):
        row = {"id": "x", "org_id": "y", "type": "hubspot", "api_key": "hk_abc"}
        result = classify(row)
        assert result["reachability_class"] == "api"


# ---------------------------------------------------------------------------
# 4. Field normalisation: sslMode → sslmode
# ---------------------------------------------------------------------------

class TestFieldNormalisation:
    def test_sslMode_normalised_to_sslmode(self):
        row = {
            "id": "n1",
            "org_id": "o1",
            "type": "postgres",
            "host": "db.example.com",
            "sslMode": "require",
            "password": "pw",
        }
        flat = _flatten(row)
        assert "sslmode" in flat
        assert flat["sslmode"] == "require"
        assert "sslMode" not in flat

    def test_ssl_mode_underscore_normalised(self):
        row = {"id": "n2", "type": "postgres", "host": "h.example.com", "ssl_mode": "verify-ca", "password": "pw"}
        flat = _flatten(row)
        assert flat.get("sslmode") == "verify-ca"

    def test_nested_connectionDetails_flattened(self):
        row = {
            "id": "n3",
            "org_id": "o1",
            "name": "nested",
            "type": "postgres",
            "connectionDetails": {
                "host": "db.internal",
                "port": 5432,
                "sslMode": "require",
                "password": "nested-pw",
            },
        }
        flat = _flatten(row)
        assert flat["host"] == "db.internal"
        assert flat["port"] == 5432
        assert flat.get("sslmode") == "require"
        assert flat["password"] == "nested-pw"
        assert "connectionDetails" not in flat

    def test_top_level_overrides_nested(self):
        """Top-level fields win over nested connectionDetails fields."""
        row = {
            "id": "n4",
            "host": "top-level-host.example.com",
            "connectionDetails": {"host": "nested-host.internal", "port": 5432},
        }
        flat = _flatten(row)
        assert flat["host"] == "top-level-host.example.com"

    def test_config_wrapper_flattened(self):
        row = {
            "id": "n5",
            "type": "mysql",
            "config": {"host": "10.0.0.1", "port": 3306, "password": "cfg-pw"},
        }
        flat = _flatten(row)
        assert flat["host"] == "10.0.0.1"
        assert flat["password"] == "cfg-pw"


# ---------------------------------------------------------------------------
# 5. Secret extraction — password separated from host/port/db/user
# ---------------------------------------------------------------------------

class TestSecretExtraction:
    def test_password_is_secret(self):
        row = _mysql_private_row(password="hunter2")
        result = classify(row)
        assert "password" in result["secret_keys"]

    def test_host_is_not_secret(self):
        row = _mysql_private_row()
        result = classify(row)
        assert "host" not in result["secret_keys"]

    def test_port_is_not_secret(self):
        row = _mysql_private_row()
        result = classify(row)
        assert "port" not in result["secret_keys"]

    def test_database_is_not_secret(self):
        row = _mysql_private_row()
        result = classify(row)
        assert "database" not in result["secret_keys"]

    def test_user_is_not_secret(self):
        row = _mysql_private_row()
        result = classify(row)
        assert "user" not in result["secret_keys"]

    def test_sslmode_is_not_secret(self):
        row = _mysql_private_row()
        result = classify(row)
        assert "sslmode" not in result["secret_keys"]

    def test_service_account_json_is_secret(self):
        row = _bigquery_row()
        result = classify(row)
        assert "service_account_json" in result["secret_keys"]

    def test_api_key_is_secret(self):
        row = {"id": "x", "org_id": "y", "type": "hubspot", "api_key": "hk_abc"}
        result = classify(row)
        assert "api_key" in result["secret_keys"]

    def test_token_is_secret(self):
        row = {"id": "x", "org_id": "y", "type": "salesforce", "token": "tok_xyz"}
        result = classify(row)
        assert "token" in result["secret_keys"]

    def test_plan_backfill_puts_password_in_secret_not_config(self):
        rows = [_postgres_public_row()]
        plans = plan_backfill(rows)
        connector_plan = next(p for p in plans if "_summary" not in p)
        assert "password" in connector_plan["secret"]
        assert "password" not in connector_plan["config"]

    def test_plan_backfill_puts_host_in_config_not_secret(self):
        rows = [_postgres_public_row()]
        plans = plan_backfill(rows)
        connector_plan = next(p for p in plans if "_summary" not in p)
        assert "host" in connector_plan["config"]
        assert "host" not in connector_plan["secret"]


# ---------------------------------------------------------------------------
# 6. plan_backfill summary counts
# ---------------------------------------------------------------------------

class TestPlanBackfillSummary:
    def test_summary_counts_correct(self):
        rows = [
            _mysql_private_row(id="id1"),              # private_vpc
            _postgres_public_row(id="id2"),            # public_db
            _bigquery_row(id="id3"),                   # api
            _mysql_private_row(id="id4", host="192.168.5.5"),  # private_vpc
        ]
        plans = plan_backfill(rows)
        summary_plan = next(p for p in plans if "_summary" in p)
        summary = summary_plan["_summary"]

        assert summary["private_vpc"] == 2
        assert summary["public_db"] == 1
        assert summary["api"] == 1

    def test_summary_is_last_item(self):
        rows = [_bigquery_row()]
        plans = plan_backfill(rows)
        assert "_summary" in plans[-1]

    def test_all_connectors_have_rotation_required(self):
        rows = [_mysql_private_row(), _postgres_public_row(), _bigquery_row()]
        plans = plan_backfill(rows)
        for plan in plans:
            if "_summary" in plan:
                continue
            assert plan["rotation_required"] is True

    def test_network_mode_injected_into_config(self):
        rows = [_mysql_private_row()]
        plans = plan_backfill(rows)
        plan = next(p for p in plans if "_summary" not in p)
        assert plan["config"]["network_mode"] == "bridge"

    def test_public_db_network_mode_is_direct(self):
        rows = [_postgres_public_row()]
        plans = plan_backfill(rows)
        plan = next(p for p in plans if "_summary" not in p)
        assert plan["config"]["network_mode"] == "direct"


# ---------------------------------------------------------------------------
# 7. Dry-run redaction
# ---------------------------------------------------------------------------

class TestDryRunRedaction:
    def test_redact_plan_replaces_secret_values(self):
        rows = [_postgres_public_row(password="my-super-secret")]
        plans = plan_backfill(rows)
        plan = next(p for p in plans if "_summary" not in p)

        assert plan["secret"]["password"] == "my-super-secret"

        redacted = _redact_plan(plan)
        assert redacted["secret"]["password"] == "<REDACTED>"

    def test_redact_does_not_modify_original(self):
        rows = [_mysql_private_row(password="dont-touch")]
        plans = plan_backfill(rows)
        plan = next(p for p in plans if "_summary" not in p)
        _redact_plan(plan)
        # Original must be unchanged.
        assert plan["secret"]["password"] == "dont-touch"

    def test_redact_preserves_non_secret_fields(self):
        rows = [_postgres_public_row()]
        plans = plan_backfill(rows)
        plan = next(p for p in plans if "_summary" not in p)
        redacted = _redact_plan(plan)
        # host, port, database, user must be intact.
        assert redacted["config"]["host"] == "41.21.218.123"
        assert redacted["config"]["port"] == 5432
        assert redacted["config"]["database"] == "analytics"

    def test_redact_service_account_json(self):
        rows = [_bigquery_row()]
        plans = plan_backfill(rows)
        plan = next(p for p in plans if "_summary" not in p)
        redacted = _redact_plan(plan)
        assert redacted["secret"].get("service_account_json") == "<REDACTED>"

    def test_summary_plan_is_returned_unchanged(self):
        rows = [_bigquery_row()]
        plans = plan_backfill(rows)
        summary = plans[-1]
        redacted = _redact_plan(summary)
        assert redacted is summary  # exact same object — no copy made

    def test_secret_value_not_in_redacted_json(self):
        """The literal password must not appear anywhere in the redacted output."""
        plaintext_password = "hunter2-DO-NOT-PRINT"
        rows = [_postgres_public_row(password=plaintext_password)]
        plans = plan_backfill(rows)
        plan = next(p for p in plans if "_summary" not in p)
        redacted = _redact_plan(plan)
        serialised = json.dumps(redacted)
        assert plaintext_password not in serialised


# ---------------------------------------------------------------------------
# 8. SQL dump parser
# ---------------------------------------------------------------------------

class TestSqlDumpParser:
    def test_parses_simple_insert(self):
        sql = (
            "INSERT INTO connectors (id, name, host, password) "
            "VALUES ('abc-123', 'prod', 'db.example.com', 'secret');"
        )
        rows = _parse_sql_dump(sql)
        assert len(rows) == 1
        assert rows[0]["id"] == "abc-123"
        assert rows[0]["name"] == "prod"
        assert rows[0]["host"] == "db.example.com"
        assert rows[0]["password"] == "secret"

    def test_parses_multiple_inserts(self):
        sql = (
            "INSERT INTO connectors (id, host) VALUES ('id-1', '10.0.0.1');\n"
            "INSERT INTO connectors (id, host) VALUES ('id-2', 'db.example.com');\n"
        )
        rows = _parse_sql_dump(sql)
        assert len(rows) == 2
        assert rows[0]["id"] == "id-1"
        assert rows[1]["id"] == "id-2"

    def test_handles_null_values(self):
        sql = "INSERT INTO connectors (id, password) VALUES ('x', NULL);"
        rows = _parse_sql_dump(sql)
        assert rows[0]["password"] is None

    def test_handles_integer_values(self):
        sql = "INSERT INTO connectors (id, port) VALUES ('x', 5432);"
        rows = _parse_sql_dump(sql)
        assert rows[0]["port"] == 5432

    def test_no_match_returns_empty(self):
        sql = "SELECT * FROM connectors;"
        rows = _parse_sql_dump(sql)
        assert rows == []


# ---------------------------------------------------------------------------
# 9. Private hostname heuristic unit tests
# ---------------------------------------------------------------------------

class TestPrivateHostnameHeuristics:
    @pytest.mark.parametrize("host", [
        "10.0.0.1",
        "10.132.0.15",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.1.1",
        "127.0.0.1",
    ])
    def test_rfc1918_addresses_are_private(self, host):
        assert _is_private_hostname(host) is True

    @pytest.mark.parametrize("host", [
        "db.prod.internal",
        "postgres.local",
        "mysql.corp",
        "redis.lan",
    ])
    def test_private_tld_suffixes_are_private(self, host):
        assert _is_private_hostname(host) is True

    @pytest.mark.parametrize("host", [
        "postgres",    # single label — k8s service
        "mysql",
        "redis",
    ])
    def test_single_label_hostnames_are_private(self, host):
        assert _is_private_hostname(host) is True

    @pytest.mark.parametrize("host", [
        "db.example.com",
        "prod-pg.mycompany.io",
        "41.21.218.123",
        "8.8.8.8",
    ])
    def test_public_hosts_are_not_private(self, host):
        assert _is_private_hostname(host) is False


# ---------------------------------------------------------------------------
# 10. Edge cases and combined scenarios
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_connector_with_no_id_still_classifiable(self):
        row = {"type": "postgres", "host": "db.example.com", "password": "pw"}
        result = classify(row)
        assert result["network_mode"] == "direct"

    def test_empty_host_falls_through_to_api_if_not_db_type(self):
        row = {"type": "notion", "api_key": "notion_key_123"}
        result = classify(row)
        assert result["reachability_class"] == "api"

    def test_plan_backfill_empty_rows_returns_only_summary(self):
        plans = plan_backfill([])
        assert len(plans) == 1
        assert "_summary" in plans[0]
        summary = plans[0]["_summary"]
        assert summary["private_vpc"] == 0
        assert summary["api"] == 0
        assert summary["public_db"] == 0

    def test_classify_extracts_multiple_secrets(self):
        row = {
            "id": "x",
            "type": "postgres",
            "host": "db.example.com",
            "password": "pw",
            "api_key": "ak",
            "token": "tok",
        }
        result = classify(row)
        assert set(result["secret_keys"]) >= {"password", "api_key", "token"}

    def test_classify_returns_required_keys(self):
        row = _bigquery_row()
        result = classify(row)
        assert set(result.keys()) == {"network_mode", "reason", "reachability_class", "secret_keys"}

    def test_172_15_not_private(self):
        """172.15.x.x is outside 172.16-31 range — should be public."""
        assert _is_private_ip("172.15.0.1") is False

    def test_172_32_not_private(self):
        """172.32.x.x is outside 172.16-31 range — should be public."""
        assert _is_private_ip("172.32.0.1") is False
