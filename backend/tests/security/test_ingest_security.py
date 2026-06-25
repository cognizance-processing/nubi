"""INGEST SECURITY — adversarial tests for the versioned write/ingest API.

Covers:
  - partition path-traversal (.., absolute, url-encoded, null byte, prefix escape)
  - part relpath traversal
  - schema-gate bypass (per-table narrowing rejected; full_replace consistency)
  - full_replace NEVER deletes _nubi/ sidecars or another table's data
  - idempotency: same key → same session, no duplicate
  - CAS state transitions cannot be raced into an invalid state
  - cross-org / IDOR: org A cannot get/transition org B's session
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.lakehouse.ingest_session import InMemoryIngestSessionStore
from app.lakehouse.managed import lake_prefix
from tests.security._custody_fixtures import (  # noqa: F401
    auth_headers,
    build_parquet,
    custody_env,
    open_session,
    sha256_hex,
)


# ---------------------------------------------------------------------------
# Partition / relpath traversal
# ---------------------------------------------------------------------------

_TRAVERSAL_PARTITIONS = [
    "../escape",
    "..",
    "../../etc",
    "/abs/path",
    "dt=2026-06-25/../../escape",
    "a/../../b",
    "..%2f..%2fetc",        # url-encoded dots (raw — server sees literally)
    "dt=\x00null",          # null byte
    "foo/./bar",            # single-dot segment via second seg
]


@pytest.mark.asyncio
@pytest.mark.parametrize("partition", _TRAVERSAL_PARTITIONS)
async def test_partition_traversal_rejected(custody_env, partition):
    e = custody_env
    r = await open_session(
        e["client"], auth_headers(e["alice_id"]), e["alice_ds"],
        mode="append", partition=partition,
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] in ("invalid_partition", "missing_partition")


@pytest.mark.asyncio
async def test_table_name_traversal_rejected(custody_env):
    e = custody_env
    r = await open_session(
        e["client"], auth_headers(e["alice_id"]), e["alice_ds"],
        mode="full_replace", table_name="../../escape",
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_partition"


@pytest.mark.parametrize(
    "relpath",
    [
        "../escape.parquet",
        "a/../../b.parquet",
        "sub/../../x",
        "..",
        "/abs.parquet",          # leading slash stripped; "" empty segment? -> rejected
        "evil/../../../x",
        "bad seg.parquet",       # space → disallowed char
        "q'uote.parquet",        # quote → disallowed char
    ],
)
def test_part_relpath_validator_rejects_traversal(relpath):
    """_validate_relpath is the server-side gate; assert it rejects escapes."""
    from app.routes.ingest import _validate_relpath
    from app.errors import AppError as _AppError

    # A pure-leading-slash path normalises to a safe single segment; everything
    # with a traversal segment or disallowed char must raise.
    safe_after_strip = relpath.lstrip("/")
    if "/" not in safe_after_strip and ".." not in safe_after_strip.split("/") \
            and all(c.isalnum() or c in "_-." for c in safe_after_strip) and safe_after_strip:
        # e.g. "abs.parquet" — legitimately safe after stripping the slash.
        assert _validate_relpath(relpath) == safe_after_strip
        return
    with pytest.raises(_AppError) as ei:
        _validate_relpath(relpath)
    assert ei.value.code == "invalid_relpath"


@pytest.mark.asyncio
async def test_part_upload_null_byte_rejected(custody_env):
    """A null byte in the part path is rejected at the HTTP layer (400)."""
    e = custody_env
    r = await open_session(e["client"], auth_headers(e["alice_id"]), e["alice_ds"])
    sid = r.json()["session_id"]
    up = await e["client"].put(
        f"/api/v1/lake/{e['alice_ds']}/ingest/sessions/{sid}/parts/ev%00il.parquet",
        content=build_parquet([{"id": 1, "val": "a"}]),
        headers=auth_headers(e["alice_id"]),
    )
    # Must never 200 with a control-char path; 400 invalid_relpath is the gate
    # (a routing-layer rejection of 404 is also acceptable — never a write).
    assert up.status_code in (400, 404)


# ---------------------------------------------------------------------------
# Schema gate
# ---------------------------------------------------------------------------


async def _commit_full_replace(e, schema, table_name="default", idem=None):
    """Open + upload + commit a full_replace session; return the commit response."""
    hdr = auth_headers(e["alice_id"])
    r = await open_session(
        e["client"], hdr, e["alice_ds"], mode="full_replace",
        schema=schema, table_name=table_name, idempotency_key=idem,
    )
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]
    data = build_parquet([{"id": 1, "val": "a"}])
    up = await e["client"].put(
        f"/api/v1/lake/{e['alice_ds']}/ingest/sessions/{sid}/parts/part0.parquet",
        content=data, headers=hdr,
    )
    assert up.status_code == 200, up.text
    manifest = up.json()
    return await e["client"].post(
        f"/api/v1/lake/{e['alice_ds']}/ingest/sessions/{sid}/commit",
        json={"files": [manifest], "row_counts": {manifest["path"]: 1}},
        headers=hdr,
    )


@pytest.mark.asyncio
async def test_append_schema_narrowing_rejected(custody_env):
    e = custody_env
    base = [{"name": "id", "type": "int64"}, {"name": "val", "type": "string"}]
    c = await _commit_full_replace(e, base, table_name="t1")
    assert c.status_code == 200, c.text

    # Append that REMOVES the `val` column → 409 schema_incompatible.
    hdr = auth_headers(e["alice_id"])
    r = await open_session(
        e["client"], hdr, e["alice_ds"], mode="append", partition="dt=2026-06-25",
        schema=[{"name": "id", "type": "int64"}], table_name="t1",
    )
    sid = r.json()["session_id"]
    data = build_parquet([{"id": 2}])
    up = await e["client"].put(
        f"/api/v1/lake/{e['alice_ds']}/ingest/sessions/{sid}/parts/p.parquet",
        content=data, headers=hdr,
    )
    m = up.json()
    c2 = await e["client"].post(
        f"/api/v1/lake/{e['alice_ds']}/ingest/sessions/{sid}/commit",
        json={"files": [m], "row_counts": {m["path"]: 1}}, headers=hdr,
    )
    assert c2.status_code == 409
    assert c2.json()["error"]["code"] == "schema_incompatible"


@pytest.mark.asyncio
async def test_append_type_change_rejected(custody_env):
    e = custody_env
    base = [{"name": "id", "type": "int64"}, {"name": "val", "type": "string"}]
    assert (await _commit_full_replace(e, base, table_name="t2")).status_code == 200

    hdr = auth_headers(e["alice_id"])
    r = await open_session(
        e["client"], hdr, e["alice_ds"], mode="append", partition="dt=2026-06-25",
        schema=[{"name": "id", "type": "string"}, {"name": "val", "type": "string"}],
        table_name="t2",
    )
    sid = r.json()["session_id"]
    up = await e["client"].put(
        f"/api/v1/lake/{e['alice_ds']}/ingest/sessions/{sid}/parts/p.parquet",
        content=build_parquet([{"id": "x", "val": "a"}]), headers=hdr,
    )
    m = up.json()
    c2 = await e["client"].post(
        f"/api/v1/lake/{e['alice_ds']}/ingest/sessions/{sid}/commit",
        json={"files": [m], "row_counts": {m["path"]: 1}}, headers=hdr,
    )
    assert c2.status_code == 409
    assert c2.json()["error"]["code"] == "schema_incompatible"


# ---------------------------------------------------------------------------
# full_replace must not nuke sidecars or other tables
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_replace_preserves_sidecars_and_other_tables(custody_env):
    e = custody_env
    schema = [{"name": "id", "type": "int64"}, {"name": "val", "type": "string"}]
    # Table A and B both committed.
    assert (await _commit_full_replace(e, schema, table_name="alpha")).status_code == 200
    assert (await _commit_full_replace(e, schema, table_name="beta")).status_code == 200

    prefix = lake_prefix(e["alice_org"], e["alice_ds"])
    lake_root = os.path.join(e["lake_dir"], prefix)

    # Both tables' data + the schema sidecar exist.
    assert os.path.isdir(os.path.join(lake_root, "alpha"))
    assert os.path.isdir(os.path.join(lake_root, "beta"))
    sidecar = os.path.join(lake_root, "_nubi", "schema.json")
    assert os.path.exists(sidecar)

    # Now full_replace table alpha again with a DIFFERENT part.
    assert (
        await _commit_full_replace(e, schema, table_name="alpha", idem=str(uuid.uuid4()))
    ).status_code == 200

    # beta must survive; the _nubi sidecar must survive.
    assert os.path.isdir(os.path.join(lake_root, "beta")), "other table wiped by full_replace"
    assert os.path.exists(sidecar), "_nubi sidecar swept by full_replace"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_idempotency_key_returns_same_session(custody_env):
    e = custody_env
    hdr = auth_headers(e["alice_id"])
    key = "idem-123"
    r1 = await open_session(e["client"], hdr, e["alice_ds"], idempotency_key=key)
    r2 = await open_session(e["client"], hdr, e["alice_ds"], idempotency_key=key)
    assert r1.status_code == 201 and r2.status_code in (200, 201)
    assert r1.json()["session_id"] == r2.json()["session_id"]


# ---------------------------------------------------------------------------
# CAS state machine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cas_transition_single_winner():
    """Two concurrent CAS transitions open→committing: only one wins."""
    store = InMemoryIngestSessionStore()
    rec = await store.create(
        org_id="o", datastore_id="d", user_id="u", mode="full_replace",
        idempotency_key="k", schema=[], partition=None, run_id="r",
    )
    sid = rec["id"]
    first = await store.transition("o", "d", sid, "committing", from_state="open")
    second = await store.transition("o", "d", sid, "committing", from_state="open")
    assert first is not None
    assert second is None  # CAS predicate failed for the loser


@pytest.mark.asyncio
async def test_cas_rejects_wrong_from_state():
    store = InMemoryIngestSessionStore()
    rec = await store.create(
        org_id="o", datastore_id="d", user_id="u", mode="full_replace",
        idempotency_key="k", schema=[], partition=None, run_id="r",
    )
    sid = rec["id"]
    # committed → cannot go committed-from-open (it's still open here, so this
    # tries an invalid path: aborting from a non-matching from_state).
    assert await store.transition("o", "d", sid, "committed", from_state="committing") is None
    # state unchanged.
    cur = await store.get("o", "d", sid)
    assert cur["state"] == "open"


# ---------------------------------------------------------------------------
# Cross-org / IDOR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_org_session_get_is_404(custody_env):
    e = custody_env
    # Alice opens a session.
    r = await open_session(e["client"], auth_headers(e["alice_id"]), e["alice_ds"])
    sid = r.json()["session_id"]
    # Bob (different org) tries to read it via alice's datastore id → 404.
    r2 = await e["client"].get(
        f"/api/v1/lake/{e['alice_ds']}/ingest/sessions/{sid}",
        headers=auth_headers(e["bob_id"]),
    )
    # Either the datastore is 404 for bob, or the session is not found.
    assert r2.status_code == 404


@pytest.mark.asyncio
async def test_cross_org_store_get_returns_none():
    store = InMemoryIngestSessionStore()
    rec = await store.create(
        org_id="orgA", datastore_id="d", user_id="u", mode="full_replace",
        idempotency_key="k", schema=[], partition=None, run_id="r",
    )
    sid = rec["id"]
    assert await store.get("orgB", "d", sid) is None
    assert await store.transition("orgB", "d", sid, "aborted") is None
    # And the legit org still sees state untouched.
    assert (await store.get("orgA", "d", sid))["state"] == "open"
