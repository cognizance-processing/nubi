"""Customer-side bridge agent runtime — control channel + agent-side ingest.

This is the agent half of Bridge v2 (design §7, phase 3).  It runs inside the
customer's network, dials OUT to the Nubi control plane over the existing
WebSocket reverse tunnel (no inbound firewall holes), and:

1. **Authenticates the control channel** with a bridge token
   (``nubi_br_…``).  The token is presented on the handshake (``X-Bridge-Token``
   header) and re-asserted on every heartbeat.  The control plane validates it;
   a *revoked* / *auth-reject* response makes the agent exit cleanly with a
   clear message — not hang.

2. **Claims ``file_ingest`` tasks** over that authenticated channel and runs
   them agent-side: it receives an ephemeral, **write-only, prefix-pinned,
   short-TTL staging grant** (presigned PUT URLs / STS-style token) delivered
   over the tunnel and **held in memory only — never written to disk**.  It
   streams from the LOCAL file-connector source to staging using the grant,
   then reports the manifest ``{files:[{path,size,sha256}], row_counts}`` back
   over the tunnel.  The central worker verifies + promotes/loads — the agent
   does NOT, and the agent NEVER receives a stored connector secret.

What this module reuses vs. defines
-----------------------------------
The binary TCP-mux tunnel (``app.bridges.agent.BridgeAgent`` /
``protocol.py``) is reused verbatim for query traffic — we do not reimplement
the WebSocket multiplexer.  The *control channel* (task claim / grant / manifest
JSON messages) is a thin text-message layer that rides the same WebSocket
connection, defined here against the design-§7 contract while the backend
control plane is built in parallel.  Every collaborator the agent talks to is
injectable (the WS, the source-connector opener, the staging uploader) so the
runtime is testable with no network and no real cloud.

Security invariants (enforced by construction + asserted in tests)
------------------------------------------------------------------
* The grant lives only in a local variable while a task runs; it is never
  written to disk and is dropped as soon as the task finishes.
* The token and grant are never logged (``_redact`` guards log lines; the
  identity ``__repr__`` redacts the token).
* Files stream in bounded chunks — whole files are never buffered in memory.
* The agent receives a staging grant only; it never receives a connector
  secret.  ``IngestTask`` carries a SOURCE descriptor (connector id + path),
  not credentials, and the local source opener resolves creds out-of-band on
  the customer machine.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, BinaryIO, Awaitable, Callable, Iterable, Protocol

logger = logging.getLogger("nubi.bridge")

# Stream chunk size for source→staging copies (bounded memory — design §7).
_CHUNK_SIZE = 1 << 20  # 1 MiB


# ---------------------------------------------------------------------------
# Control-channel message kinds (JSON text frames)
# ---------------------------------------------------------------------------


class ControlMsg:
    """String constants for control-channel message ``type`` fields.

    These ride the bridge WebSocket as JSON TEXT frames, alongside the binary
    TCP-mux frames the tunnel already uses.  The set mirrors design §7's
    handshake / heartbeat / claim / grant / manifest flow.
    """

    HANDSHAKE = "handshake"            # agent → cp: present token + identity
    HANDSHAKE_OK = "handshake_ok"      # cp → agent: accepted
    AUTH_REJECT = "auth_reject"        # cp → agent: token invalid / revoked
    HEARTBEAT = "heartbeat"            # agent → cp: liveness + token re-assert
    HEARTBEAT_OK = "heartbeat_ok"      # cp → agent: still valid
    TASK_ASSIGN = "task_assign"        # cp → agent: a file_ingest task to claim
    TASK_CLAIM = "task_claim"          # agent → cp: I'll take this task
    GRANT = "grant"                    # cp → agent: ephemeral staging grant
    MANIFEST = "manifest"             # agent → cp: staged-output manifest
    TASK_ERROR = "task_error"          # agent → cp: task failed before manifest


# Close codes the broker uses for auth-reject / unknown-bridge on the WS
# handshake (see backend/app/routes/bridges.py).  Treated as clean, terminal
# "bridge revoked / not authorised" exits — never retried.
WS_CLOSE_AUTH_REJECT = 4401
WS_CLOSE_UNKNOWN_BRIDGE = 4404


class BridgeRevoked(Exception):
    """Raised when the control plane rejects the token (revoked / invalid).

    The agent treats this as terminal: it stops cleanly with a clear message
    rather than reconnecting in a hot loop.
    """


# ---------------------------------------------------------------------------
# Injectable collaborators (Protocols — fakes in tests, real impls in prod)
# ---------------------------------------------------------------------------


class ControlChannel(Protocol):
    """Bidirectional JSON control channel over the bridge WebSocket.

    ``send`` serialises a dict to a TEXT frame; ``recv`` blocks for the next
    inbound control message (already decoded to a dict).  The production
    implementation wraps a ``websockets`` connection; tests inject a fake.
    """

    async def send(self, msg: dict[str, Any]) -> None: ...
    async def recv(self) -> dict[str, Any]: ...


class SourceOpener(Protocol):
    """Opens a LOCAL file-connector source for streaming read.

    Given a source descriptor (connector id + path) the opener resolves the
    file connector reachable on the customer machine (sftp/ftp/bucket via the
    file-connector interface) and yields ``(rel_path, BinaryIO)`` pairs.  The
    opener resolves connector creds OUT-OF-BAND on the customer side; the agent
    never receives a stored connector secret from the control plane.
    """

    def open_source(self, source: dict[str, Any]) -> Iterable[tuple[str, BinaryIO]]: ...


class StagingUploader(Protocol):
    """Streams bytes to a staging target using an ephemeral grant (memory-only).

    The grant (presigned PUT URLs / STS token) is passed per-call and held only
    for the duration of the upload — never persisted.  ``upload`` consumes an
    async byte iterator so whole files are never buffered, and returns the
    number of bytes written (the agent computes size + sha256 independently as
    it streams, for the manifest).
    """

    async def upload(
        self, grant: dict[str, Any], rel_path: str, chunks: AsyncIterator[bytes]
    ) -> int: ...


# ---------------------------------------------------------------------------
# Task / manifest value objects
# ---------------------------------------------------------------------------


@dataclass
class IngestTask:
    """A ``file_ingest`` task the agent claims over the control channel.

    Carries only a SOURCE descriptor (connector id + path) and run binding —
    NOT credentials.  The grant arrives separately, after the claim.
    """

    task_id: str
    run_id: str
    source: dict[str, Any]
    format: str = "auto"

    @classmethod
    def from_msg(cls, msg: dict[str, Any]) -> "IngestTask":
        return cls(
            task_id=str(msg["task_id"]),
            run_id=str(msg["run_id"]),
            source=dict(msg.get("source") or {}),
            format=str(msg.get("format") or "auto"),
        )


@dataclass
class ManifestEntry:
    """One staged object: ``{path, size, sha256}`` (matches backend staging)."""

    path: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "size": self.size, "sha256": self.sha256}


@dataclass
class Manifest:
    """Producer-reported manifest — design §5: ``{files, row_counts}``."""

    files: list[ManifestEntry] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": [e.to_dict() for e in self.files],
            "row_counts": dict(self.row_counts),
        }


# ---------------------------------------------------------------------------
# Streaming helpers (bounded memory; sha256 computed on the fly)
# ---------------------------------------------------------------------------


async def _iter_file_chunks(
    handle: BinaryIO, hasher: "hashlib._Hash", chunk_size: int = _CHUNK_SIZE
) -> AsyncIterator[bytes]:
    """Yield *handle* in bounded chunks, updating *hasher* as we go.

    Reads run in a thread (``asyncio.to_thread``) so a blocking
    SFTP/FTP/bucket handle does not stall the event loop.  Memory stays bounded
    to one chunk — whole files are never buffered.
    """
    while True:
        chunk = await asyncio.to_thread(handle.read, chunk_size)
        if not chunk:
            break
        hasher.update(chunk)
        yield chunk


# ---------------------------------------------------------------------------
# Agent-side ingest execution
# ---------------------------------------------------------------------------


async def run_ingest_task(
    task: IngestTask,
    grant: dict[str, Any],
    opener: SourceOpener,
    uploader: StagingUploader,
) -> Manifest:
    """Stream a claimed ``file_ingest`` task's source to staging; build manifest.

    For each ``(rel_path, handle)`` the local source yields, the bytes stream
    SOURCE → staging through *uploader* using *grant*, while size + sha256 are
    computed incrementally.  Nothing is buffered whole; the grant is read from
    the *grant* argument only (memory) and is never written anywhere.

    Returns the :class:`Manifest` to report back over the control channel; the
    central worker verifies + promotes/loads (the agent does neither).

    NOTE: ``grant`` and the source bytes never appear in a log line — only file
    paths, sizes, and counts.
    """
    entries: list[ManifestEntry] = []
    row_counts: dict[str, int] = {}

    for rel_path, handle in opener.open_source(task.source):
        hasher = hashlib.sha256()
        try:
            written = await uploader.upload(
                grant, rel_path, _iter_file_chunks(handle, hasher)
            )
        finally:
            close = getattr(handle, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 — best-effort handle cleanup
                    pass
        entries.append(
            ManifestEntry(path=rel_path, size=written, sha256=hasher.hexdigest())
        )
        # row_counts: 0 here (the agent ships raw bytes; the central worker
        # parses + counts rows on load).  Reported for contract completeness;
        # a future format-aware agent path can populate it.
        row_counts.setdefault(rel_path, 0)
        logger.info(
            "bridge ingest: staged %s (%d bytes) for run %s",
            rel_path, written, task.run_id,
        )

    return Manifest(files=entries, row_counts=row_counts)


# ---------------------------------------------------------------------------
# Control-channel session (handshake, heartbeat, claim/grant/manifest loop)
# ---------------------------------------------------------------------------


class BridgeSession:
    """Drives the authenticated control channel for one connected session.

    Lifecycle::

        session = BridgeSession(channel, identity, opener, uploader)
        await session.handshake()          # present token; raises BridgeRevoked
        await session.run()                # claim/grant/manifest + heartbeats

    The token is held only on ``identity`` (memory) and is sent on the handshake
    and every heartbeat; it is never logged.  On an auth-reject (revoked token)
    the session raises :class:`BridgeRevoked` so the caller exits cleanly.
    """

    def __init__(
        self,
        channel: ControlChannel,
        identity: Any,  # BridgeIdentity — duck-typed to avoid a hard import
        opener: SourceOpener,
        uploader: StagingUploader,
        *,
        heartbeat_interval_s: float = 30.0,
    ) -> None:
        self._ch = channel
        self._id = identity
        self._opener = opener
        self._uploader = uploader
        self._heartbeat_interval_s = heartbeat_interval_s
        self._stopped = False

    async def handshake(self) -> None:
        """Present the bridge token + identity; raise BridgeRevoked on reject."""
        await self._ch.send(
            {
                "type": ControlMsg.HANDSHAKE,
                "bridge_id": self._id.bridge_id,
                # control channel only — token authenticates, grants no data read
                "token": self._id.token,
            }
        )
        reply = await self._ch.recv()
        rtype = reply.get("type")
        if rtype == ControlMsg.AUTH_REJECT:
            raise BridgeRevoked(reply.get("reason") or "bridge token rejected")
        if rtype != ControlMsg.HANDSHAKE_OK:
            raise BridgeRevoked(f"unexpected handshake reply: {rtype!r}")
        logger.info("bridge %s control channel authenticated", self._id.bridge_id)

    async def send_heartbeat(self) -> None:
        """Send one heartbeat (re-asserting the token); raise on auth-reject.

        Used by the heartbeat loop; the token is re-presented every beat so a
        mid-session revoke is caught on the next beat (design §7).
        """
        await self._ch.send(
            {
                "type": ControlMsg.HEARTBEAT,
                "bridge_id": self._id.bridge_id,
                "token": self._id.token,
            }
        )

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        """Dispatch one inbound control message."""
        mtype = msg.get("type")

        if mtype == ControlMsg.AUTH_REJECT:
            raise BridgeRevoked(msg.get("reason") or "bridge token revoked")

        if mtype == ControlMsg.HEARTBEAT_OK:
            return  # liveness ack — nothing to do

        if mtype == ControlMsg.TASK_ASSIGN:
            await self._claim_and_run(IngestTask.from_msg(msg))
            return

        logger.debug("bridge %s ignoring control msg %r", self._id.bridge_id, mtype)

    async def _claim_and_run(self, task: IngestTask) -> None:
        """Claim a file_ingest task, await its grant, ingest, report manifest.

        The grant is received inline and passed straight into
        :func:`run_ingest_task` as a local — it is never stored on the session
        or written to disk.  A failure before the manifest is reported as a
        TASK_ERROR (no partial/garbage manifest is sent).
        """
        # Claim it over the authenticated channel (extends the claim/lease model
        # to this remote claimant — backend binds bridge↔org↔task before granting).
        await self._ch.send(
            {"type": ControlMsg.TASK_CLAIM, "task_id": task.task_id, "run_id": task.run_id}
        )

        grant_msg = await self._ch.recv()
        if grant_msg.get("type") == ControlMsg.AUTH_REJECT:
            raise BridgeRevoked(grant_msg.get("reason") or "bridge token revoked")
        if grant_msg.get("type") != ControlMsg.GRANT or grant_msg.get("task_id") != task.task_id:
            await self._ch.send(
                {
                    "type": ControlMsg.TASK_ERROR,
                    "task_id": task.task_id,
                    "error": "expected a grant for the claimed task",
                }
            )
            return

        # Grant held in a LOCAL only — memory, never disk; dropped at scope exit.
        grant = grant_msg.get("grant") or {}
        try:
            manifest = await run_ingest_task(task, grant, self._opener, self._uploader)
        except Exception as exc:  # noqa: BLE001 — report, do not crash the session
            logger.warning(
                "bridge %s task %s failed: %s",
                self._id.bridge_id, task.task_id, _redact(str(exc)),
            )
            await self._ch.send(
                {
                    "type": ControlMsg.TASK_ERROR,
                    "task_id": task.task_id,
                    "error": _redact(str(exc)),
                }
            )
            return
        finally:
            grant = None  # explicit: do not retain the grant past the task

        await self._ch.send(
            {
                "type": ControlMsg.MANIFEST,
                "task_id": task.task_id,
                "run_id": task.run_id,
                "manifest": manifest.to_dict(),
            }
        )

    async def run(self) -> None:
        """Run the control loop until stopped or the token is revoked.

        Spawns a heartbeat task (re-asserting the token on the configured
        interval) and dispatches inbound control messages.  Exits cleanly on
        :class:`BridgeRevoked` (re-raised so the caller can print + exit).
        """
        hb = asyncio.ensure_future(self._heartbeat_loop())
        try:
            while not self._stopped:
                try:
                    msg = await self._ch.recv()
                except BridgeRevoked:
                    raise
                except Exception as exc:  # noqa: BLE001 — channel closed / drop
                    logger.info(
                        "bridge %s control channel closed: %s",
                        self._id.bridge_id, _redact(str(exc)),
                    )
                    break
                await self._handle_message(msg)
        finally:
            hb.cancel()
            try:
                await hb
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _heartbeat_loop(self) -> None:
        """Send a token-bearing heartbeat every interval until cancelled."""
        try:
            while not self._stopped:
                await asyncio.sleep(self._heartbeat_interval_s)
                await self.send_heartbeat()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — connection drop; loop will end
            logger.debug("bridge heartbeat stopped: %s", _redact(str(exc)))

    def stop(self) -> None:
        """Signal the control loop to stop after the current message."""
        self._stopped = True


# ---------------------------------------------------------------------------
# Redaction — keep tokens / grants out of every log line
# ---------------------------------------------------------------------------


def _redact(text: str) -> str:
    """Strip anything that looks like a bridge token / presigned URL from *text*.

    Defensive: log lines never intentionally include the token or grant, but an
    exception string could echo a URL.  This blanks ``nubi_br_…`` tokens and
    query strings (presigned-URL signatures) so a stray log can't leak them.
    """
    out: list[str] = []
    for word in text.split():
        if word.startswith("nubi_br_"):
            out.append("nubi_br_<redacted>")
        elif "?" in word and ("X-Amz" in word or "Signature" in word or "sig=" in word):
            out.append(word.split("?", 1)[0] + "?<redacted>")
        else:
            out.append(word)
    return " ".join(out)


# ---------------------------------------------------------------------------
# Production control channel + connect/serve loop
# ---------------------------------------------------------------------------


class WebsocketControlChannel:
    """``ControlChannel`` over a ``websockets`` connection (JSON TEXT frames).

    The bridge WebSocket carries both the binary TCP-mux tunnel frames AND these
    JSON control messages.  This adapter sends/receives only the JSON control
    messages; binary frames are handled by the reused
    :class:`app.bridges.agent.BridgeAgent` multiplexer.
    """

    def __init__(self, ws: Any) -> None:
        self._ws = ws

    async def send(self, msg: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(msg))

    async def recv(self) -> dict[str, Any]:
        while True:
            raw = await self._ws.recv()
            # Skip binary tunnel frames — those belong to BridgeAgent, not us.
            if isinstance(raw, (bytes, bytearray)):
                continue
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue


async def serve(
    identity: Any,
    opener: SourceOpener,
    uploader: StagingUploader,
    *,
    connect: Callable[[str, dict[str, str]], Awaitable[Any]] | None = None,
    reconnect_delay_s: float = 5.0,
    once: bool = False,
) -> None:  # pragma: no cover — exercised via the CLI / integration, not unit
    """Connect OUT to the control plane and serve the control channel.

    Reuses the existing reverse-tunnel WebSocket: the agent dials
    ``{control_plane_url}/bridges/{bridge_id}/connect`` with the bridge token in
    the ``X-Bridge-Token`` header (the broker validates it and closes 4401 on
    reject / 4404 on unknown — both surface here as a clean ``BridgeRevoked``).

    *connect* is injectable for tests; it defaults to ``websockets.connect``.
    On a transient connection error the agent reconnects after
    *reconnect_delay_s*; on :class:`BridgeRevoked` it re-raises so the CLI can
    print the reason and exit non-zero.
    """
    if connect is None:
        import websockets  # type: ignore[import]

        async def connect(url: str, headers: dict[str, str]) -> Any:  # noqa: F811
            return await websockets.connect(url, additional_headers=headers)

    ws_url = f"{identity.control_plane_url}/bridges/{identity.bridge_id}/connect"
    headers = {"X-Bridge-Token": identity.token}

    while True:
        try:
            ws = await connect(ws_url, headers)
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            if code in (WS_CLOSE_AUTH_REJECT, WS_CLOSE_UNKNOWN_BRIDGE):
                raise BridgeRevoked(
                    "bridge revoked or token rejected by the control plane"
                ) from exc
            logger.warning(
                "bridge %s connect failed: %s — retrying in %ss",
                identity.bridge_id, _redact(str(exc)), reconnect_delay_s,
            )
            if once:
                return
            await asyncio.sleep(reconnect_delay_s)
            continue

        channel = WebsocketControlChannel(ws)
        session = BridgeSession(channel, identity, opener, uploader)
        try:
            await session.handshake()
            await session.run()
        finally:
            closer = getattr(ws, "close", None)
            if callable(closer):
                try:
                    await closer()
                except Exception:  # noqa: BLE001
                    pass

        if once:
            return
        await asyncio.sleep(reconnect_delay_s)
