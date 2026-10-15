from __future__ import annotations

import pytest

from ai_almanac.server.services.benchmark_domain import is_tool_available
from ai_almanac.settings import settings


def _map_outputs_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "output_dir", "/mnt/outputs")
    monkeypatch.setattr(settings, "bucket_mounts", {"/mnt/outputs": "gs://almanac-outputs"})


@pytest.mark.parametrize("tool", ["run_code", "run_code_sandbox"])
@pytest.mark.parametrize(
    ("enabled", "runner", "available"),
    [(True, "modal", True), (True, "local", False), (False, "modal", False)],
)
def test_code_tools_need_both_the_setting_and_a_remote_runner(
    monkeypatch: pytest.MonkeyPatch, tool: str, enabled: bool, runner: str, available: bool
) -> None:
    # run_code additionally needs the outputs bucket mapping; covered below.
    _map_outputs_bucket(monkeypatch)
    monkeypatch.setattr(settings, f"enable_{tool}", enabled)
    monkeypatch.setattr(settings, "job_runner", runner)
    assert is_tool_available(tool) is available


def test_run_code_requires_outputs_bucket_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "enable_run_code", True)
    monkeypatch.setattr(settings, "job_runner", "modal")
    monkeypatch.setattr(settings, "bucket_mounts", {})
    assert is_tool_available("run_code") is False
