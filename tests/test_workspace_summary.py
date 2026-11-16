"""The workspace summary: season state, setup, latest forecast and headline skill."""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest

from ai_almanac.server.services import workspace_summary as summary
from ai_almanac.server.services.metrics import JobMetrics
from ai_almanac.server.services.seasons import SeasonWindow
from ai_almanac.server.services.workspace_settings import WorkspaceSettings
from tests.helpers.records import insert_job

KIREMT = SeasonWindow(name="Kiremt", onset_search_start="05-01", last_issue_date="07-31")

_POOLED_CSV = """id,brier,rps,auc,n,model,cv_method,brier_skill,rps_skill,AUC diff
ALL,0.52,0.44,0.87,70175,blended_model,global,0.0486,0.1152,0.89
ALL,0.55,0.50,0.86,70175,unc_clim_raw,raw,0.0,0.0,0.0
"""

_FORECAST_CSV = """id,time,cv_week1,cv_week2,cv_week3,cv_week4,cv_later,lat,lon
a,2026-07-01,0.6,0.2,0.1,0.05,0.05,9.0,38.0
a,2026-05-04,0.1,0.1,0.1,0.1,0.6,9.0,38.0
b,2026-07-01,0.2,0.5,0.2,0.05,0.05,9.5,38.5
"""


def _job(job_type: str, config: dict, *, status="complete", created="2026-06-01", run_id=None):
    return {
        "id": f"{job_type}-{created}",
        "job_type": job_type,
        "status": status,
        "run_id": run_id,
        "created_at": created,
        "config": config,
        "metrics_cache": None,
    }


def _metrics(job_id: str, rows: dict[str, dict[str, float]], window: str = "1-15") -> JobMetrics:
    stat = {"min": 0, "max": 0, "p25": 0, "p50": 0, "p75": 0, "p90": 0, "unit": "days"}
    return JobMetrics.model_validate(
        {
            "job_id": job_id,
            "windows": [
                {
                    "window": window,
                    "model": model,
                    "tolerance_days": 3,
                    "metrics": {name: {**stat, "mean": mean} for name, mean in metrics.items()},
                }
                for model, metrics in rows.items()
            ],
        }
    )


@pytest.mark.parametrize(
    ("settings", "today", "name", "phase", "is_default", "issue_dates"),
    [
        (
            WorkspaceSettings(season=KIREMT),
            date(2027, 3, 1),
            "Kiremt",
            "upcoming",
            False,
            (date(2027, 5, 1), date(2027, 7, 31)),
        ),
        (
            WorkspaceSettings(season=KIREMT),
            date(2026, 6, 10),
            "Kiremt",
            "in_season",
            False,
            (date(2026, 5, 1), date(2026, 7, 31)),
        ),
        (
            WorkspaceSettings(),
            date(2026, 11, 9),
            "Rainy season",
            "ended",
            True,
            (date(2026, 5, 1), date(2026, 7, 31)),
        ),
    ],
    ids=["set, before it starts", "set, under way", "unset, after it ended"],
)
def test_the_season_follows_the_workspace_setting_or_falls_back_and_says_so(
    settings, today, name, phase, is_default, issue_dates
) -> None:
    season = summary.season_summary(settings, today)

    assert (season.name, season.phase, season.is_default) == (name, phase, is_default)
    assert (season.first_issue_date, season.last_issue_date) == issue_dates


def test_setup_names_the_observations_and_each_model_once_with_display_names() -> None:
    jobs = [
        _job(
            "blend",
            {"model_names": ["aifs", "fuxi"], "region_id": "ethiopia"},
            created="2026-06-02",
        ),
        _job(
            "benchmark",
            {
                "model_name": "aifs",
                "model_display_name": "AIFS",
                "region_id": "ethiopia",
                "dataset_config": {"source_name": "CHIRPS Ethiopia"},
            },
            created="2026-06-01",
        ),
    ]
    setup = summary.setup_summary(jobs, "Ethiopia")
    assert setup.region == "Ethiopia"
    assert setup.observations == "CHIRPS Ethiopia"
    assert setup.models == ["AIFS", "fuxi"]
    assert summary.most_common_region(jobs) == "ethiopia"


def test_the_latest_forecast_reports_its_issue_date_span_and_blend() -> None:
    job = _job("forecast", {"blend_id": "b1"}, created="2026-07-02")
    forecast = summary.forecast_summary(job, "AIFS-FuXi Ethiopia Blend", _FORECAST_CSV)
    assert forecast.blended and forecast.blend_name == "AIFS-FuXi Ethiopia Blend"
    assert (forecast.first_issue_date, forecast.last_issue_date) == (
        date(2026, 5, 4),
        date(2026, 7, 1),
    )
    assert forecast.issue_dates == 2

    raw = summary.forecast_summary(_job("forecast", {"models": ["aifs"]}), None, None)
    assert not raw.blended and raw.issue_dates == 0 and raw.first_issue_date is None


def test_blend_skill_reads_the_week_level_row_and_the_held_out_years() -> None:
    blend = _job(
        "blend",
        {
            "blend_name": "AIFS-FuXi Ethiopia Blend",
            "blend_params": {"cv_holdout_years": "2019:2024"},
        },
    )
    skill = summary.blend_skill(blend, _POOLED_CSV)
    assert skill is not None
    assert skill.name == "AIFS-FuXi Ethiopia Blend"
    assert (skill.rps_skill, skill.brier_skill) == (0.1152, 0.0486)
    assert skill.holdout_years == "2019–2024"
    assert summary.blend_skill(blend, "model,rps_skill\nunc_clim_raw,0\n") is None


def test_benchmark_skill_is_the_lowest_onset_error_over_the_shortest_window() -> None:
    metrics = {
        "job-aifs": _metrics(
            "job-aifs", {"AIFS": {"mean_mae": 6.42}, "climatology": {"mean_mae": 11.9}}
        ),
        "job-fuxi": _metrics(
            "job-fuxi", {"FuXi": {"mean_mae": 6.61}, "climatology": {"mean_mae": 11.9}}
        ),
    }
    skill = summary.benchmark_skill(metrics)
    assert skill is not None
    assert (skill.job_id, skill.model, skill.mae_days) == ("job-aifs", "AIFS", 6.4)
    assert (skill.climatology_mae_days, skill.window) == (11.9, "1-15")

    longer_only = {"j": _metrics("j", {"AIFS": {"mean_mae": 12.0}}, window="16-30")}
    assert summary.benchmark_skill(longer_only).window == "16-30"
    assert summary.benchmark_skill({}) is None


# --- endpoint -------------------------------------------------------------------


def _output_dir(job_id: str) -> Path:
    from ai_almanac.server.services.storage import get_storage

    output, _ = get_storage().job_output_uri(job_id)
    return Path(output)


@pytest.mark.asyncio
async def test_the_summary_endpoint_reads_the_newest_blend_and_forecast(
    client, as_new_user, user_id
) -> None:
    from ai_almanac.server.services import workspaces
    from ai_almanac.server.services.artifacts import index_job_artifacts

    response = await client.post("/workspaces", headers=as_new_user, json={"name": "Kiremt"})
    wid = response.json()["id"]

    blend_id = insert_job(
        user_id,
        {
            "blend_name": "AIFS-FuXi Ethiopia Blend",
            "model_names": ["aifs", "fuxi"],
            "region_id": "ethiopia",
            "blend_params": {"cv_holdout_years": "2019:2024"},
        },
        job_type="blend",
        created_at="2026-06-01T00:00:00",
    )
    (_output_dir(blend_id) / "summary_models_pooled_fixed_cutoff_2019_2024.csv").write_text(
        _POOLED_CSV
    )
    forecast_id = insert_job(
        user_id,
        {"blend_id": blend_id, "region_id": "ethiopia"},
        job_type="forecast",
        created_at="2026-07-02T00:00:00",
    )
    (_output_dir(forecast_id) / "blended_forecast_probabilities.csv").write_text(_FORECAST_CSV)
    newer_failed = insert_job(
        user_id,
        {"blend_name": "Retry", "region_id": "ethiopia"},
        job_type="blend",
        status="failed",
        created_at="2026-06-05T00:00:00",
    )
    for job_id in (blend_id, forecast_id):
        await index_job_artifacts(job_id)
        await workspaces.link_job(wid, job_id)
    await workspaces.link_job(wid, newer_failed)

    response = await client.get(f"/workspaces/{wid}/summary", headers=as_new_user)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["season"]["is_default"] is True
    assert body["season"]["name"] == "Kiremt", "the region's own name for its rainy season"
    assert body["setup"]["region"] == "Ethiopia"
    assert body["setup"]["models"] == ["aifs", "fuxi"]
    assert body["latest_forecast"]["blend_name"] == "AIFS-FuXi Ethiopia Blend"
    assert body["latest_forecast"]["last_issue_date"] == "2026-07-01"
    assert body["skill"]["kind"] == "blend", "the newest finished blend, not a newer failed one"
    assert body["skill"]["rps_skill"] == pytest.approx(0.1152)
    assert body["skill"]["holdout_years"] == "2019–2024"

    detail = (await client.get(f"/workspaces/{wid}", headers=as_new_user)).json()
    headlines = {job["job_type"]: job["headline"] for job in detail["jobs"]}
    assert headlines["blend"]["kind"] == "blend"
    assert headlines["blend"]["rps_skill"] == pytest.approx(0.1152)
    assert headlines["forecast"]["kind"] == "forecast"
    assert headlines["forecast"]["last_issue_date"] == "2026-07-01"


def test_a_workspace_without_a_season_works_in_the_one_usual_in_its_country() -> None:
    today = date(2026, 3, 1)
    unset, kiremt = WorkspaceSettings(), WorkspaceSettings(season=SeasonWindow(name="Belg"))

    ethiopia = summary.season_summary(unset, today, "ETH")
    unnamed = summary.season_summary(unset, today, "KEN")
    own = summary.season_summary(kiremt, today, "ETH")

    assert (ethiopia.name, ethiopia.is_default) == ("Kiremt", True)
    assert ethiopia.starts_label == "onset search starts 1 May"
    assert (
        summary.season_summary(
            WorkspaceSettings(season=SeasonWindow(onset_search_start="03-15")), today
        ).starts_label
        == "onset search starts 15 March"
    )
    assert ethiopia.first_issue_date == unnamed.first_issue_date == date(2026, 5, 1)
    assert unnamed.name == summary.season_summary(unset, today).name == "Rainy season"
    assert (own.name, own.is_default) == ("Belg", False)


@pytest.mark.asyncio
async def test_an_empty_workspace_still_has_a_season(client, as_new_user) -> None:
    response = await client.post("/workspaces", headers=as_new_user, json={"name": "Fresh"})
    wid = response.json()["id"]

    body = (await client.get(f"/workspaces/{wid}/summary", headers=as_new_user)).json()
    assert body["season"]["name"] == "Rainy season" and body["season"]["is_default"] is True
    assert body["setup"] == {"region": None, "observations": None, "models": []}
    assert body["latest_forecast"] is None and body["skill"] is None


@pytest.mark.asyncio
async def test_another_users_workspace_summary_is_not_found(client, as_new_user) -> None:
    response = await client.post("/workspaces", headers=as_new_user, json={"name": "Mine"})
    wid = response.json()["id"]
    other = {"X-Forwarded-User": f"pytest-{uuid.uuid4()}"}

    assert (await client.get(f"/workspaces/{wid}/summary", headers=other)).status_code == 404
