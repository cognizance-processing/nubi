"""Server-side bridge broker — manages connected bridge agents and local TCP proxies.

Architecture
------------
The ``BridgeBroker`` singleton holds a registry of bridge agents connected via
WebSocket.  When a connector needs to reach a private database through a bridge,
it calls ``open_tcp_proxy(bridge_id, host, port)`` which:

1. Verifies an agent is registered for that bridge.
2. Starts an ephemeral TCP listener on 127.0.0.1 with an OS-assigned port.
3. For every TCP connection arriving on that port:
   a. Allocates a new stream_id.
   b. Sends an OPEN frame over the bridge WebSocket.
   c. Pumps DATA frames in both directions until one side closes.
4. Returns the local (host, port) the connector can dial.

WebSocket send/recv interface
-----------------------------
The broker expects each registered WebSocket to implement:

    ws.send(data: bytes) → Awaitable[None]   — send raw bytes
    ws.recv() → Awaitable[bytes | str]        — receive raw bytes (binary frames)

This is compatible with both the ``websockets`` library objects AND with
FastAPI ``WebSocket`` objects (which use ``send_bytes`` / ``receive_bytes``).

To paper over the API difference, the broker wraps each WebSocket behind a
``_WsAdapter`` that normalises the two call conventions.

Thread / concurrency model
--------------------------
Everything runs in a single asyncio event loop (same as the FastAPI app).
The broker's internal state is protected by an ``asyncio.Lock`` where needed.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import Any, Protocol, runtime_checkable

from app.bridges.protocol import (
    FrameType,
    decode_frame,
    decode_open_payload,
    encode_frame,
    encode_open,
    FrameError,
)
from app.errors import AppError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# WebSocket adapter
# ---------------------------------------------------------------------------


@runtime_checkable
class _WsLike(Protocol):
    """Minimal protocol that both websockets and FastAPI WebSocket objects satisfy
    once wrapped by _WsAdapter."""

    async def send(self, data: bytes) -> None: ...
    async def recv(self) -> bytes: ...


class _WsAdapter:
    """Normalise FastAPI WebSocket and websockets client objects to a common API.

    FastAPI WebSocket:
        send_bytes(data) / receive_bytes() -> bytes
        (may also have send/recv but they handle text frames differently)

    websockets.WebSocketClientProtocol / ServerConnection:
        send(data: bytes) / recv() -> bytes | str

    We detect which flavour we have at construction time.
    """

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        # FastAPI WebSocket has send_bytes / receive_bytes
        self._is_fastapi = hasattr(ws, "send_bytes") and hasattr(ws, "receive_bytes")

    async def send(self, data: bytes) -> None:
        if self._is_fastapi:
            await self._ws.send_bytes(data)
        else:
            await self._ws.send(data)

    async def recv(self) -> bytes:
        if self._is_fastapi:
            return await self._ws.receive_bytes()
        else:
            raw = await self._ws.recv()
            if isinstance(raw, str):
                return raw.encode("utf-8")
            return raw


# ---------------------------------------------------------------------------
# Stream state
# ---------------------------------------------------------------------------


class _Stream:
    """In-flight TCP stream multiplexed over a bridge WebSocket."""

    def __init__(self, stream_id: int) -> None:
        self.stream_id = stream_id
        # Queues carry bytes payload to be forwarded in each direction.
        self.inbound: asyncio.Queue[bytes | None] = asyncio.Queue()  # ws → tcp socket
        self.ready_event: asyncio.Event = asyncio.Event()
        self.error: str | None = None


# ---------------------------------------------------------------------------
# Bridge agent connection
# ---------------------------------------------------------------------------


class _BridgeConnection:
    """Represents one connected bridge agent WebSocket.

    Manages a reader loop that demultiplexes frames from the agent and routes
    DATA / READY / ERROR / CLOSE payloads into the appropriate ``_Stream``
    queues.
    """

    def __init__(self, bridge_id: str, ws_adapter: _WsAdapter) -> None:
        self.bridge_id = bridge_id
        self._ws = ws_adapter
        self._streams: dict[int, _Stream] = {}
        self._next_stream_id: int = 1
        self._lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background frame-reader coroutine."""
        self._reader_task = asyncio.ensure_future(self._reader_loop())

    async def close(self) -> None:
        """Stop the reader loop and cancel all in-flight streams."""
        self._closed = True
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        async with self._lock:
            for stream in list(self._streams.values()):
                await stream.inbound.put(None)  # poison pill
            self._streams.clear()

    # ------------------------------------------------------------------
    # Stream allocation
    # ------------------------------------------------------------------

    async def open_stream(self, host: str, port: int) -> _Stream:
        """Allocate a new stream and send an OPEN frame to the agent.

        Returns the ``_Stream`` object; the caller must await
        ``stream.ready_event`` before piping data.
        """
        async with self._lock:
            stream_id = self._next_stream_id
            self._next_stream_id += 1
            stream = _Stream(stream_id)
            self._streams[stream_id] = stream

        frame = encode_open(stream_id, host, port)
        await self._ws.send(frame)
        return stream

    async def send_data(self, stream_id: int, data: bytes) -> None:
        """Forward *data* to the agent for the given stream."""
        frame = encode_frame(FrameType.DATA, stream_id, data)
        await self._ws.send(frame)

    async def send_close(self, stream_id: int) -> None:
        """Send a CLOSE frame for the given stream."""
        frame = encode_frame(FrameType.CLOSE, stream_id)
        await self._ws.send(frame)
        async with self._lock:
            self._streams.pop(stream_id, None)

    # ------------------------------------------------------------------
    # Internal frame reader
    # ------------------------------------------------------------------

    async def _reader_loop(self) -> None:
        """Read frames from the agent WebSocket and route them to streams."""
        buf = bytearray()
        try:
            while not self._closed:
                try:
                    chunk = await self._ws.recv()
                except Exception:
                    break
                if not chunk:
                    continue
                buf.extend(chunk)
                # Drain as many complete frames as possible from the buffer.
                while True:
                    try:
                        ftype, stream_id, payload, consumed = decode_frame(buf)
                    except FrameError as exc:
                        logger.warning("bridge %s frame decode error: %s", self.bridge_id, exc)
                        break
                    if ftype is None:
                        break  # need more data
                    del buf[:consumed]
                    await self._dispatch(ftype, stream_id, payload)  # type: ignore[arg-type]
        finally:
            # Wake up all waiting streams with poison pills.
            async with self._lock:
                for stream in list(self._streams.values()):
                    await stream.inbound.put(None)
                self._streams.clear()
            logger.debug("bridge %s reader loop finished", self.bridge_id)

    async def _dispatch(self, ftype: FrameType, stream_id: int, payload: bytes) -> None:
        async with self._lock:
            stream = self._streams.get(stream_id)

        if stream is None:
            logger.debug(
                "bridge %s received %s for unknown stream %s",
                self.bridge_id, ftype.name, stream_id,
            )
            return

        if ftype == FrameType.READY:
            stream.ready_event.set()

        elif ftype == FrameType.ERROR:
            stream.error = payload.decode("utf-8", errors="replace") if payload else "unknown error"
            stream.ready_event.set()  # wake up the waiter so it sees the error
            await stream.inbound.put(None)

        elif ftype == FrameType.DATA:
            await stream.inbound.put(payload)

        elif ftype == FrameType.CLOSE:
            await stream.inbound.put(None)  # poison pill
            async with self._lock:
                self._streams.pop(stream_id, None)


# ---------------------------------------------------------------------------
# Local TCP proxy listener
# ---------------------------------------------------------------------------


class _TcpProxy:
    """Ephemeral 127.0.0.1 TCP listener that funnels connections through a bridge.

    One ``_TcpProxy`` is created per ``open_tcp_proxy()`` call.  It starts
    an asyncio TCP server on an OS-assigned port, then for each inbound client
    opens a stream over the bridge WebSocket and pipes bytes bidirectionally.
    """

    def __init__(self, bridge_conn: _BridgeConnection, remote_host: str, remote_port: int) -> None:
        self._bridge_conn = bridge_conn
        self._remote_host = remote_host
        self._remote_port = remote_port
        self._server: asyncio.AbstractServer | None = None
        self._local_port: int = 0

    async def start(self) -> tuple[str, int]:
        """Start the local TCP listener; return (host, port)."""
        self._server = await asyncio.start_server(
            self._handle_client,
            host="127.0.0.1",
            port=0,  # OS assigns a free port
        )
        sockets = self._server.sockets
        if not sockets:
            raise RuntimeError("TCP proxy server has no sockets after start")
        self._local_port = sockets[0].getsockname()[1]
        return "127.0.0.1", self._local_port

    async def close(self) -> None:
        """Stop the TCP listener."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle one inbound TCP connection — open a stream and pipe bytes."""
        stream = await self._bridge_conn.open_stream(self._remote_host, self._remote_port)

        try:
            # Wait for the agent to confirm the TCP connection inside the VPC.
            try:
                await asyncio.wait_for(stream.ready_event.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "bridge %s stream %s READY timeout",
                    self._bridge_conn.bridge_id, stream.stream_id,
                )
                writer.close()
                await self._bridge_conn.send_close(stream.stream_id)
                return

            if stream.error:
                logger.warning(
                    "bridge %s stream %s error: %s",
                    self._bridge_conn.bridge_id, stream.stream_id, stream.error,
                )
                writer.close()
                return

            # Pipe in both directions concurrently.
            await asyncio.gather(
                self._tcp_to_ws(reader, stream),
                self._ws_to_tcp(stream, writer),
                return_exceptions=True,
            )
        finally:
            if not writer.is_closing():
                writer.close()
            try:
                await self._bridge_conn.send_close(stream.stream_id)
            except Exception:
                pass

    async def _tcp_to_ws(self, reader: asyncio.StreamReader, stream: _Stream) -> None:
        """Read bytes from the local TCP client and forward as DATA frames."""
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await self._bridge_conn.send_data(stream.stream_id, data)
        except Exception:
            pass

    async def _ws_to_tcp(self, stream: _Stream, writer: asyncio.StreamWriter) -> None:
        """Read DATA frames from the broker queue and write to the TCP client."""
        try:
            while True:
                chunk = await stream.inbound.get()
                if chunk is None:
                    break
                writer.write(chunk)
                await writer.drain()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# BridgeBroker — singleton
# ---------------------------------------------------------------------------


class BridgeBroker:
    """Server-side singleton that manages all connected bridge agent WebSockets.

    Usage::

        broker = get_broker()

        # In the WS endpoint:
        await broker.register(bridge_id, ws)
        try:
            await ws.wait_for_disconnect()
        finally:
            await broker.unregister(bridge_id)

        # In the connector network resolver:
        host, port = await broker.open_tcp_proxy(bridge_id, "db.internal", 5432)
        # ... later ...
        await broker.close_tcp_proxy(host, port)
    """

    def __init__(self) -> None:
        self._agents: dict[str, _BridgeConnection] = {}
        self._proxies: dict[tuple[str, int], _TcpProxy] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Agent registration
    # ------------------------------------------------------------------

    async def register(self, bridge_id: str, ws: Any) -> None:
        """Register a connected bridge agent WebSocket for *bridge_id*.

        Parameters
        ----------
        bridge_id:
            The UUID of the bridge row.
        ws:
            A WebSocket object (FastAPI ``WebSocket`` OR ``websockets`` client).
            The broker wraps it in a ``_WsAdapter`` internally.
        """
        adapter = _WsAdapter(ws)
        conn = _BridgeConnection(bridge_id, adapter)
        async with self._lock:
            # If there's an existing connection, close it first.
            old = self._agents.get(bridge_id)
            if old is not None:
                await old.close()
            self._agents[bridge_id] = conn
        conn.start()
        logger.info("bridge %s registered", bridge_id)

    async def unregister(self, bridge_id: str) -> None:
        """Unregister the bridge agent for *bridge_id* and clean up streams."""
        async with self._lock:
            conn = self._agents.pop(bridge_id, None)
        if conn is not None:
            await conn.close()
            logger.info("bridge %s unregistered", bridge_id)

    def is_connected(self, bridge_id: str) -> bool:
        """Return True if an agent is currently registered for *bridge_id*."""
        return bridge_id in self._agents

    async def drop(self, bridge_id: str) -> bool:
        """Forcibly drop the live tunnel for *bridge_id* (revocation path, §7).

        Called when a bridge token is revoked: the broker tears down the live
        WebSocket connection so a now-untrusted agent cannot keep tunnelling.
        The bridge transitions to ``offline`` and connectors pinned to it then
        fail fast (``open_tcp_proxy`` raises ``bridge_not_connected``) rather
        than hanging on a dead tunnel.

        Returns True if a connection was dropped, False if none was registered.
        Idempotent.
        """
        async with self._lock:
            conn = self._agents.pop(bridge_id, None)
        if conn is None:
            return False
        await conn.close()
        logger.info("bridge %s tunnel dropped (token revoked)", bridge_id)
        return True

    # ------------------------------------------------------------------
    # TCP proxy management
    # ------------------------------------------------------------------

    async def open_tcp_proxy(
        self,
        bridge_id: str,
        host: str,
        port: int,
    ) -> tuple[str, int]:
        """Start a local ephemeral TCP proxy for *host:port* through *bridge_id*.

        Parameters
        ----------
        bridge_id:
            The UUID of the bridge that should reach *host*:*port*.
        host:
            The hostname or IP address *inside* the customer VPC.
        port:
            The TCP port on the target host.

        Returns
        -------
        (local_host, local_port)
            A ``127.0.0.1`` address that the connector can dial.  Every TCP
            connection to this address is tunnelled through the bridge agent.

        Raises
        ------
        AppError
            ``code="bridge_not_connected"`` (503) if no agent is currently
            registered for *bridge_id*.
        """
        async with self._lock:
            conn = self._agents.get(bridge_id)
        if conn is None:
            raise AppError(
                "bridge_not_connected",
                f"Bridge {bridge_id!r} has no connected agent. "
                "Start the bridge agent process and wait for it to register.",
                503,
            )

        proxy = _TcpProxy(conn, host, port)
        local_host, local_port = await proxy.start()

        async with self._lock:
            self._proxies[(local_host, local_port)] = proxy

        logger.info(
            "bridge %s tcp proxy %s:%s → %s:%s opened",
            bridge_id, local_host, local_port, host, port,
        )
        return local_host, local_port

    async def close_tcp_proxy(self, host: str, port: int) -> None:
        """Stop the local TCP proxy at *(host, port)* and release resources."""
        async with self._lock:
            proxy = self._proxies.pop((host, port), None)
        if proxy is not None:
            await proxy.close()
            logger.debug("tcp proxy %s:%s closed", host, port)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_broker: BridgeBroker | None = None


def get_broker() -> BridgeBroker:
    """Return the module-level ``BridgeBroker`` singleton (created on first call)."""
    global _broker
    if _broker is None:
        _broker = BridgeBroker()
    return _broker


def reset_broker() -> None:
    """Replace the singleton with a fresh instance — for use in tests only."""
    global _broker
    _broker = BridgeBroker()
