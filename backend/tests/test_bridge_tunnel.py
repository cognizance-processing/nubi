"""M22-B: Reverse-tunnel bridge transport tests.

Test strategy
-------------
All tests run in-process with no external network dependencies.

Coverage
--------
(1) Frame codec roundtrip — encode/decode for all five frame types.
(2) OPEN payload encode/decode roundtrip.
(3) Partial frame buffer — decode_frame returns (None,…,0) when incomplete.
(4) Unknown frame type → FrameError raised by decode_frame.
(5) Full loopback — local TCP echo server + in-process broker + agent wired
    over an asyncio pipe (no real WebSocket server needed):
    - register a fake bridge agent
    - open_tcp_proxy → client connects → sends bytes → receives echo
(6) No agent registered → AppError("bridge_not_connected", 503).
(7) network.py bridge mode → resolve_network_async returns proxy NetworkTarget
    when agent is connected.
(8) network.py bridge mode → resolve_network (sync) raises 501 when no agent.
(9) network.py bridge mode → resolve_network_async raises 503 when no agent.
"""

from __future__ import annotations

import asyncio
import struct
import uuid
from typing import Any

import pytest
import pytest_asyncio

from app.bridges.protocol import (
    FrameError,
    FrameType,
    decode_frame,
    decode_open_payload,
    encode_frame,
    encode_open,
)
from app.bridges.broker import BridgeBroker, reset_broker
from app.bridges.agent import BridgeAgent
from app.connectors.network import resolve_network, resolve_network_async, NetworkTarget
from app.errors import AppError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_broker_singleton():
    """Ensure a fresh broker for every test."""
    reset_broker()
    yield
    reset_broker()


# ---------------------------------------------------------------------------
# (1) Frame codec roundtrip — all frame types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "frame_type, stream_id, payload",
    [
        (FrameType.OPEN,  1,      b"\x00\x50db.internal\x00"),
        (FrameType.READY, 1,      b""),
        (FrameType.ERROR, 2,      b"connection refused"),
        (FrameType.DATA,  42,     b"SELECT 1"),
        (FrameType.CLOSE, 99999,  b""),
    ],
)
def test_codec_roundtrip(frame_type, stream_id, payload):
    """encode_frame → decode_frame roundtrip preserves all fields."""
    wire = encode_frame(frame_type, stream_id, payload)
    ft, sid, pl, consumed = decode_frame(wire)

    assert ft == frame_type
    assert sid == stream_id
    assert pl == payload
    assert consumed == len(wire)


# ---------------------------------------------------------------------------
# (2) OPEN payload encode/decode roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host, port",
    [
        ("db.internal", 5432),
        ("10.0.0.1", 3306),
        ("redis.corp.local", 6379),
        ("a", 65535),
    ],
)
def test_open_payload_roundtrip(host, port):
    """encode_open → decode_open_payload preserves host and port."""
    wire = encode_open(42, host, port)
    ft, sid, payload, _ = decode_frame(wire)
    assert ft == FrameType.OPEN
    assert sid == 42
    decoded_host, decoded_port = decode_open_payload(payload)
    assert decoded_host == host
    assert decoded_port == port


# ---------------------------------------------------------------------------
# (3) Partial buffer → (None, None, None, 0)
# ---------------------------------------------------------------------------


def test_decode_frame_incomplete_returns_none():
    """decode_frame returns all-None / 0 consumed when buffer is too short."""
    wire = encode_frame(FrameType.DATA, 1, b"hello world")
    # Give it one byte fewer than the full frame.
    partial = wire[:-1]
    ft, sid, pl, consumed = decode_frame(partial)
    assert ft is None
    assert sid is None
    assert pl is None
    assert consumed == 0


def test_decode_frame_empty_returns_none():
    """decode_frame on an empty buffer returns (None, None, None, 0)."""
    ft, sid, pl, consumed = decode_frame(b"")
    assert ft is None and consumed == 0


# ---------------------------------------------------------------------------
# (4) Unknown frame type → FrameError
# ---------------------------------------------------------------------------


def test_decode_unknown_frame_type_raises():
    """decode_frame raises FrameError for an unrecognised frame type byte."""
    # Manually craft a frame with type byte 0xFF.
    total_length = 5  # header only, no payload
    raw = struct.pack(">IBI", total_length, 0xFF, 1)
    with pytest.raises(FrameError, match="Unknown frame type"):
        decode_frame(raw)


# ---------------------------------------------------------------------------
# In-process WebSocket duplex pipe helper
# ---------------------------------------------------------------------------


class _InMemoryDuplex:
    """A pair of asyncio Queues that simulate a bidirectional WebSocket.

    _InMemoryDuplex.side_a and side_b are each ws-like objects with
    send(bytes)/recv()->bytes.

    Data sent on side_a is received on side_b and vice versa.
    """

    class _Half:
        def __init__(self, send_q: asyncio.Queue, recv_q: asyncio.Queue) -> None:
            self._send_q = send_q
            self._recv_q = recv_q

        async def send(self, data: bytes) -> None:
            await self._send_q.put(data)

        async def recv(self) -> bytes:
            chunk = await self._recv_q.get()
            if chunk is None:
                raise ConnectionResetError("duplex closed")
            return chunk

        async def close(self) -> None:
            await self._send_q.put(None)  # poison pill

        # FastAPI WebSocket compatibility (send_bytes / receive_bytes)
        async def send_bytes(self, data: bytes) -> None:
            await self.send(data)

        async def receive_bytes(self) -> bytes:
            return await self.recv()

    def __init__(self) -> None:
        q_ab: asyncio.Queue = asyncio.Queue()  # a → b
        q_ba: asyncio.Queue = asyncio.Queue()  # b → a
        self.side_a = self._Half(send_q=q_ab, recv_q=q_ba)
        self.side_b = self._Half(send_q=q_ba, recv_q=q_ab)


# ---------------------------------------------------------------------------
# (5) Full loopback test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_loopback_echo():
    """
    Full round-trip through the tunnel:

        TCP client → local proxy (broker) → ws frames → agent → echo server
                  ← local proxy (broker) ← ws frames ← agent ←
    """
    # Step 1: Start a real TCP echo server on localhost.
    echo_received: list[bytes] = []

    async def echo_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            data = await reader.read(1024)
            if not data:
                break
            echo_received.append(data)
            writer.write(data)
            await writer.drain()
        writer.close()

    echo_server = await asyncio.start_server(echo_handler, "127.0.0.1", 0)
    echo_port = echo_server.sockets[0].getsockname()[1]
    echo_task = asyncio.ensure_future(echo_server.serve_forever())

    try:
        # Step 2: Wire an in-process agent ↔ broker over an in-memory duplex.
        bridge_id = str(uuid.uuid4())
        duplex = _InMemoryDuplex()

        broker = BridgeBroker()

        # Register the broker-side connection.
        await broker.register(bridge_id, duplex.side_a)

        # Start the agent-side (connects to the echo server inside the "VPC").
        agent = BridgeAgent(ws=duplex.side_b, bridge_id=bridge_id)
        agent_task = asyncio.ensure_future(agent.run())

        try:
            # Step 3: Open a proxy through the broker.
            local_host, local_port = await broker.open_tcp_proxy(
                bridge_id, "127.0.0.1", echo_port
            )

            # Step 4: Connect a client to the local proxy.
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(local_host, local_port), timeout=5.0
            )

            # Step 5: Send data and assert the echo comes back.
            message = b"hello tunnel world"
            writer.write(message)
            await writer.drain()

            response = await asyncio.wait_for(reader.read(1024), timeout=5.0)
            assert response == message, f"Expected {message!r}, got {response!r}"

            # Clean teardown.
            writer.close()
        finally:
            agent_task.cancel()
            try:
                await agent_task
            except (asyncio.CancelledError, Exception):
                pass
            await broker.unregister(bridge_id)
    finally:
        echo_task.cancel()
        echo_server.close()
        try:
            await echo_task
        except (asyncio.CancelledError, Exception):
            pass


# ---------------------------------------------------------------------------
# (6) No agent → clear error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_agent_raises_bridge_not_connected():
    """open_tcp_proxy raises AppError bridge_not_connected when no agent is registered."""
    broker = BridgeBroker()
    with pytest.raises(AppError) as exc_info:
        await broker.open_tcp_proxy("nonexistent-bridge", "db.internal", 5432)
    assert exc_info.value.code == "bridge_not_connected"
    assert exc_info.value.status == 503


# ---------------------------------------------------------------------------
# (7) network.py bridge mode → proxy NetworkTarget when connected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_network_async_bridge_returns_proxy_when_connected():
    """resolve_network_async bridge mode returns a local proxy NetworkTarget."""
    # Start an echo server to have something for the agent to connect to.
    async def _noop(reader, writer):
        writer.close()

    target_server = await asyncio.start_server(_noop, "127.0.0.1", 0)
    target_port = target_server.sockets[0].getsockname()[1]
    target_task = asyncio.ensure_future(target_server.serve_forever())

    try:
        bridge_id = str(uuid.uuid4())
        duplex = _InMemoryDuplex()

        from app.bridges.broker import get_broker
        broker = get_broker()
        await broker.register(bridge_id, duplex.side_a)

        # Start agent so OPEN frames get handled (needed for proxy to work).
        agent = BridgeAgent(ws=duplex.side_b, bridge_id=bridge_id)
        agent_task = asyncio.ensure_future(agent.run())

        try:
            datastore_config = {
                "network_mode": "bridge",
                "bridge_id": bridge_id,
                "host": "127.0.0.1",
                "port": target_port,
            }
            bridge_row = {"id": bridge_id}

            result = await resolve_network_async(datastore_config, bridge_row)
            assert isinstance(result, NetworkTarget)
            assert result.mode == "bridge"
            assert result.host == "127.0.0.1"
            assert isinstance(result.port, int)
            assert result.port != target_port  # ephemeral port, not the original
            assert callable(result.cleanup)

            # Cleanup must not raise.
            result.cleanup()
        finally:
            agent_task.cancel()
            try:
                await agent_task
            except (asyncio.CancelledError, Exception):
                pass
            await broker.unregister(bridge_id)
    finally:
        target_task.cancel()
        target_server.close()
        try:
            await target_task
        except (asyncio.CancelledError, Exception):
            pass


# ---------------------------------------------------------------------------
# (8) network.py sync bridge mode → 501 when no agent
# ---------------------------------------------------------------------------


def test_resolve_network_sync_bridge_raises_501():
    """resolve_network (sync) raises 501 for bridge mode when no agent is connected."""
    with pytest.raises(AppError) as exc_info:
        resolve_network(
            {"network_mode": "bridge", "bridge_id": "no-such-bridge", "host": "x", "port": 5432},
            bridge={"id": "no-such-bridge"},
        )
    err = exc_info.value
    assert err.status == 501
    # Code matches what existing tests and the API contract expect.
    assert err.code == "network_mode_unavailable"
    assert "bridge" in err.message.lower()


# ---------------------------------------------------------------------------
# (9) resolve_network_async bridge mode → 503 when no agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_network_async_bridge_raises_503_when_no_agent():
    """resolve_network_async raises 503 when bridge mode but no agent is connected."""
    from app.bridges.broker import get_broker
    broker = get_broker()
    # Ensure no agents are registered.
    bridge_id = str(uuid.uuid4())
    assert not broker.is_connected(bridge_id)

    with pytest.raises(AppError) as exc_info:
        await resolve_network_async(
            {"network_mode": "bridge", "host": "db.internal", "port": 5432},
            bridge={"id": bridge_id},
        )
    err = exc_info.value
    assert err.status in (501, 503)
    assert "bridge" in err.code.lower()


# ---------------------------------------------------------------------------
# (10) Direct mode still works after the bridge changes
# ---------------------------------------------------------------------------


def test_resolve_network_direct_unchanged():
    """resolve_network direct mode still returns a verbatim NetworkTarget."""
    target = resolve_network({"host": "db.example.com", "port": 5432})
    assert target.mode == "direct"
    assert target.host == "db.example.com"
    assert target.port == 5432
