"""Which sandbox workspace run_code uses, and when startup refuses the configured one."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_almanac.envs import manager
from ai_almanac.paths import analysis_env_dir
from ai_almanac.server import app as server_app
from ai_almanac.server.services import code_sandbox
from ai_almanac.settings import Settings, settings


def test_an_unknown_sandbox_fails_at_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODE_SANDBOX", "docker")
    with pytest.raises(ValidationError):
        Settings()


def test_startup_fails_when_srt_cannot_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "code_sandbox", "srt")
    monkeypatch.setattr(code_sandbox, "sandbox_unavailable_reason", lambda: "srt is not installed")
    with pytest.raises(RuntimeError, match="srt is not installed"):
        server_app._require_code_sandbox()


def test_startup_rejects_an_unknown_sandbox_from_an_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "code_sandbox", "docker")
    with pytest.raises(RuntimeError, match="code_sandbox must be one of"):
        server_app._require_code_sandbox()


def test_startup_does_not_probe_srt_for_other_sandboxes(monkeypatch: pytest.MonkeyPatch) -> None:
    def probe():
        raise AssertionError("srt probed")

    monkeypatch.setattr(code_sandbox, "sandbox_unavailable_reason", probe)
    for sandbox in ("off", "modal"):
        monkeypatch.setattr(settings, "code_sandbox", sandbox)
        server_app._require_code_sandbox()


def _install_fake_analysis_env(lock: bytes) -> None:
    python = manager.analysis_python()
    python.parent.mkdir(parents=True, exist_ok=True)
    python.touch()
    (analysis_env_dir() / "pixi.lock").write_bytes(lock)


def test_analysis_env_must_match_the_packaged_lockfile() -> None:
    _install_fake_analysis_env(b"an older solve")
    assert "out of date" in manager.analysis_env_unavailable_reason()

    _install_fake_analysis_env(manager.analysis_pixi_lock().read_bytes())
    assert manager.analysis_env_unavailable_reason() is None


def test_env_digest_changes_with_the_installed_lockfile() -> None:
    _install_fake_analysis_env(b"solve one")
    first = manager.analysis_env_digest()
    _install_fake_analysis_env(b"solve two")
    assert first.startswith("sha256:") and manager.analysis_env_digest() != first


@pytest.mark.asyncio
async def test_enabling_srt_where_it_cannot_run_is_refused_with_the_reason(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(code_sandbox, "recheck_sandbox", lambda: "srt is not installed")
    response = await client.patch("/settings", json={"values": {"code_sandbox": "srt"}})
    assert response.status_code == 400
    assert "srt is not installed" in response.text
