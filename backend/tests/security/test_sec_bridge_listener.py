"""A bridge tunnel's local listener serves only the connection it was opened for.

Each tunnel listens on its own unpredictable loopback address, and only a
connection made by this server process is carried into the customer network.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="loopback range and /proc are Linux-specific")


class _Stream:
    def __init__(self) -> None:
        self.stream_id = 1
        self.ready_event = asyncio.Event()
        self.ready_event.set()
        self.error = None
        self.inbound: asyncio.Queue = asyncio.Queue()


class _BridgeConn:
    bridge_id = "b1"

    def __init__(self) -> None:
        self.opened: list[tuple[str, int]] = []
        self.data: list[bytes] = []
        self.streams: list[_Stream] = []

    async def open_stream(self, host, port):
        self.opened.append((host, port))
        stream = _Stream()
        self.streams.append(stream)
        return stream

    def finish(self) -> None:
        """The far end closes every stream (lets the proxy shut down)."""
        for stream in self.streams:
            stream.inbound.put_nowait(None)

    async def send_data(self, _stream_id, data):
        self.data.append(data)

    async def send_close(self, _stream_id):
        return None


async def _proxy():
    from app.bridges.broker import _TcpProxy

    conn = _BridgeConn()
    proxy = _TcpProxy(conn, "db.internal", 5432)
    host, port = await proxy.start()
    return conn, proxy, host, port


@pytest.mark.asyncio
async def test_each_tunnel_listens_on_its_own_loopback_address():
    _c1, p1, host1, _port1 = await _proxy()
    _c2, p2, host2, _port2 = await _proxy()
    try:
        for host in (host1, host2):
            assert host.startswith("127.") and host != "127.0.0.1"
        assert host1 != host2
    finally:
        await p1.close()
        await p2.close()


@pytest.mark.asyncio
async def test_the_server_process_itself_is_carried():
    conn, proxy, host, port = await _proxy()
    try:
        _reader, writer = await asyncio.open_connection(host, port)
        writer.write(b"hello")
        await writer.drain()
        for _ in range(50):
            if conn.data:
                break
            await asyncio.sleep(0.02)
        assert conn.opened == [("db.internal", 5432)]
        assert conn.data == [b"hello"]
        writer.close()
    finally:
        conn.finish()
        await proxy.close()


@pytest.mark.asyncio
async def test_another_local_process_is_not_carried():
    conn, proxy, host, port = await _proxy()
    code = (
        "import socket,sys\n"
        "s=socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=5)\n"
        "s.sendall(b'intruder')\n"
        "s.settimeout(5)\n"
        "try:\n    print(len(s.recv(16)))\nexcept OSError:\n    print(0)\n"
    )
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-I", "-S", "-c", code, host, str(port)],
            capture_output=True, text=True, timeout=30,
        )
        await asyncio.sleep(0.1)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "0"  # closed without a byte
        assert conn.opened == [] and conn.data == []
    finally:
        conn.finish()
        await proxy.close()
