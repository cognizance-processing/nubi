"""Production source-opener + staging-uploader for the bridge agent.

These are the real (non-test) implementations of the ``SourceOpener`` and
``StagingUploader`` protocols in :mod:`nubi_cli.bridge_agent`.  They run on the
customer machine and embody two security invariants from design §7:

* The **source** is opened with credentials resolved OUT-OF-BAND on the
  customer side (a local agent config / the customer's own keychain), NOT from
  the control plane.  The agent never receives a stored connector secret.
* The **staging grant** (presigned PUT URLs / STS token) is consumed
  per-upload, held in memory only, and never written to disk.

Streaming is bounded: a file is read + uploaded one chunk at a time, so neither
the source read nor the staging write ever buffers a whole file.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, BinaryIO, Iterable

logger = logging.getLogger("nubi.bridge")


class LocalFileSourceOpener:
    """Opens a local file-connector source for streaming read (sftp/ftp/bucket).

    The ``source`` descriptor is ``{connector_id, path, ...}`` exactly as
    delivered in the task assignment — it carries NO credentials.  This opener
    resolves the concrete connector (and its creds) from agent-side config on
    the customer machine, lists files under ``path``, and yields
    ``(rel_path, BinaryIO)`` pairs for streaming.

    The connector resolution is intentionally pluggable: a deployment wires its
    file-connector factory via :meth:`set_connector_factory`.  Absent a factory
    the opener raises a clear error rather than guessing — the agent never
    fabricates credentials.
    """

    _factory = None  # callable(source: dict) -> Iterable[tuple[str, BinaryIO]]

    @classmethod
    def set_connector_factory(cls, factory: Any) -> None:
        """Register the agent-side factory that opens a source for streaming.

        ``factory(source) -> Iterable[(rel_path, BinaryIO)]``.  Set once at
        agent install/config time; resolves connector creds locally.
        """
        cls._factory = factory

    def open_source(self, source: dict[str, Any]) -> Iterable[tuple[str, BinaryIO]]:
        if self._factory is None:
            raise RuntimeError(
                "No local file-connector factory configured. The bridge agent "
                "resolves source credentials on the customer machine; register "
                "one via LocalFileSourceOpener.set_connector_factory(...)."
            )
        return self._factory(source)


class PresignedStagingUploader:
    """Streams bytes to a staging target via a presigned PUT URL (memory-only).

    Understands the control-plane grant wire shape (``app.lakehouse.grants``)::

        {"kind": "s3_presigned",
         "uploads": {"<rel_path>": {"method": "PUT", "url": "<presigned>"}}, ...}

    The URL is read from *grant* per call and used immediately; it is never
    persisted.  Upload streams the async chunk iterator straight into the PUT
    body so the whole file is never buffered.  Returns bytes written.

    Cloud-specific grants (STS tokens / multipart) plug in by swapping this
    uploader — the agent runtime only depends on the ``StagingUploader``
    protocol.
    """

    @staticmethod
    def _url_for(grant: dict[str, Any], rel_path: str) -> str | None:
        """Extract the PUT URL for *rel_path* from a grant (real or flat shape)."""
        # Real grant shape: uploads[rel] = {"method": "PUT", "url": "…"}.
        cap = (grant.get("uploads") or {}).get(rel_path)
        if isinstance(cap, dict):
            return cap.get("url")
        if isinstance(cap, str):
            return cap
        # Forward/back-compat: a flat {"urls": {rel: "…"}} form.
        flat = (grant.get("urls") or {}).get(rel_path)
        return flat if isinstance(flat, str) else None

    async def upload(
        self, grant: dict[str, Any], rel_path: str, chunks: AsyncIterator[bytes]
    ) -> int:
        url = self._url_for(grant, rel_path)
        if not url:
            raise RuntimeError(f"grant has no staging URL for {rel_path!r}")

        import httpx

        written = 0

        async def _body() -> AsyncIterator[bytes]:
            nonlocal written
            async for chunk in chunks:
                written += len(chunk)
                yield chunk

        async with httpx.AsyncClient() as cli:
            resp = await cli.put(url, content=_body())
            resp.raise_for_status()
        return written
