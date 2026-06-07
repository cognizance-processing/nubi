"""Customer-side bridge agent — connects outbound to the Nubi control plane.

This module is the *customer-side* half of the reverse-tunnel.  It runs inside
the customer's VPC (or on-prem network) and dials OUT to the Nubi backend over
WebSocket.  No inbound ports need to be opened.

When the control plane sends an OPEN(stream_id, host, port) frame, the agent
dials the real TCP target inside the VPC and relays bytes back as DATA frames.

Usage
-----
Run as a module::

    BRIDGE_ID=<uuid> BRIDGE_TOKEN=<token> CONTROL_PLANE_URL=wss://api.nubi.dev/api/v1 \\
        python -m app.bridges.agent

Or import programmatically (e.g. for tests)::

    from app.bridges.agent import BridgeAgent
    agent = BridgeAgent(ws=ws_client, bridge_id="...", )
    await agent.run()

Environment variables
---------------------
BRIDGE_ID           UUID of the bridge row (required).
BRIDGE_TOKEN        Secret token for authenticating this agent (required).
CONTROL_PLANE_URL   Base WebSocket URL, e.g. wss://api.nubi.dev/api/v1 (required).
BRIDGE_RECONNECT_DELAY  Seconds to wait between reconnect attempts (default 5).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from app.bridges.protocol import (
    FrameError,
    FrameType,
    decode_frame,
    decode_open_payload,
    encode_frame,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WebSocket adapter (same as broker side, duplicated to keep agent standalone)
# ---------------------------------------------------------------------------


class _AgentWsAdapter:
    """Normalise FastAPI WebSocket and websockets client objects."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws
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
# Per-stream TCP connection state
# ---------------------------------------------------------------------------


class _AgentStream:
    """One TCP connection inside the VPC, multiplexed as a stream."""

    def __init__(self, stream_id: int) -> None:
        self.stream_id = stream_id
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        # Queue of data chunks to send over TCP (fed by the ws-reader coroutine)
        self.outbound: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.tcp_task: asyncio.Task[None] | None = None
        self.ws_sender_task: asyncio.Task[None] | None = None


# ---------------------------------------------------------------------------
# BridgeAgent
# ---------------------------------------------------------------------------


class BridgeAgent:
    """Customer-side agent that connects outbound to the Nubi control plane.

    Instantiate with a connected WebSocket object (either a ``websockets``
    client connection or any object with ``send(bytes)`` / ``recv()→bytes``).

    Parameters
    ----------
    ws:
        An open WebSocket connection to ``WS /api/v1/bridges/{id}/connect``.
    bridge_id:
        UUID of the bridge row — used only for log messages.
    """

    def __init__(self, ws: Any, bridge_id: str = "") -> None:
        self._ws = _AgentWsAdapter(ws)
        self.bridge_id = bridge_id
        self._streams: dict[int, _AgentStream] = {}
        self._stopped = False

    async def run(self) -> None:
        """Process frames from the control plane until the connection closes."""
        buf = bytearray()
        try:
            while not self._stopped:
                try:
                    chunk = await self._ws.recv()
                except Exception as exc:
                    logger.info("bridge %s ws recv error: %s", self.bridge_id, exc)
                    break
                if not chunk:
                    continue
                buf.extend(chunk)
                while True:
                    try:
                        ftype, stream_id, payload, consumed = decode_frame(buf)
                    except FrameError as exc:
                        logger.warning("bridge %s frame error: %s", self.bridge_id, exc)
                        break
                    if ftype is None:
                        break
                    del buf[:consumed]
                    await self._dispatch(ftype, stream_id, payload)  # type: ignore[arg-type]
        finally:
            await self._teardown()

    async def stop(self) -> None:
        """Signal the run loop to stop and clean up all streams."""
        self._stopped = True
        await self._teardown()

    # ------------------------------------------------------------------
    # Frame dispatching
    # ------------------------------------------------------------------

    async def _dispatch(self, ftype: FrameType, stream_id: int, payload: bytes) -> None:
        if ftype == FrameType.OPEN:
            try:
                host, port = decode_open_payload(payload)
            except FrameError as exc:
                logger.warning("bridge %s OPEN decode error: %s", self.bridge_id, exc)
                err_frame = encode_frame(FrameType.ERROR, stream_id, str(exc).encode())
                await self._ws.send(err_frame)
                return
            asyncio.ensure_future(self._open_tcp_stream(stream_id, host, port))

        elif ftype == FrameType.DATA:
            stream = self._streams.get(stream_id)
            if stream is not None:
                await stream.outbound.put(payload)

        elif ftype == FrameType.CLOSE:
            stream = self._streams.pop(stream_id, None)
            if stream is not None:
                await stream.outbound.put(None)  # poison pill
                await self._close_stream(stream)

    # ------------------------------------------------------------------
    # TCP stream management
    # ------------------------------------------------------------------

    async def _open_tcp_stream(self, stream_id: int, host: str, port: int) -> None:
        """Dial host:port inside the VPC and register the stream."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=10.0
            )
        except Exception as exc:
            err_msg = f"Cannot connect to {host}:{port}: {exc}"
            logger.warning("bridge %s stream %s: %s", self.bridge_id, stream_id, err_msg)
            err_frame = encode_frame(FrameType.ERROR, stream_id, err_msg.encode())
            await self._ws.send(err_frame)
            return

        stream = _AgentStream(stream_id)
        stream.reader = reader
        stream.writer = writer
        self._streams[stream_id] = stream

        # Acknowledge the TCP connection.
        ready_frame = encode_frame(FrameType.READY, stream_id)
        await self._ws.send(ready_frame)

        # Start the two pump tasks.
        stream.tcp_task = asyncio.ensure_future(self._tcp_to_ws(stream))
        stream.ws_sender_task = asyncio.ensure_future(self._ws_sender(stream))

    async def _tcp_to_ws(self, stream: _AgentStream) -> None:
        """Read bytes from the real TCP connection, send as DATA frames."""
        assert stream.reader is not None
        try:
            while True:
                data = await stream.reader.read(65536)
                if not data:
                    break
                frame = encode_frame(FrameType.DATA, stream.stream_id, data)
                await self._ws.send(frame)
        except Exception:
            pass
        finally:
            close_frame = encode_frame(FrameType.CLOSE, stream.stream_id)
            try:
                await self._ws.send(close_frame)
            except Exception:
                pass
            # Notify the ws_sender to stop.
            await stream.outbound.put(None)
            self._streams.pop(stream.stream_id, None)

    async def _ws_sender(self, stream: _AgentStream) -> None:
        """Read DATA chunks from the outbound queue, write to the TCP socket."""
        assert stream.writer is not None
        try:
            while True:
                chunk = await stream.outbound.get()
                if chunk is None:
                    break
                stream.writer.write(chunk)
                await stream.writer.drain()
        except Exception:
            pass
        finally:
            if stream.writer and not stream.writer.is_closing():
                stream.writer.close()

    async def _close_stream(self, stream: _AgentStream) -> None:
        """Cancel pump tasks and close the TCP socket."""
        for task in (stream.tcp_task, stream.ws_sender_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        if stream.writer and not stream.writer.is_closing():
            stream.writer.close()

    async def _teardown(self) -> None:
        """Close all in-flight streams."""
        for stream in list(self._streams.values()):
            await stream.outbound.put(None)
            await self._close_stream(stream)
        self._streams.clear()


# ---------------------------------------------------------------------------
# __main__ entry point (for running as python -m app.bridges.agent)
# ---------------------------------------------------------------------------


async def _main() -> None:  # pragma: no cover
    try:
        import websockets  # type: ignore[import]
    except ImportError as exc:
        raise SystemExit("websockets is required: pip install websockets") from exc

    bridge_id = os.environ["BRIDGE_ID"]
    bridge_token = os.environ["BRIDGE_TOKEN"]
    base_url = os.environ["CONTROL_PLANE_URL"].rstrip("/")
    reconnect_delay = float(os.environ.get("BRIDGE_RECONNECT_DELAY", "5"))

    ws_url = f"{base_url}/bridges/{bridge_id}/connect"
    headers = {"X-Bridge-Token": bridge_token}

    logging.basicConfig(level=logging.INFO)
    logger.info("bridge agent starting: %s", ws_url)

    while True:
        try:
            async with websockets.connect(ws_url, additional_headers=headers) as ws:
                logger.info("bridge %s connected", bridge_id)
                agent = BridgeAgent(ws=ws, bridge_id=bridge_id)
                await agent.run()
                logger.info("bridge %s disconnected", bridge_id)
        except Exception as exc:
            logger.warning("bridge %s connection error: %s — retrying in %ss",
                           bridge_id, exc, reconnect_delay)
        await asyncio.sleep(reconnect_delay)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())
