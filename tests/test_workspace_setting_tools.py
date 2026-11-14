"""The assistant's tools for a workspace's season and area, and the runs that take them."""

from __future__ import annotations

import pytest
from pydantic_ai import ApprovalRequired

from ai_almanac.server.services import workspace_settings
from ai_almanac.server.services.focus_area import FocusBox, FocusUnits
from ai_almanac.server.services.primitives import runs
from ai_almanac.server.services.primitives import workspace as workspace_tools
from ai_almanac.server.services.seasons import SeasonWindow
from tests.helpers.specs import india_blend_spec
from tests.helpers.tool_calls import tool_ctx
from tests.helpers.workspaces import workspace_with_session

KIREMT = {
    "name": "Kiremt",
    "onset_search_start": "05-01",
    "reference_onset": "06-01",
    "last_issue_date": "07-31",
}
KENYA = FocusUnits(level="adm0", units=["Kenya"], iso="KEN")
HIGHLANDS = {"lat_min": 8.0, "lat_max": 12.0, "lon_min": 36.0, "lon_max": 40.0}


async def test_the_assistant_sets_the_season_without_writing_provenance(
    client, as_new_user, user_id
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    ctx = tool_ctx(user_id, session["id"], workspace["id"])

    result = await workspace_tools.set_workspace_season(ctx, season=SeasonWindow(**KIREMT))

    assert result["summary"].startswith("Season: Kiremt — onset searched from 1 May")
    read = await client.get(f"/workspaces/{workspace['id']}/settings", headers=as_new_user)
    assert read.json() == {"season": KIREMT, "study_area": None}
    artifacts = await client.get(f"/workspaces/{workspace['id']}/artifacts", headers=as_new_user)
    assert artifacts.json() == []


async def test_the_assistant_sets_and_is_told_the_workspaces_area(
    client, as_new_user, user_id
) -> None:
    from ai_almanac.server.services.focus_area import FocusUnits

    workspace, session = await workspace_with_session(client, as_new_user)
    ctx = tool_ctx(user_id, session["id"], workspace["id"])

    result = await workspace_tools.set_workspace_area(
        ctx, area=FocusUnits(level="adm0", units=["Kenya"], iso="KEN")
    )

    assert result["summary"].endswith("Area: Kenya.")
    block = workspace_settings.prompt_block(await workspace_settings.get_settings(workspace["id"]))
    assert "- Area: Kenya." in block


async def _card_params(tool, ctx, spec) -> dict:
    with pytest.raises(ApprovalRequired) as asked:
        await tool(ctx, spec=spec)
    return asked.value.metadata["params"]["spec"]


async def _in_kenya(client, as_new_user, user_id):
    workspace, session = await workspace_with_session(client, as_new_user)
    ctx = tool_ctx(user_id, session["id"], workspace["id"])
    await workspace_tools.set_workspace_area(ctx, area=KENYA)
    return ctx


async def test_a_blend_is_put_to_the_user_over_the_workspaces_area_unless_it_names_its_own(
    client, as_new_user, user_id
) -> None:
    ctx = await _in_kenya(client, as_new_user, user_id)
    unnamed = await india_blend_spec("2020,2021")
    own = unnamed.model_copy(update={"focus_area": FocusBox(**HIGHLANDS)})

    taken = await _card_params(runs.submit_blend, ctx, unnamed)
    kept = await _card_params(runs.submit_blend, ctx, own)

    assert taken["focus_area"]["units"] == ["Kenya"]
    assert FocusBox(**kept["focus_area"]) == FocusBox(**HIGHLANDS)


async def test_a_benchmark_is_put_to_the_user_over_the_workspaces_area_unless_it_names_its_own(
    client, as_new_user, user_id
) -> None:
    ctx = await _in_kenya(client, as_new_user, user_id)
    unnamed = runs.BenchmarkSpec(dataset_id="obs", model_ids=["model"])
    own = runs.BenchmarkSpec(
        dataset_id="obs", model_ids=["model"], advanced_params={"focus_area": HIGHLANDS}
    )

    taken = await _card_params(runs.submit_benchmark, ctx, unnamed)
    kept = await _card_params(runs.submit_benchmark, ctx, own)

    assert taken["advanced_params"]["focus_area"]["units"] == ["Kenya"]
    assert FocusBox(**kept["advanced_params"]["focus_area"]) == FocusBox(**HIGHLANDS)
