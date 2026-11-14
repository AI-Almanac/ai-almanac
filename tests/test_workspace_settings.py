"""Workspace settings: the season a workspace works in, through REST and the assistant."""

from __future__ import annotations

import pytest

from ai_almanac.server.services import workspace_settings
from ai_almanac.server.services.seasons import SeasonWindow
from tests.helpers.workspaces import workspace_with_session

KIREMT = {
    "name": "Kiremt",
    "onset_search_start": "05-01",
    "reference_onset": "06-01",
    "last_issue_date": "07-31",
}


async def _patch(client, headers, workspace_id: str, body: dict):
    return await client.patch(f"/workspaces/{workspace_id}/settings", headers=headers, json=body)


async def test_a_season_is_set_kept_and_cleared_through_the_settings(client, as_new_user) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)
    url = f"/workspaces/{workspace['id']}/settings"
    unset = {"season": None, "study_area": None}

    fresh = await client.get(url, headers=as_new_user)
    updated = await _patch(client, as_new_user, workspace["id"], {"season": KIREMT})
    detail = await client.get(f"/workspaces/{workspace['id']}", headers=as_new_user)
    kept = await _patch(client, as_new_user, workspace["id"], {})
    cleared = await _patch(client, as_new_user, workspace["id"], {"season": None})

    assert (fresh.status_code, fresh.json()) == (200, unset)
    assert updated.status_code == 200, updated.text
    assert updated.json() == {"season": KIREMT, "study_area": None}
    assert detail.json()["settings"] == {"season": KIREMT, "study_area": None}
    assert kept.json() == {"season": KIREMT, "study_area": None}, "a patch naming nothing"
    assert cleared.json() == unset


async def test_an_unknown_setting_is_refused_by_name(client, as_new_user) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)

    response = await _patch(client, as_new_user, workspace["id"], {"colour": "blue"})

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid workspace settings: unknown setting colour"


async def test_a_season_out_of_order_is_refused_in_plain_words(client, as_new_user) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)
    backwards = {**KIREMT, "reference_onset": "04-01"}

    response = await _patch(client, as_new_user, workspace["id"], {"season": backwards})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail.startswith("Invalid workspace settings: season: the reference onset date 04-01")
    assert "Value error" not in detail
    unchanged = await client.get(f"/workspaces/{workspace['id']}/settings", headers=as_new_user)
    assert unchanged.json() == {"season": None, "study_area": None}


async def test_another_user_cannot_read_or_change_the_settings(client, as_new_user) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)
    stranger = {"X-Forwarded-User": "pytest-stranger"}

    read = await client.get(f"/workspaces/{workspace['id']}/settings", headers=stranger)
    written = await _patch(client, stranger, workspace["id"], {"season": KIREMT})

    assert (read.status_code, written.status_code) == (404, 404)


def test_settings_written_by_a_newer_release_still_open() -> None:
    raw = '{"season": {"name": "Kiremt"}, "observation_dataset": "chirps"}'

    assert workspace_settings.parse_settings(raw).season == SeasonWindow(name="Kiremt")


async def test_the_assistant_is_told_the_workspace_season_each_turn(
    client, as_new_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    seen: list[str] = []

    async def stream(messages: list, info: AgentInfo):
        seen.append(info.instructions or "")
        yield "Noted."

    monkeypatch.setattr(
        "ai_almanac.server.services.llm._build_model", lambda: FunctionModel(stream_function=stream)
    )
    workspace, session = await workspace_with_session(client, as_new_user)
    await _patch(client, as_new_user, workspace["id"], {"season": KIREMT})

    await client.post(
        f"/chat/sessions/{session['id']}/message", headers=as_new_user, json={"content": "hi"}
    )

    assert (
        "- Season: Kiremt — onset searched from 1 May, reference onset 1 June, last issue date 31 July."
        in seen[-1]
    )


def test_with_nothing_set_the_assistant_is_told_the_platform_defaults() -> None:
    block = workspace_settings.prompt_block(workspace_settings.WorkspaceSettings())

    assert "Season: none set; platform defaults apply: onset searched from 1 May" in block
    assert "- Area: none set; a run covers all of its data unless it names an area." in block


async def test_effective_settings_fill_an_unset_season_with_the_platform_default(
    client, as_new_user
) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)
    url = f"/workspaces/{workspace['id']}/settings/effective"

    unset = await client.get(url, headers=as_new_user)
    await _patch(client, as_new_user, workspace["id"], {"season": KIREMT})
    chosen = await client.get(url, headers=as_new_user)

    assert unset.json() == {"season": SeasonWindow().model_dump(), "study_area": None}
    assert chosen.json() == {"season": KIREMT, "study_area": None}


# --- the place a workspace works in --------------------------------------------

AMHARA = {"level": "adm1", "units": ["Amhara"], "iso": "ETH", "geometry": None}
HIGHLANDS = {"lat_min": 8.0, "lat_max": 12.0, "lon_min": 36.0, "lon_max": 40.0}


@pytest.mark.parametrize("area", [AMHARA, HIGHLANDS], ids=["places", "box"])
async def test_a_workspaces_area_is_read_back_and_leaves_its_season_alone(
    client, as_new_user, area: dict
) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)
    await _patch(client, as_new_user, workspace["id"], {"season": KIREMT})

    updated = await _patch(client, as_new_user, workspace["id"], {"study_area": area})

    assert updated.status_code == 200, updated.text
    assert updated.json() == {"season": KIREMT, "study_area": area}


async def test_places_in_a_workspace_must_name_their_country(client, as_new_user) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)

    refused = await _patch(
        client, as_new_user, workspace["id"], {"study_area": {"level": "adm1", "units": ["Amhara"]}}
    )

    assert refused.status_code == 422
    assert "three-letter code" in refused.json()["detail"]
