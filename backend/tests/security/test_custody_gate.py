"""CUSTODY GATE — every /lake/* route fails closed (403) when custody is OFF.

Enumerates ALL custody routes and asserts each returns 403 ``custody_disabled``
when ``NUBI_CUSTODY_ENABLED`` is false:

  - ingest: open / upload / commit / status / abort
  - export: sync export, async enqueue, async status
  - tables-list

The gate is ``assert_custody_enabled()`` called at the TOP of each route.
"""

from __future__ import annotations

import uuid

import pytest

from tests.security._custody_fixtures import auth_headers, custody_env  # noqa: F401


def _disable_custody(monkeypatch) -> None:
    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "false")
    from app.config import get_settings
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ingest_open_fails_closed(custody_env, monkeypatch):
    e = custody_env
    _disable_custody(monkeypatch)
    r = await e["client"].post(
        f"/api/v1/lake/{e['alice_ds']}/ingest/sessions",
        json={"mode": "full_replace", "schema": [], "idempotency_key": "k"},
        headers=auth_headers(e["alice_id"]),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "custody_disabled"


@pytest.mark.asyncio
async def test_ingest_upload_fails_closed(custody_env, monkeypatch):
    e = custody_env
    _disable_custody(monkeypatch)
    sid = str(uuid.uuid4())
    r = await e["client"].put(
        f"/api/v1/lake/{e['alice_ds']}/ingest/sessions/{sid}/parts/part0.parquet",
        content=b"x",
        headers=auth_headers(e["alice_id"]),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "custody_disabled"


@pytest.mark.asyncio
async def test_ingest_commit_fails_closed(custody_env, monkeypatch):
    e = custody_env
    _disable_custody(monkeypatch)
    sid = str(uuid.uuid4())
    r = await e["client"].post(
        f"/api/v1/lake/{e['alice_ds']}/ingest/sessions/{sid}/commit",
        json={"files": []},
        headers=auth_headers(e["alice_id"]),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "custody_disabled"


@pytest.mark.asyncio
async def test_ingest_status_fails_closed(custody_env, monkeypatch):
    e = custody_env
    _disable_custody(monkeypatch)
    sid = str(uuid.uuid4())
    r = await e["client"].get(
        f"/api/v1/lake/{e['alice_ds']}/ingest/sessions/{sid}",
        headers=auth_headers(e["alice_id"]),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "custody_disabled"


@pytest.mark.asyncio
async def test_ingest_abort_fails_closed(custody_env, monkeypatch):
    e = custody_env
    _disable_custody(monkeypatch)
    sid = str(uuid.uuid4())
    r = await e["client"].post(
        f"/api/v1/lake/{e['alice_ds']}/ingest/sessions/{sid}/abort",
        headers=auth_headers(e["alice_id"]),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "custody_disabled"


@pytest.mark.asyncio
async def test_export_sync_fails_closed(custody_env, monkeypatch):
    e = custody_env
    _disable_custody(monkeypatch)
    r = await e["client"].post(
        f"/api/v1/lake/{e['alice_ds']}/export",
        json={"dest_uri": "file:///tmp/out"},
        headers=auth_headers(e["alice_id"]),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "custody_disabled"


@pytest.mark.asyncio
async def test_export_enqueue_fails_closed(custody_env, monkeypatch):
    e = custody_env
    _disable_custody(monkeypatch)
    r = await e["client"].post(
        f"/api/v1/lake/{e['alice_ds']}/export/jobs",
        json={"dest_uri": "file:///tmp/out"},
        headers=auth_headers(e["alice_id"]),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "custody_disabled"


@pytest.mark.asyncio
async def test_export_job_status_fails_closed(custody_env, monkeypatch):
    e = custody_env
    _disable_custody(monkeypatch)
    job_id = str(uuid.uuid4())
    r = await e["client"].get(
        f"/api/v1/lake/{e['alice_ds']}/export/jobs/{job_id}",
        headers=auth_headers(e["alice_id"]),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "custody_disabled"


@pytest.mark.asyncio
async def test_tables_list_fails_closed(custody_env, monkeypatch):
    e = custody_env
    _disable_custody(monkeypatch)
    r = await e["client"].get(
        f"/api/v1/lake/{e['alice_ds']}/tables",
        headers=auth_headers(e["alice_id"]),
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "custody_disabled"
