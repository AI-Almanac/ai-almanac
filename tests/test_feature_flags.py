"""Feature-flag gating for in-development features (data management, forecasting, workspaces)."""

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
    resp = await client.post("/data-sources/validate", json={}, headers=headers)
    assert (resp.status_code == 404) is not can_manage
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("audience", "role", "can_use"),
    [
        ("off", "admin", False),
        ("off", "user", False),
        ("admins", "admin", True),
        ("admins", "user", False),
        ("everyone", "admin", True),
        ("everyone", "user", True),
    ],
)
async def test_workspaces_audience_hides_workspaces_from_everyone_outside_it(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    audience: str,
    role: str,
    can_use: bool,
) -> None:
    monkeypatch.setattr(settings, "workspaces_audience", audience)
    headers = _caller_headers(monkeypatch, role)

    caps = (await client.get("/auth/me", headers=headers)).json()["capabilities"]
    assert caps["can_use_workspaces"] is can_use
    # Unlike data management, reads are hidden too.
    assert ((await client.get("/workspaces", headers=headers)).status_code == 404) is not can_use
    created = await client.post("/workspaces", json={"name": "Season review"}, headers=headers)
    assert (created.status_code == 404) is not can_use


@pytest.mark.asyncio
async def test_a_workspace_conversation_is_gone_once_its_owner_leaves_the_audience(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "workspaces_audience", "everyone")
    headers = _caller_headers(monkeypatch, "user")
    workspace = (
        await client.post("/workspaces", json={"name": "Season review"}, headers=headers)
    ).json()
    scope = {"kind": "workspace", "key": workspace["id"], "title": "Season review"}
    session = await client.post("/chat/sessions", json={"scope": scope}, headers=headers)
    assert session.status_code == 201

    monkeypatch.setattr(settings, "workspaces_audience", "admins")

    again = await client.post("/chat/sessions", json={"scope": scope}, headers=headers)
    assert again.status_code == 404
    message = await client.post(
        f"/chat/sessions/{session.json()['id']}/message",
        json={"content": "What changed since last week?"},
        headers=headers,
    )
    assert message.status_code == 404


@pytest.mark.asyncio
async def test_every_way_into_a_workspace_conversation_closes_once_its_owner_leaves_the_audience(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "workspaces_audience", "everyone")
    headers = _caller_headers(monkeypatch, "user")
    workspace = (
        await client.post("/workspaces", json={"name": "Season review"}, headers=headers)
    ).json()
    scope = {"kind": "workspace", "key": workspace["id"], "title": "Season review"}
    session_id = (
        await client.post("/chat/sessions", json={"scope": scope}, headers=headers)
    ).json()["id"]
    url = f"/chat/sessions/{session_id}"

    monkeypatch.setattr(settings, "workspaces_audience", "admins")

    listed = await client.get("/chat/sessions", headers=headers)
    assert session_id not in {item["id"] for item in listed.json()}
    attempts = {
        "read": await client.get(url, headers=headers),
        "job events": await client.get(f"{url}/job-events", headers=headers),
        "rename": await client.patch(url, json={"title": "Renamed"}, headers=headers),
        "approve a step": await client.post(
            f"{url}/tool-approval", json={"tool_call_id": "c-1", "approved": True}, headers=headers
        ),
        "delete": await client.delete(url, headers=headers),
    }
    assert {name: r.status_code for name, r in attempts.items()} == dict.fromkeys(attempts, 404)
