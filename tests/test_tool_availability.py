from __future__ import annotations

import pytest

from ai_almanac.server.services.benchmark_domain import is_tool_available
from ai_almanac.settings import settings


def _map_outputs_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "output_dir", "/mnt/outputs")
    monkeypatch.setattr(settings, "bucket_mounts", {"/mnt/outputs": "gs://almanac-outputs"})


@pytest.mark.parametrize("tool", ["run_code", "run_code_sandbox"])
def test_enabled_with_modal_runner_is_available(monkeypatch: pytest.MonkeyPatch, tool: str) -> None:
    monkeypatch.setattr(settings, f"enable_{tool}", True)
    monkeypatch.setattr(settings, "job_runner", "modal")
    if tool == "run_code":  # run_code additionally needs the outputs bucket mapping
        _map_outputs_bucket(monkeypatch)
    assert is_tool_available(tool) is True


def test_run_code_requires_outputs_bucket_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "enable_run_code", True)
    monkeypatch.setattr(settings, "job_runner", "modal")
    monkeypatch.setattr(settings, "bucket_mounts", {})
    assert is_tool_available("run_code") is False


@pytest.mark.parametrize("tool", ["run_code", "run_code_sandbox"])
def test_enabled_without_remote_backend_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    monkeypatch.setattr(settings, f"enable_{tool}", True)
    monkeypatch.setattr(settings, "job_runner", "local")
    assert is_tool_available(tool) is False


@pytest.mark.parametrize("tool", ["run_code", "run_code_sandbox"])
def test_disabled_is_unavailable(monkeypatch: pytest.MonkeyPatch, tool: str) -> None:
    monkeypatch.setattr(settings, f"enable_{tool}", False)
    monkeypatch.setattr(settings, "job_runner", "modal")
    assert is_tool_available(tool) is False
