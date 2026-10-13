from __future__ import annotations

import pytest

from ai_almanac.server.services.benchmark_domain import is_tool_available
from ai_almanac.settings import settings


@pytest.mark.parametrize("tool", ["run_code", "run_code_sandbox"])
@pytest.mark.parametrize(
    ("enabled", "runner", "available"),
    [(True, "modal", True), (True, "local", False), (False, "modal", False)],
)
def test_code_tools_need_both_the_setting_and_a_remote_runner(
    monkeypatch: pytest.MonkeyPatch, tool: str, enabled: bool, runner: str, available: bool
) -> None:
    monkeypatch.setattr(settings, f"enable_{tool}", enabled)
    monkeypatch.setattr(settings, "job_runner", runner)
    assert is_tool_available(tool) is available
