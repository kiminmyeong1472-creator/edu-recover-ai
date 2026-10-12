"""Mount verification rejects container paths and escaped runtime stores."""
from pathlib import Path

import pytest

from app.training.backup_service import ENV, _persistent_volume_verified


@pytest.fixture
def runtime_mount(tmp_path, monkeypatch):
    root = tmp_path / "volume"
    root.mkdir()
    for name in ("source", "backups", "state"):
        (root / name).mkdir()
    manifest = root / "manifest.json"
    manifest.write_text("[]")
    for key, path in {
        "source_root": root / "source",
        "server_root": root / "backups",
        "state_dir": root / "state",
        "manifest": manifest,
    }.items():
        monkeypatch.setenv(ENV[key], str(path))
    monkeypatch.delenv("EDURECOVER_PERSISTENT_MOUNT_PATH", raising=False)
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    return root


def observed_mount(monkeypatch, root):
    # Model the OS mount check for positive cases; the real check is exercised
    # separately on an ordinary, unmounted temporary directory.
    monkeypatch.setattr(Path, "is_mount", lambda path: path == root)


def test_aws_mount_opt_in_does_not_require_railway_variables(runtime_mount, monkeypatch):
    observed_mount(monkeypatch, runtime_mount)
    monkeypatch.setenv("EDURECOVER_PERSISTENT_MOUNT_PATH", str(runtime_mount))
    assert _persistent_volume_verified() is True


def test_existing_railway_mount_remains_supported(runtime_mount, monkeypatch):
    observed_mount(monkeypatch, runtime_mount)
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(runtime_mount))
    assert _persistent_volume_verified() is True


def test_unmounted_directory_cannot_claim_durability(runtime_mount, monkeypatch):
    monkeypatch.setenv("EDURECOVER_PERSISTENT_MOUNT_PATH", str(runtime_mount))
    assert runtime_mount.is_mount() is False
    assert _persistent_volume_verified() is False


@pytest.mark.parametrize("explicit", ["", "not-an-absolute-mount", "/missing-mount"])
def test_invalid_explicit_mount_never_falls_back_to_railway(
        runtime_mount, monkeypatch, explicit):
    observed_mount(monkeypatch, runtime_mount)
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(runtime_mount))
    monkeypatch.setenv("EDURECOVER_PERSISTENT_MOUNT_PATH", explicit)
    assert _persistent_volume_verified() is False


def test_root_filesystem_is_not_a_persistent_data_volume(runtime_mount, monkeypatch):
    monkeypatch.setenv("EDURECOVER_PERSISTENT_MOUNT_PATH", "/")
    assert Path("/").is_mount() is True
    assert _persistent_volume_verified() is False


@pytest.mark.parametrize("key", ["source_root", "server_root", "state_dir", "manifest"])
def test_each_runtime_path_must_stay_on_the_verified_mount(
        runtime_mount, tmp_path, monkeypatch, key):
    observed_mount(monkeypatch, runtime_mount)
    monkeypatch.setenv("EDURECOVER_PERSISTENT_MOUNT_PATH", str(runtime_mount))
    monkeypatch.setenv(ENV[key], str(tmp_path / "outside"))
    assert _persistent_volume_verified() is False


def test_symlink_escape_cannot_claim_durability(runtime_mount, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    escape = runtime_mount / "linked-state"
    escape.symlink_to(outside, target_is_directory=True)
    observed_mount(monkeypatch, runtime_mount)
    monkeypatch.setenv("EDURECOVER_PERSISTENT_MOUNT_PATH", str(runtime_mount))
    monkeypatch.setenv(ENV["state_dir"], str(escape))
    assert _persistent_volume_verified() is False


def test_missing_runtime_path_blocks_verification(runtime_mount, monkeypatch):
    observed_mount(monkeypatch, runtime_mount)
    monkeypatch.setenv("EDURECOVER_PERSISTENT_MOUNT_PATH", str(runtime_mount))
    monkeypatch.delenv(ENV["source_root"])
    assert _persistent_volume_verified() is False


def test_mount_inspection_failure_is_reported_as_unverified(runtime_mount, monkeypatch):
    def denied(_path):
        raise OSError("test mount metadata unavailable")
    monkeypatch.setattr(Path, "is_mount", denied)
    monkeypatch.setenv("EDURECOVER_PERSISTENT_MOUNT_PATH", str(runtime_mount))
    assert _persistent_volume_verified() is False
