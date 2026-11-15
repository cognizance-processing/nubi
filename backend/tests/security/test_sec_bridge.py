"""Bridge agents only dial what their operator allowed, over a protected control channel."""

from __future__ import annotations

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)


def _allowed(host, port=5432, env=None, monkeypatch=None):
    from app.bridges.agent import target_allowed

    if monkeypatch is not None:
        if env is None:
            monkeypatch.delenv("BRIDGE_ALLOWED_TARGETS", raising=False)
        else:
            monkeypatch.setenv("BRIDGE_ALLOWED_TARGETS", env)
    return target_allowed(host, port)


@pytest.mark.parametrize("host", ["169.254.169.254", "169.254.10.10", "fe80::1", "fd00:ec2::254"])
def test_link_local_and_metadata_are_refused_by_default(monkeypatch, host):
    assert _allowed(host, 80, None, monkeypatch) is False


def test_default_allows_ordinary_private_targets(monkeypatch):
    assert _allowed("10.0.0.5", 5432, None, monkeypatch) is True
    assert _allowed("127.0.0.1", 3306, None, monkeypatch) is True


def test_allow_list_restricts_what_the_agent_dials(monkeypatch):
    env = "10.0.0.5:5432, db.internal, 192.168.0.0/24:3306, *.corp.example"
    assert _allowed("10.0.0.5", 5432, env, monkeypatch) is True
    assert _allowed("10.0.0.5", 22, env, monkeypatch) is False
    assert _allowed("db.internal", 1234, env, monkeypatch) is True
    assert _allowed("192.168.0.77", 3306, env, monkeypatch) is True
    assert _allowed("192.168.0.77", 5432, env, monkeypatch) is False
    assert _allowed("192.168.1.77", 3306, env, monkeypatch) is False
    assert _allowed("pg.corp.example", 5432, env, monkeypatch) is True
    assert _allowed("10.0.0.6", 5432, env, monkeypatch) is False
    assert _allowed("169.254.169.254", 80, env, monkeypatch) is False
    # An operator can still opt a link-local address in explicitly.
    assert _allowed("169.254.169.254", 80, "169.254.169.254:80", monkeypatch) is True


@pytest.mark.parametrize(
    "url,ok",
    [
        ("wss://api.example.com/api/v1", True),
        ("ws://localhost:8000/api/v1", True),
        ("ws://127.0.0.1:8000/api/v1", True),
        ("ws://[::1]:8000/api/v1", True),
        ("ws://api.example.com/api/v1", False),
        ("ws://10.0.0.5:8000/api/v1", False),
        ("http://api.example.com", False),
    ],
)
def test_control_channel_must_be_encrypted_off_localhost(url, ok):
    from app.bridges.agent import control_plane_url_allowed

    assert control_plane_url_allowed(url) is ok


def test_cli_agent_applies_the_same_control_channel_rule():
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[3] / "cli" / "nubi_cli" / "bridge_agent.py"
    src = path.read_text()
    assert "control_plane_url_allowed" in src


@pytest.mark.asyncio
async def test_agent_refuses_a_disallowed_target_without_dialling(monkeypatch):
    import asyncio

    from app.bridges import agent as agent_mod
    from app.bridges.protocol import FrameType, decode_frame

    monkeypatch.setenv("BRIDGE_ALLOWED_TARGETS", "db.internal:5432")
    sent: list[bytes] = []

    class _WS:
        async def send(self, data):
            sent.append(data)

    async def _no_dial(*a, **k):
        raise AssertionError("the agent dialled a target that is not allowed")

    monkeypatch.setattr(asyncio, "open_connection", _no_dial)
    agent = agent_mod.BridgeAgent(ws=_WS(), bridge_id="b1")
    await agent._open_tcp_stream(7, "10.9.9.9", 22)
    assert sent, "an error frame should be returned"
    ftype, stream_id, payload = decode_frame(sent[0])[:3]
    assert ftype == FrameType.ERROR and stream_id == 7
    assert b"10.9.9.9" not in payload or b"not allowed" in payload


def test_bridge_rows_never_carry_a_token():
    from app.routes.bridges import BridgeOut

    out = BridgeOut(
        id="b", org_id="o", created_by="u", name="n", status="offline", last_seen_at=None,
        config={"token": "plaintext-secret", "region": "eu"}, created_at="t", updated_at="t",
    )
    assert "plaintext-secret" not in out.model_dump_json()
    assert out.config == {"region": "eu"}


def test_plaintext_config_token_no_longer_authenticates():
    import inspect

    from app.routes import bridges

    src = inspect.getsource(bridges.bridge_connect)
    assert 'config", {}).get("token")' not in src
    assert "legacy_token" not in src
