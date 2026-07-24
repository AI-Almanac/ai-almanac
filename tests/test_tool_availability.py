from __future__ import annotations

import pytest

from ai_almanac.server.services.benchmark_domain import is_tool_available
from ai_almanac.settings import settings


@pytest.mark.parametrize("tool", ["run_code", "run_code_sandbox"])
def test_enabled_with_modal_runner_is_available(monkeypatch: pytest.MonkeyPatch, tool: str) -> None:
    monkeypatch.setattr(settings, f"enable_{tool}", True)
    monkeypatch.setattr(settings, "job_runner", "modal")
    assert is_tool_available(tool) is True


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
