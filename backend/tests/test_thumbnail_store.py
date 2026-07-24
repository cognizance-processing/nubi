"""Tests for durable board-thumbnail storage (app/dashboards/thumbnail_store.py).

The store is deliberately fail-soft — a thumbnail is decorative and a broken
bucket must never break a dashboard list — so most of what matters here is that
failures degrade to "no thumbnail" rather than raising, and that the key carries
every dimension a render's identity depends on.
"""

from __future__ import annotations

import pytest

from app.dashboards import thumbnail_store as ts

URI_ENV = "NUBI_THUMBNAIL_STORAGE_URI"


class _FakeClient:
    """Minimal StorageClient stand-in recording what it was asked to do."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def upload_bytes(self, data: bytes, key: str) -> str:
        self.objects[key] = data
        return f"s3://bucket/{key}"

    def download_bytes(self, key: str) -> bytes:
        return self.objects[key]          # KeyError on miss, like a real backend

    def list(self, prefix: str = "") -> list[str]:
        return [k for k in self.objects if k.startswith(prefix)]

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


@pytest.fixture
def fake_storage(monkeypatch):
    client = _FakeClient()
    monkeypatch.setenv(URI_ENV, "s3://bucket/")
    monkeypatch.setattr(ts, "_client", lambda: client)
    return client


# ── enablement ───────────────────────────────────────────────────────────────

def test_disabled_without_uri(monkeypatch):
    monkeypatch.delenv(URI_ENV, raising=False)
    assert ts.is_enabled() is False


def test_disabled_store_is_a_no_op(monkeypatch):
    """With no storage configured the caller must still work — just uncached."""
    monkeypatch.delenv(URI_ENV, raising=False)
    assert ts.load("b", "h", "light", "fp") is None
    assert ts.save("b", "h", "light", "fp", "<svg/>") is None
    assert ts.prune_other_versions("b", set()) == 0


# ── key construction ─────────────────────────────────────────────────────────

def test_key_includes_every_identity_dimension():
    key = ts.object_key("board1", "abc123", "dark", "fp9")
    assert key == "boards/board1/abc123-dark-fp9.svg"


def test_theme_is_normalised_to_light_or_dark():
    # Anything that isn't dark renders light, so the key must not fork on case
    # or on an unexpected value — that would strand objects nobody reads back.
    assert "-dark-" in ts.object_key("b", "h", "DARK", "fp")
    assert "-light-" in ts.object_key("b", "h", "", "fp")
    assert "-light-" in ts.object_key("b", "h", "sepia", "fp")


def test_path_traversal_in_board_id_cannot_escape_the_prefix():
    # board_id comes from the URL; a key is a path.
    key = ts.object_key("../../etc/passwd", "h", "light", "fp")
    assert ".." not in key
    assert key.startswith("boards/")
    assert key.count("/") == 2


def test_policy_fingerprint_changes_the_key():
    """Two viewers with different RLS policies must not share one object.

    A thumbnail is rendered data — serving one viewer a picture rendered under
    another's policies would leak rows through an image.
    """
    a = ts.object_key("b", "h", "light", "fp-a")
    b = ts.object_key("b", "h", "light", "fp-b")
    assert a != b


# ── round trip ───────────────────────────────────────────────────────────────

def test_save_then_load_round_trips(fake_storage):
    svg = "<svg><rect/></svg>"
    assert ts.save("b1", "hash1", "light", "fp", svg) is not None
    assert ts.load("b1", "hash1", "light", "fp") == svg


def test_load_miss_returns_none(fake_storage):
    assert ts.load("b1", "never-rendered", "light", "fp") is None


def test_load_survives_a_storage_error(fake_storage, monkeypatch):
    def boom(_key):
        raise RuntimeError("bucket on fire")

    monkeypatch.setattr(fake_storage, "download_bytes", boom)
    assert ts.load("b1", "h", "light", "fp") is None


def test_save_survives_a_storage_error(fake_storage, monkeypatch):
    def boom(_data, _key):
        raise RuntimeError("bucket on fire")

    monkeypatch.setattr(fake_storage, "upload_bytes", boom)
    assert ts.save("b1", "h", "light", "fp", "<svg/>") is None


def test_different_themes_are_separate_objects(fake_storage):
    ts.save("b1", "h", "light", "fp", "<svg>light</svg>")
    ts.save("b1", "h", "dark", "fp", "<svg>dark</svg>")
    assert ts.load("b1", "h", "light", "fp") == "<svg>light</svg>"
    assert ts.load("b1", "h", "dark", "fp") == "<svg>dark</svg>"


# ── pruning ──────────────────────────────────────────────────────────────────

def test_prune_removes_superseded_versions_only(fake_storage):
    old = ts.object_key("b1", "oldhash", "light", "fp")
    new = ts.object_key("b1", "newhash", "light", "fp")
    ts.save("b1", "oldhash", "light", "fp", "<svg>old</svg>")
    ts.save("b1", "newhash", "light", "fp", "<svg>new</svg>")

    assert ts.prune_other_versions("b1", {new}) == 1
    assert fake_storage.deleted == [old]
    assert ts.load("b1", "newhash", "light", "fp") == "<svg>new</svg>"


def test_prune_leaves_other_boards_alone(fake_storage):
    ts.save("b1", "h", "light", "fp", "<svg/>")
    ts.save("b2", "h", "light", "fp", "<svg/>")
    ts.prune_other_versions("b1", set())
    assert ts.load("b2", "h", "light", "fp") == "<svg/>"


def test_prune_keeps_every_key_it_is_told_to(fake_storage):
    """Both themes of the current version survive a prune."""
    light = ts.object_key("b1", "h", "light", "fp")
    dark = ts.object_key("b1", "h", "dark", "fp")
    ts.save("b1", "h", "light", "fp", "<svg/>")
    ts.save("b1", "h", "dark", "fp", "<svg/>")
    assert ts.prune_other_versions("b1", {light, dark}) == 0
