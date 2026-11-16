"""Oversized static assets stay deliverable behind a response-size cap."""

from __future__ import annotations

import gzip
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import static_assets
from app.static_assets import LargeAssetStaticFiles


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(static_assets, "LARGE_ASSET_BYTES", 1024)
    (tmp_path / "small.js").write_text("void 0")
    (tmp_path / "engine.wasm").write_bytes(b"\0asm" + os.urandom(64) + b"a" * 8192)
    app = FastAPI()
    app.mount("/assets", LargeAssetStaticFiles(directory=str(tmp_path)), name="assets")
    return TestClient(app), tmp_path


def test_small_files_are_served_as_before(client):
    c, _ = client
    resp = c.get("/assets/small.js")
    assert resp.status_code == 200 and resp.text == "void 0"
    assert "content-encoding" not in resp.headers
    assert c.get("/assets/missing.js").status_code == 404


def test_a_large_file_is_sent_compressed_to_clients_that_accept_it(client):
    c, root = client
    original = (root / "engine.wasm").read_bytes()
    with c.stream("GET", "/assets/engine.wasm", headers={"accept-encoding": "gzip"}) as resp:
        raw = b"".join(resp.iter_raw())
        assert resp.headers["content-encoding"] == "gzip"
        assert resp.headers["content-type"] == "application/wasm"
        assert int(resp.headers["content-length"]) == len(raw) < len(original)
    assert gzip.decompress(raw) == original


def test_a_large_file_is_streamed_without_a_length_otherwise(client):
    c, root = client
    resp = c.get("/assets/engine.wasm", headers={"accept-encoding": "identity"})
    assert resp.status_code == 200
    assert resp.content == (root / "engine.wasm").read_bytes()
    assert "content-length" not in resp.headers
    assert "content-encoding" not in resp.headers


def test_a_large_file_revalidates_and_picks_up_a_new_version(client):
    c, root = client
    first = c.get("/assets/engine.wasm")
    again = c.get("/assets/engine.wasm", headers={"if-none-match": first.headers["etag"]})
    assert again.status_code == 304
    (root / "engine.wasm").write_bytes(b"\0asm" + b"b" * 9000)
    fresh = c.get("/assets/engine.wasm", headers={"if-none-match": first.headers["etag"]})
    assert fresh.status_code == 200 and fresh.content == b"\0asm" + b"b" * 9000
