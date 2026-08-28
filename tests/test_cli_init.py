"""Tests for `ai-almanac init`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from ai_almanac.cli import app
from ai_almanac.settings import reload_settings, settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_ALMANAC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SETUP_COMPLETE", raising=False)
    reload_settings()
    from ai_almanac.paths import ensure_layout
    from ai_almanac.server.app import _apply_migrations

    ensure_layout()
    _apply_migrations()
    reload_settings()
    yield
    reload_settings()


def _ok_llm_result():
    r = MagicMock()
    r.ok = True
    r.models_ok = True
    r.completion_ok = True
    r.models = ["llama3"]
    r.error = None
    return r


def _fail_llm_result():
    r = MagicMock()
    r.ok = False
    r.models_ok = False
    r.completion_ok = False
    r.models = []
    r.error = "connection refused"
    return r


@patch("ai_almanac.server.services.setup.test_llm_connection", new_callable=AsyncMock)
def test_headless_yes_writes_setup_complete(mock_llm, tmp_path: Path) -> None:
    mock_llm.return_value = _ok_llm_result()
    result = runner.invoke(
        app,
        [
            "init",
            "--yes",
            "--no-prepare-envs",
            "--llm-base-url",
            "http://localhost:11434/v1",
            "--llm-model",
            "llama3",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "setup complete" in result.output
    reload_settings()
    assert settings.setup_complete is True


@patch("ai_almanac.server.services.setup.test_llm_connection", new_callable=AsyncMock)
def test_failed_llm_test_exits_nonzero_with_yes(mock_llm, tmp_path: Path) -> None:
    mock_llm.return_value = _fail_llm_result()
    result = runner.invoke(
        app,
        [
            "init",
            "--yes",
            "--no-prepare-envs",
            "--llm-base-url",
            "http://localhost:11434/v1",
            "--llm-model",
            "llama3",
        ],
    )
    assert result.exit_code != 0


@patch("ai_almanac.server.services.setup.test_llm_connection", new_callable=AsyncMock)
def test_skip_llm_test_saves_without_testing(mock_llm, tmp_path: Path) -> None:
    mock_llm.return_value = _ok_llm_result()
    result = runner.invoke(
        app,
        [
            "init",
            "--yes",
            "--no-prepare-envs",
            "--skip-llm-test",
            "--llm-base-url",
            "http://localhost:11434/v1",
            "--llm-model",
            "llama3",
        ],
    )
    assert result.exit_code == 0
    mock_llm.assert_not_called()
    reload_settings()
    assert settings.setup_complete is True


def test_no_llm_provided_skips_llm_step(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--yes", "--no-prepare-envs"])
    assert result.exit_code == 0
    assert "skipping LLM" in result.output
    reload_settings()
    assert settings.setup_complete is True


@patch("ai_almanac.server.services.setup.test_llm_connection", new_callable=AsyncMock)
def test_dataset_mount_roots_saved(mock_llm, tmp_path: Path) -> None:
    mock_llm.return_value = _ok_llm_result()
    result = runner.invoke(
        app,
        [
            "init",
            "--yes",
            "--no-prepare-envs",
            "--llm-base-url",
            "http://localhost:11434/v1",
            "--llm-model",
            "llama3",
            "--dataset-mount-roots",
            "/mnt/data1,/mnt/data2",
        ],
    )
    assert result.exit_code == 0, result.output
    reload_settings()
    assert "/mnt/data1" in settings.dataset_mount_roots


@patch("ai_almanac.server.services.setup.test_llm_connection", new_callable=AsyncMock)
def test_prepare_envs_called_when_flag_set(mock_llm, tmp_path: Path) -> None:
    mock_llm.return_value = _ok_llm_result()
    with patch("ai_almanac.envs.manager.ensure_env") as mock_env:
        mock_env.return_value = (tmp_path / "benchmark", tmp_path / "blending", None)
        result = runner.invoke(
            app,
            [
                "init",
                "--yes",
                "--llm-base-url",
                "http://localhost:11434/v1",
                "--llm-model",
                "llama3",
                "--prepare-envs",
                "--no-include-forecast",
            ],
        )
    assert result.exit_code == 0, result.output
    mock_env.assert_called_once()
    _, kwargs = mock_env.call_args
    assert kwargs.get("include_forecast") is False


@patch("ai_almanac.server.services.setup.probe_gpu")
def test_headless_yes_prepares_envs_by_default(mock_gpu, tmp_path: Path) -> None:
    mock_gpu.return_value = {"name": "NVIDIA GB10", "memory_total_mb": 128000}
    with patch("ai_almanac.envs.manager.ensure_env") as mock_env:
        mock_env.return_value = (tmp_path / "benchmark", tmp_path / "blending", tmp_path / "fc")
        result = runner.invoke(app, ["init", "--yes"])
    assert result.exit_code == 0, result.output
    mock_env.assert_called_once()
    _, kwargs = mock_env.call_args
    assert kwargs.get("include_forecast") is True


@patch("ai_almanac.server.services.setup.probe_gpu")
def test_headless_yes_without_gpu_skips_forecast_env(mock_gpu, tmp_path: Path) -> None:
    mock_gpu.return_value = None
    with patch("ai_almanac.envs.manager.ensure_env") as mock_env:
        mock_env.return_value = (tmp_path / "benchmark", tmp_path / "blending", None)
        result = runner.invoke(app, ["init", "--yes"])
    assert result.exit_code == 0, result.output
    mock_env.assert_called_once()
    _, kwargs = mock_env.call_args
    assert kwargs.get("include_forecast") is False
    assert "skipping forecast environments" in result.output


@patch("ai_almanac.server.services.setup.test_llm_connection", new_callable=AsyncMock)
def test_already_complete_with_yes_reruns(mock_llm, tmp_path: Path) -> None:
    mock_llm.return_value = _ok_llm_result()
    # First run to mark complete
    runner.invoke(
        app,
        [
            "init",
            "--yes",
            "--no-prepare-envs",
            "--llm-base-url",
            "http://localhost:11434/v1",
            "--llm-model",
            "llama3",
        ],
    )
    reload_settings()
    assert settings.setup_complete is True
    # Second run with --yes should succeed (re-run)
    result = runner.invoke(
        app,
        [
            "init",
            "--yes",
            "--no-prepare-envs",
            "--llm-base-url",
            "http://localhost:11434/v1",
            "--llm-model",
            "llama3",
        ],
    )
    assert result.exit_code == 0
