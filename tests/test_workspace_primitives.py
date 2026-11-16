"""The assistant's workspace tools: finding and adding runs, reading the catalog and
forecast results, and refusing a workspace that is not the caller's."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_almanac.server.services import (
    workspaces,
)
from ai_almanac.server.services.chat_state import ChatScope
from ai_almanac.server.services.llm import (
    chat_deps,
)
from ai_almanac.server.services.primitives import inspection, runs
from ai_almanac.server.services.primitives import workspace as workspace_tools
from tests.helpers.files import write_nc, write_season_nc
from tests.helpers.records import BENCHMARK_CONFIG, insert_job
from tests.helpers.tool_calls import tool_ctx
from tests.helpers.workspaces import (
    job_step_in_record,
    register_file,
    workspace_with_session,
    write_job_outputs,
)


async def test_promote_session_links_jobs_and_rescopes(client, as_new_user) -> None:
    scope = {"kind": "benchmark_run_group", "key": "run-1", "title": "Run 1", "job_ids": []}
    response = await client.post(
        "/chat/sessions", headers=as_new_user, json={"title": "Run 1 chat", "scope": scope}
    )
    session = response.json()
    response = await client.post(
        "/workspaces/promote", headers=as_new_user, json={"session_id": session["id"]}
    )
    assert response.status_code == 201, response.text
    workspace = response.json()
    assert workspace["name"] == "Run 1 chat"
    response = await client.get(f"/chat/sessions/{session['id']}", headers=as_new_user)
    assert response.json()["scope"] == {
        "kind": "workspace",
        "key": workspace["id"],
        "title": "Run 1 chat",
        "job_ids": [],
    }
    listed = await client.get(
        "/chat/sessions",
        headers=as_new_user,
        params={"scope_kind": "workspace", "scope_key": workspace["id"]},
    )
    assert [s["id"] for s in listed.json()] == [session["id"]]


@pytest.mark.asyncio
async def test_a_run_made_elsewhere_is_found_by_its_name_and_added(
    client, as_new_user, user_id
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    ctx = tool_ctx(user_id, session["id"], workspace["id"])
    september = insert_job(
        user_id,
        {
            "blend_name": "AIFS-FuXi Ethiopia Blend",
            "model_names": ["aifs", "fuxi"],
            "region_id": "ethiopia",
            "blend_params": {"training_years": "2008:2018"},
        },
        job_type="blend",
    )
    insert_job(
        user_id,
        {
            "blend_name": "GraphCast-FuXi India Blend",
            "model_names": ["graphcast", "fuxi"],
            "region_id": "india",
        },
        job_type="blend",
    )

    found = await workspace_tools.find_runs(
        ctx, kind="blend", name="aifs fuxi", region_id="ethiopia"
    )

    [run] = found["runs"]
    assert (run["name"], run["models"], run["training_years"]) == (
        "AIFS-FuXi Ethiopia Blend",
        ["aifs", "fuxi"],
        "2008:2018",
    )
    assert not run["in_workspace"]
    await workspace_tools.add_job(ctx, job_id=run["job_id"])
    [again] = (await workspace_tools.find_runs(ctx, name="AIFS-FuXi"))["runs"]
    assert again["job_id"] == september and again["in_workspace"]


@pytest.mark.asyncio
async def test_finding_runs_never_shows_another_users_runs(client, as_new_user, user_id) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    insert_job("someone-else", {"blend_name": "Their blend"}, job_type="blend")

    found = await workspace_tools.find_runs(tool_ctx(user_id, session["id"], workspace["id"]))

    assert all(run["name"] != "Their blend" for run in found["runs"])


@pytest.mark.asyncio
async def test_the_record_says_when_the_assistant_added_an_existing_run(
    client, as_new_user, user_id
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    existing = insert_job(user_id, BENCHMARK_CONFIG)
    write_job_outputs(existing, 1.0)

    await workspace_tools.add_job(tool_ctx(user_id, session["id"], wid), job_id=existing)

    step = await job_step_in_record(client, as_new_user, wid, existing)
    assert step["added_by"] == "assistant" and step["added_at"] is not None


@pytest.mark.asyncio
async def test_the_assistant_sees_the_workspaces_files_and_what_needs_approval(
    client, as_new_user, user_id, tmp_path: Path
) -> None:
    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    from ai_almanac.server.services.llm import trim_chat_history

    workspace, session = await workspace_with_session(client, as_new_user)
    await register_file(user_id, workspace["id"], write_nc(tmp_path / "obs.nc"), "obs_2026")
    ctx = tool_ctx(user_id, session["id"], workspace["id"])

    listed = await inspection.list_artifacts(ctx)
    part = ToolReturnPart(tool_name="list_artifacts", content=listed, tool_call_id="c1")
    [request] = await trim_chat_history([ModelRequest(parts=[part])])
    assert "obs_2026" in json.dumps(request.parts[0].content)

    capabilities = (await inspection.list_capabilities(ctx))["capabilities"]
    needs_approval = {c["name"]: c["needs_approval"] for c in capabilities}
    assert needs_approval["benchmark.submit_benchmark"] is True
    assert needs_approval["benchmark.validate_benchmark"] is False


_FORECAST_CSV = """id,time,cv_week1,cv_week2,cv_week3,cv_week4,cv_later,lat,lon
a,2026-07-01,0.6,0.2,0.1,0.05,0.05,9.0,38.0
b,2026-07-01,0.2,0.5,0.2,0.05,0.05,9.5,38.5
a,2026-07-08,0.1,0.1,0.1,0.1,0.6,9.0,38.0
"""


@pytest.mark.asyncio
async def test_a_forecasts_results_and_raw_season_are_readable_once_it_is_linked(
    client, as_new_user, user_id
) -> None:
    from ai_almanac.server.services.forecast_pipeline import season_output_name
    from ai_almanac.server.services.storage import get_storage

    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    job_id = insert_job(user_id, {"blend_id": "b1"}, job_type="forecast")
    output = Path(get_storage().job_output_uri(job_id)[0])
    (output / "blended_forecast_probabilities.csv").write_text(_FORECAST_CSV)
    write_season_nc(output / season_output_name("aifs"))
    ctx = tool_ctx(user_id, session["id"], wid)

    unlinked = await runs.get_forecast_results(ctx, job_id=job_id)
    assert "not linked" in unlinked["error"]
    await workspaces.link_job(wid, job_id)

    results = await runs.get_forecast_results(ctx, job_id=job_id)
    first, second = results["issue_dates"]
    assert first["issue_date"] == "2026-07-01" and first["points"] == 2
    assert first["mean_probability"]["week1"] == 0.4
    assert first["most_likely_window_points"] == {
        "week1": 1,
        "week2": 1,
        "week3": 0,
        "week4": 0,
        "later": 0,
    }
    assert second["most_likely_window_points"]["later"] == 1

    season = await workspaces.resolve(wid, "aifs_season_forecast")
    assert season["kind"] == "dataset"


@pytest.mark.asyncio
async def test_a_workspace_tool_refuses_a_workspace_that_is_not_the_callers(
    client, as_new_user, user_id
) -> None:
    """A session's scope says where it points; the tools still check whose workspace it is."""
    owner = {"X-Forwarded-User": "workspace-owner"}
    theirs = (await client.post("/workspaces", headers=owner, json={"name": "Theirs"})).json()
    _, session = await workspace_with_session(client, as_new_user)

    scope = ChatScope(kind="workspace", key=theirs["id"], job_ids=[])
    deps = await chat_deps(user_id, session["id"], scope)
    result = await inspection.list_artifacts(SimpleNamespace(deps=deps))

    assert result == {"error": "This workspace is not available in this conversation."}
