"""`ai-almanac serve --container`: the bind guard opt-in and its access token."""

from __future__ import annotations

from pathlib import Path

import pytest
import uvicorn
from typer.testing import CliRunner

import ai_almanac.secrets_bootstrap as sb
from ai_almanac.cli import app
from ai_almanac.paths import secrets_env_path
from ai_almanac.settings import reload_settings, settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_ALMANAC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SERVE_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("AI_ALMANAC_CONTAINER", raising=False)
    reload_settings()
    from ai_almanac.paths import ensure_layout
    from ai_almanac.server.app import _apply_migrations

    ensure_layout()
    _apply_migrations()
    reload_settings()
    yield
    reload_settings()


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record uvicorn.run calls instead of starting a server."""
    calls: list[str] = []
    monkeypatch.setattr(uvicorn, "run", lambda target, **kwargs: calls.append(kwargs["host"]))
    return calls


def test_personal_mode_refuses_all_interfaces_without_container(served: list[str]) -> None:
    result = runner.invoke(app, ["serve", "--bind", "0.0.0.0", "--no-open"])
    assert result.exit_code == 2
    assert served == []


@pytest.mark.parametrize("via_env", [False, True], ids=["flag", "env"])
def test_container_mode_binds_all_interfaces_behind_a_persisted_token(
    served: list[str], monkeypatch: pytest.MonkeyPatch, via_env: bool
) -> None:
    args = ["serve", "--bind", "0.0.0.0", "--no-open"]
    if via_env:
        monkeypatch.setenv("AI_ALMANAC_CONTAINER", "1")
    else:
        args.append("--container")

    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    assert served == ["0.0.0.0"]
    token = settings.serve_access_token
    assert token and f"?token={token}" in first.output

    # A restart reuses the same token rather than minting a new one.
    second = runner.invoke(app, args)
    assert second.exit_code == 0, second.output
    reload_settings()
    assert settings.serve_access_token == token


def test_access_token_keeps_existing_secrets() -> None:
    assert sb.ensure_local_secrets()
    before = secrets_env_path().read_text()
    assert sb.ensure_access_token()
    after = secrets_env_path().read_text()
    assert before.removeprefix(sb._SECRETS_HEADER) in after
    assert not sb.ensure_access_token()


def test_access_token_from_env_is_not_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVE_ACCESS_TOKEN", "from-env")
    assert not sb.ensure_access_token()
    assert not secrets_env_path().exists()


def test_shutdown_stops_waiting_on_open_replies_in_time_to_sleep_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Uvicorn otherwise waits on every open response before the lifespan hook
    # that sleeps the local model runs. A streaming chat reply outlasted the
    # container's stop grace period, and the model server was SIGKILLed
    # mid-kernel, leaving the GPU busy with no process until a reboot.
    import re

    import yaml

    seen: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda target, **kwargs: seen.update(kwargs))

    result = runner.invoke(app, ["serve", "--no-open"])

    assert result.exit_code == 0, result.output
    grace = seen["timeout_graceful_shutdown"]
    compose = yaml.safe_load((Path(__file__).parents[1] / "compose.spark.yaml").read_text())
    stop_after = compose["services"]["almanac"]["stop_grace_period"]
    seconds = int(re.fullmatch(r"(\d+)s", stop_after).group(1))
    # Room left after the wait for the sleep itself (a few seconds).
    assert 0 < grace <= seconds - 30
