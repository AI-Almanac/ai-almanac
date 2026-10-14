"""The forecast Modal app loads forecast_pipeline.py by file path, the way its
containers do. These run that loader locally so a module that only breaks when
loaded that way fails here instead of on Modal."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

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


MAY_4, MAY_7, MAY_11 = dt.date(2020, 5, 4), dt.date(2020, 5, 7), dt.date(2020, 5, 11)


class _FakeRollouts:
    """Stands in for run_season_rollouts_<env>: records the dates it was asked
    for and stores a trajectory for each, as the GPU function does."""

    def __init__(self, pipeline, store: Path):
        self.pipeline, self.store, self.calls = pipeline, store, []

    def remote(self, member, config, issue_dates):
        self.calls.append((member, issue_dates))
        plan = self.pipeline.SeasonPlan.parse(config["season_plan"])
        key = plan.key(plan.run(member))
        for issue_date in issue_dates:
            self.pipeline.write_stored_trajectory(
                self.store, key, dt.date.fromisoformat(issue_date), _trajectory()
            )


def _trajectory():
    import numpy as np
    import xarray as xr

    return xr.DataArray(
        np.zeros((2, 2, 2), dtype="float32"),
        dims=("day", "lat", "lon"),
        coords={"day": [0, 1], "lat": [1.0, 2.0], "lon": [10.0, 11.0]},
        name="tp",
    )


class _FakeVolume:
    def __init__(self):
        self.reloads = 0

    def reload(self):
        self.reloads += 1


@pytest.fixture
def season_bundle(forecasts_app, monkeypatch, tmp_path):
    """run_season_forecast_bundle against a Volume-style store in tmp_path, with
    the GPU function stubbed and assembly recorded instead of run."""
    pipeline = forecasts_app._load_pipeline(forecasts_app.FORECAST_PIPELINE_PY)
    rollouts = _FakeRollouts(pipeline, tmp_path)
    assembled: list[tuple] = []

    def fake_assemble(store, key, run, scratch_root, out_path):
        assembled.append(run.issue_dates)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"season")
        return out_path

    monkeypatch.setattr(forecasts_app, "_VOLUME_STORE", tmp_path)
    monkeypatch.setattr(forecasts_app, "SEASON_ROLLOUT_FNS", {"base": rollouts})
    volume = _FakeVolume()
    monkeypatch.setattr(forecasts_app, "forecast_volume", volume)
    monkeypatch.setattr(pipeline, "assemble_season_netcdf", fake_assemble)

    def run(env: str = "base") -> bytes:
        model = pipeline.ForecastModel.parse(
            {"id": "fuxi", "earth2studio_class": "FuXi", "env": env}
        )
        plan = pipeline.plan_season(
            {"fuxi": (model, pipeline.ArchiveLayout.parse({"init_days": "0,3"}))},
            season=2020,
            season_start_month_day="05-01",
            init_source="gfs",
            today=MAY_11,
        )
        config = {"season_plan": plan.to_dict()}
        return forecasts_app.run_season_forecast_bundle.local("job-1", "fuxi", config)

    def store(*issue_dates):
        key = pipeline.TrajectoryKey("fuxi", "gfs")
        for issue_date in issue_dates:
            pipeline.write_stored_trajectory(tmp_path, key, issue_date, _trajectory())

    return SimpleNamespace(
        run=run, store=store, rollouts=rollouts, assembled=assembled, volume=volume
    )


def test_a_fully_stored_season_never_calls_the_gpu(season_bundle):
    season_bundle.store(MAY_4, MAY_7, MAY_11)

    bundle = season_bundle.run()

    assert season_bundle.rollouts.calls == []
    assert season_bundle.volume.reloads == 0
    assert season_bundle.assembled == [(MAY_4, MAY_7, MAY_11)]
    assert bundle  # a tar.gz of the assembled season


def test_the_gpu_rolls_out_only_the_dates_the_store_is_missing(season_bundle):
    season_bundle.store(MAY_7)

    season_bundle.run()

    assert season_bundle.rollouts.calls == [("fuxi", ["2020-05-04", "2020-05-11"])]
    # The Volume-backed store is reloaded to see what the GPU container wrote.
    assert season_bundle.volume.reloads == 1
    assert season_bundle.assembled == [(MAY_4, MAY_7, MAY_11)]


def test_a_model_with_no_rollout_image_is_refused_by_name(season_bundle):
    with pytest.raises(ValueError, match="fuxi runs in environment 'gpu9'"):
        season_bundle.run(env="gpu9")
