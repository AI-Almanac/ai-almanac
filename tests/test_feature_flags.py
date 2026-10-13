"""Feature-flag gating for in-development features (data-management, forecasting)."""

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
    write_settings_overlay({"data_management_audience": "off"})
    # Simulate a fresh process / redeploy: drop the in-memory value, then
    # re-resolve settings from scratch.
    settings.data_management_audience = "everyone"
    reload_settings()
    assert settings.data_management_audience == "off"


def _caller_headers(monkeypatch: pytest.MonkeyPatch, role: str) -> dict[str, str]:
    """A personal-mode admin, or a proxy-mode user with no admin allow-list."""
    if role == "admin":
        return {}
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "")
    monkeypatch.setattr(settings, "admin_emails", "")
    return {"X-Forwarded-User": "rando"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("audience", "role", "can_manage"),
    [
        ("off", "admin", False),
        ("off", "user", False),
        ("admins", "admin", True),
        ("admins", "user", False),
        ("everyone", "admin", True),
        ("everyone", "user", True),
    ],
)
async def test_data_management_audience_gates_mutations_but_not_reads(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    audience: str,
    role: str,
    can_manage: bool,
) -> None:
    monkeypatch.setattr(settings, "data_management_audience", audience)
    headers = _caller_headers(monkeypatch, role)

    caps = (await client.get("/auth/me", headers=headers)).json()["capabilities"]
    assert caps["can_manage_data"] is can_manage
    # Behind the gate the route is hidden; past it, other checks may still refuse.
    for path in ("/data-sources/validate", "/regions"):
        resp = await client.post(path, json={}, headers=headers)
        assert (resp.status_code == 404) is not can_manage, path
    # Reads stay available so benchmarks can still list datasets and regions.
    assert (await client.get("/data-sources", headers=headers)).status_code == 200
    assert (await client.get("/regions", headers=headers)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_forecasting_flag_gates_the_whole_forecasts_router(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    monkeypatch.setattr(settings, "enable_forecasting", enabled)

    caps = (await client.get("/auth/me")).json()["capabilities"]
    assert caps["can_use_forecasting"] is enabled
    # Unlike data management, reads are gated too.
    for path in ("/forecasts", "/forecasts/models"):
        assert ((await client.get(path)).status_code == 404) is not enabled, path
    if not enabled:
        assert (await client.post("/forecasts", json={"blend_id": "nope"})).status_code == 404
