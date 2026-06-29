"""Feature-flag gating for the in-development data-management feature."""

from __future__ import annotations

import httpx
import pytest

from ai_almanac.settings import (
    reload_settings,
    settings,
    write_settings_overlay,
)


def test_overlay_persists_across_reload() -> None:
    """An admin's setting change survives a settings reload (i.e. a redeploy),
    because it lives in the database overlay, not the ephemeral config.yaml."""
    original = settings.enable_data_management
    try:
        write_settings_overlay({"enable_data_management": False})
        # Simulate a fresh process / redeploy: drop the in-memory value, then
        # re-resolve settings from scratch.
        settings.enable_data_management = True
        reload_settings()
        assert settings.enable_data_management is False
    finally:
        write_settings_overlay({"enable_data_management": None})
        reload_settings()
        assert settings.enable_data_management == original


@pytest.mark.asyncio
async def test_capability_reports_flag_state(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "enable_data_management", False)
    caps = (await client.get("/auth/me")).json()["capabilities"]
    assert caps["can_manage_data"] is False


@pytest.mark.asyncio
async def test_region_mutations_hidden_when_disabled(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "enable_data_management", False)
    resp = await client.post("/regions", json={"display_name": "Nope"})
    assert resp.status_code == 404

    # Reads stay available so benchmarks can still list regions.
    assert (await client.get("/regions")).status_code == 200


@pytest.mark.asyncio
async def test_region_create_allowed_when_enabled(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "enable_data_management", True)
    resp = await client.post(
        "/regions",
        json={
            "display_name": "Testland",
            "lat_min": 0,
            "lat_max": 1,
            "lon_min": 0,
            "lon_max": 1,
        },
    )
    # Not a 404 from the flag gate; the feature is reachable.
    assert resp.status_code != 404
