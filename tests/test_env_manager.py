"""Tests for pixi_bootstrap and the env manager's progress-callback refactor.

No real network calls: httpx transport is monkeypatched and pin dict is
replaced with a synthetic asset so sha256s are always computable.
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import tarfile as _tarfile
from pathlib import Path

import pytest

from ai_almanac.envs import manager as mgr
from ai_almanac.envs import pixi_bootstrap as pb

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_tarball(content: bytes = b"#!/bin/sh\necho pixi") -> bytes:
    """Build a minimal .tar.gz containing a single 'pixi' member."""
    buf = io.BytesIO()
    with _tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = _tarfile.TarInfo(name="pixi")
        info.size = len(content)
        info.mode = 0o755
        tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


FAKE_CONTENT = b"#!/bin/sh\necho fake-pixi"
FAKE_TARBALL = _fake_tarball(FAKE_CONTENT)
FAKE_SHA = _sha256(FAKE_TARBALL)


@pytest.fixture(autouse=True)
def _patch_assets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the real pin dict with a synthetic entry for linux-64."""
    monkeypatch.setattr(
        pb,
        "_PIXI_ASSETS",
        {"linux-64": ("pixi-fake-linux.tar.gz", FAKE_SHA)},
    )
    monkeypatch.setattr(pb, "PIXI_VERSION", "0.0.0-test")
    monkeypatch.setattr(pb, "_current_pixi_platform", lambda: "linux-64")


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AI_ALMANAC_DATA_DIR", str(tmp_path))
    # Reload data_root cache if any
    return tmp_path


# ---------------------------------------------------------------------------
# ensure_pixi — resolution order
# ---------------------------------------------------------------------------


def test_ensure_pixi_prefers_path(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/pixi" if name == "pixi" else None)
    monkeypatch.setattr(pb, "_download", lambda url, lines: pytest.fail("should not download"))
    result = pb.ensure_pixi()
    assert result == "/usr/bin/pixi"


def test_ensure_pixi_uses_cached_binary_when_stamp_matches(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    bin_dir = data_dir / "bin"
    bin_dir.mkdir()
    cached = bin_dir / "pixi"
    cached.write_bytes(b"cached")
    cached.chmod(0o755)
    (bin_dir / "pixi.version").write_text("0.0.0-test\n")

    monkeypatch.setattr(pb, "_download", lambda url, lines: pytest.fail("should not download"))
    result = pb.ensure_pixi()
    assert result == str(cached)


def test_ensure_pixi_redownloads_on_version_bump(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    bin_dir = data_dir / "bin"
    bin_dir.mkdir()
    cached = bin_dir / "pixi"
    cached.write_bytes(b"old-content")
    cached.chmod(0o755)
    (bin_dir / "pixi.version").write_text("0.0.0-old\n")

    monkeypatch.setattr(pb, "_download", lambda url, lines: FAKE_TARBALL)
    pb.ensure_pixi()

    assert cached.read_bytes() == FAKE_CONTENT
    assert (bin_dir / "pixi.version").read_text().strip() == "0.0.0-test"


def test_ensure_pixi_downloads_verifies_and_marks_executable(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(pb, "_download", lambda url, lines: FAKE_TARBALL)

    events: list[mgr.EnvProgressEvent] = []
    result = pb.ensure_pixi(progress=events.append)

    cached = data_dir / "bin" / "pixi"
    assert cached.exists()
    assert os.access(cached, os.X_OK)
    assert cached.read_bytes() == FAKE_CONTENT

    kinds = [e.kind for e in events if e.phase == "pixi-bootstrap"]
    assert "phase_started" in kinds
    assert "phase_finished" in kinds
    assert result == str(cached)


def test_ensure_pixi_rejects_sha256_mismatch(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    bad_tarball = _fake_tarball(b"tampered")
    monkeypatch.setattr(pb, "_download", lambda url, lines: bad_tarball)

    with pytest.raises(RuntimeError, match="sha256"):
        pb.ensure_pixi()

    cached = data_dir / "bin" / "pixi"
    assert not cached.exists()
    tmp_files = list((data_dir / "bin").glob(".pixi.tmp-*"))
    assert not tmp_files


def test_ensure_pixi_unsupported_platform_message(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(pb, "_current_pixi_platform", lambda: "win-64")

    with pytest.raises(RuntimeError, match="pixi.sh"):
        pb.ensure_pixi()


def test_ensure_pixi_offline_error_is_actionable(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    import httpx

    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(pb, "_RETRY_DELAYS", (0, 0, 0))

    def always_fail(url: str, lines: list) -> bytes:
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(pb, "_download", always_fail)

    with pytest.raises(RuntimeError) as exc_info:
        pb.ensure_pixi()

    msg = str(exc_info.value)
    assert "bin/pixi" in msg
    assert "pixi.sh" in msg


# ---------------------------------------------------------------------------
# _run_streaming
# ---------------------------------------------------------------------------


def test_run_streaming_emits_lines_and_phase_events(tmp_path: Path) -> None:
    events: list[mgr.EnvProgressEvent] = []
    mgr._run_streaming(
        ["/bin/echo", "hello world"],
        phase="test-phase",
        progress=events.append,
    )
    kinds = [e.kind for e in events]
    assert "phase_started" in kinds
    assert "phase_finished" in kinds
    lines = [e.line for e in events if e.kind == "line"]
    assert any("hello world" in (ln or "") for ln in lines)


def test_run_streaming_failure_includes_tail(tmp_path: Path) -> None:
    events: list[mgr.EnvProgressEvent] = []
    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        mgr._run_streaming(
            ["/bin/sh", "-c", "echo boom; exit 3"],
            phase="test-phase",
            progress=events.append,
        )
    assert exc_info.value.returncode == 3
    assert "boom" in (exc_info.value.output or "")
    failed_events = [e for e in events if e.kind == "phase_failed"]
    assert failed_events
    assert "boom" in (failed_events[0].detail or "")


# ---------------------------------------------------------------------------
# _install event sequence
# ---------------------------------------------------------------------------


def test_install_event_sequence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mgr, "_ensure_pixi", lambda progress=None: "/bin/echo")

    monkeypatch.setattr(
        "ai_almanac.envs.manager._pixi_spec",
        lambda: tmp_path / "benchmark.pixi.toml",
    )
    spec = tmp_path / "benchmark.pixi.toml"
    spec.write_text("[workspace]\n")

    events: list[mgr.EnvProgressEvent] = []
    env_dir = tmp_path / "env"

    # _install runs: _ensure_pixi (no-op here) then _run_streaming(["/bin/echo", "install", ...])
    # The pixi command becomes ["/bin/echo", "install", ...] so it succeeds and emits lines.
    mgr._install(spec, env_dir, "benchmark", events.append)

    phases = [e.phase for e in events]
    assert "benchmark" in phases
    kinds = {e.kind for e in events}
    assert "phase_started" in kinds
    assert "phase_finished" in kinds


# ---------------------------------------------------------------------------
# ensure_env default progress prints
# ---------------------------------------------------------------------------


def test_ensure_env_default_progress_prints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(mgr, "_ensure_pixi", lambda progress=None: "/bin/echo")
    monkeypatch.setattr(
        mgr,
        "ensure_blending_env",
        lambda progress=None: tmp_path / "blending-env",
    )
    monkeypatch.setattr(
        mgr,
        "ensure_forecast_env",
        lambda progress=None: None,
    )
    monkeypatch.setattr(
        mgr,
        "_install",
        lambda spec, env_dir, phase, progress, environments=None: progress(
            mgr.EnvProgressEvent(kind="phase_finished", phase=phase, detail=str(env_dir))
        ),
    )

    mgr.ensure_env()

    out = capsys.readouterr().out
    assert "benchmark" in out


# ---------------------------------------------------------------------------
# env_versions — no bootstrap on fresh dir
# ---------------------------------------------------------------------------


def test_env_versions_without_env_does_not_bootstrap(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    monkeypatch.setattr(
        pb,
        "_download",
        lambda url, lines: pytest.fail("should not bootstrap for env_versions on fresh dir"),
    )
    monkeypatch.setattr("shutil.which", lambda name: None)

    result = mgr.env_versions()
    assert "not prepared" in list(result.values())[0]


# ---------------------------------------------------------------------------
# CLI: env prepare no longer requires pixi on PATH
# ---------------------------------------------------------------------------


def test_cli_env_prepare_no_longer_requires_path_pixi(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path
) -> None:
    from typer.testing import CliRunner

    from ai_almanac.cli import app

    monkeypatch.setattr("shutil.which", lambda name: None)

    fake_result = (data_dir / "benchmark-env", data_dir / "blending-env", None)
    monkeypatch.setattr("ai_almanac.envs.manager.ensure_env", lambda progress=None: fake_result)

    runner = CliRunner()
    result = runner.invoke(app, ["env", "prepare"])
    assert result.exit_code == 0, result.output
