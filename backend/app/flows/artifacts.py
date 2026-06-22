"""B4 — Typed artifact channel for Flow cells.

An *artifact* is a heavyweight, non-row object (trained model, serialised
closure, binary blob, JSON config) that cannot cross a cell boundary through
the existing rows/Arrow channel.  This module provides:

ArtifactHandle
    Lightweight, JSON-serialisable descriptor for a stored artifact.  A cell
    that produces an artifact serialises the object, uploads it to the object
    store, and returns an ``ArtifactHandle`` dict in its result.  A downstream
    cell that needs the artifact calls ``ctx.get_artifact(handle)`` to
    download and deserialise it.

ArtifactStore / InMemoryArtifactStore / ObjectStoreArtifactStore
    Two implementations of the same interface:
    - ``InMemoryArtifactStore``     — dict-backed; used by tests (no I/O).
    - ``ObjectStoreArtifactStore``  — writes blobs to the configured
      ``ARTIFACTS_BASE_URI`` (``file://``, ``s3://``, ``gs://``, ``az://``).
      Falls back to a temporary local directory when no URI is configured
      (development / CI).

get_artifact_store() / set_artifact_store()
    Singleton provider pattern mirroring ``app.flows.store``.

Security
--------
- Artifacts are namespaced under ``orgs/<org_id>/`` inside the store so a
  read/write in one tenant NEVER touches another tenant's prefix.
- ``get_artifact`` refuses to deserialise across org boundaries: the
  ``org_id`` on the handle MUST match the executing org or the call raises
  ``PermissionError``.
- Only ``pickle``, ``joblib``, ``bytes``, and ``json`` kinds are supported.
  ``pickle``/``joblib`` are deserialised with the standard library; callers
  must trust that the producing cell is from the same codebase (flow authors
  have full Python execution already).
- The module never exposes URI internals to user code.  Handles contain an
  ``artifact_id`` (UUID) and a ``kind``; the physical ``uri`` is stored in the
  handle dict but is intended for the server, not for untrusted display.

Artifact kinds
--------------
- ``pickle``  — serialised with ``pickle.dumps``; loaded with ``pickle.loads``.
- ``joblib``  — serialised with ``joblib.dump``; loaded with ``joblib.load``
               (better for sklearn/numpy large arrays).
- ``bytes``   — raw bytes; returned as-is on load.
- ``json``    — serialised with ``json.dumps``; loaded with ``json.loads``.
"""

from __future__ import annotations

import io
import json
import os
import pickle
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Size cap for artifact uploads (protects object-store from runaway writes).
# Set NUBI_ARTIFACT_MAX_BYTES=0 to disable (not recommended in production).
# Default: 500 MB.
# ---------------------------------------------------------------------------

_ARTIFACT_MAX_BYTES: int = int(os.environ.get("NUBI_ARTIFACT_MAX_BYTES", 500 * 1024 * 1024))


# ---------------------------------------------------------------------------
# ArtifactHandle — the lightweight descriptor that crosses cell boundaries
# ---------------------------------------------------------------------------

# Supported serialisation kinds.
ArtifactKind = Literal["pickle", "joblib", "bytes", "json"]

# Shape of the handle dict stored in task_run results / passed between cells.
# JSON-serialisable so it survives the rows channel / jsonb column.
ArtifactHandle = dict[str, Any]


def make_handle(
    artifact_id: str,
    kind: str,
    uri: str,
    org_id: str,
    produced_by_run: str | None,
    name: str | None = None,
    meta: dict[str, Any] | None = None,
) -> ArtifactHandle:
    """Return a canonical ArtifactHandle dict.

    Parameters
    ----------
    artifact_id:
        UUID string identifying this artifact.
    kind:
        One of ``'pickle'``, ``'joblib'``, ``'bytes'``, ``'json'``.
    uri:
        Physical URI of the uploaded blob (org-namespaced).
    org_id:
        The org that owns this artifact (used for cross-org isolation checks).
    produced_by_run:
        The ``flow_run_id`` that produced this artifact (for lineage).
    name:
        Optional human-readable name (e.g. ``"trained_model"``).
    meta:
        Optional free-form metadata (e.g. ``{"rows": 12000, "algo": "lgbm"}``).
    """
    return {
        "artifact_id": artifact_id,
        "kind": kind,
        "uri": uri,
        "org_id": org_id,
        "produced_by_run": produced_by_run,
        "name": name,
        "meta": meta,
        "__type__": "artifact_handle",
    }


def is_handle(obj: Any) -> bool:
    """Return ``True`` if *obj* looks like an ``ArtifactHandle`` dict."""
    return (
        isinstance(obj, dict)
        and obj.get("__type__") == "artifact_handle"
        and "artifact_id" in obj
        and "kind" in obj
    )


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _estimate_obj_bytes(obj: Any) -> int | None:
    """Return a best-effort in-memory byte size estimate for *obj*, or ``None``.

    Only handles types where we can compute a cheap, reliable upper-bound
    without serialising the object.  Returns ``None`` for all other types so
    the caller can fall through to the post-serialise cap.

    Supported types
    ---------------
    - ``numpy.ndarray``     — ``ndarray.nbytes``
    - ``pandas.DataFrame``  — ``df.memory_usage(deep=True).sum()``
    - ``pandas.Series``     — ``series.memory_usage(deep=True)``
    """
    # numpy — available without pandas
    try:
        import numpy as np  # noqa: PLC0415

        if isinstance(obj, np.ndarray):
            return int(obj.nbytes)
    except ImportError:
        pass

    # pandas DataFrame / Series
    try:
        import pandas as pd  # noqa: PLC0415

        if isinstance(obj, pd.DataFrame):
            return int(obj.memory_usage(deep=True).sum())
        if isinstance(obj, pd.Series):
            return int(obj.memory_usage(deep=True))
    except ImportError:
        pass

    return None


def _serialise(obj: Any, kind: str) -> bytes:
    """Serialise *obj* according to *kind*.

    Raises
    ------
    ValueError
        If *kind* is unsupported.
    ImportError
        If ``joblib`` is requested but not installed.
    """
    if kind == "pickle":
        return pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    if kind == "joblib":
        try:
            import joblib  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "joblib is required for artifact kind='joblib'. "
                "Install it with: pip install joblib"
            ) from exc
        buf = io.BytesIO()
        joblib.dump(obj, buf)
        return buf.getvalue()
    if kind == "bytes":
        if not isinstance(obj, (bytes, bytearray)):
            raise TypeError(
                f"Artifact kind='bytes' requires a bytes/bytearray object, "
                f"got {type(obj).__name__}."
            )
        return bytes(obj)
    if kind == "json":
        return json.dumps(obj).encode("utf-8")
    raise ValueError(
        f"Unsupported artifact kind {kind!r}. "
        "Supported: 'pickle', 'joblib', 'bytes', 'json'."
    )


def _deserialise(data: bytes, kind: str) -> Any:
    """Deserialise *data* according to *kind*."""
    if kind == "pickle":
        return pickle.loads(data)  # noqa: S301 — caller trusts the artifact
    if kind == "joblib":
        try:
            import joblib  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "joblib is required for artifact kind='joblib'. "
                "Install it with: pip install joblib"
            ) from exc
        return joblib.load(io.BytesIO(data))
    if kind == "bytes":
        return data
    if kind == "json":
        return json.loads(data.decode("utf-8"))
    raise ValueError(f"Unsupported artifact kind {kind!r}.")


# ---------------------------------------------------------------------------
# ArtifactStore interface + implementations
# ---------------------------------------------------------------------------


class InMemoryArtifactStore:
    """Dict-backed artifact store for tests (no real I/O).

    Thread-safe enough for sequential test usage.  Not async — all methods
    are synchronous (same contract as ``StorageClient``).
    """

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}  # artifact_id → raw bytes

    def upload(self, artifact_id: str, org_id: str, data: bytes) -> str:
        """Store *data* under an org-namespaced key; return its logical URI."""
        key = f"orgs/{org_id}/artifacts/{artifact_id}"
        self._blobs[key] = data
        return f"mem://{key}"

    def download(self, artifact_id: str, org_id: str) -> bytes:
        """Retrieve bytes for *artifact_id* owned by *org_id*.

        Raises
        ------
        FileNotFoundError
            If no artifact with this id exists for the org.
        """
        key = f"orgs/{org_id}/artifacts/{artifact_id}"
        if key not in self._blobs:
            raise FileNotFoundError(
                f"Artifact {artifact_id!r} not found for org {org_id!r}."
            )
        return self._blobs[key]

    def exists(self, artifact_id: str, org_id: str) -> bool:
        """Return True if the artifact exists for this org."""
        key = f"orgs/{org_id}/artifacts/{artifact_id}"
        return key in self._blobs


class ObjectStoreArtifactStore:
    """Artifact store backed by the existing ``app.storage`` layer.

    Uses the ``ARTIFACTS_BASE_URI`` setting (e.g. ``s3://my-bucket/nubi`` or
    ``file:///tmp/nubi-artifacts``).  When the setting is absent, falls back
    to a local temp directory so development / CI works without S3.

    All keys are org-namespaced: ``orgs/<org_id>/artifacts/<artifact_id>``.
    """

    def __init__(self, base_uri: str | None = None) -> None:
        self._base_uri = base_uri or self._default_uri()

    @staticmethod
    def _default_uri() -> str:
        """Resolve base URI from settings; fall back to a temp directory."""
        try:
            from app.config import get_settings  # noqa: PLC0415

            uri = getattr(get_settings(), "ARTIFACTS_BASE_URI", None)
            if uri and str(uri).strip():
                return str(uri).strip().rstrip("/")
        except Exception:  # noqa: BLE001
            pass
        # Fallback: use a well-known temp dir (same across the process lifetime).
        import tempfile  # noqa: PLC0415

        tmp = os.path.join(tempfile.gettempdir(), "nubi-artifacts")
        os.makedirs(tmp, exist_ok=True)
        return f"file://{tmp}"

    def _client(self) -> Any:
        from app.storage.base import get_storage_client  # noqa: PLC0415

        return get_storage_client(self._base_uri)

    def _key(self, artifact_id: str, org_id: str) -> str:
        return f"orgs/{org_id}/artifacts/{artifact_id}"

    def upload(self, artifact_id: str, org_id: str, data: bytes) -> str:
        """Upload *data* and return the full URI."""
        key = self._key(artifact_id, org_id)
        client = self._client()
        uri = client.upload_bytes(data, key)
        return uri

    def download(self, artifact_id: str, org_id: str) -> bytes:
        """Download and return bytes for the artifact."""
        key = self._key(artifact_id, org_id)
        client = self._client()
        return client.download_bytes(key)

    def exists(self, artifact_id: str, org_id: str) -> bool:
        """Return True if the artifact exists."""
        key = self._key(artifact_id, org_id)
        client = self._client()
        return client.exists(key)


# ---------------------------------------------------------------------------
# Singleton provider
# ---------------------------------------------------------------------------

_artifact_store: InMemoryArtifactStore | ObjectStoreArtifactStore | None = None


def get_artifact_store() -> InMemoryArtifactStore | ObjectStoreArtifactStore:
    """Return (or lazily create) the module-level artifact store.

    In production (no override), returns an ``ObjectStoreArtifactStore``.
    Tests inject an ``InMemoryArtifactStore`` via ``set_artifact_store``.
    """
    global _artifact_store
    if _artifact_store is None:
        _artifact_store = ObjectStoreArtifactStore()
    return _artifact_store


def set_artifact_store(
    store: InMemoryArtifactStore | ObjectStoreArtifactStore | None,
) -> None:
    """Override the module-level store singleton.

    Pass an ``InMemoryArtifactStore`` to inject a test double.
    Pass ``None`` to reset (next call creates a fresh ``ObjectStoreArtifactStore``).
    """
    global _artifact_store
    _artifact_store = store


# ---------------------------------------------------------------------------
# High-level helpers used by TaskContext
# ---------------------------------------------------------------------------


def put_artifact(
    obj: Any,
    *,
    kind: str = "pickle",
    name: str | None = None,
    org_id: str,
    flow_run_id: str | None = None,
    meta: dict[str, Any] | None = None,
    store: InMemoryArtifactStore | ObjectStoreArtifactStore | None = None,
) -> ArtifactHandle:
    """Serialise *obj* and upload it to the artifact store.

    Parameters
    ----------
    obj:
        The Python object to persist.
    kind:
        Serialisation format: ``'pickle'``, ``'joblib'``, ``'bytes'``, or ``'json'``.
    name:
        Optional human-readable name for the artifact.
    org_id:
        The owning organisation (used for storage namespacing and isolation).
    flow_run_id:
        The flow run that produced this artifact (for lineage).
    meta:
        Optional free-form metadata dict.
    store:
        Optional store override (uses the singleton when ``None``).

    Returns
    -------
    ArtifactHandle
        A lightweight dict you can return from a cell and pass to
        ``get_artifact`` in a downstream cell.
    """
    if not org_id:
        raise ValueError("org_id is required for artifact.put_artifact().")

    # [LOW resource] Best-effort pre-serialisation size estimate for known
    # large types (numpy ndarray, pandas DataFrame).  This avoids OOMing
    # inside pickle.dumps/joblib on objects we can already see are too big.
    # We only check when the cap is active to preserve the fast path for
    # small objects.  The post-serialise cap below is the definitive backstop
    # for all other types.
    if _ARTIFACT_MAX_BYTES > 0:
        _pre_size = _estimate_obj_bytes(obj)
        if _pre_size is not None and _pre_size > _ARTIFACT_MAX_BYTES:
            raise ValueError(
                f"Artifact pre-serialisation size estimate {_pre_size:,} bytes "
                f"exceeds the maximum allowed {_ARTIFACT_MAX_BYTES:,} bytes "
                "(NUBI_ARTIFACT_MAX_BYTES). Reduce the artifact size or raise "
                "the limit."
            )

    data = _serialise(obj, kind)

    # Definitive post-serialise cap — catches all other types not covered by
    # the pre-serialisation estimate above.
    if _ARTIFACT_MAX_BYTES > 0 and len(data) > _ARTIFACT_MAX_BYTES:
        raise ValueError(
            f"Artifact size {len(data):,} bytes exceeds the maximum allowed "
            f"{_ARTIFACT_MAX_BYTES:,} bytes (NUBI_ARTIFACT_MAX_BYTES). "
            "Reduce the artifact size or raise the limit."
        )

    artifact_id = str(uuid.uuid4())

    _store = store or get_artifact_store()
    uri = _store.upload(artifact_id, org_id, data)

    return make_handle(
        artifact_id=artifact_id,
        kind=kind,
        uri=uri,
        org_id=org_id,
        produced_by_run=flow_run_id,
        name=name,
        meta=meta,
    )


def get_artifact(
    handle: ArtifactHandle,
    *,
    org_id: str,
    store: InMemoryArtifactStore | ObjectStoreArtifactStore | None = None,
) -> Any:
    """Download and deserialise the artifact referenced by *handle*.

    Security
    --------
    ``handle["org_id"]`` MUST match *org_id*.  A mismatched org raises
    ``PermissionError`` so cross-tenant artefact access is impossible even
    if a handle were somehow smuggled across orgs.

    Parameters
    ----------
    handle:
        An ``ArtifactHandle`` dict returned by ``put_artifact``.
    org_id:
        The requesting organisation (must match the handle's ``org_id``).
    store:
        Optional store override.

    Returns
    -------
    Any
        The deserialised Python object.

    Raises
    ------
    PermissionError
        When the handle's ``org_id`` does not match *org_id*.
    ValueError
        When *handle* is not a valid ``ArtifactHandle``.
    """
    if not is_handle(handle):
        raise ValueError(
            "get_artifact() requires a valid ArtifactHandle dict "
            "(produced by ctx.put_artifact or put_artifact())."
        )

    handle_org = handle.get("org_id", "")
    if str(handle_org) != str(org_id):
        raise PermissionError(
            f"Artifact org isolation violation: handle belongs to org "
            f"{handle_org!r} but caller org is {org_id!r}."
        )

    artifact_id = handle["artifact_id"]
    kind = handle["kind"]

    _store = store or get_artifact_store()
    data = _store.download(artifact_id, org_id)
    return _deserialise(data, kind)
