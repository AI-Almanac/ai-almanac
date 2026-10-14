"""The forecast Modal app loads forecast_pipeline.py by file path, the way its
containers do. These run that loader locally so a module that only breaks when
loaded that way fails here instead of on Modal."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

_APP_PATH = Path(__file__).parents[1] / "modal" / "forecasts_app.py"


@pytest.fixture
def forecasts_app(monkeypatch):
    monkeypatch.delitem(sys.modules, "forecast_pipeline", raising=False)
    spec = importlib.util.spec_from_file_location("almanac_forecasts_app", _APP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("forecast_pipeline", None)


def test_the_bundled_pipeline_builds_store_keys_when_loaded_by_path(forecasts_app):
    pipeline = forecasts_app._load_pipeline(forecasts_app.FORECAST_PIPELINE_PY)
    key = pipeline.TrajectoryKey("fuxi", "gfs", lead_day=2)

    assert key.uri("gs://bucket/season-forecasts", dt.date(2026, 5, 4)) == (
        "gs://bucket/season-forecasts/fuxi/gfs/v1/lead2d/2026-05-04.nc"
    )


def test_the_bundled_pipeline_plans_a_season_when_loaded_by_path(forecasts_app):
    pipeline = forecasts_app._load_pipeline(forecasts_app.FORECAST_PIPELINE_PY)
    plan = pipeline.plan_season(
        {
            "fuxi": (
                pipeline.ForecastModel.parse({"id": "fuxi", "earth2studio_class": "FuXi"}),
                pipeline.ArchiveLayout.parse({"init_days": "0,3"}),
            )
        },
        season=2020,
        season_start_month_day="05-01",
        init_source="gfs",
        today=dt.date(2020, 5, 12),
    )

    assert pipeline.SeasonPlan.parse(plan.to_dict()) == plan


def test_every_season_function_the_runner_calls_is_registered(forecasts_app):
    functions = set(forecasts_app.app.registered_functions)

    assert {"run_forecast", "run_season_forecast_bundle"} <= functions
    assert {f"run_season_rollouts_{env}" for env in forecasts_app.INFERENCE_IMAGES} <= functions
