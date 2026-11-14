"""A fetch of a few days of ERA5 over Senegal, run by a stand-in for the sandbox."""

from __future__ import annotations

import json
from datetime import date

import pytest
from ai_almanac_tools.sources import FetchRequest

from ai_almanac.server.services import (
    code_sandbox,
)
from ai_almanac.server.services.primitives import acquire
from ai_almanac.settings import settings
from tests.helpers.files import write_nc


def senegal_fetch(**overrides) -> FetchRequest:
    fields = {
        "source": "era5_arco",
        "variables": ["precip"],
        "lat_bounds": (12, 15),
        "lon_bounds": (-17, -14),
        "start": "2026-06-01",
        "end": "2026-06-10",
    }
    return FetchRequest(**(fields | overrides))


def local_fetch_sandbox(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Stands in for srt: writes the file a fetch would, and records the hosts it was allowed."""
    monkeypatch.setattr(settings, "code_sandbox", "srt")
    monkeypatch.setattr(code_sandbox, "sandbox_unavailable_reason", lambda: None)
    monkeypatch.setattr(code_sandbox, "analysis_env_digest", lambda: "sha256:analysis-lock")

    async def record_end(source):
        return date(2026, 7, 31)

    monkeypatch.setattr(acquire, "_record_end", record_end)
    allowed: list[tuple[str, ...]] = []

    def run_script(script, inputs, into, *, timeout, allowed_domains=()):
        allowed.append(allowed_domains)
        write_nc(into / "precip.nc")
        line = {"ok": True, "file": "precip.nc", "source_version": "record through 2026-06-30"}
        run = code_sandbox.SandboxedRun(0, json.dumps(line), "")
        runtime = {"sandbox": "srt", "env_digest": "sha256:analysis-lock"}
        return code_sandbox.SandboxResult(run, [into / "precip.nc"], runtime)

    monkeypatch.setattr(code_sandbox, "run_script", run_script)
    return allowed
