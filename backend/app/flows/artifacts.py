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

import hashlib
import hmac
import io
import json
import logging
import os
import pickle
import re
import threading
import uuid
import warnings
from datetime import datetime, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Size cap for artifact uploads (protects object-store from runaway writes).
# Set NUBI_ARTIFACT_MAX_BYTES=0 to disable (not recommended in production).
# Default: 500 MB.
# ---------------------------------------------------------------------------

_ARTIFACT_MAX_BYTES: int = int(os.environ.get("NUBI_ARTIFACT_MAX_BYTES", 500 * 1024 * 1024))

# ---------------------------------------------------------------------------
# Per-run artifact count cap (MED disk unbounded fix).
# Keyed by run_id; in-process/best-effort only (no DB migration needed).
# Set NUBI_MAX_ARTIFACTS_PER_RUN=0 to disable (not recommended in production).
# Default: 200 artifacts per run.
# ---------------------------------------------------------------------------

_ARTIFACT_MAX_PER_RUN: int = int(os.environ.get("NUBI_MAX_ARTIFACTS_PER_RUN", 200))

# Thread-safe counter: run_id -> int
_run_artifact_counts: dict[str, int] = {}
_run_artifact_counts_lock = threading.Lock()

# Thread-safe handle registry: run_id -> list[(artifact_id, org_id)]
# Populated by put_artifact so evict_run_artifacts can delete each blob.
_run_artifact_handles: dict[str, list[tuple[str, str]]] = {}
_run_artifact_handles_lock = threading.Lock()


# ---------------------------------------------------------------------------
# HMAC integrity protection for stored artifact blobs
#
# Every blob written to the object store is prefixed with a compact envelope:
#
#   b"NUBI1:" + <hmac-sha256-hex-64-chars> + b":" + <payload>
#
# The HMAC is computed over the raw *payload* bytes using a secret key
# resolved from the environment (NUBI_ARTIFACT_HMAC_KEY, or NUBI_SECRET_KEY
# as a fallback).  On get_artifact the envelope is parsed and the HMAC is
# verified *before* any pickle/joblib deserialisation occurs so a forged or
# tampered blob can never trigger arbitrary code execution.
#
# Key resolution order:
#   1. NUBI_ARTIFACT_HMAC_KEY   (dedicated, highest priority)
#   2. NUBI_SECRET_KEY          (shared app secret, dev fallback)
#   3. A hard-coded dev sentinel (local dev only — logs a warning)
# ---------------------------------------------------------------------------

_HMAC_ENVELOPE_PREFIX = b"NUBI1:"
_HMAC_DIGEST_LEN = 64  # hex chars for SHA-256

# ---------------------------------------------------------------------------
# Org-bound HMAC envelope (NUBI2)
#
# SECURITY (MED cross-org): the original NUBI1 envelope signed only the raw
# payload bytes.  Because the HMAC key is shared process-wide, a blob signed
# for org_A verifies identically when fetched as org_B — so a writer-crafted
# handle that normalises to ANOTHER org's storage key (path traversal) would
# still pass HMAC verification, enabling cross-org reads / pickle RCE.
#
# NUBI2 binds the signature to the owning org by computing the HMAC over
# ``org_id_utf8 + b"\\x00" + payload``.  Verification REQUIRES the caller to
# pass the org_id it expects the blob to belong to; a blob signed for org_Y
# can never verify when fetched as org_X.
#
# NUBI1 (legacy / org-less) signing+verification is retained ONLY for callers
# that pass ``org_id=None`` (internal helpers / tests that exercise raw
# integrity without an org context).  All real artifact paths pass an org.
# ---------------------------------------------------------------------------

_HMAC_ENVELOPE_PREFIX_V2 = b"NUBI2:"

# A bare UUID (uuid4) string — the only shape a real artifact_id can take.
# Used to reject writer-crafted ids such as ``../../other_org/artifacts/<uuid>``
# BEFORE they are ever interpolated into a storage key or filesystem path.
_UUID_RE = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)


def is_valid_artifact_id(artifact_id: Any) -> bool:
    """Return True iff *artifact_id* is a bare UUID string.

    Real artifact_ids are always ``uuid.uuid4()`` strings.  Anything else
    (path separators, ``..`` traversal, empty, non-str) is rejected so a
    crafted handle cannot escape the org-namespaced storage key / spool dir.
    """
    return isinstance(artifact_id, str) and bool(_UUID_RE.match(artifact_id))


def _get_hmac_key() -> bytes:
    """Return the HMAC signing key as bytes.

    Precedence: NUBI_ARTIFACT_HMAC_KEY > NUBI_SECRET_KEY > dev sentinel.
    The dev sentinel is only used when neither env var is set AND the
    environment is NOT production.

    In production (ENV=production) with no real key configured this function
    raises ``RuntimeError`` (fail-closed) rather than using the insecure
    dev sentinel — an HMAC signed with a known, public sentinel key is
    forgeable, which defeats the pickle-RCE protection entirely.
    """
    # Strip whitespace before truthy-testing so that ENV vars set to blank or
    # whitespace-only values (e.g. NUBI_ARTIFACT_HMAC_KEY="  ") are treated as
    # absent rather than being used as a known-weak signing key.
    _raw_hmac = os.environ.get("NUBI_ARTIFACT_HMAC_KEY", "").strip()
    _raw_secret = os.environ.get("NUBI_SECRET_KEY", "").strip()
    key: str = _raw_hmac or _raw_secret
    if key:
        return key.encode("utf-8")
    # No real key configured.  In any named/deployed environment this is a
    # hard failure — using a known sentinel key allows anyone to forge valid
    # HMAC signatures and trigger pickle RCE via a crafted blob in the object
    # store.  Only explicitly-named local dev / CI / test environments are safe
    # to fall through to the insecure sentinel.  An UNSET ENV (empty string)
    # and a BLANK/whitespace-only ENV both normalise to '' after .strip(), which
    # is NOT in the allowlist: a docker/helm deployment that forgets to set ENV
    # must not silently fall back to the public sentinel — it must fail closed.
    # Environments where the dev sentinel is ALWAYS safe (no extra checks needed).
    _UNCONDITIONAL_DEV_ALLOWLIST = {"dev", "development", "ci"}
    # Environments where the sentinel is only safe when actually running under
    # pytest — a real server (e.g. CD staging) may set ENV=test, which would
    # expose the publicly-known sentinel key and allow pickle-RCE via a crafted
    # blob.  Gate these on the presence of pytest runtime signals.
    _PYTEST_GATED_ALLOWLIST = {"test", "testing"}
    env = os.environ.get("ENV", "").strip().lower()
    if env in _UNCONDITIONAL_DEV_ALLOWLIST:
        pass  # fall through to sentinel
    elif env in _PYTEST_GATED_ALLOWLIST:
        # Only allow the sentinel when we are genuinely running under pytest.
        # Two reliable signals: the env var pytest sets on every test item, and
        # the presence of the 'pytest' module in sys.modules (set at collection).
        import sys  # noqa: PLC0415
        _under_pytest = (
            "PYTEST_CURRENT_TEST" in os.environ
            or "pytest" in sys.modules
        )
        if not _under_pytest:
            raise RuntimeError(
                f"Artifact HMAC key is not configured (ENV={env!r}) and the "
                "process does not appear to be running under pytest. "
                "Set NUBI_ARTIFACT_HMAC_KEY (or NUBI_SECRET_KEY) before "
                "starting the server. Refusing to sign/verify artifacts with "
                "an insecure dev-only sentinel key — a staging/CD server with "
                "ENV=test and no key set is exploitable for pickle RCE."
            )
        # Under pytest with ENV=test/testing: fall through to sentinel.
    else:
        raise RuntimeError(
            f"Artifact HMAC key is not configured (ENV={env!r}). "
            "Set NUBI_ARTIFACT_HMAC_KEY (or NUBI_SECRET_KEY) before starting "
            "the server. Refusing to sign/verify artifacts with an insecure "
            "dev-only sentinel key. "
            "For local development set ENV=dev (or test/ci) in your environment."
        )
    # Dev/test sentinel — not secret, but safe for local development and CI.
    import warnings  # noqa: PLC0415
    warnings.warn(
        "NUBI_ARTIFACT_HMAC_KEY (or NUBI_SECRET_KEY) is not set. "
        "Artifact HMAC will use an insecure dev-only key. "
        "Set NUBI_ARTIFACT_HMAC_KEY in production.",
        stacklevel=3,
    )
    return b"nubi-dev-hmac-sentinel-NOT-FOR-PRODUCTION"


def _hmac_message(org_id: str | None, payload: bytes) -> bytes:
    """Return the byte string the HMAC is computed over.

    When *org_id* is provided the signature is bound to the org
    (``org_id_utf8 + b"\\x00" + payload``) so a blob signed for one org can
    never verify under another (NUBI2).  When *org_id* is ``None`` the legacy
    org-less message (payload only) is used (NUBI1).
    """
    if org_id is None:
        return payload
    return org_id.encode("utf-8") + b"\x00" + payload


def _hmac_sign(payload: bytes, org_id: str | None = None) -> bytes:
    """Return the full HMAC envelope for *payload*.

    When *org_id* is given the org-bound NUBI2 envelope is produced; otherwise
    the legacy org-less NUBI1 envelope is produced.  Both share the layout
    ``<prefix><64-hex-digest>:<payload>``.
    """
    key = _get_hmac_key()
    message = _hmac_message(org_id, payload)
    digest = hmac.new(key, message, hashlib.sha256).hexdigest().encode("ascii")
    prefix = _HMAC_ENVELOPE_PREFIX_V2 if org_id is not None else _HMAC_ENVELOPE_PREFIX
    return prefix + digest + b":" + payload


def _hmac_verify_and_strip(blob: bytes, org_id: str | None = None) -> bytes:
    """Verify the HMAC envelope and return the inner payload bytes.

    Parameters
    ----------
    blob:
        The stored blob (envelope + payload).
    org_id:
        The org the caller expects this blob to belong to.  When provided the
        blob MUST carry the org-bound NUBI2 envelope AND its signature must
        verify against *org_id* — a blob signed for a different org (or with
        the legacy org-less NUBI1 envelope) is rejected.  When ``None`` the
        legacy NUBI1 (org-less) envelope is required.

    Raises
    ------
    ValueError
        When the blob lacks the expected envelope (missing/legacy signature)
        or the HMAC does not match (tampered blob / wrong org / wrong key).
        Any of these is an integrity failure — the caller must not proceed to
        deserialise.
    """
    expected_prefix = _HMAC_ENVELOPE_PREFIX_V2 if org_id is not None else _HMAC_ENVELOPE_PREFIX
    if not blob.startswith(expected_prefix):
        raise ValueError(
            "Artifact integrity check failed: missing HMAC envelope. "
            "The blob was not produced by this system, was signed for a "
            "different org, or the signature was stripped."
        )
    # Structure: b"<prefix><64-hex-chars>:<payload>"
    rest = blob[len(expected_prefix):]  # "<64-hex-chars>:<payload>"
    sep_pos = _HMAC_DIGEST_LEN  # the colon is right after the 64 hex chars
    if len(rest) <= sep_pos or rest[sep_pos:sep_pos + 1] != b":":
        raise ValueError(
            "Artifact integrity check failed: malformed HMAC envelope."
        )
    stored_digest = rest[:sep_pos]  # 64 ascii bytes
    payload = rest[sep_pos + 1:]    # the actual serialised object bytes

    key = _get_hmac_key()
    message = _hmac_message(org_id, payload)
    expected_digest = hmac.new(key, message, hashlib.sha256).hexdigest().encode("ascii")

    if not hmac.compare_digest(stored_digest, expected_digest):
        raise ValueError(
            "Artifact integrity check failed: HMAC mismatch. "
            "The blob may have been tampered with, signed for a different org, "
            "or produced with a different key."
        )
    return payload


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


class _LimitedWriter:
    """A file-like writer that aborts mid-serialization when the byte cap is hit.

    Used as the target for ``pickle.Pickler`` so that oversized objects are
    rejected *during* serialization rather than *after* a full
    ``pickle.dumps()`` call has already built the entire oversized buffer in
    memory (2× peak-memory + wasted work).

    Parameters
    ----------
    max_bytes:
        Hard ceiling on the total number of bytes that may be written.
        When a ``write()`` call would push the running total past this
        ceiling the writer raises ``ValueError`` immediately, aborting
        the pickle operation mid-stream.  Set to ``0`` to disable the cap.
    """

    __slots__ = ("_max_bytes", "_written", "_buf")

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._written = 0
        self._buf = io.BytesIO()

    def write(self, data: bytes) -> int:
        n = len(data)
        self._written += n
        if self._max_bytes > 0 and self._written > self._max_bytes:
            raise ValueError(
                f"Artifact size exceeds the maximum allowed {self._max_bytes:,} bytes "
                "(NUBI_ARTIFACT_MAX_BYTES) during serialization. The object was too "
                "large; reduce the artifact size or raise the limit."
            )
        self._buf.write(data)
        return n

    def getvalue(self) -> bytes:
        return self._buf.getvalue()


def _serialise(obj: Any, kind: str) -> bytes:
    """Serialise *obj* according to *kind*.

    Raises
    ------
    ValueError
        If *kind* is unsupported or if the object exceeds the byte cap
        *during* pickle serialization (mid-stream abort via _LimitedWriter).
    ImportError
        If ``joblib`` is requested but not installed.
    """
    if kind == "pickle":
        # Route through _LimitedWriter so we abort mid-serialization if the
        # object exceeds the cap, rather than building a full oversized buffer
        # in memory and only then checking the size (2× OOM + wasted work).
        writer = _LimitedWriter(_ARTIFACT_MAX_BYTES)
        pickle.Pickler(writer, protocol=pickle.HIGHEST_PROTOCOL).dump(obj)
        return writer.getvalue()
    if kind == "joblib":
        try:
            import joblib  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "joblib is required for artifact kind='joblib'. "
                "Install it with: pip install joblib"
            ) from exc
        # Route through _LimitedWriter so we abort mid-serialization if the
        # object exceeds the cap, mirroring the pickle path.  joblib.dump calls
        # .write() on the target, so _LimitedWriter is a drop-in replacement
        # for io.BytesIO() here.
        writer = _LimitedWriter(_ARTIFACT_MAX_BYTES)
        joblib.dump(obj, writer)
        return writer.getvalue()
    if kind == "bytes":
        if not isinstance(obj, (bytes, bytearray)):
            raise TypeError(
                f"Artifact kind='bytes' requires a bytes/bytearray object, "
                f"got {type(obj).__name__}."
            )
        # [LOW memory/robustness] Check length BEFORE bytes(obj) copy so we
        # never allocate a full oversized buffer only to discard it.  bytes()
        # on a large bytearray copies the full payload into a new object
        # (peak RSS = 2× size); reject early when the cap is active.
        if _ARTIFACT_MAX_BYTES > 0 and len(obj) > _ARTIFACT_MAX_BYTES:
            raise ValueError(
                f"Artifact size {len(obj):,} bytes exceeds the maximum allowed "
                f"{_ARTIFACT_MAX_BYTES:,} bytes (NUBI_ARTIFACT_MAX_BYTES). "
                "Reduce the artifact size or raise the limit."
            )
        return bytes(obj)
    if kind == "json":
        # [MED memory] Stream JSON into a _LimitedWriter via
        # json.JSONEncoder().iterencode(obj) so we abort mid-stream the moment
        # the running byte count exceeds the cap — identical strategy to pickle
        # and joblib.  The old approach (json.dumps(obj).encode()) built the
        # full JSON string in one shot and only checked the size AFTER the
        # entire encoded object was already in memory (2× peak: Python string +
        # UTF-8 bytes object).  With iterencode each chunk is written to
        # _LimitedWriter, which raises as soon as the running total exceeds the
        # cap so no full oversized buffer is ever constructed.
        #
        # Circular-reference / non-serializable errors are still caught and
        # wrapped into a clear ValueError (same contract as before).
        if _ARTIFACT_MAX_BYTES > 0:
            _pre = _estimate_obj_bytes(obj)
            if _pre is not None and _pre > _ARTIFACT_MAX_BYTES:
                raise ValueError(
                    f"Artifact pre-serialisation size estimate {_pre:,} bytes "
                    f"exceeds the maximum allowed {_ARTIFACT_MAX_BYTES:,} bytes "
                    "(NUBI_ARTIFACT_MAX_BYTES). Reduce the artifact size or raise "
                    "the limit."
                )
        writer = _LimitedWriter(_ARTIFACT_MAX_BYTES)
        try:
            for chunk in json.JSONEncoder().iterencode(obj):
                writer.write(chunk.encode("utf-8"))
        except ValueError as exc:
            # Two distinct sources of ValueError:
            #   1. _LimitedWriter.write() — cap exceeded; message contains
            #      "exceeded" / "NUBI_ARTIFACT_MAX_BYTES".  Re-raise verbatim.
            #   2. json.JSONEncoder.iterencode() — circular reference detected.
            #      Wrap into a friendlier message (same as the old dumps path).
            if "NUBI_ARTIFACT_MAX_BYTES" in str(exc):
                raise  # cap error — leave the message intact
            raise ValueError(
                f"Artifact kind='json': object is not JSON-serializable "
                f"(circular reference or unsupported type). "
                f"Original error: {exc}"
            ) from exc
        except TypeError as exc:
            raise ValueError(
                f"Artifact kind='json': object is not JSON-serializable "
                f"(circular reference or unsupported type). "
                f"Original error: {exc}"
            ) from exc
        return writer.getvalue()
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


_IN_MEMORY_DEFAULT_MAX_TOTAL_BYTES: int = 2 * 1024 * 1024 * 1024  # 2 GiB


class InMemoryArtifactStore:
    """Dict-backed artifact store for tests (no real I/O).

    Thread-safe enough for sequential test usage.  Not async — all methods
    are synchronous (same contract as ``StorageClient``).

    Parameters
    ----------
    max_total_bytes:
        Total in-memory byte ceiling across *all* blobs stored in this
        instance.  Defaults to 2 GiB.  Set to ``0`` to disable the cap
        (not recommended outside of targeted unit tests).  When a call to
        ``upload`` would push the cumulative total past this ceiling,
        ``MemoryError`` is raised and the blob is NOT stored.
    """

    def __init__(self, max_total_bytes: int = _IN_MEMORY_DEFAULT_MAX_TOTAL_BYTES) -> None:
        self._blobs: dict[str, bytes] = {}  # key → raw bytes
        self._total_bytes: int = 0
        self._max_total_bytes: int = max_total_bytes

    def upload(self, artifact_id: str, org_id: str, data: bytes) -> str:
        """Store *data* under an org-namespaced key; return its logical URI.

        Raises
        ------
        MemoryError
            When the total stored bytes (including *data*) would exceed
            ``max_total_bytes`` (and the cap is active, i.e. > 0).
        """
        key = f"orgs/{org_id}/artifacts/{artifact_id}"
        incoming = len(data)
        # Account for replacement: if the key already exists subtract its
        # current size before adding the new one so we don't double-count.
        existing = len(self._blobs[key]) if key in self._blobs else 0
        new_total = self._total_bytes - existing + incoming
        if self._max_total_bytes > 0 and new_total > self._max_total_bytes:
            raise MemoryError(
                f"InMemoryArtifactStore total-bytes ceiling exceeded: "
                f"storing {incoming:,} bytes would bring the total to "
                f"{new_total:,} bytes, which exceeds the configured limit of "
                f"{self._max_total_bytes:,} bytes. "
                "Use a smaller artifact, raise max_total_bytes, or call "
                "clear() to reset the store."
            )
        self._blobs[key] = data
        self._total_bytes = new_total
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

    def delete(self, artifact_id: str, org_id: str) -> None:
        """Delete the artifact blob for *artifact_id* owned by *org_id*.

        Silently does nothing when the artifact does not exist (idempotent)
        so callers can safely call delete() in finally-blocks without catching
        ``FileNotFoundError``.
        """
        key = f"orgs/{org_id}/artifacts/{artifact_id}"
        if key in self._blobs:
            self._total_bytes -= len(self._blobs.pop(key))

    def clear(self) -> None:
        """Remove all stored blobs and reset the byte counter.

        Intended for test teardown so a shared store instance can be reused
        across tests without bleed-over.
        """
        self._blobs.clear()
        self._total_bytes = 0


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
        """Resolve base URI from settings; fall back to a temp directory.

        In production (ENV not in dev/development/ci/test/testing) raises
        ``RuntimeError`` instead of silently using /tmp — mirroring the
        HMAC-key production guard so misconfigured deployments fail closed
        rather than quietly accumulating unbounded blobs in a tmp dir with
        NO TTL, eviction, or quota guard.
        """
        try:
            from app.config import get_settings  # noqa: PLC0415

            uri = getattr(get_settings(), "ARTIFACTS_BASE_URI", None)
            if uri and str(uri).strip():
                return str(uri).strip().rstrip("/")
        except Exception:  # noqa: BLE001
            pass

        # No URI configured.  Mirror the HMAC-key guard: fail-closed in
        # production; allow the tmpdir fallback only in dev/test/ci.
        _UNCONDITIONAL_DEV_ALLOWLIST = {"dev", "development", "ci"}
        _PYTEST_GATED_ALLOWLIST = {"test", "testing"}
        env = os.environ.get("ENV", "").strip().lower()

        if env not in _UNCONDITIONAL_DEV_ALLOWLIST and env not in _PYTEST_GATED_ALLOWLIST:
            raise RuntimeError(
                "ARTIFACTS_BASE_URI is not configured and the environment "
                f"(ENV={os.environ.get('ENV', '')!r}) is not a recognised dev/test "
                "environment. Set ARTIFACTS_BASE_URI (e.g. s3://bucket/nubi-artifacts) "
                "before starting the server. Refusing to silently accumulate artifact "
                "blobs in /tmp with no TTL, eviction, or quota guard. "
                "For local development set ENV=dev (or test/ci) in your environment."
            )

        # In pytest-gated environments (test/testing) require actual pytest signals,
        # matching the HMAC-key guard behaviour.
        if env in _PYTEST_GATED_ALLOWLIST:
            import sys  # noqa: PLC0415
            _under_pytest = (
                "PYTEST_CURRENT_TEST" in os.environ
                or "pytest" in sys.modules
            )
            if not _under_pytest:
                raise RuntimeError(
                    f"ARTIFACTS_BASE_URI is not configured (ENV={env!r}) and the "
                    "process does not appear to be running under pytest. "
                    "Set ARTIFACTS_BASE_URI before starting the server."
                )

        # Fallback: use a well-known temp dir (same across the process lifetime).
        # Warn operators so they notice this in logs — artifacts written here have
        # NO TTL, eviction, or quota guard and will grow unboundedly in production.
        import tempfile  # noqa: PLC0415

        tmp = os.path.join(tempfile.gettempdir(), "nubi-artifacts")
        os.makedirs(tmp, exist_ok=True)
        _msg = (
            "ARTIFACTS_BASE_URI is not set; artifact blobs will be written to "
            f"a local temp directory ({tmp}) with NO TTL, eviction, or quota guard. "
            "This is only safe for local development / CI. "
            "Set ARTIFACTS_BASE_URI in production (e.g. s3://bucket/nubi-artifacts)."
        )
        warnings.warn(_msg, stacklevel=4)
        logger.warning(_msg)
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

    def delete(self, artifact_id: str, org_id: str) -> None:
        """Delete the artifact blob for *artifact_id* owned by *org_id*.

        Best-effort: silently ignores ``FileNotFoundError`` / ``KeyError`` so
        callers can safely call delete() in finally-blocks.  Other storage
        errors (auth, network) are allowed to propagate.
        """
        key = self._key(artifact_id, org_id)
        client = self._client()
        try:
            client.delete(key)
        except (FileNotFoundError, KeyError):
            pass


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


def reset_run_artifact_counts() -> None:
    """Clear all per-run artifact counters.

    Intended for test teardown so a shared process can reuse run IDs without
    carrying over counts from previous tests.
    """
    with _run_artifact_counts_lock:
        _run_artifact_counts.clear()


def evict_run_artifact_count(run_id: str) -> None:
    """Remove the per-run artifact counter for *run_id* (if present).

    Called by the runtime when a flow run reaches a terminal state so that
    the ``_run_artifact_counts`` dict does not accumulate one entry per
    completed run forever in long-lived workers.

    Thread-safe (acquires ``_run_artifact_counts_lock``).  No-op when
    *run_id* is not in the dict (idempotent — safe to call multiple times).
    """
    with _run_artifact_counts_lock:
        _run_artifact_counts.pop(run_id, None)


def evict_run_artifacts(
    run_id: str,
    store: "InMemoryArtifactStore | ObjectStoreArtifactStore",
) -> None:
    """Delete every blob uploaded during *run_id* from *store*, then evict the counter.

    This is the companion cleanup to ``evict_run_artifact_count``: in addition
    to removing the count entry it physically deletes each artifact blob from
    the object store (or in-memory dict) so tmp-dir / object-store blobs do not
    accumulate forever after a run completes.

    Deletion is best-effort per handle: a failure to delete one blob is logged
    but does NOT prevent the remaining blobs from being deleted.  The counter
    entry is always popped regardless of deletion outcomes.

    Thread-safe (acquires ``_run_artifact_handles_lock`` to pop the list, then
    acquires ``_run_artifact_counts_lock`` to pop the counter).

    Parameters
    ----------
    run_id:
        The flow_run_id whose artifacts should be deleted.
    store:
        The artifact store instance that holds the blobs.
    """
    with _run_artifact_handles_lock:
        handles = _run_artifact_handles.pop(run_id, [])

    for artifact_id, org_id in handles:
        try:
            store.delete(artifact_id, org_id)
        except Exception:  # noqa: BLE001 — best-effort per handle
            logger.debug(
                "evict_run_artifacts: failed to delete artifact %s for org %s run %s",
                artifact_id, org_id, run_id, exc_info=True,
            )

    evict_run_artifact_count(run_id)


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

    # Best-effort per-run artifact count cap (MED disk: unbounded tmpdir).
    # Keyed by flow_run_id; in-process only (survives only for this process
    # lifetime).  No DB migration required.  Set NUBI_MAX_ARTIFACTS_PER_RUN=0
    # to disable.
    if flow_run_id and _ARTIFACT_MAX_PER_RUN > 0:
        with _run_artifact_counts_lock:
            current_count = _run_artifact_counts.get(flow_run_id, 0)
            if current_count >= _ARTIFACT_MAX_PER_RUN:
                raise ValueError(
                    f"Artifact count limit reached for run {flow_run_id!r}: "
                    f"{current_count} artifacts already stored "
                    f"(NUBI_MAX_ARTIFACTS_PER_RUN={_ARTIFACT_MAX_PER_RUN}). "
                    "Raise NUBI_MAX_ARTIFACTS_PER_RUN or reduce the number of "
                    "artifacts produced per run."
                )
            _run_artifact_counts[flow_run_id] = current_count + 1

    # Sign the serialised bytes before uploading so any tampering can be
    # detected at load time before pickle/joblib deserialisation.  The
    # signature is BOUND to org_id so a blob signed for this org can never
    # verify when fetched as another org (closes the cross-org bypass).
    signed_data = _hmac_sign(data, org_id=str(org_id))

    _store = store or get_artifact_store()
    uri = _store.upload(artifact_id, org_id, signed_data)

    # Register the (artifact_id, org_id) pair under this run so
    # evict_run_artifacts can delete the blob when the run finalises.
    if flow_run_id:
        with _run_artifact_handles_lock:
            _run_artifact_handles.setdefault(flow_run_id, []).append(
                (artifact_id, str(org_id))
            )

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

    # SECURITY (path traversal / cross-org): a real artifact_id is always a
    # bare uuid4.  A writer-crafted handle id such as
    # ``../../other_org/artifacts/<uuid>`` would, after the (passing) org
    # check above, normalise to another org's storage key on a file:// store.
    # Reject anything that is not a bare UUID before it is used in any key.
    if not is_valid_artifact_id(artifact_id):
        raise ValueError(
            f"Invalid artifact_id {artifact_id!r}: expected a bare UUID. "
            "Refusing to fetch a non-canonical artifact id (possible path "
            "traversal / cross-org access attempt)."
        )

    _store = store or get_artifact_store()
    blob = _store.download(artifact_id, org_id)

    # Verify HMAC *before* deserialising — rejects tampered/forged blobs so a
    # compromised object store cannot trigger pickle RCE.  Bind verification to
    # the requesting org so a blob signed for a different org cannot verify.
    data = _hmac_verify_and_strip(blob, org_id=str(org_id))

    return _deserialise(data, kind)
