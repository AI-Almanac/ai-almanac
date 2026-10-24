"""Tests for `ai-almanac init`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from ai_almanac.cli import app
from ai_almanac.settings import DEFAULT_LOCAL_LLM_MODEL, reload_settings, settings

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
    # Restore the env first so the reload doesn't leave this test's data dir
    # and unset SETUP_COMPLETE in the settings singleton for later modules.
    monkeypatch.undo()
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


@patch("ai_almanac.server.services.setup.detect_platform")
@patch("ai_almanac.server.services.setup.probe_gpu")
def test_headless_yes_prepares_envs_by_default(mock_gpu, mock_platform, tmp_path: Path) -> None:
    mock_gpu.return_value = {"name": "NVIDIA GB10", "memory_total_mb": 128000}
    mock_platform.return_value = {"platform": "linux-aarch64", "machine": "aarch64"}
    with (
        patch("ai_almanac.envs.manager.ensure_env") as mock_env,
        patch("ai_almanac.envs.manager.llm_env_ready", return_value=True),
        patch("ai_almanac.envs.manager.download_llm_model") as mock_download,
    ):
        mock_env.return_value = (tmp_path / "benchmark", tmp_path / "blending", tmp_path / "fc")
        result = runner.invoke(app, ["init", "--yes"])
    assert result.exit_code == 0, result.output
    mock_env.assert_called_once()
    _, kwargs = mock_env.call_args
    assert kwargs.get("include_forecast") is True
    # A GPU on Linux defaults the assistant to the bundled local model.
    assert kwargs.get("include_llm") is True
    mock_download.assert_called_once()
    assert mock_download.call_args.args[0] == DEFAULT_LOCAL_LLM_MODEL
    reload_settings()
    assert settings.llm_engine == "managed"
    assert settings.llm_base_url == f"http://127.0.0.1:{settings.llm_port}/v1"


@patch("ai_almanac.server.services.setup.probe_gpu")
@patch("ai_almanac.server.services.setup.test_llm_connection", new_callable=AsyncMock)
def test_llm_base_url_keeps_external_engine_with_gpu(mock_llm, mock_gpu, tmp_path: Path) -> None:
    mock_llm.return_value = _ok_llm_result()
    mock_gpu.return_value = {"name": "NVIDIA GB10", "memory_total_mb": 128000}
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
    reload_settings()
    assert settings.llm_engine == "external"


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


@patch("ai_almanac.server.services.setup.detect_platform")
@patch("ai_almanac.server.services.setup.probe_gpu")
@patch("ai_almanac.server.services.setup.test_llm_connection", new_callable=AsyncMock)
def test_interactive_llm_choice_prompt(mock_llm, mock_gpu, mock_platform, tmp_path: Path) -> None:
    mock_llm.return_value = _ok_llm_result()
    mock_gpu.return_value = {"name": "NVIDIA GB10", "memory_total_mb": 128000}
    mock_platform.return_value = {"platform": "linux-aarch64", "machine": "aarch64"}
    # Re-run confirmation (the suite's LLM_BASE_URL grandfathers the install),
    # output dir, mount roots, assistant choice "2" (existing server), URL,
    # model, API key.
    answers = "y\n\n\n2\nhttp://localhost:11434/v1\nllama3\n\n"
    result = runner.invoke(app, ["init", "--no-prepare-envs"], input=answers)
    assert result.exit_code == 0, result.output
    assert "run a model on this machine" in result.output
    reload_settings()
    assert settings.llm_engine == "external"
    assert settings.llm_model == "llama3"


@patch("ai_almanac.server.services.setup.detect_platform")
@patch("ai_almanac.server.services.setup.probe_gpu")
def test_interactive_local_model_picks_from_the_catalog(
    mock_gpu, mock_platform, tmp_path: Path
) -> None:
    mock_gpu.return_value = {"name": "NVIDIA GB10", "memory_total_mb": 128000}
    mock_platform.return_value = {"platform": "linux-aarch64", "machine": "aarch64"}
    # Re-run confirmation, output dir, mount roots, assistant choice "1" (this
    # machine), then catalog entry 2.
    answers = "y\n\n\n1\n2\n"
    result = runner.invoke(app, ["init", "--no-prepare-envs"], input=answers)
    assert result.exit_code == 0, result.output
    assert "Other Hugging Face model" in result.output
    reload_settings()
    assert settings.llm_engine == "managed"
    assert settings.llm_model == "RedHatAI/Qwen3.6-35B-A3B-NVFP4"


def test_llm_use_switches_model_without_downloading(tmp_path: Path) -> None:
    with patch("ai_almanac.envs.manager.download_llm_model") as mock_download:
        result = runner.invoke(
            app, ["llm", "use", "RedHatAI/Qwen3.6-35B-A3B-NVFP4", "--no-download"]
        )
    assert result.exit_code == 0, result.output
    mock_download.assert_not_called()
    # An unverified catalog model says so.
    assert "not been tested" in result.output
    reload_settings()
    assert settings.llm_engine == "managed"
    assert settings.llm_model == "RedHatAI/Qwen3.6-35B-A3B-NVFP4"


def test_llm_models_lists_the_catalog(tmp_path: Path) -> None:
    result = runner.invoke(app, ["llm", "models"])
    assert result.exit_code == 0, result.output
    assert DEFAULT_LOCAL_LLM_MODEL in result.output
