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

    assert {"run_forecast", "roll_out_missing_dates", "assemble_season_bundle"} <= functions
    assert {f"run_season_rollouts_{env}" for env in forecasts_app.INFERENCE_IMAGES} <= functions


MAY_4, MAY_7, MAY_11 = dt.date(2020, 5, 4), dt.date(2020, 5, 7), dt.date(2020, 5, 11)


class _FakeCall:
    def __init__(self, result):
        self._result = result
        self.cancelled = False

    def get(self):
        return self._result()

    def cancel(self):
        self.cancelled = True


class _FakeRollouts:
    """Stands in for run_season_rollouts_<env>: each spawn is one GPU
    container's chunk, which stores its dates (or fails) as the real one does."""

    def __init__(self, pipeline, store: Path, failing_member: str | None = None):
        self.pipeline, self.store, self.failing_member = pipeline, store, failing_member
        self.calls: list[tuple[str, list[str]]] = []

    def spawn(self, member, config, issue_dates):
        self.calls.append((member, issue_dates))
        return _FakeCall(lambda: self._roll_out(member, config, issue_dates))

    def _roll_out(self, member, config, issue_dates):
        if member == self.failing_member:
            raise RuntimeError(f"GPU fell over rolling out {member}")
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
        self.commits = 0

    def reload(self):
        self.reloads += 1

    def commit(self):
        self.commits += 1


@pytest.fixture
def season(forecasts_app, monkeypatch, tmp_path):
    """The season phases against a Volume-style store in tmp_path, with the
    GPU functions stubbed and assembly recorded instead of run."""
    pipeline = forecasts_app._load_pipeline(forecasts_app.FORECAST_PIPELINE_PY)
    assembled: list[tuple] = []

    def fake_assemble(store, key, run, scratch_root, out_path):
        assembled.append((run.member, run.issue_dates))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"season")
        return out_path

    monkeypatch.setattr(forecasts_app, "_VOLUME_STORE", tmp_path)
    monkeypatch.setattr(forecasts_app, "forecast_volume", _FakeVolume())
    monkeypatch.setattr(pipeline, "assemble_season_netcdf", fake_assemble)

    def config(models: dict[str, tuple[str, str]]) -> dict:
        """{member: (registry id, env)} -> a job config with a frozen plan."""
        plan = pipeline.plan_season(
            {
                member: (
                    pipeline.ForecastModel.parse(
                        {"id": model_id, "earth2studio_class": "FuXi", "env": env}
                    ),
                    pipeline.ArchiveLayout.parse({"init_days": "0,3"}),
                )
                for member, (model_id, env) in models.items()
            },
            season=2020,
            season_start_month_day="05-01",
            init_source="gfs",
            today=MAY_11,
        )
        return {"season_plan": plan.to_dict()}

    def gpus(failing_member: str | None = None) -> _FakeRollouts:
        rollouts = _FakeRollouts(pipeline, tmp_path, failing_member)
        monkeypatch.setattr(forecasts_app, "SEASON_ROLLOUT_FNS", {"base": rollouts})
        return rollouts

    def store(model_id: str, *issue_dates):
        key = pipeline.TrajectoryKey(model_id, "gfs")
        for issue_date in issue_dates:
            pipeline.write_stored_trajectory(tmp_path, key, issue_date, _trajectory())

    return SimpleNamespace(
        app=forecasts_app, config=config, gpus=gpus, store=store, assembled=assembled
    )


FUXI_ONLY = {"fuxi": ("fuxi", "base")}


def test_a_fully_stored_season_starts_no_gpu_container(season):
    gpus = season.gpus()
    season.store("fuxi", MAY_4, MAY_7, MAY_11)

    failures = season.app.roll_out_missing_dates.local(season.config(FUXI_ONLY))

    assert failures == {}
    assert gpus.calls == []


def test_missing_dates_fan_out_across_gpu_containers(season, monkeypatch):
    monkeypatch.setattr(season.app, "DATES_PER_ROLLOUT", 1)
    gpus = season.gpus()
    season.store("fuxi", MAY_7)

    season.app.roll_out_missing_dates.local(season.config(FUXI_ONLY))

    # One container per missing date, all spawned before any is awaited.
    assert gpus.calls == [("fuxi", ["2020-05-04"]), ("fuxi", ["2020-05-11"])]


def test_members_sharing_a_model_roll_out_its_dates_once(season):
    gpus = season.gpus()
    shared = {"fuxi_india": ("fuxi", "base"), "fuxi_india_wide": ("fuxi", "base")}

    season.app.roll_out_missing_dates.local(season.config(shared))

    rolled_out = [date for _, dates in gpus.calls for date in dates]
    assert sorted(rolled_out) == ["2020-05-04", "2020-05-07", "2020-05-11"]


def test_a_failed_chunk_fails_every_member_sharing_its_model(season):
    season.gpus(failing_member="fuxi_india")
    models = {
        "fuxi_india": ("fuxi", "base"),
        "fuxi_india_wide": ("fuxi", "base"),
        "graphcast": ("graphcast", "base"),
    }

    failures = season.app.roll_out_missing_dates.local(season.config(models))

    assert set(failures) == {"fuxi_india", "fuxi_india_wide"}
    assert "GPU fell over" in failures["fuxi_india_wide"]


def test_a_model_with_no_rollout_image_is_refused_by_name(season):
    season.gpus()

    with pytest.raises(ValueError, match="fuxi runs in environment 'gpu9'"):
        season.app.roll_out_missing_dates.local(season.config({"fuxi": ("fuxi", "gpu9")}))


def test_assembly_builds_the_season_from_the_store(season):
    season.store("fuxi", MAY_4, MAY_7, MAY_11)

    bundle = season.app.assemble_season_bundle.local("job-1", "fuxi", season.config(FUXI_ONLY))

    assert season.assembled == [("fuxi", (MAY_4, MAY_7, MAY_11))]
    assert bundle  # a tar.gz of the assembled season


def test_assembly_refuses_a_season_the_store_is_missing_dates_for(season):
    season.store("fuxi", MAY_4)

    with pytest.raises(RuntimeError, match="no stored trajectory for"):
        season.app.assemble_season_bundle.local("job-1", "fuxi", season.config(FUXI_ONLY))
    assert season.assembled == []


def test_a_failed_rollout_still_commits_what_it_wrote_to_the_volume(
    forecasts_app, monkeypatch, tmp_path
):
    pipeline = forecasts_app._load_pipeline(forecasts_app.FORECAST_PIPELINE_PY)
    volume = _FakeVolume()

    def fail_midway(*_args):
        raise RuntimeError("GPU fell over on the second date")

    monkeypatch.setattr(forecasts_app, "_VOLUME_STORE", tmp_path)
    monkeypatch.setattr(forecasts_app, "forecast_volume", volume)
    monkeypatch.setattr(pipeline, "roll_out_issue_dates", fail_midway)
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
        today=MAY_11,
    )

    with pytest.raises(RuntimeError, match="fell over"):
        forecasts_app._season_rollouts_impl(
            "fuxi", {"season_plan": plan.to_dict()}, ["2020-05-04", "2020-05-07"]
        )

    assert volume.commits == 1


def test_a_failed_volume_commit_does_not_hide_the_rollout_error(
    forecasts_app, monkeypatch, tmp_path
):
    pipeline = forecasts_app._load_pipeline(forecasts_app.FORECAST_PIPELINE_PY)

    class _BrokenVolume:
        def reload(self):
            pass

        def commit(self):
            raise OSError("volume unavailable")

    def fail_midway(*_args):
        raise RuntimeError("GPU fell over on the second date")

    monkeypatch.setattr(forecasts_app, "_VOLUME_STORE", tmp_path)
    monkeypatch.setattr(forecasts_app, "forecast_volume", _BrokenVolume())
    monkeypatch.setattr(pipeline, "roll_out_issue_dates", fail_midway)
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
        today=MAY_11,
    )

    with pytest.raises(RuntimeError, match="fell over"):
        forecasts_app._season_rollouts_impl(
            "fuxi", {"season_plan": plan.to_dict()}, ["2020-05-04", "2020-05-07"]
        )


def test_run_forecast_fails_a_job_queued_without_a_season_plan(forecasts_app, monkeypatch):
    """A job submitted before season plans existed fails with a clear reason
    instead of starting any season work."""
    forecasts_app._load_pipeline(forecasts_app.FORECAST_PIPELINE_PY)
    started: list = []
    monkeypatch.setattr(
        forecasts_app.roll_out_missing_dates, "remote", lambda *a: started.append(a)
    )
    monkeypatch.delenv("SERVICE_ACCOUNT_JSON", raising=False)

    with pytest.raises(RuntimeError, match="season_plan") as exc:
        forecasts_app.run_forecast.local(
            "job-old", {"blend_config_snapshot": {"model_names": ["fuxi"]}}, ""
        )

    assert "no season plan" in str(exc.value)
    assert started == []


def test_the_gpu_skips_dates_another_job_stored_after_the_sweep(
    forecasts_app, monkeypatch, tmp_path
):
    pipeline = forecasts_app._load_pipeline(forecasts_app.FORECAST_PIPELINE_PY)
    rolled_out: list = []
    monkeypatch.setattr(forecasts_app, "_VOLUME_STORE", tmp_path)
    monkeypatch.setattr(forecasts_app, "forecast_volume", _FakeVolume())
    monkeypatch.setattr(
        pipeline, "roll_out_issue_dates", lambda run, key, dates, *_: rolled_out.append(dates)
    )
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
        today=MAY_11,
    )
    pipeline.write_stored_trajectory(
        tmp_path, pipeline.TrajectoryKey("fuxi", "gfs"), MAY_4, _trajectory()
    )

    forecasts_app._season_rollouts_impl(
        "fuxi", {"season_plan": plan.to_dict()}, ["2020-05-04", "2020-05-07"]
    )

    assert rolled_out == [[MAY_7]]


def test_more_chunks_than_two_container_rounds_are_flagged(season, monkeypatch, capsys):
    monkeypatch.setattr(season.app, "DATES_PER_ROLLOUT", 1)
    monkeypatch.setattr(season.app, "MAX_PARALLEL_ROLLOUTS", 1)
    season.gpus()

    season.app.roll_out_missing_dates.local(season.config(FUXI_ONLY))

    assert "exceed two rounds of 1 containers" in capsys.readouterr().out


def test_gpu_calls_still_running_are_cancelled_when_the_phase_dies(season, monkeypatch):
    monkeypatch.setattr(season.app, "DATES_PER_ROLLOUT", 1)
    spawned: list[_FakeCall] = []

    class _GpusThatStopSpawning:
        def spawn(self, member, config, issue_dates):
            if spawned:
                raise RuntimeError("Modal refused the second container")
            spawned.append(_FakeCall(lambda: None))
            return spawned[-1]

    monkeypatch.setattr(season.app, "SEASON_ROLLOUT_FNS", {"base": _GpusThatStopSpawning()})

    with pytest.raises(RuntimeError, match="refused"):
        season.app.roll_out_missing_dates.local(season.config(FUXI_ONLY))

    assert [call.cancelled for call in spawned] == [True]


def test_a_failed_rollout_skips_assembling_the_other_members(season, monkeypatch):
    plan = season.app._season_plan(
        season.app._load_pipeline(),
        season.config({"fuxi": ("fuxi", "base"), "graphcast": ("graphcast", "base")}),
    )
    assembled: list = []
    monkeypatch.setattr(
        season.app.roll_out_missing_dates, "remote", lambda config: {"fuxi": "GPU fell over"}
    )
    monkeypatch.setattr(
        season.app.assemble_season_bundle, "spawn", lambda *args: assembled.append(args)
    )
    failures: dict[str, str] = {}

    bundles = season.app._season_bundles("job-1", plan, {}, failures)

    assert bundles == {}
    assert assembled == []
    assert failures == {"fuxi (season)": "GPU fell over"}
