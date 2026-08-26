"""Tests for AI_ALMANAC_ENV_ROOT and the file_lock serialization primitive."""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

import ai_almanac.paths as ap
from ai_almanac.locking import file_lock


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AI_ALMANAC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AI_ALMANAC_ENV_ROOT", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# env_root / path derivation
# ---------------------------------------------------------------------------


def test_env_root_defaults_to_data_root(tmp_path: Path) -> None:
    assert ap.env_root() == ap.data_root()


def test_env_root_overridden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shared = tmp_path / "shared-envs"
    monkeypatch.setenv("AI_ALMANAC_ENV_ROOT", str(shared))
    assert ap.env_root() == shared.resolve()
    assert ap.data_root() != ap.env_root()


def test_benchmark_blending_forecast_dirs_under_env_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = tmp_path / "shared-envs"
    monkeypatch.setenv("AI_ALMANAC_ENV_ROOT", str(shared))
    assert ap.benchmark_env_dir() == shared.resolve() / "benchmark-env"
    assert ap.blending_env_dir() == shared.resolve() / "blending-env"
    assert ap.forecast_env_dir() == shared.resolve() / "forecast-env"


def test_env_dirs_under_data_root_when_env_root_unset(tmp_path: Path) -> None:
    root = ap.data_root()
    assert ap.benchmark_env_dir() == root / "benchmark-env"
    assert ap.blending_env_dir() == root / "blending-env"
    assert ap.forecast_env_dir() == root / "forecast-env"


def test_build_job_env_data_dir_is_data_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = tmp_path / "shared-envs"
    monkeypatch.setenv("AI_ALMANAC_ENV_ROOT", str(shared))
    from ai_almanac.server.services.bundle import build_job_env

    env = build_job_env({}, output_dir="/out", figure_dir="/figs")
    assert env["AI_ALMANAC_DATA_DIR"] == str(ap.data_root())


# ---------------------------------------------------------------------------
# file_lock serialization
# ---------------------------------------------------------------------------


def _hold_lock(path: str, held_event, release_event) -> None:
    """Worker: acquire lock, signal held, wait for release signal."""
    with file_lock(Path(path), message="test lock"):
        held_event.set()
        release_event.wait(timeout=5)


def test_file_lock_serializes_two_processes(tmp_path: Path) -> None:
    lock_path = tmp_path / "test.lock"

    ctx = multiprocessing.get_context("fork")
    held = ctx.Event()
    release = ctx.Event()

    p = ctx.Process(target=_hold_lock, args=(str(lock_path), held, release))
    p.start()

    # Wait until the child holds the lock.
    held.wait(timeout=3)
    assert held.is_set(), "child never acquired the lock"

    # The parent should NOT be able to acquire non-blocking right now.
    try:
        import fcntl

        with open(lock_path, "a") as fh:
            got = True
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                fcntl.flock(fh, fcntl.LOCK_UN)
            except BlockingIOError:
                got = False
        assert not got, "parent should have been blocked while child holds the lock"
    except ImportError:
        pytest.skip("fcntl not available")

    release.set()
    p.join(timeout=3)
    assert p.exitcode == 0
