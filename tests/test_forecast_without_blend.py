"""Forecasting with model archives alone, no blend: planning, cost checks,
the workload that skips scoring, and re-issuing from a workspace."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from ai_almanac.server.db import get_db
from ai_almanac.server.services import (
    forecast_checks,
    job_submission,
    trajectory_sets,
)
from ai_almanac.server.services.forecast_pipeline import SeasonPlan, season_output_name
from ai_almanac.server.services.job_submission import ForecastCreate
from ai_almanac.server.services.seasons import SeasonWindow
from ai_almanac.server.tables import jobs
from tests.helpers.forecasts import FakeRunner, live_model_archive
from tests.helpers.records import seed_source

FORECASTS_APP = Path(__file__).parents[1] / "modal" / "forecasts_app.py"


@pytest.fixture
def _stub_runner(monkeypatch):
    monkeypatch.setattr(job_submission, "get_job_runner", lambda: FakeRunner())


@pytest.fixture
def forecasts_app(monkeypatch):
    import importlib.util
    import sys

    monkeypatch.delitem(sys.modules, "forecast_pipeline", raising=False)
    spec = importlib.util.spec_from_file_location("almanac_forecasts_app", FORECASTS_APP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("forecast_pipeline", None)


async def _saved_config(job_id: str) -> dict:
    async with get_db() as conn:
        saved = (
            await conn.execute(sa.select(jobs.c.config_json).where(jobs.c.id == job_id))
        ).scalar_one()
    return json.loads(saved)


def test_a_forecast_names_a_blend_or_models_but_not_both():
    with pytest.raises(ValidationError, match="exactly one of blend_id"):
        ForecastCreate(blend_id="b1", models=["s1"])
    with pytest.raises(ValidationError, match="exactly one of blend_id"):
        ForecastCreate()
    with pytest.raises(ValidationError, match="same data source twice"):
        ForecastCreate(models=["s1", "s1"])


@pytest.mark.asyncio
async def test_a_raw_forecast_plans_its_models_season_and_records_no_blend(
    user_id, _stub_runner, monkeypatch
):
    monkeypatch.setattr(job_submission, "utc_today", lambda: date(2026, 11, 5))
    source_id = await live_model_archive()

    forecast = await job_submission.create_forecast_for_user(
        ForecastCreate(
            models=[source_id],
            season_year=2026,
            season=SeasonWindow(name="Kiremt", last_issue_date="07-15"),
        ),
        user_id,
    )

    config = await _saved_config(forecast.id)
    assert config["blend_id"] is None and config["blend_config_snapshot"] is None
    assert config["model_source_ids"] == [source_id]
    assert forecast.blend_id == ""
    plan = SeasonPlan.parse(config["season_plan"])
    (run,) = plan.runs
    assert (plan.season, run.member, run.model.registry_id) == (2026, "aifs_v2_india_0_25", "aifs2")
    assert run.issue_dates[-1] == date(2026, 7, 13)


@pytest.mark.asyncio
async def test_a_raw_forecast_of_an_ended_season_is_refused_unless_asked_for_by_year(
    user_id, _stub_runner, monkeypatch
):
    from fastapi import HTTPException

    monkeypatch.setattr(job_submission, "utc_today", lambda: date(2026, 11, 5))
    source_id = await live_model_archive()

    with pytest.raises(HTTPException) as refused:
        await job_submission.build_forecast_job(ForecastCreate(models=[source_id]), user_id)

    assert refused.value.status_code == 409
    assert "ended on 31 July 2026; the first 2027 issue date is 1 May 2027" in (
        refused.value.detail
    )


@pytest.mark.asyncio
async def test_a_raw_forecast_refuses_a_model_with_no_live_model(user_id, _stub_runner):
    from fastapi import HTTPException

    unlinked = await seed_source("model", "Old archive", "gs://data/models/old", grid_step=0.25)

    with pytest.raises(HTTPException) as refused:
        await job_submission.build_forecast_job(ForecastCreate(models=[unlinked]), user_id)

    assert refused.value.status_code == 400
    assert refused.value.detail.startswith("These models cannot run as a live forecast.")


@pytest.mark.asyncio
async def test_check_forecast_reports_a_cold_season_then_a_cached_one(user_id, monkeypatch):
    monkeypatch.setattr(job_submission, "utc_today", lambda: date(2026, 6, 15))
    source_id = await live_model_archive()
    request = ForecastCreate(models=[source_id])

    cold = await forecast_checks.check_forecast(request, user_id)

    assert cold["can_run"] is True and cold["needs_gpu"] is True
    assert cold["season"]["phase"] == "in_season"
    (model,) = cold["models"]
    assert model["live_model"] == "aifs2" and model["season_cache_ready"] is False
    assert len(model["issue_dates_missing"]) == model["issue_dates_planned"] > 0
    assert model["gpu_estimate"]["gpu_minutes"] > 0

    async with get_db() as conn:
        cached = await trajectory_sets.create_or_get_set(
            conn,
            model_id="aifs_v2_india_0_25",
            model_name="aifs_v2_india_0_25",
            init_source="gfs",
            season="2026",
            triggered_by_user_id=user_id,
            storage_prefix="season-forecasts",
        )
        await trajectory_sets.mark_dates_covered(conn, cached["id"], model["issue_dates_missing"])
        await trajectory_sets.set_status(conn, cached["id"], "complete")

    warm = await forecast_checks.check_forecast(request, user_id)

    assert warm["needs_gpu"] is False
    (model,) = warm["models"]
    assert model["season_cache_ready"] is True and model["issue_dates_missing"] == []
    assert model["gpu_estimate"]["gpu_minutes"] == 0


@pytest.mark.asyncio
async def test_check_forecast_before_the_season_says_when_it_starts(user_id, monkeypatch):
    monkeypatch.setattr(job_submission, "utc_today", lambda: date(2026, 4, 1))
    source_id = await live_model_archive()

    checked = await forecast_checks.check_forecast(ForecastCreate(models=[source_id]), user_id)

    assert checked["can_run"] is False
    assert "first issue date is 1 May 2026" in checked["reason"]
    assert checked["models"] == [{"model": "aifs_v2_india_0_25", "live_model": "aifs2"}]


def test_a_local_raw_forecast_publishes_each_season_without_scoring(monkeypatch, tmp_path):
    from ai_almanac.server.services import job_workload

    plan = SeasonPlan.parse(
        {
            "season": 2026,
            "init_source": "gfs",
            "lead_day": 45,
            "runs": [
                {
                    "member": "aifs",
                    "model": {"id": "aifs2", "earth2studio_class": "AIFS2", "env": "aifs2"},
                    "unit_cvt": 1.0,
                    "spatial_bounds": None,
                    "issue_dates": ["2026-05-04"],
                }
            ],
        }
    )
    config = {"job_type": "forecast", "blend_config_snapshot": None, "season_plan": plan.to_dict()}
    output_dir = tmp_path / "job" / "output"
    output_dir.mkdir(parents=True)
    monkeypatch.setattr(
        job_workload,
        "get_storage",
        lambda: type(
            "S", (), {"is_local": True, "job_output_uri": lambda _, j: (output_dir, None)}
        )(),
    )

    def fake_inference(cmd, env=None, environment="base"):
        staging = Path(cmd[cmd.index("--staging-dir") + 1])
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "aifs.nc").write_bytes(b"season")

    scored: list = []
    monkeypatch.setattr(job_workload, "forecast_pixi_run", fake_inference)
    monkeypatch.setattr(job_workload, "blending_pixi_run", lambda *a, **k: scored.append(a))
    monkeypatch.setattr(job_workload, "_stream_process", lambda process: None)
    monkeypatch.setattr(job_workload, "_mark_trajectory_coverage", lambda config: None)

    job_workload._run_forecast("job", config)

    assert scored == []
    assert (output_dir / season_output_name("aifs")).read_bytes() == b"season"


def test_a_modal_raw_forecast_uploads_each_season_without_scoring(forecasts_app, monkeypatch):
    pipeline = forecasts_app._load_pipeline(forecasts_app.FORECAST_PIPELINE_PY)
    plan = pipeline.plan_season(
        {
            "fuxi": (
                pipeline.ForecastModel.parse({"id": "fuxi", "earth2studio_class": "FuXi"}),
                pipeline.ArchiveLayout.parse({"init_days": "0,3"}),
            )
        },
        season=2026,
        season_start_month_day="05-01",
        init_source="gfs",
        today=date(2026, 5, 12),
    )
    uploaded: list = []
    monkeypatch.delenv("SERVICE_ACCOUNT_JSON", raising=False)
    monkeypatch.setattr(forecasts_app, "_season_bundles", lambda *a: {"fuxi": b"bundle"})
    monkeypatch.setattr(
        forecasts_app, "_upload_season_bundles_to_gcs", lambda *a: uploaded.append(a[-1])
    )
    monkeypatch.setattr(
        forecasts_app.modal.Function,
        "from_name",
        lambda *a: pytest.fail("a raw forecast must not be scored"),
    )

    forecasts_app.run_forecast.local(
        "job-raw", {"blend_config_snapshot": None, "season_plan": plan.to_dict()}, "bucket"
    )

    assert uploaded == [{"fuxi": b"bundle"}]
