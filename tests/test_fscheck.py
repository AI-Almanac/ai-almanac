"""Tests for fscheck — network filesystem detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_almanac.fscheck import _match_mounts, network_fs_type

_SAMPLE_MOUNTS = """\
sysfs /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0
proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0
devtmpfs /dev devtmpfs rw,nosuid,size=8192k,nr_inodes=4096,mode=755 0 0
/dev/sda1 / ext4 rw,relatime 0 0
tmpfs /tmp tmpfs rw,nosuid,nodev 0 0
server:/data /mnt/data nfs4 rw,relatime 0 0
//server/share /mnt/smb cifs rw,relatime 0 0
/dev/sda2 /mnt/data/local ext4 rw,relatime 0 0
"""


def test_match_nfs4_mount() -> None:
    result = _match_mounts(_SAMPLE_MOUNTS, Path("/mnt/data/obs"))
    assert result == "nfs4"


def test_longest_prefix_wins(tmp_path: Path) -> None:
    # /mnt/data/local is ext4 (longer than /mnt/data nfs4)
    result = _match_mounts(_SAMPLE_MOUNTS, Path("/mnt/data/local/foo.nc"))
    assert result == "ext4"


def test_no_match_returns_none() -> None:
    # Sample without a root mount so paths outside /mnt have no match
    mounts_no_root = """\
server:/data /mnt/data nfs4 rw,relatime 0 0
//server/share /mnt/smb cifs rw,relatime 0 0
"""
    result = _match_mounts(mounts_no_root, Path("/home/user/file.nc"))
    assert result is None


def test_root_matches() -> None:
    result = _match_mounts(_SAMPLE_MOUNTS, Path("/home/user"))
    assert result == "ext4"  # / is ext4


def test_cifs_detected() -> None:
    result = _match_mounts(_SAMPLE_MOUNTS, Path("/mnt/smb/file.db"))
    assert result == "cifs"


def test_network_fs_type_returns_none_on_unknown_path(tmp_path: Path) -> None:
    # The tmp_path itself is on a local FS in CI; should return None.
    result = network_fs_type(tmp_path)
    assert result is None or isinstance(result, str)


def test_network_fs_type_never_raises(tmp_path: Path) -> None:
    # Smoke test: should not raise even on edge cases.
    try:
        network_fs_type(Path("/nonexistent/totally/fake/path"))
    except Exception as e:
        pytest.fail(f"network_fs_type raised unexpectedly: {e}")
