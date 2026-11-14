"""Forecasting from a workspace: the assistant's forecast tools take the workspace's season,
say what an approval covers, and re-issue a linked forecast only when something is new."""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from ai_almanac.server.db import get_db
from ai_almanac.server.services import (
    job_submission,
    workspaces,
)
from ai_almanac.server.services.primitives import runs
from ai_almanac.server.services.seasons import SeasonWindow
from ai_almanac.server.tables import jobs
from tests.helpers.forecasts import FakeRunner, forecast_a_linked_blend, live_model_archive
from tests.helpers.tool_calls import approve, tool_ctx
from tests.helpers.workspaces import workspace_with_session


@pytest.fixture
def _stub_runner(monkeypatch):
    monkeypatch.setattr(job_submission, "get_job_runner", lambda: FakeRunner())


@pytest.mark.asyncio
async def test_updating_a_workspace_forecast_with_nothing_new_says_it_is_up_to_date(
    client, as_new_user, user_id, _stub_runner, monkeypatch
):
    workspace, session = await workspace_with_session(client, as_new_user)
    _, forecast_id = await forecast_a_linked_blend(user_id, monkeypatch, date(2026, 6, 15))
    ctx = tool_ctx(user_id, session["id"], workspace["id"])

    unlinked = await approve(runs.update_forecast, ctx, job_id=forecast_id)
    assert "not linked" in unlinked["error"]
    await workspaces.link_job(workspace["id"], forecast_id)

    updated = await approve(runs.update_forecast, ctx, job_id=forecast_id)

    assert updated["up_to_date"] is True
    assert "already up to date" in updated["message"]


@pytest.mark.asyncio
async def test_a_raw_forecast_has_no_blended_results_and_points_to_its_seasons(
    client, as_new_user, user_id, _stub_runner, monkeypatch
):
    monkeypatch.setattr(job_submission, "utc_today", lambda: date(2026, 6, 15))
    workspace, session = await workspace_with_session(client, as_new_user)
    ctx = tool_ctx(user_id, session["id"], workspace["id"])
    source_id = await live_model_archive()

    ran = await approve(runs.run_forecast, ctx, models=[source_id])
    assert ran["blended"] is False and ran["season"]["phase"] == "in_season"
    async with get_db() as conn:
        await conn.execute(
            sa.update(jobs).where(jobs.c.id == ran["job"]["id"]).values(status="complete")
        )

    results = await runs.get_forecast_results(ctx, job_id=ran["job"]["id"])

    assert results["blended"] is False
    assert "no blended probabilities" in results["message"]
    assert "_season_forecast" in results["message"]


@pytest.mark.asyncio
async def test_a_forecast_naming_neither_a_blend_nor_models_is_refused_before_approval(
    client, as_new_user, user_id
):
    workspace, session = await workspace_with_session(client, as_new_user)

    refused = await runs.run_forecast(tool_ctx(user_id, session["id"], workspace["id"]))

    assert "exactly one of blend_id" in refused["error"]


KIREMT = SeasonWindow(name="Kiremt", onset_search_start="05-01", last_issue_date="07-31")


async def _workspace_in_kiremt(client, as_new_user, user_id):
    from ai_almanac.server.services import workspace_settings

    workspace, session = await workspace_with_session(client, as_new_user)
    await workspace_settings.update_settings(
        workspace["id"], workspace_settings.WorkspaceSettings(season=KIREMT)
    )
    return workspace, session


@pytest.mark.asyncio
async def test_a_workspace_forecast_uses_the_workspace_season(
    client, as_new_user, user_id, monkeypatch
):
    monkeypatch.setattr(job_submission, "utc_today", lambda: date(2026, 11, 5))
    workspace, session = await _workspace_in_kiremt(client, as_new_user, user_id)
    source_id = await live_model_archive()

    checked = await runs.check_forecast(
        tool_ctx(user_id, session["id"], workspace["id"]), models=[source_id]
    )

    assert checked["can_run"] is False
    assert checked["season"]["summary"].startswith("Kiremt 2026 ended on 31 July 2026")


@pytest.mark.asyncio
async def test_a_forecast_approval_says_where_the_season_stands_and_what_it_costs(
    client, as_new_user, user_id, monkeypatch
):
    from pydantic_ai import ApprovalRequired

    monkeypatch.setattr(job_submission, "utc_today", lambda: date(2026, 6, 15))
    workspace, session = await _workspace_in_kiremt(client, as_new_user, user_id)
    source_id = await live_model_archive()

    with pytest.raises(ApprovalRequired) as asked:
        await runs.run_forecast(
            tool_ctx(user_id, session["id"], workspace["id"]), models=[source_id]
        )

    shown = {f["label"]: f["value"] for f in asked.value.metadata["display"]["facts"]}
    assert shown["Season"].startswith("Kiremt 2026")
    assert shown["GPU time"].startswith("About ")
    # The approval covers the season the forecast will run in, not only what was typed.
    assert asked.value.metadata["params"]["season"]["name"] == "Kiremt"


def test_a_workspace_blend_takes_only_the_season_dates_it_leaves_unset():
    spec = runs.BlendSpec(
        name="Kiremt blend",
        obs_dataset_id="obs",
        model_ids=["a", "b"],
        training_years="2008:2021",
        cv_holdout_years="2008:2021",
        ref_onset_month_day="06-15",
    )
    season = KIREMT.model_copy(update={"onset_search_start": "04-15"})

    filled = runs.with_season_dates(spec, season)

    assert (filled.cutoff_month_day, filled.ref_onset_month_day) == ("04-15", "06-15")
    assert runs.with_season_dates(spec, None) == spec
