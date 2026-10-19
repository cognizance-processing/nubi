"""Storage root for uploaded dashboard images (app.dashboards.images)."""

from __future__ import annotations

import os

import pytest

from app.dashboards import images

_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("NUBI_IMAGE_STORAGE_DIR", raising=False)
    monkeypatch.delenv("LOCAL_STORAGE_ROOT", raising=False)
    monkeypatch.delenv("NUBI_ASSET_STORAGE_URI", raising=False)
    return monkeypatch


def test_default_root_is_durable_storage_data(clean_env) -> None:
    root = images._root_dir()
    assert not root.startswith("/tmp"), root
    assert root.endswith(os.path.join("storage_data", "images")), root


def test_root_follows_local_storage_root(clean_env, tmp_path) -> None:
    clean_env.setenv("LOCAL_STORAGE_ROOT", str(tmp_path))
    assert images._root_dir() == str(tmp_path / "images")


def test_explicit_dir_wins(clean_env, tmp_path) -> None:
    clean_env.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "other"))
    clean_env.setenv("NUBI_IMAGE_STORAGE_DIR", str(tmp_path / "explicit"))
    assert images._root_dir() == str(tmp_path / "explicit")


def test_save_then_load_round_trips(clean_env, tmp_path) -> None:
    clean_env.setenv("LOCAL_STORAGE_ROOT", str(tmp_path))
    saved = images.save_image_bytes(_PNG, "image/png")
    assert (tmp_path / "images" / saved["id"]).is_file()
    assert images.load_image(saved["id"]) == (_PNG, "image/png")


def test_images_under_legacy_tmp_root_still_load(clean_env, tmp_path) -> None:
    """Images uploaded before the default moved out of /tmp keep resolving."""
    clean_env.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "new"))
    legacy = tmp_path / "legacy"
    clean_env.setattr(images, "_LEGACY_ROOT_DIR", str(legacy))
    legacy.mkdir()
    (legacy / "abc123").write_bytes(_PNG)
    (legacy / "abc123.ct").write_bytes(b"image/png")

    assert images.load_image("abc123") == (_PNG, "image/png")

    with pytest.raises(FileNotFoundError):
        images.load_image("missing")


# ── object storage (NUBI_ASSET_STORAGE_URI) ─────────────────────────────────


class _FakeBucket:
    """Stand-in StorageClient: a dict, with a real backend's miss behaviour."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload_bytes(self, data: bytes, key: str) -> str:
        self.objects[key] = data
        return f"s3://bucket/{key}"

    def download_bytes(self, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError:
            raise FileNotFoundError(key) from None


@pytest.fixture
def bucket(clean_env, tmp_path):
    from app.storage import assets

    fake = _FakeBucket()
    clean_env.setenv("NUBI_ASSET_STORAGE_URI", "s3://bucket/")
    clean_env.setattr(assets, "get_storage_client", lambda uri, creds=None: fake)
    # Point local storage somewhere empty, to prove nothing is written there.
    clean_env.setenv("LOCAL_STORAGE_ROOT", str(tmp_path))
    return fake


def test_images_go_to_object_storage_when_configured(bucket, tmp_path) -> None:
    saved = images.save_image_bytes(_PNG, "image/png")
    assert bucket.objects[f"images/{saved['id']}"] == _PNG
    assert bucket.objects[f"images/{saved['id']}.ct"] == b"image/png"
    assert not (tmp_path / "images").exists()
    assert images.load_image(saved["id"]) == (_PNG, "image/png")


def test_object_storage_miss_does_not_fall_back_to_local(bucket, tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        images.load_image("missing")


def test_avatars_use_object_storage_when_configured(bucket) -> None:
    import asyncio

    from app.assets.service import _store_local

    url = asyncio.run(_store_local(_PNG, "avatars/user/u1/abc.png"))
    assert bucket.objects["avatars/user/u1/abc.png"] == _PNG
    assert url.endswith("/assets/avatars/user/u1/abc.png")


def test_avatar_serve_route_reads_object_storage(bucket) -> None:
    import asyncio

    from app.assets.routes import serve_avatar

    bucket.objects["avatars/user/u1/abc.png"] = _PNG
    resp = asyncio.run(serve_avatar("user/u1/abc.png"))
    assert resp.status_code == 200 and resp.body == _PNG
    assert asyncio.run(serve_avatar("user/u1/missing.png")).status_code == 404
