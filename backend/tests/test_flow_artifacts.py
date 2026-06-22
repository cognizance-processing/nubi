"""B4 — Typed artifact channel tests.

Coverage
--------
1. put_artifact / get_artifact round-trip (pickle).
2. put_artifact / get_artifact round-trip (json).
3. put_artifact / get_artifact round-trip (bytes).
4. put_artifact raises for unsupported kind.
5. get_artifact raises PermissionError across orgs (isolation).
6. get_artifact raises ValueError for non-handle input.
7. ArtifactHandle is_handle detection.
8. Artifact linked to its producing run (lineage via flow_run_outputs).
9. End-to-end: train-in-one-cell / score-in-another via drain_flow_run
   (pickle artifact produced by cell A, consumed by cell B).
10. Org isolation end-to-end: cell from org B cannot get org A artifact.
11. Existing row channel still works alongside artifacts (no regression).
12. ctx.put_artifact / ctx.get_artifact via TaskContext (unit test, no subprocess).
13. InMemoryArtifactStore upload/download/exists basic contract.
"""

from __future__ import annotations

import asyncio
import pickle
from datetime import datetime, timezone
from typing import Any

import pytest

from app.flows.artifacts import (
    InMemoryArtifactStore,
    get_artifact,
    get_artifact_store,
    is_handle,
    make_handle,
    put_artifact,
    set_artifact_store,
)
from app.flows.executor import TaskContext
from app.flows.registry import reset_for_tests
from app.flows.runtime import drain_flow_run, materialize_flow_run
from app.flows.store import InMemoryFlowStore, set_flow_store

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc() -> datetime:
    return datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


NOW = _utc()
CLAIMS: dict[str, Any] = {"org_id": "org-test", "sub": "user-test"}
ORG_A = "org-a"
ORG_B = "org-b"


def _mem_store() -> InMemoryArtifactStore:
    return InMemoryArtifactStore()


async def _make_flow(
    store: InMemoryFlowStore,
    spec: dict[str, Any],
    org_id: str = "org-test",
) -> dict[str, Any]:
    return await store.create_flow(
        org_id=org_id,
        created_by="user-test",
        name="test_flow",
        spec=spec,
    )


# ---------------------------------------------------------------------------
# 1. put_artifact / get_artifact round-trip (pickle)
# ---------------------------------------------------------------------------


def test_pickle_round_trip():
    art_store = _mem_store()
    obj = {"model": "lgbm", "params": [1, 2, 3], "nested": {"a": 1}}
    handle = put_artifact(obj, kind="pickle", org_id=ORG_A, store=art_store)

    assert is_handle(handle)
    assert handle["kind"] == "pickle"
    assert handle["org_id"] == ORG_A

    loaded = get_artifact(handle, org_id=ORG_A, store=art_store)
    assert loaded == obj


# ---------------------------------------------------------------------------
# 2. put_artifact / get_artifact round-trip (json)
# ---------------------------------------------------------------------------


def test_json_round_trip():
    art_store = _mem_store()
    obj = {"scores": [0.9, 0.85, 0.95], "label": "test"}
    handle = put_artifact(obj, kind="json", org_id=ORG_A, store=art_store)

    assert handle["kind"] == "json"
    loaded = get_artifact(handle, org_id=ORG_A, store=art_store)
    assert loaded == obj


# ---------------------------------------------------------------------------
# 3. put_artifact / get_artifact round-trip (bytes)
# ---------------------------------------------------------------------------


def test_bytes_round_trip():
    art_store = _mem_store()
    raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    handle = put_artifact(raw, kind="bytes", org_id=ORG_A, store=art_store)

    assert handle["kind"] == "bytes"
    loaded = get_artifact(handle, org_id=ORG_A, store=art_store)
    assert loaded == raw


# ---------------------------------------------------------------------------
# 4. put_artifact raises for unsupported kind
# ---------------------------------------------------------------------------


def test_unsupported_kind_raises():
    art_store = _mem_store()
    with pytest.raises(ValueError, match="Unsupported artifact kind"):
        put_artifact({"x": 1}, kind="avro", org_id=ORG_A, store=art_store)


# ---------------------------------------------------------------------------
# 5. get_artifact raises PermissionError across orgs (isolation)
# ---------------------------------------------------------------------------


def test_cross_org_get_raises_permission_error():
    art_store = _mem_store()
    obj = {"secret": "only org A should see this"}
    handle = put_artifact(obj, kind="pickle", org_id=ORG_A, store=art_store)

    with pytest.raises(PermissionError, match="org isolation"):
        get_artifact(handle, org_id=ORG_B, store=art_store)


# ---------------------------------------------------------------------------
# 6. get_artifact raises ValueError for non-handle input
# ---------------------------------------------------------------------------


def test_get_artifact_invalid_handle_raises():
    art_store = _mem_store()
    with pytest.raises(ValueError, match="ArtifactHandle"):
        get_artifact({"not": "a handle"}, org_id=ORG_A, store=art_store)

    with pytest.raises(ValueError, match="ArtifactHandle"):
        get_artifact("just a string", org_id=ORG_A, store=art_store)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 7. is_handle detection
# ---------------------------------------------------------------------------


def test_is_handle_positive():
    handle = make_handle(
        artifact_id="abc-123",
        kind="pickle",
        uri="mem://orgs/org-a/artifacts/abc-123",
        org_id=ORG_A,
        produced_by_run="run-1",
    )
    assert is_handle(handle) is True


def test_is_handle_negative():
    assert is_handle({}) is False
    assert is_handle({"artifact_id": "x"}) is False  # missing kind / __type__
    assert is_handle("string") is False
    assert is_handle(None) is False
    assert is_handle({"__type__": "artifact_handle"}) is False  # missing artifact_id


# ---------------------------------------------------------------------------
# 8. Artifact linked to its producing run (lineage)
# ---------------------------------------------------------------------------


async def test_artifact_lineage_recorded():
    """A task that puts an artifact → a flow_run_output with type='artifact'."""
    reset_for_tests()
    art_store = _mem_store()
    set_artifact_store(art_store)

    flow_store = InMemoryFlowStore()
    set_flow_store(flow_store)

    spec = {
        "version": 1,
        "name": "lineage_flow",
        "tasks": [
            {
                "key": "train",
                "kind": "python",
                "needs": [],
                "config": {
                    "code": (
                        "model = {'weights': [1, 2, 3]}\n"
                        "handle = ctx.put_artifact(model, name='my_model', kind='pickle')\n"
                        "result = {'model_handle': handle}\n"
                    )
                },
                "timeout_s": 30,
            }
        ],
    }

    flow = await _make_flow(flow_store, spec, org_id="org-test")
    run = await materialize_flow_run(flow_store, flow, {}, "manual", NOW)
    final = await drain_flow_run(flow_store, run["id"], NOW, {"org_id": "org-test", "sub": "u1"})

    assert final["state"] == "success", f"Flow failed: {final}"

    outputs = await flow_store.list_run_outputs(run["id"])
    artifact_outputs = [o for o in outputs if o["output_type"] == "artifact"]
    assert len(artifact_outputs) >= 1, (
        f"Expected at least 1 artifact lineage record, got: {outputs}"
    )
    assert artifact_outputs[0]["output_key"] == "my_model"

    set_artifact_store(None)
    set_flow_store(None)


# ---------------------------------------------------------------------------
# 9. End-to-end: train in cell A, score in cell B
# ---------------------------------------------------------------------------


async def test_train_score_across_cells():
    """Artifact handle crosses cell boundary: model trained in A, scored in B."""
    reset_for_tests()
    art_store = _mem_store()
    set_artifact_store(art_store)

    flow_store = InMemoryFlowStore()
    set_flow_store(flow_store)

    train_code = (
        "model = {'coef': [0.5, 1.2, -0.3], 'intercept': 0.1}\n"
        "handle = ctx.put_artifact(model, name='model', kind='pickle')\n"
        "result = {'model_handle': handle}\n"
    )
    score_code = (
        "import json\n"
        "upstream = inputs.get('train', {})\n"
        "handle = upstream.get('model_handle')\n"
        "model = ctx.get_artifact(handle)\n"
        "score = sum(c * x for c, x in zip(model['coef'], [1.0, 2.0, 3.0])) + model['intercept']\n"
        "result = {'score': score}\n"
    )

    spec = {
        "version": 1,
        "name": "train_score_flow",
        "tasks": [
            {
                "key": "train",
                "kind": "python",
                "needs": [],
                "config": {"code": train_code},
                "timeout_s": 30,
            },
            {
                "key": "score",
                "kind": "python",
                "needs": ["train"],
                "config": {"code": score_code},
                "timeout_s": 30,
            },
        ],
    }

    flow = await _make_flow(flow_store, spec, org_id="org-test")
    run = await materialize_flow_run(flow_store, flow, {}, "manual", NOW)
    final = await drain_flow_run(flow_store, run["id"], NOW, {"org_id": "org-test", "sub": "u1"})

    assert final["state"] == "success", f"Flow failed: {final}"

    task_runs = await flow_store.list_task_runs(run["id"])
    score_tr = next((t for t in task_runs if t["task_key"] == "score"), None)
    assert score_tr is not None
    assert score_tr["state"] == "success", f"Score cell did not succeed: {score_tr}"
    result = score_tr.get("result") or {}
    # score = 0.5*1 + 1.2*2 + (-0.3)*3 + 0.1 = 0.5 + 2.4 - 0.9 + 0.1 = 2.1
    assert abs(result.get("score", 0) - 2.1) < 1e-6, (
        f"Unexpected score: {result.get('score')}"
    )

    set_artifact_store(None)
    set_flow_store(None)


# ---------------------------------------------------------------------------
# 10. Org isolation end-to-end
# ---------------------------------------------------------------------------


async def test_cross_org_artifact_raises_in_cell():
    """A cell from org B that tries to get org A's artifact must get PermissionError."""
    reset_for_tests()
    art_store = _mem_store()
    set_artifact_store(art_store)

    # Directly put an artifact for org A.
    obj = {"secret": "org-a-model"}
    handle_a = put_artifact(obj, kind="pickle", org_id=ORG_A, store=art_store)

    # Build a cell for org B that tries to consume org A's handle.
    # Inject the handle as a JSON literal in the code.
    import json
    handle_json = json.dumps(handle_a)

    code = (
        f"import json\n"
        f"handle = json.loads({handle_json!r})\n"
        "model = ctx.get_artifact(handle)\n"
        "result = {'got': True}\n"
    )

    # Run the cell as org B.
    ctx = TaskContext(
        flow_params={},
        inputs={},
        now=NOW,
        org_id=ORG_B,
        run_id="run-org-b",
        _artifact_store=art_store,
    )

    # The PermissionError should surface from ctx.get_artifact.
    with pytest.raises(PermissionError, match="org isolation"):
        ctx.get_artifact(handle_a)

    set_artifact_store(None)
    set_flow_store(None)


# ---------------------------------------------------------------------------
# 11. Row channel still works (no regression)
# ---------------------------------------------------------------------------


async def test_row_channel_not_broken():
    """The existing rows/Arrow channel still works alongside artifacts."""
    reset_for_tests()
    art_store = _mem_store()
    set_artifact_store(art_store)

    flow_store = InMemoryFlowStore()
    set_flow_store(flow_store)

    spec = {
        "version": 1,
        "name": "row_channel_flow",
        "tasks": [
            {
                "key": "source",
                "kind": "python",
                "needs": [],
                "config": {
                    "code": (
                        "result = {'rows': [{'x': 1}, {'x': 2}], 'row_count': 2, 'columns': ['x']}\n"
                    )
                },
                "timeout_s": 10,
            },
            {
                "key": "consume",
                "kind": "python",
                "needs": ["source"],
                "config": {
                    "code": (
                        "upstream_rows = inputs.get('source', {}).get('rows', [])\n"
                        "result = {'total': sum(r['x'] for r in upstream_rows)}\n"
                    )
                },
                "timeout_s": 10,
            },
        ],
    }

    flow = await _make_flow(flow_store, spec, org_id="org-test")
    run = await materialize_flow_run(flow_store, flow, {}, "manual", NOW)
    final = await drain_flow_run(flow_store, run["id"], NOW, {"org_id": "org-test", "sub": "u1"})

    assert final["state"] == "success", f"Row-channel flow failed: {final}"
    trs = await flow_store.list_task_runs(run["id"])
    consume_tr = next((t for t in trs if t["task_key"] == "consume"), None)
    assert consume_tr is not None
    assert consume_tr["result"]["total"] == 3

    set_artifact_store(None)
    set_flow_store(None)


# ---------------------------------------------------------------------------
# 12. ctx.put_artifact / ctx.get_artifact via TaskContext (unit test)
# ---------------------------------------------------------------------------


def test_task_context_put_get_artifact():
    """TaskContext.put_artifact / get_artifact round-trip without subprocess."""
    art_store = _mem_store()
    ctx = TaskContext(
        flow_params={},
        inputs={},
        now=NOW,
        org_id=ORG_A,
        run_id="run-unit-1",
        _artifact_store=art_store,
    )

    obj = {"weights": [0.1, 0.2, 0.3]}
    handle = ctx.put_artifact(obj, name="test_model", kind="pickle")
    assert is_handle(handle)
    assert handle["org_id"] == ORG_A
    assert handle["name"] == "test_model"
    assert handle["produced_by_run"] == "run-unit-1"

    loaded = ctx.get_artifact(handle)
    assert loaded == obj


def test_task_context_put_artifact_no_org_raises():
    """put_artifact requires org_id."""
    art_store = _mem_store()
    ctx = TaskContext(
        flow_params={},
        inputs={},
        now=NOW,
        org_id=None,
        _artifact_store=art_store,
    )
    with pytest.raises(ValueError, match="org_id"):
        ctx.put_artifact({"x": 1})


# ---------------------------------------------------------------------------
# 13. InMemoryArtifactStore upload/download/exists
# ---------------------------------------------------------------------------


def test_in_memory_store_basic():
    store = InMemoryArtifactStore()
    data = b"hello bytes"
    artifact_id = "test-artifact-001"
    org_id = "org-store-test"

    assert store.exists(artifact_id, org_id) is False

    uri = store.upload(artifact_id, org_id, data)
    assert "artifacts" in uri
    assert store.exists(artifact_id, org_id) is True

    downloaded = store.download(artifact_id, org_id)
    assert downloaded == data


def test_in_memory_store_download_missing_raises():
    store = InMemoryArtifactStore()
    with pytest.raises(FileNotFoundError):
        store.download("nonexistent", "some-org")


def test_in_memory_store_org_isolation():
    """An artifact stored for org A is not visible to org B."""
    store = InMemoryArtifactStore()
    artifact_id = "shared-id"
    store.upload(artifact_id, ORG_A, b"org-a-data")

    # org B should NOT be able to download org A's artifact.
    assert store.exists(artifact_id, ORG_B) is False
    with pytest.raises(FileNotFoundError):
        store.download(artifact_id, ORG_B)


# ---------------------------------------------------------------------------
# 14. Oversized artifact raises ValueError (MED resource fix)
# ---------------------------------------------------------------------------


def test_oversized_artifact_raises_value_error():
    """put_artifact raises ValueError when the serialised bytes exceed the cap.

    REGRESSION: before the fix, there was no size cap — any size object could
    be uploaded to the object store, exhausting storage.
    """
    import unittest.mock  # noqa: PLC0415
    from app.flows import artifacts as _art_mod  # noqa: PLC0415

    art_store = _mem_store()
    # Temporarily lower the cap to 10 bytes so we can test without allocating
    # 500 MB of data.
    with unittest.mock.patch.object(_art_mod, "_ARTIFACT_MAX_BYTES", 10):
        # A 11-byte payload must be rejected.
        with pytest.raises(ValueError, match="exceeds the maximum"):
            put_artifact(b"x" * 11, kind="bytes", org_id=ORG_A, store=art_store)

        # A 10-byte payload (exactly at the cap) must succeed.
        handle = put_artifact(b"x" * 10, kind="bytes", org_id=ORG_A, store=art_store)
        assert is_handle(handle)

    # With cap=0 (disabled), any size must pass.
    with unittest.mock.patch.object(_art_mod, "_ARTIFACT_MAX_BYTES", 0):
        handle2 = put_artifact(b"x" * 10_000, kind="bytes", org_id=ORG_A, store=art_store)
        assert is_handle(handle2)


def test_oversized_artifact_json_raises():
    """put_artifact with kind='json' also enforces the size cap."""
    import unittest.mock  # noqa: PLC0415
    from app.flows import artifacts as _art_mod  # noqa: PLC0415

    art_store = _mem_store()
    big_obj = {"data": "a" * 1000}  # serialises to >> 10 bytes
    with unittest.mock.patch.object(_art_mod, "_ARTIFACT_MAX_BYTES", 10):
        with pytest.raises(ValueError, match="exceeds the maximum"):
            put_artifact(big_obj, kind="json", org_id=ORG_A, store=art_store)


# ---------------------------------------------------------------------------
# 15. Concurrent log capture does not cross-contaminate (LOW concurrency fix)
# ---------------------------------------------------------------------------


def test_concurrent_log_capture_no_cross_contamination():
    """_run_handler_with_logs must not let concurrent tasks bleed logs.

    REGRESSION: the old implementation added/removed a handler on the ROOT
    logger, which is process-global.  Two threads running handlers concurrently
    would see each other's log output.

    This test runs two handlers in parallel threads, each emitting a unique
    sentinel string, and asserts that each handler only captures its own sentinel.
    """
    import logging  # noqa: PLC0415
    import threading  # noqa: PLC0415
    from app.flows.executor import TaskContext, _run_handler_with_logs  # noqa: PLC0415

    SENTINEL_A = "LOG_SENTINEL_ALPHA_XYZ"
    SENTINEL_B = "LOG_SENTINEL_BETA_XYZ"

    def _make_handler(sentinel: str):
        """Return a handler that logs *sentinel* via a named logger."""
        def _handler(config, ctx, claims):
            # Log via a module logger (not root) — mirrors real handler code.
            task_log = logging.getLogger(f"nubi.flow.task.{threading.get_ident()}")
            task_log.warning(sentinel)
            return {"ok": True}
        return _handler

    ctx = TaskContext(flow_params={}, inputs={}, now=None, org_id="org-test")

    captured_a: list[str] = []
    captured_b: list[str] = []
    errors: list[Exception] = []

    barrier = threading.Barrier(2)

    def _run_a():
        try:
            barrier.wait()
            _, logs = _run_handler_with_logs(
                _make_handler(SENTINEL_A), {}, ctx, {}, []
            )
            captured_a.extend(logs)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def _run_b():
        try:
            barrier.wait()
            _, logs = _run_handler_with_logs(
                _make_handler(SENTINEL_B), {}, ctx, {}, []
            )
            captured_b.extend(logs)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t_a = threading.Thread(target=_run_a)
    t_b = threading.Thread(target=_run_b)
    t_a.start()
    t_b.start()
    t_a.join(timeout=10)
    t_b.join(timeout=10)

    assert not errors, f"Thread errors: {errors}"

    # Each capture list should contain only its own sentinel (no cross-contamination).
    all_a = "\n".join(captured_a)
    all_b = "\n".join(captured_b)

    # Sentinel B must NOT appear in A's capture and vice versa.
    assert SENTINEL_B not in all_a, (
        f"Cross-contamination: sentinel B found in A's captured logs.\n"
        f"captured_a={captured_a}\ncaptured_b={captured_b}"
    )
    assert SENTINEL_A not in all_b, (
        f"Cross-contamination: sentinel A found in B's captured logs.\n"
        f"captured_a={captured_a}\ncaptured_b={captured_b}"
    )


# ---------------------------------------------------------------------------
# 16. Oversized ndarray / DataFrame rejected PRE-serialisation (LOW resource)
# ---------------------------------------------------------------------------


def test_oversized_ndarray_rejected_pre_serialise():
    """A numpy ndarray that exceeds the cap is rejected before pickle.dumps.

    Before the fix, _serialise (pickle.dumps) was called unconditionally,
    which could OOM on a multi-GB array.  The fix checks .nbytes first.
    """
    import unittest.mock  # noqa: PLC0415
    from app.flows import artifacts as _art_mod  # noqa: PLC0415

    try:
        import numpy as np  # noqa: PLC0415
    except ImportError:
        pytest.skip("numpy not available")

    art_store = _mem_store()
    # 1 000 float64 values = 8 000 bytes; cap at 1 000 bytes → must reject pre-serialize.
    big_arr = np.zeros(1_000, dtype=np.float64)  # 8 000 bytes

    with unittest.mock.patch.object(_art_mod, "_ARTIFACT_MAX_BYTES", 1_000):
        with pytest.raises(ValueError, match="pre-serialisation size estimate|exceeds the maximum"):
            put_artifact(big_arr, kind="pickle", org_id=ORG_A, store=art_store)

    # A small array under the cap must succeed.
    small_arr = np.zeros(10, dtype=np.float64)  # 80 bytes
    with unittest.mock.patch.object(_art_mod, "_ARTIFACT_MAX_BYTES", 1_000):
        handle = put_artifact(small_arr, kind="pickle", org_id=ORG_A, store=art_store)
        assert is_handle(handle)


def test_oversized_dataframe_rejected_pre_serialise():
    """A pandas DataFrame that exceeds the cap is rejected before pickle.dumps."""
    import unittest.mock  # noqa: PLC0415
    from app.flows import artifacts as _art_mod  # noqa: PLC0415

    try:
        import pandas as pd  # noqa: PLC0415
    except ImportError:
        pytest.skip("pandas not available")

    art_store = _mem_store()
    # DataFrame with 10 000 int64 rows × 2 cols ≈ 160 000+ bytes; cap at 1 000.
    big_df = pd.DataFrame({"a": range(10_000), "b": range(10_000)})

    with unittest.mock.patch.object(_art_mod, "_ARTIFACT_MAX_BYTES", 1_000):
        with pytest.raises(ValueError, match="pre-serialisation size estimate|exceeds the maximum"):
            put_artifact(big_df, kind="pickle", org_id=ORG_A, store=art_store)

    # A small DataFrame under the cap must succeed.
    small_df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    with unittest.mock.patch.object(_art_mod, "_ARTIFACT_MAX_BYTES", 500 * 1024 * 1024):
        handle = put_artifact(small_df, kind="pickle", org_id=ORG_A, store=art_store)
        assert is_handle(handle)


# ---------------------------------------------------------------------------
# 17–19. HMAC integrity — tampered blob, round-trip, missing signature
# ---------------------------------------------------------------------------


def test_hmac_tampered_blob_rejected():
    """A blob tampered after upload must be rejected before pickle.loads.

    SECURITY (MED RCE-surface): without HMAC, an attacker who can write to
    the object store can supply a crafted pickle payload that executes
    arbitrary code on deserialization.  With HMAC the verify step raises
    ValueError before _deserialise is ever called.
    """
    art_store = _mem_store()
    obj = {"safe": "payload"}
    handle = put_artifact(obj, kind="pickle", org_id=ORG_A, store=art_store)

    artifact_id = handle["artifact_id"]
    key = f"orgs/{ORG_A}/artifacts/{artifact_id}"

    # Directly corrupt the stored bytes (simulates attacker write to object store).
    original_blob = art_store._blobs[key]
    # Flip a byte in the payload region (after the 71-byte NUBI1:<64hex>: header).
    tampered = bytearray(original_blob)
    tampered[-1] ^= 0xFF  # flip last byte
    art_store._blobs[key] = bytes(tampered)

    # get_artifact must detect the tampering and refuse to deserialise.
    with pytest.raises(ValueError, match="HMAC mismatch|integrity check failed"):
        get_artifact(handle, org_id=ORG_A, store=art_store)


def test_hmac_legitimate_round_trip():
    """A legitimately-written artifact survives the HMAC check and round-trips.

    Covers all four supported kinds.
    """
    for kind, obj in [
        ("pickle", {"nested": [1, 2, 3]}),
        ("json", {"scores": [0.1, 0.2]}),
        ("bytes", b"\x00\x01\x02\x03"),
    ]:
        art_store = _mem_store()
        handle = put_artifact(obj, kind=kind, org_id=ORG_A, store=art_store)
        loaded = get_artifact(handle, org_id=ORG_A, store=art_store)
        assert loaded == obj, f"Round-trip failed for kind={kind!r}: {loaded!r} != {obj!r}"


def test_hmac_missing_signature_rejected():
    """A blob with no HMAC envelope (e.g. legacy / externally-written) is rejected.

    This prevents an attacker from bypassing the HMAC by writing a raw pickle
    blob directly to the object store without the NUBI1: envelope.
    """
    art_store = _mem_store()
    # Write a raw pickle blob directly — no HMAC envelope.
    raw_pickle = pickle.dumps({"evil": "payload"})
    artifact_id = "unsigned-artifact"
    key = f"orgs/{ORG_A}/artifacts/{artifact_id}"
    art_store._blobs[key] = raw_pickle

    # Craft a handle that points at the unsigned blob.
    handle = make_handle(
        artifact_id=artifact_id,
        kind="pickle",
        uri=f"mem://{key}",
        org_id=ORG_A,
        produced_by_run=None,
    )

    with pytest.raises(ValueError, match="missing HMAC envelope|integrity check failed"):
        get_artifact(handle, org_id=ORG_A, store=art_store)


def test_oversized_ndarray_pre_serialise_cap_disabled():
    """When cap is disabled (=0), even a large ndarray must pass through."""
    import unittest.mock  # noqa: PLC0415
    from app.flows import artifacts as _art_mod  # noqa: PLC0415

    try:
        import numpy as np  # noqa: PLC0415
    except ImportError:
        pytest.skip("numpy not available")

    art_store = _mem_store()
    arr = np.zeros(1_000, dtype=np.float64)

    with unittest.mock.patch.object(_art_mod, "_ARTIFACT_MAX_BYTES", 0):
        # Cap disabled — must not raise.
        handle = put_artifact(arr, kind="pickle", org_id=ORG_A, store=art_store)
        assert is_handle(handle)
