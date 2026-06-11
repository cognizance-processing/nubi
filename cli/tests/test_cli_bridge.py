"""Tests for the Bridge v2 agent (design §7, phase 3 — agent side).

Coverage
--------
- ``nubi bridge start`` wires the token into the WS handshake header AND the
  handshake control message (mock the connect/channel — no real network).
- A revoked / auth-reject from the control plane exits the agent cleanly with a
  clear message (CLI exit code 2; runtime raises BridgeRevoked).
- Agent-side ingest claims a task, consumes an INJECTED fake grant to "upload"
  to a fake staging target, and reports a correct manifest
  ``{files:[{path,size,sha256}], row_counts}``.
- The agent never touches a connector secret: the task/source descriptor it is
  given carries no credentials, and the only secret material it sees is the
  ephemeral grant (held in memory, asserted not written to disk).
- Token resolution precedence (flag > env > config file) and config helpers.

No real WebSocket, cloud, or file connector is used anywhere.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from nubi_cli import bridge_agent as ba
from nubi_cli import bridge_config as bc
from nubi_cli.bridge_agent import (
    BridgeRevoked,
    BridgeSession,
    ControlMsg,
    IngestTask,
    run_ingest_task,
)
from nubi_cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeChannel:
    """In-memory ControlChannel: scripted inbound queue + recorded outbound."""

    def __init__(self, inbound: list[dict[str, Any]]) -> None:
        self._inbound = list(inbound)
        self.sent: list[dict[str, Any]] = []

    async def send(self, msg: dict[str, Any]) -> None:
        self.sent.append(msg)

    async def recv(self) -> dict[str, Any]:
        if not self._inbound:
            # Nothing left — emulate a closed channel so run() loops exit.
            raise ConnectionError("channel closed")
        return self._inbound.pop(0)


class FakeIdentity:
    bridge_id = "bridge-123"
    control_plane_url = "ws://cp.test/api/v1"
    token = "nubi_br_TESTTOKEN"


class FakeSourceOpener:
    """Yields (rel_path, BytesIO) pairs — carries NO credentials.

    Records the source descriptor it was handed so a test can assert the agent
    passed only connector_id/path (never a secret).
    """

    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files
        self.seen_source: dict[str, Any] | None = None

    def open_source(self, source: dict[str, Any]):
        self.seen_source = source
        for rel, data in self._files.items():
            yield rel, io.BytesIO(data)


class FakeStagingUploader:
    """Captures grant + bytes; verifies the grant is memory-only (never disk)."""

    def __init__(self) -> None:
        self.grants_seen: list[dict[str, Any]] = []
        self.uploaded: dict[str, bytes] = {}

    async def upload(self, grant: dict[str, Any], rel_path: str, chunks) -> int:
        self.grants_seen.append(grant)
        buf = bytearray()
        async for chunk in chunks:
            buf.extend(chunk)
        self.uploaded[rel_path] = bytes(buf)
        return len(buf)


# ---------------------------------------------------------------------------
# run_ingest_task — grant consumed in-memory, manifest correct
# ---------------------------------------------------------------------------


class TestRunIngestTask:
    def test_streams_and_builds_manifest(self) -> None:
        files = {"outbound/a.csv": b"id,name\n1,foo\n", "outbound/b.csv": b"x" * 5000}
        opener = FakeSourceOpener(files)
        uploader = FakeStagingUploader()
        task = IngestTask(
            task_id="t1", run_id="run-1",
            source={"connector_id": "sftp-1", "path": "outbound/*.csv"},
        )
        grant = {"urls": {"outbound/a.csv": "https://stg/a", "outbound/b.csv": "https://stg/b"}}

        manifest = asyncio.run(run_ingest_task(task, grant, opener, uploader))

        # Bytes streamed to staging unchanged.
        assert uploader.uploaded == files
        # Manifest shape: {files:[{path,size,sha256}], row_counts}.
        by_path = {e.path: e for e in manifest.files}
        assert set(by_path) == set(files)
        for rel, data in files.items():
            assert by_path[rel].size == len(data)
            assert by_path[rel].sha256 == hashlib.sha256(data).hexdigest()
        assert set(manifest.row_counts) == set(files)
        # to_dict matches the backend staging manifest contract.
        d = manifest.to_dict()
        assert set(d) == {"files", "row_counts"}
        assert all(set(f) == {"path", "size", "sha256"} for f in d["files"])

    def test_grant_held_in_memory_only(self, tmp_path: Path, monkeypatch) -> None:
        """The grant must never be written to disk during ingest."""
        # Run from an empty cwd; assert nothing on disk contains the grant token.
        monkeypatch.chdir(tmp_path)
        opener = FakeSourceOpener({"f.csv": b"hello"})
        uploader = FakeStagingUploader()
        task = IngestTask(task_id="t", run_id="r", source={"connector_id": "c", "path": "f.csv"})
        secret_url = "https://stg/SECRETSIGNATURE?X-Amz-Signature=deadbeef"
        grant = {"urls": {"f.csv": secret_url}}

        asyncio.run(run_ingest_task(task, grant, opener, uploader))

        assert uploader.grants_seen == [grant]
        # No file under tmp_path leaks the grant signature.
        for p in tmp_path.rglob("*"):
            if p.is_file():
                assert "SECRETSIGNATURE" not in p.read_text(errors="ignore")

    def test_source_descriptor_carries_no_secret(self) -> None:
        """The agent only ever sees connector_id/path — never a connector secret."""
        opener = FakeSourceOpener({"f.csv": b"x"})
        uploader = FakeStagingUploader()
        task = IngestTask(
            task_id="t", run_id="r",
            source={"connector_id": "sftp-1", "path": "outbound/*.csv"},
        )
        asyncio.run(run_ingest_task(task, {"urls": {"f.csv": "u"}}, opener, uploader))
        # The descriptor handed to the opener has no credential-ish keys.
        assert opener.seen_source is not None
        forbidden = {"secret", "password", "private_key", "credentials", "token"}
        assert not (set(opener.seen_source) & forbidden)


# ---------------------------------------------------------------------------
# BridgeSession — handshake, revoke, claim/grant/manifest
# ---------------------------------------------------------------------------


class TestBridgeSessionHandshake:
    def test_handshake_presents_token(self) -> None:
        ch = FakeChannel([{"type": ControlMsg.HANDSHAKE_OK}])
        session = BridgeSession(ch, FakeIdentity(), FakeSourceOpener({}), FakeStagingUploader())
        asyncio.run(session.handshake())
        hs = ch.sent[0]
        assert hs["type"] == ControlMsg.HANDSHAKE
        assert hs["token"] == FakeIdentity.token
        assert hs["bridge_id"] == FakeIdentity.bridge_id

    def test_handshake_auth_reject_raises_clean(self) -> None:
        ch = FakeChannel([{"type": ControlMsg.AUTH_REJECT, "reason": "revoked"}])
        session = BridgeSession(ch, FakeIdentity(), FakeSourceOpener({}), FakeStagingUploader())
        with pytest.raises(BridgeRevoked, match="revoked"):
            asyncio.run(session.handshake())

    def test_heartbeat_reasserts_token(self) -> None:
        ch = FakeChannel([])
        session = BridgeSession(ch, FakeIdentity(), FakeSourceOpener({}), FakeStagingUploader())
        asyncio.run(session.send_heartbeat())
        hb = ch.sent[0]
        assert hb["type"] == ControlMsg.HEARTBEAT
        assert hb["token"] == FakeIdentity.token


class TestBridgeSessionClaim:
    def test_claim_grant_manifest_roundtrip(self) -> None:
        files = {"outbound/orders.csv": b"id\n1\n2\n"}
        opener = FakeSourceOpener(files)
        uploader = FakeStagingUploader()
        assign = {
            "type": ControlMsg.TASK_ASSIGN, "task_id": "t9", "run_id": "run-9",
            "source": {"connector_id": "sftp-1", "path": "outbound/*.csv"},
        }
        grant_msg = {
            "type": ControlMsg.GRANT, "task_id": "t9",
            "grant": {"urls": {"outbound/orders.csv": "https://stg/orders"}},
        }
        # assign is passed directly to _handle_message; only the grant reply is
        # queued for the in-claim recv().
        ch = FakeChannel([grant_msg])
        session = BridgeSession(ch, FakeIdentity(), opener, uploader, heartbeat_interval_s=999)

        # Drive one message dispatch directly (avoids the infinite run loop).
        asyncio.run(session._handle_message(assign))

        kinds = [m["type"] for m in ch.sent]
        assert ControlMsg.TASK_CLAIM in kinds
        assert ControlMsg.MANIFEST in kinds
        manifest_msg = next(m for m in ch.sent if m["type"] == ControlMsg.MANIFEST)
        files_out = manifest_msg["manifest"]["files"]
        assert files_out[0]["path"] == "outbound/orders.csv"
        assert files_out[0]["sha256"] == hashlib.sha256(files[next(iter(files))]).hexdigest()
        # Claim came BEFORE the grant was consumed.
        assert kinds.index(ControlMsg.TASK_CLAIM) < kinds.index(ControlMsg.MANIFEST)

    def test_revoke_mid_claim_raises(self) -> None:
        assign = {
            "type": ControlMsg.TASK_ASSIGN, "task_id": "t", "run_id": "r",
            "source": {"connector_id": "c", "path": "p"},
        }
        # Control plane revokes instead of granting.
        ch = FakeChannel([{"type": ControlMsg.AUTH_REJECT, "reason": "revoked"}])
        session = BridgeSession(ch, FakeIdentity(), FakeSourceOpener({}), FakeStagingUploader())
        with pytest.raises(BridgeRevoked):
            asyncio.run(session._handle_message(assign))

    def test_failed_ingest_reports_task_error_not_manifest(self) -> None:
        class BoomUploader(FakeStagingUploader):
            async def upload(self, grant, rel_path, chunks):
                raise RuntimeError("staging unreachable")

        assign = {
            "type": ControlMsg.TASK_ASSIGN, "task_id": "tE", "run_id": "rE",
            "source": {"connector_id": "c", "path": "p"},
        }
        grant_msg = {"type": ControlMsg.GRANT, "task_id": "tE", "grant": {"urls": {"f": "u"}}}
        ch = FakeChannel([grant_msg])
        session = BridgeSession(ch, FakeIdentity(), FakeSourceOpener({"f": b"x"}), BoomUploader())
        asyncio.run(session._handle_message(assign))
        kinds = [m["type"] for m in ch.sent]
        assert ControlMsg.TASK_ERROR in kinds
        assert ControlMsg.MANIFEST not in kinds


# ---------------------------------------------------------------------------
# serve() — token in WS handshake header; auth-reject close → BridgeRevoked
# ---------------------------------------------------------------------------


class TestServe:
    def test_connect_uses_bridge_token_header(self) -> None:
        captured: dict[str, Any] = {}

        class FakeWs:
            def __init__(self, scripted: list[Any]) -> None:
                self._scripted = scripted
            async def send(self, data: Any) -> None:
                pass
            async def recv(self) -> Any:
                if not self._scripted:
                    raise ConnectionError("closed")
                return self._scripted.pop(0)
            async def close(self) -> None:
                pass

        async def fake_connect(url: str, headers: dict[str, str]) -> Any:
            captured["url"] = url
            captured["headers"] = headers
            # Handshake OK then channel closes → serve returns (once=True).
            return FakeWs([json.dumps({"type": ControlMsg.HANDSHAKE_OK})])

        asyncio.run(
            ba.serve(
                FakeIdentity(), FakeSourceOpener({}), FakeStagingUploader(),
                connect=fake_connect, once=True,
            )
        )
        assert captured["headers"]["X-Bridge-Token"] == FakeIdentity.token
        assert captured["url"].endswith(f"/bridges/{FakeIdentity.bridge_id}/connect")

    def test_auth_reject_close_code_raises_revoked(self) -> None:
        class Rejected(Exception):
            code = ba.WS_CLOSE_AUTH_REJECT

        async def fake_connect(url: str, headers: dict[str, str]) -> Any:
            raise Rejected("4401")

        with pytest.raises(BridgeRevoked):
            asyncio.run(
                ba.serve(
                    FakeIdentity(), FakeSourceOpener({}), FakeStagingUploader(),
                    connect=fake_connect, once=True,
                )
            )


# ---------------------------------------------------------------------------
# PresignedStagingUploader — reads the real control-plane grant shape
# ---------------------------------------------------------------------------


class TestPresignedUploader:
    def test_reads_real_grant_uploads_shape(self) -> None:
        from nubi_cli.bridge_sources import PresignedStagingUploader

        grant = {
            "kind": "s3_presigned",
            "prefix": "orgs/o1/staging/r1/",
            "uploads": {"a.csv": {"method": "PUT", "url": "https://stg/a"}},
        }
        assert PresignedStagingUploader._url_for(grant, "a.csv") == "https://stg/a"
        assert PresignedStagingUploader._url_for(grant, "missing.csv") is None

    def test_streams_to_presigned_url(self, monkeypatch) -> None:
        from nubi_cli.bridge_sources import PresignedStagingUploader

        captured: dict[str, Any] = {}

        class FakeResp:
            def raise_for_status(self) -> None:
                pass

        class FakeAsyncClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def put(self, url, content):
                captured["url"] = url
                buf = bytearray()
                async for chunk in content:
                    buf.extend(chunk)
                captured["body"] = bytes(buf)
                return FakeResp()

        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: FakeAsyncClient())

        async def chunks():
            yield b"hello "
            yield b"world"

        grant = {"uploads": {"f": {"method": "PUT", "url": "https://stg/f"}}}
        n = asyncio.run(PresignedStagingUploader().upload(grant, "f", chunks()))
        assert n == 11
        assert captured["url"] == "https://stg/f"
        assert captured["body"] == b"hello world"


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


class TestBridgeConfig:
    def test_token_precedence_flag_env_file(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "bridge.json"
        cfg.write_text(json.dumps({"token": "nubi_br_FILE", "bridge_id": "b-file"}))
        monkeypatch.setattr(bc, "_BRIDGE_CONFIG_PATH", cfg)
        monkeypatch.delenv("NUBI_BRIDGE_TOKEN", raising=False)

        assert bc.resolve_token() == "nubi_br_FILE"  # file
        monkeypatch.setenv("NUBI_BRIDGE_TOKEN", "nubi_br_ENV")
        assert bc.resolve_token() == "nubi_br_ENV"  # env > file
        assert bc.resolve_token("nubi_br_FLAG") == "nubi_br_FLAG"  # flag > env

    def test_save_and_resolve_identity(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "bridge.json"
        monkeypatch.setattr(bc, "_BRIDGE_CONFIG_PATH", cfg)
        for var in ("NUBI_BRIDGE_TOKEN", "NUBI_BRIDGE_ID", "NUBI_CONTROL_PLANE_URL"):
            monkeypatch.delenv(var, raising=False)
        bc.save_bridge_config(
            token="nubi_br_X", bridge_id="b1", control_plane_url="wss://cp/api/v1"
        )
        ident = bc.resolve_identity()
        assert ident.bridge_id == "b1"
        assert ident.token == "nubi_br_X"
        assert ident.control_plane_url == "wss://cp/api/v1"
        # __repr__ never exposes the token.
        assert "nubi_br_X" not in repr(ident)
        assert "<redacted>" in repr(ident)

    def test_resolve_identity_missing_token_raises(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "bridge.json"
        monkeypatch.setattr(bc, "_BRIDGE_CONFIG_PATH", cfg)
        for var in ("NUBI_BRIDGE_TOKEN", "NUBI_BRIDGE_ID"):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(bc.BridgeConfigError):
            bc.resolve_identity()


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class TestBridgeCLI:
    def test_status_shows_token_presence_not_value(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "bridge.json"
        cfg.write_text(json.dumps({"token": "nubi_br_SECRET", "bridge_id": "b1"}))
        monkeypatch.setattr(bc, "_BRIDGE_CONFIG_PATH", cfg)
        for var in ("NUBI_BRIDGE_TOKEN", "NUBI_BRIDGE_ID"):
            monkeypatch.delenv(var, raising=False)
        result = runner.invoke(app, ["bridge", "status"])
        out = result.output + (result.stderr or "")
        assert result.exit_code == 0
        assert "present" in out
        assert "nubi_br_SECRET" not in out  # never print the value

    def test_configure_persists_token(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "bridge.json"
        monkeypatch.setattr(bc, "_BRIDGE_CONFIG_PATH", cfg)
        result = runner.invoke(
            app, ["bridge", "configure", "--token", "nubi_br_NEW", "--bridge-id", "bx"]
        )
        assert result.exit_code == 0
        saved = json.loads(cfg.read_text())
        assert saved["token"] == "nubi_br_NEW"
        assert saved["bridge_id"] == "bx"

    def test_start_missing_token_exits_1(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "bridge.json"
        monkeypatch.setattr(bc, "_BRIDGE_CONFIG_PATH", cfg)
        for var in ("NUBI_BRIDGE_TOKEN", "NUBI_BRIDGE_ID"):
            monkeypatch.delenv(var, raising=False)
        result = runner.invoke(app, ["bridge", "start"])
        out = result.output + (result.stderr or "")
        assert result.exit_code == 1
        assert "No bridge token" in out

    def test_start_revoked_exits_2(self, tmp_path: Path, monkeypatch) -> None:
        cfg = tmp_path / "bridge.json"
        cfg.write_text(json.dumps({"token": "nubi_br_X", "bridge_id": "b1"}))
        monkeypatch.setattr(bc, "_BRIDGE_CONFIG_PATH", cfg)
        for var in ("NUBI_BRIDGE_TOKEN", "NUBI_BRIDGE_ID"):
            monkeypatch.delenv(var, raising=False)

        async def fake_serve(*args, **kwargs):
            raise BridgeRevoked("bridge revoked")

        monkeypatch.setattr(ba, "serve", fake_serve)
        result = runner.invoke(app, ["bridge", "start"])
        out = result.output + (result.stderr or "")
        assert result.exit_code == 2
        assert "revoked" in out.lower()
