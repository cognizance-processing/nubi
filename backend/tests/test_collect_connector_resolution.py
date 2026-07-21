"""Server-side board collection resolves connectors exactly like POST /query.

Regression suite for a bug that silently broke EVERY server-side board export —
`export.pdf`, `export.csv`, `export.json` and the board thumbnail — for every
real (non-demo) board, while the identical query succeeded through `POST /query`.

`app/dashboards/collect.py` used to hand-roll its own simplified copy of the
datastore→connector path. The simplification WAS the bug; it skipped:

  1. `connector_type` (it read only the legacy `type` key) → every datastore
     created through the UI resolved to None → `unknown_connector`.
  2. Secret injection → connections built with NO password →
     "Access denied for user 'root'@... (using password: NO)".
  3. The target sqlglot dialect → warehouse-native SQL rejected as INVALID_SQL.

All three are now delegated to the single shared resolver
(`app/connectors/resolve.py`), which `routes/query.py` also uses. These tests pin
that delegation, because the failure mode is silent: the export still returns
200, just with every widget carrying an inline error.

Strategy (house pattern — see test_connector_resolution.py / test_resources.py):
- `InMemoryRepo` via `set_repo()`; `InMemorySecretStore` via a patched
  `get_secret_store` (the resolver lazily imports it at call time).
- The connector factory is monkeypatched to CAPTURE the config it is handed —
  that captured cfg is the assertion surface (did the password arrive? was the
  bridge host rewritten?) without opening a real connection.
- InMemoryRepo is a dict lookup and will not reproduce driver/Postgres-side
  failures; these tests prove RESOLUTION logic only.
"""

from __future__ import annotations

import base64
import os
import secrets as _secrets
from typing import Any
from unittest.mock import patch

import pytest

from app.connectors.secret_store import InMemorySecretStore
from app.dashboards.collect import _dialect_for_registered, _resolve_connector
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

_ORG = "11111111-2222-3333-4444-555555555555"


def _set_single_key() -> None:
    """Install a deterministic AES key so InMemorySecretStore can seal/open."""
    from app.security.crypto import reset_keys_for_tests

    os.environ["CONNECTOR_SECRET_KEY"] = base64.b64encode(_secrets.token_bytes(32)).decode()
    os.environ["CONNECTOR_SECRET_KEY_VERSION"] = "1"
    os.environ.pop("CONNECTOR_SECRET_KEYS", None)
    reset_keys_for_tests()


class _FakePlan:
    """Minimal stand-in for PhysicalPlan — the resolver only reads rls_claims."""

    def __init__(self, policies: dict[str, Any] | None = None) -> None:
        self.rls_claims = {"policies": policies or {}}


class _Registered:
    """Minimal stand-in for a RegisteredQuery — the resolver reads datastore_id."""

    def __init__(self, datastore_id: str | None) -> None:
        self.datastore_id = datastore_id


class _CapturingConnector:
    """Records the cfg it was constructed with; never opens anything."""

    last_cfg: dict[str, Any] | None = None

    def __init__(self, cfg: Any) -> None:
        type(self).last_cfg = cfg if isinstance(cfg, dict) else {"_positional": cfg}

    def capabilities(self) -> dict[str, bool]:
        return {"predicate_rls": True}

    def close(self) -> None:
        pass


class _NoRlsConnector(_CapturingConnector):
    def capabilities(self) -> dict[str, bool]:
        return {"predicate_rls": False}


@pytest.fixture
def repo() -> InMemoryRepo:
    r = InMemoryRepo()
    set_repo(r)
    yield r
    set_repo(None)


async def _make_ds(repo: InMemoryRepo, ds_id: str, config: dict[str, Any]) -> None:
    await repo.create(
        "datastores", org_id=_ORG, created_by="test", name=ds_id, config=config, id=ds_id
    )


def _patched_registry(connector_cls: type = _CapturingConnector):
    """Patch the registry the SHARED resolver reads (not routes.query's)."""
    from unittest.mock import MagicMock

    reg = MagicMock()
    reg.get.return_value = lambda cfg: connector_cls(cfg)
    return patch("app.connectors.resolve.get_connector_registry", return_value=reg)


class TestSecretInjection:
    """The bug that broke every real board: no credentials reached the connector."""

    @pytest.mark.asyncio
    async def test_vault_password_is_injected_into_collect_path(self, repo: InMemoryRepo) -> None:
        _set_single_key()
        ds_id = "aaaaaaaa-0000-0000-0000-000000000001"
        # A UI-created datastore: connector_type, and NO password (it lives in
        # the secret store) — exactly the real MacMobile bridge-MySQL shape.
        await _make_ds(repo, ds_id, {
            "connector_type": "mysql",
            "host": "db.internal",
            "port": 3306,
            "database": "fieldforce",
            "user": "root",
        })
        store = InMemorySecretStore()
        # Real store API is `async put` (the `seed` helper in
        # test_connector_resolution.py belongs to that file's own local double).
        await store.put(ds_id, _ORG, {"password": "s3cr3t-from-vault"})

        _CapturingConnector.last_cfg = None
        with (
            patch("app.connectors.secret_store.get_secret_store", return_value=store),
            _patched_registry(),
        ):
            connector, owned, net_cleanup = await _resolve_connector(
                _Registered(ds_id), _ORG, repo, _FakePlan()
            )

        assert owned is True
        assert callable(net_cleanup)
        cfg = _CapturingConnector.last_cfg
        assert cfg is not None, "factory was never called"
        # THE regression: without injection this key is absent and MySQL answers
        # "Access denied ... (using password: NO)".
        assert cfg.get("password") == "s3cr3t-from-vault"

    @pytest.mark.asyncio
    async def test_connector_type_key_resolves(self, repo: InMemoryRepo) -> None:
        """`connector_type` (what the UI writes) must resolve, not just `type`."""
        _set_single_key()
        ds_id = "aaaaaaaa-0000-0000-0000-000000000002"
        await _make_ds(repo, ds_id, {"connector_type": "mysql", "host": "h", "database": "d"})
        store = InMemorySecretStore()

        from unittest.mock import MagicMock

        reg = MagicMock()
        reg.get.return_value = lambda cfg: _CapturingConnector(cfg)
        with (
            patch("app.connectors.secret_store.get_secret_store", return_value=store),
            patch("app.connectors.resolve.get_connector_registry", return_value=reg),
        ):
            await _resolve_connector(_Registered(ds_id), _ORG, repo, _FakePlan())

        # Reading only `type` resolved this to None → AppError("unknown_connector").
        reg.get.assert_called_once_with("mysql")


class TestRlsGate:
    """Defence-in-depth preserved through the refactor."""

    @pytest.mark.asyncio
    async def test_policy_bearing_query_refused_on_unsecurable_source(
        self, repo: InMemoryRepo
    ) -> None:
        from app.errors import AppError

        _set_single_key()
        ds_id = "aaaaaaaa-0000-0000-0000-000000000003"
        await _make_ds(repo, ds_id, {"connector_type": "mysql", "host": "h", "database": "d"})

        with (
            patch("app.connectors.secret_store.get_secret_store", return_value=InMemorySecretStore()),
            _patched_registry(_NoRlsConnector),
        ):
            with pytest.raises(AppError) as exc:
                await _resolve_connector(
                    _Registered(ds_id), _ORG, repo, _FakePlan(policies={"tenant": "t1"})
                )
        assert exc.value.code == "source_unsupported_rls"

    @pytest.mark.asyncio
    async def test_no_policies_is_allowed_on_the_same_source(self, repo: InMemoryRepo) -> None:
        """The gate keys off POLICIES, not the capability alone."""
        _set_single_key()
        ds_id = "aaaaaaaa-0000-0000-0000-000000000004"
        await _make_ds(repo, ds_id, {"connector_type": "mysql", "host": "h", "database": "d"})

        with (
            patch("app.connectors.secret_store.get_secret_store", return_value=InMemorySecretStore()),
            _patched_registry(_NoRlsConnector),
        ):
            connector, owned, _cleanup = await _resolve_connector(
                _Registered(ds_id), _ORG, repo, _FakePlan(policies={})
            )
        assert connector is not None and owned is True


class TestDemoPath:
    @pytest.mark.asyncio
    async def test_demo_connector_is_not_owned_and_has_a_noop_cleanup(
        self, repo: InMemoryRepo
    ) -> None:
        """A queryless/demo registration must not be closed — it's a singleton."""
        connector, owned, net_cleanup = await _resolve_connector(
            _Registered(None), _ORG, repo, _FakePlan()
        )
        assert connector is not None
        assert owned is False, "closing the demo singleton breaks every later request"
        net_cleanup()  # must be callable and a no-op


class TestDialectResolution:
    """Third divergence: collect planned everything as postgres → INVALID_SQL."""

    @pytest.mark.asyncio
    async def test_mysql_datastore_plans_as_mysql(self, repo: InMemoryRepo) -> None:
        ds_id = "aaaaaaaa-0000-0000-0000-000000000005"
        await _make_ds(repo, ds_id, {"connector_type": "mysql", "host": "h", "database": "d"})
        assert await _dialect_for_registered(_Registered(ds_id), _ORG, repo) == "mysql"

    @pytest.mark.asyncio
    async def test_demo_and_unknown_fall_back_to_the_default_dialect(
        self, repo: InMemoryRepo
    ) -> None:
        from app.connectors.dialects import DEFAULT_DIALECT

        # No datastore → demo/lake path keeps the historical default.
        assert await _dialect_for_registered(_Registered(None), _ORG, repo) == DEFAULT_DIALECT
        # Missing datastore row → fail-safe, never break collection.
        assert (
            await _dialect_for_registered(_Registered("nope"), _ORG, repo) == DEFAULT_DIALECT
        )

    @pytest.mark.asyncio
    async def test_dialect_reads_through_the_prefetch_cache(self, repo: InMemoryRepo) -> None:
        """The board collector pre-fetches datastores; don't re-query per widget."""
        ds_id = "aaaaaaaa-0000-0000-0000-000000000006"
        ds_row = {"id": ds_id, "org_id": _ORG, "config": {"connector_type": "postgres"}}
        # Deliberately NOT in the repo — only in the cache. If the cache is
        # honoured this resolves; if it is ignored it falls back to the default.
        got = await _dialect_for_registered(
            _Registered(ds_id), _ORG, repo, {ds_id: ds_row}
        )
        assert got == "postgres"


class TestBridgeFetchFallback:
    """A failing bridge lookup must degrade to 'no bridge', not explode.

    The shared resolver was extracted verbatim from `routes/query.py`, where
    `_AppError` is a FUNCTION-LOCAL alias (`from app.errors import AppError as
    _AppError`). The alias did not come along, so the `except (..., _AppError)`
    tuple referenced an unbound name — and an except tuple is only evaluated
    once something is raised inside the try. The bridge fetch failing therefore
    turned a soft fallback into `NameError`, on exactly the bridge-backed path
    the MacMobile board uses.
    """

    @pytest.mark.asyncio
    async def test_bridge_lookup_error_falls_back_instead_of_raising(
        self, repo: InMemoryRepo
    ) -> None:
        from app.connectors.resolve import resolve_datastore_connector
        from app.errors import AppError

        _set_single_key()
        ds_id = "aaaaaaaa-0000-0000-0000-000000000007"
        # network_mode 'direct' with a stale bridge_id — the shape left behind
        # when a bridge row is deleted or belongs to another org.
        await _make_ds(repo, ds_id, {
            "connector_type": "mysql",
            "host": "db.internal",
            "port": 3306,
            "database": "fieldforce",
            "user": "root",
            "network_mode": "direct",
            "bridge_id": "bbbbbbbb-0000-0000-0000-000000000001",
        })
        store = InMemorySecretStore()
        await store.put(ds_id, _ORG, {"password": "pw"})

        async def _missing_bridge(*_a: Any, **_k: Any) -> dict[str, Any]:
            raise AppError("bridge_not_found", "Bridge not found.", 404)

        _CapturingConnector.last_cfg = None
        with (
            patch("app.connectors.secret_store.get_secret_store", return_value=store),
            patch("app.routes.bridges._get_bridge", _missing_bridge),
            _patched_registry(),
        ):
            connector, conn_kind, net_cleanup = await resolve_datastore_connector(
                _FakePlan(), ds_id, _ORG, repo
            )

        assert conn_kind == "mysql"
        assert connector is not None
        # Direct mode: the connector still dials the datastore's own host.
        cfg = _CapturingConnector.last_cfg
        assert cfg is not None, "factory was never called"
        assert cfg["host"] == "db.internal"
        net_cleanup()
