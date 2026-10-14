from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_almanac.server.services.forecast_pipeline import (
    ArchiveLayout,
    Bounds,
    EnsembleSpec,
    ForecastModel,
    ModelRun,
    PlanError,
    SeasonPlan,
    TrajectoryKey,
    ensemble_config,
    plan_season,
    season_issue_dates,
)

FUXI = ForecastModel.parse({"id": "fuxi", "earth2studio_class": "FuXi"})
AIFS2 = ForecastModel.parse({"id": "aifs2", "earth2studio_class": "AIFS2", "env": "aifs2"})
END_OF_2020 = dt.date(2020, 12, 31)


def _plan(members: dict, *, today=END_OF_2020, **overrides) -> SeasonPlan:
    return plan_season(
        members,
        season=today.year,
        season_start_month_day=overrides.pop("season_start_month_day", "05-01"),
        init_source=overrides.pop("init_source", "gfs"),
        today=today,
        **overrides,
    )


def test_every_member_rolls_out_on_the_union_of_archive_calendars():
    # FuXi and AIFS-single-v2 archives were built on different calendars; the
    # blend inner-joins on issue date, so each must roll out on the other's too.
    plan = _plan(
        {
            "fuxi": (FUXI, ArchiveLayout.parse({"init_month_days": ["05-02", "05-09", "05-13"]})),
            "aifs_single_v2": (
                AIFS2,
                ArchiveLayout.parse({"init_month_days": ["05-01", "05-09"], "unit_cvt": 1000}),
            ),
        }
    )

    union = tuple(dt.date(2020, 5, day) for day in (1, 2, 9, 13))
    assert plan.run("fuxi").issue_dates == union
    assert plan.run("aifs_single_v2").issue_dates == union
    assert plan.run("aifs_single_v2").unit_cvt == 1000.0


def test_members_keep_their_weekdays_when_no_archive_has_a_calendar():
    plan = _plan(
        {
            "fuxi": (FUXI, ArchiveLayout.parse({"init_days": "0,3"})),
            "aifs2": (AIFS2, ArchiveLayout.parse({"init_days": "1"})),
        },
        today=dt.date(2020, 5, 12),
    )

    assert plan.run("fuxi").issue_dates == (
        dt.date(2020, 5, 4),
        dt.date(2020, 5, 7),
        dt.date(2020, 5, 11),
    )
    assert plan.run("aifs2").issue_dates == (dt.date(2020, 5, 5), dt.date(2020, 5, 12))


def test_plan_covers_the_season_up_to_today_and_trims_to_the_latest_dates():
    plan = _plan(
        {"fuxi": (FUXI, ArchiveLayout.parse({"init_days": "0,3"}))},
        today=dt.date(2020, 5, 12),
        max_issue_dates=2,
    )

    assert plan.run("fuxi").issue_dates == (dt.date(2020, 5, 7), dt.date(2020, 5, 11))
    assert plan.covered_dates() == {"fuxi": ["2020-05-07", "2020-05-11"]}


def test_plan_refuses_a_season_that_has_no_issue_dates_yet():
    with pytest.raises(PlanError, match="No issue dates for fuxi"):
        _plan(
            {"fuxi": (FUXI, ArchiveLayout.parse({}))},
            today=dt.date(2020, 4, 20),
        )


@pytest.mark.parametrize(
    ("season_start", "today"), [("13-01", END_OF_2020), ("02-29", dt.date(2021, 12, 31))]
)
def test_plan_refuses_a_season_start_that_is_not_a_date(season_start, today):
    with pytest.raises(PlanError, match="season start"):
        _plan(
            {"fuxi": (FUXI, ArchiveLayout.parse({}))},
            today=today,
            season_start_month_day=season_start,
        )


def test_plan_reads_back_unchanged_from_the_job_config():
    ensemble_model = ForecastModel.parse(
        {"id": "aifsens", "earth2studio_class": "AIFSENS", "ensemble": True, "nensemble": 4}
    )
    bounds = {"lat_min": 3, "lat_max": 15, "lon_min": 33, "lon_max": 48}
    plan = _plan(
        {
            "aifsens": (ensemble_model, ArchiveLayout.parse({"spatial_bounds": bounds})),
            "fuxi": (FUXI, ArchiveLayout.parse({"unit_cvt": "1000"})),
        },
        init_source="ifs",
        lead_day=2,
    )

    stored = json.loads(json.dumps(plan.to_dict()))

    assert SeasonPlan.parse(stored) == plan
    assert plan.run("aifsens").model.ensemble == EnsembleSpec(members=4, batch_size=1)
    assert plan.run("aifsens").spatial_bounds == Bounds(3.0, 15.0, 33.0, 48.0)


def test_plan_keys_trajectories_by_the_registry_model_not_the_member():
    plan = _plan({"aifs_v2_india_0_25": (AIFS2, ArchiveLayout.parse({}))})

    assert plan.key(plan.run("aifs_v2_india_0_25")) == TrajectoryKey("aifs2", "gfs")


def _stored_plan() -> dict:
    return _plan({"fuxi": (FUXI, ArchiveLayout.parse({}))}).to_dict()


@pytest.mark.parametrize(
    ("corrupt", "message"),
    [
        (lambda raw: raw.pop("runs"), "Malformed season plan"),
        (lambda raw: raw["runs"][0].update(issue_dates=["not-a-date"]), "Malformed"),
        (lambda raw: raw["runs"][0].update(issue_dates=[]), "No issue dates for fuxi"),
        (lambda raw: raw.update(init_source="era5"), "Unknown init source"),
        (lambda raw: raw.update(lead_day=0), "lead_day"),
        (lambda raw: raw["runs"].append(raw["runs"][0]), "member twice"),
        (lambda raw: raw["runs"][0]["model"].pop("earth2studio_class"), "missing"),
    ],
)
def test_a_corrupt_plan_is_refused_with_what_is_wrong(corrupt, message):
    raw = _stored_plan()
    corrupt(raw)

    with pytest.raises(PlanError, match=message):
        SeasonPlan.parse(raw)


@pytest.mark.parametrize(
    "settings", [{"nensemble": "eight"}, {"nensemble": -1}, {"nensemble_batch": -2}]
)
def test_a_registry_model_with_unusable_ensemble_settings_is_refused(settings):
    entry = {"id": "aifsens", "earth2studio_class": "AIFSENS", "ensemble": True, **settings}

    with pytest.raises(PlanError, match="unusable settings"):
        ForecastModel.parse(entry)


def test_a_job_without_a_plan_is_refused():
    with pytest.raises(PlanError, match="no season plan"):
        SeasonPlan.parse(None)


@pytest.mark.parametrize(
    "metadata",
    [
        {"init_days": "0,9"},
        {"init_days": "mon,thu"},
        {"init_month_days": ["13-01"]},
        {"unit_cvt": "lots"},
        {"spatial_bounds": {"lat_min": "north", "lat_max": 1, "lon_min": 0, "lon_max": 1}},
    ],
)
def test_archive_layout_refuses_unusable_metadata(metadata):
    with pytest.raises(PlanError):
        ArchiveLayout.parse(metadata)


def test_archive_layout_keeps_a_season_global_when_bounds_are_incomplete():
    layout = ArchiveLayout.parse({"spatial_bounds": {"lat_min": 3, "lat_max": 15}})

    assert layout.spatial_bounds is None
    assert layout.init_weekdays == (0, 3)
    assert layout.unit_cvt == 1.0


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ({"id": "aifs"}, None),
        ({"id": "aifs", "ensemble": False}, None),
        ({"id": "aifsens", "ensemble": True}, EnsembleSpec(members=8, batch_size=1)),
        # batch_size never exceeds the member count.
        (
            {"id": "x", "ensemble": True, "nensemble": 2, "nensemble_batch": 16},
            EnsembleSpec(members=2, batch_size=2),
        ),
    ],
)
def test_ensemble_config(model: dict, expected: EnsembleSpec | None):
    assert ensemble_config(model) == expected


def test_season_issue_dates_matches_weekday_cadence():
    dates = season_issue_dates("05-01", init_weekdays=[0, 3], year=2020, today=END_OF_2020)

    assert dates[:3] == [dt.date(2020, 5, 4), dt.date(2020, 5, 7), dt.date(2020, 5, 11)]
    assert all(d.weekday() in (0, 3) for d in dates)
    assert dates[-1] == dt.date(2020, 12, 31)


def test_season_issue_dates_stops_at_today():
    dates = season_issue_dates("05-01", init_weekdays=[0, 3], year=2020, today=dt.date(2020, 5, 8))

    assert dates == [dt.date(2020, 5, 4), dt.date(2020, 5, 7)]


def test_season_issue_dates_follows_calendar_schedule_over_weekday():
    # A calendar-anchored archive: Apr 1 2020 is a Wednesday and Apr 4 a
    # Saturday, so the weekday grid [0, 3] (Mon/Thu) would miss them entirely.
    # With a schedule present, the exact month-days win regardless of weekday.
    schedule = ["04-01", "04-04", "04-08", "05-02", "05-06"]
    dates = season_issue_dates(
        "01-01", init_weekdays=[0, 3], year=2020, schedule_month_days=schedule, today=END_OF_2020
    )
    assert dates == [
        dt.date(2020, 4, 1),
        dt.date(2020, 4, 4),
        dt.date(2020, 4, 8),
        dt.date(2020, 5, 2),
        dt.date(2020, 5, 6),
    ]


def test_season_issue_dates_schedule_respects_season_start_window():
    schedule = ["04-01", "04-04", "05-02", "05-06"]
    dates = season_issue_dates(
        "05-01", init_weekdays=[6], year=2020, schedule_month_days=schedule, today=END_OF_2020
    )
    assert dates == [dt.date(2020, 5, 2), dt.date(2020, 5, 6)]  # Apr dates trimmed


def test_season_issue_dates_schedule_skips_dates_absent_in_year():
    # 2021 is not a leap year, so 02-29 has no calendar date and is dropped.
    dates = season_issue_dates(
        "01-01",
        init_weekdays=[0],
        year=2021,
        schedule_month_days=["02-29", "03-01"],
        today=dt.date(2021, 12, 31),
    )
    assert dates == [dt.date(2021, 3, 1)]


def _trajectory():
    import numpy as np
    import xarray as xr

    return xr.DataArray(
        np.arange(24, dtype="float32").reshape(2, 3, 4),
        dims=("day", "lat", "lon"),
        coords={"day": [0, 1], "lat": [1.0, 2.0, 3.0], "lon": [10.0, 11.0, 12.0, 13.0]},
        name="tp",
    )


MAY_4, MAY_7, MAY_11 = dt.date(2026, 5, 4), dt.date(2026, 5, 7), dt.date(2026, 5, 11)
AIFS_GFS = TrajectoryKey("aifs", "gfs")
FUXI_GFS = TrajectoryKey("fuxi", "gfs")
FUXI_RUN = ModelRun("fuxi", FUXI, 1.0, None, (MAY_4, MAY_7))


def test_stored_trajectory_reads_back_what_was_written(tmp_path):
    import xarray as xr

    from ai_almanac.server.services.forecast_pipeline import (
        read_stored_trajectory,
        write_stored_trajectory,
    )

    write_stored_trajectory(tmp_path, AIFS_GFS, MAY_4, _trajectory())

    xr.testing.assert_allclose(read_stored_trajectory(tmp_path, AIFS_GFS, MAY_4), _trajectory())


def test_unstored_issue_dates_lists_only_missing_dates_in_order(tmp_path):
    from ai_almanac.server.services.forecast_pipeline import (
        unstored_issue_dates,
        write_stored_trajectory,
    )

    write_stored_trajectory(tmp_path, AIFS_GFS, MAY_7, _trajectory())

    assert unstored_issue_dates(tmp_path, AIFS_GFS, [MAY_4, MAY_7, MAY_11]) == [MAY_4, MAY_11]


@pytest.mark.parametrize(
    "other",
    [
        TrajectoryKey("gencast", "gfs"),
        TrajectoryKey("aifs", "ifs"),
        # A short smoke-test horizon never reads as (or overwrites) a full one.
        TrajectoryKey("aifs", "gfs", lead_day=2),
    ],
)
def test_store_separates_models_init_sources_and_horizons(tmp_path, other):
    from ai_almanac.server.services.forecast_pipeline import (
        stored_issue_dates,
        write_stored_trajectory,
    )

    write_stored_trajectory(tmp_path, AIFS_GFS, MAY_4, _trajectory())

    assert stored_issue_dates(tmp_path, AIFS_GFS) == {MAY_4}
    assert stored_issue_dates(tmp_path, other) == set()


def test_store_misses_after_a_version_bump(tmp_path, monkeypatch):
    from ai_almanac.server.services import forecast_pipeline

    forecast_pipeline.write_stored_trajectory(tmp_path, AIFS_GFS, MAY_4, _trajectory())
    monkeypatch.setattr(
        forecast_pipeline,
        "TRAJECTORY_CACHE_VERSION",
        forecast_pipeline.TRAJECTORY_CACHE_VERSION + 1,
    )

    assert forecast_pipeline.stored_issue_dates(tmp_path, AIFS_GFS) == set()


def test_trajectory_key_defaults_to_the_canonical_horizon_and_refuses_empty_ones():
    from ai_almanac.server.services.forecast_pipeline import CANONICAL_LEAD_DAY

    assert AIFS_GFS.lead_day == CANONICAL_LEAD_DAY
    with pytest.raises(ValueError, match="lead_day"):
        TrajectoryKey("aifs", "gfs", lead_day=0)


def test_store_ignores_in_flight_temp_files(tmp_path):
    from ai_almanac.server.services.forecast_pipeline import stored_issue_dates

    prefix = Path(AIFS_GFS.prefix(tmp_path))
    prefix.mkdir(parents=True)
    (prefix / ".2026-05-04.nc.abc123.tmp").write_bytes(b"partial")

    assert stored_issue_dates(tmp_path, AIFS_GFS) == set()


class _FakeModel:
    """Stands in for an earth2studio prognostic: two input lead times, like
    FuXi/GraphCast, so each init needs two data-source calls."""

    def input_coords(self):
        import numpy as np

        return {
            "variable": np.array(["t2m", "tp06"]),
            "lead_time": np.array([np.timedelta64(-6, "h"), np.timedelta64(0, "h")]),
        }


class _RecordingSource:
    def __init__(self):
        import threading

        self.calls: list[tuple] = []
        self.called = threading.Condition()

    def __call__(self, time, variable):
        with self.called:
            self.calls.append((time[0], tuple(variable)))
            self.called.notify_all()
        return f"data@{time[0]}"

    def wait_for_call_at(self, when, timeout=5.0) -> bool:
        with self.called:
            return self.called.wait_for(
                lambda: any(t == when for t, _ in self.calls), timeout=timeout
            )


def _fake_rollout_env(monkeypatch, source, during_rollout=None):
    """Patch the GPU pieces of roll_out_issue_dates; the fake rollout fetches
    its init data the way earth2studio's workflows do (see init_requests)."""
    from ai_almanac.server.services import forecast_pipeline

    rolled_out: list[dt.date] = []

    def fake_trajectory(model, data, issue_date, *_args):
        for time, variable in forecast_pipeline.init_requests(model, issue_date):
            data(time, variable)
        if during_rollout:
            during_rollout(issue_date)
        rolled_out.append(issue_date)
        return _trajectory()

    monkeypatch.setattr(forecast_pipeline, "load_model", lambda _cls: _FakeModel())
    monkeypatch.setattr(forecast_pipeline, "resolve_data_source", lambda _name: source)
    monkeypatch.setattr(forecast_pipeline, "_daily_precip_trajectory", fake_trajectory)
    return rolled_out


def test_roll_out_writes_every_date_to_the_store_fetching_each_init_once(
    tmp_path, monkeypatch, capsys
):
    from ai_almanac.server.services import forecast_pipeline

    source = _RecordingSource()
    rolled_out = _fake_rollout_env(monkeypatch, source)

    forecast_pipeline.roll_out_issue_dates(FUXI_RUN, FUXI_GFS, [MAY_4, MAY_7], tmp_path, tmp_path)

    assert rolled_out == [MAY_4, MAY_7]
    assert forecast_pipeline.stored_issue_dates(tmp_path, FUXI_GFS) == {MAY_4, MAY_7}
    assert len(source.calls) == 4  # 2 dates x 2 input lead times, no refetches
    assert "Prefetched init data served 2/2 issue date(s)" in capsys.readouterr().out


def test_roll_out_fetches_next_init_while_current_date_rolls_out(tmp_path, monkeypatch):
    import numpy as np

    from ai_almanac.server.services import forecast_pipeline

    source = _RecordingSource()
    prefetched_during: dict[dt.date, bool] = {}
    may_7_init = np.datetime64(MAY_7, "ns")

    def during_rollout(issue_date):
        if issue_date == MAY_4:
            prefetched_during[issue_date] = source.wait_for_call_at(may_7_init)

    _fake_rollout_env(monkeypatch, source, during_rollout)

    forecast_pipeline.roll_out_issue_dates(FUXI_RUN, FUXI_GFS, [MAY_4, MAY_7], tmp_path, tmp_path)

    assert prefetched_during == {MAY_4: True}


def test_roll_out_without_missing_dates_never_loads_the_model(tmp_path, monkeypatch):
    from ai_almanac.server.services import forecast_pipeline

    def fail(_cls):
        raise AssertionError("model loaded for a fully stored season")

    monkeypatch.setattr(forecast_pipeline, "load_model", fail)

    forecast_pipeline.roll_out_issue_dates(FUXI_RUN, FUXI_GFS, [], tmp_path, tmp_path)


def test_prefetching_source_fetches_unplanned_requests_directly():
    import numpy as np

    from ai_almanac.server.services.forecast_pipeline import PrefetchingSource

    inner = _RecordingSource()
    source = PrefetchingSource(inner)
    planned = [(np.array([np.datetime64(MAY_4, "ns")]), np.array(["t2m"]))]
    source.prefetch(planned)

    unplanned_time = np.array([np.datetime64(MAY_7, "ns")])
    fetched = source(unplanned_time, np.array(["t2m"]))
    # The planned request was never consumed, so discarding it reports it.
    discarded = source.discard(planned)
    source.close()

    assert fetched == f"data@{unplanned_time[0]}"
    assert discarded == 1


def test_short_horizon_rollout_runs_and_stores_only_that_horizon(tmp_path, monkeypatch):
    from ai_almanac.server.services import forecast_pipeline

    _fake_rollout_env(monkeypatch, _RecordingSource())
    horizons: list[int] = []
    rollout = forecast_pipeline._daily_precip_trajectory

    def recording_rollout(model, data, issue_date, max_lead_day, *args):
        horizons.append(max_lead_day)
        return rollout(model, data, issue_date, max_lead_day, *args)

    monkeypatch.setattr(forecast_pipeline, "_daily_precip_trajectory", recording_rollout)
    smoke_key = TrajectoryKey("fuxi", "gfs", lead_day=2)

    forecast_pipeline.roll_out_issue_dates(FUXI_RUN, smoke_key, [MAY_4], tmp_path, tmp_path)

    assert horizons == [2]
    assert forecast_pipeline.stored_issue_dates(tmp_path, smoke_key) == {MAY_4}
    assert forecast_pipeline.stored_issue_dates(tmp_path, FUXI_GFS) == set()


def test_gcs_store_sweep_lists_the_key_prefix_once_and_reads_dates_from_names(monkeypatch):
    from ai_almanac.server.services import forecast_pipeline

    listings: list[tuple[str, str]] = []

    class _Client:
        def list_blobs(self, bucket, prefix):
            listings.append((bucket, prefix))
            return [
                SimpleNamespace(name=f"{prefix}2026-05-04.nc"),
                SimpleNamespace(name=f"{prefix}2026-05-11.nc"),
                SimpleNamespace(name=f"{prefix}notes.txt"),
            ]

    monkeypatch.setattr(forecast_pipeline, "_gcs_client", lambda: _Client())

    missing = forecast_pipeline.unstored_issue_dates(
        "gs://data-bucket/season-forecasts/", AIFS_GFS, [MAY_4, MAY_7, MAY_11]
    )

    assert missing == [MAY_7]
    assert listings == [("data-bucket", "season-forecasts/aifs/gfs/v1/lead45d/")]


def test_roll_out_reports_dates_whose_prefetch_went_unused(tmp_path, monkeypatch, capsys):
    import numpy as np

    from ai_almanac.server.services import forecast_pipeline

    _fake_rollout_env(monkeypatch, _RecordingSource())
    # Predict init data one hour off what the (fake) workflow actually asks for.
    real_requests = forecast_pipeline.init_requests
    monkeypatch.setattr(
        forecast_pipeline,
        "init_requests",
        lambda model, issue_date: [
            (time + np.timedelta64(1, "h"), variable)
            for time, variable in real_requests(model, issue_date)
        ],
    )

    def rollout_asking_for_the_real_times(model, data, issue_date, *_args):
        for time, variable in real_requests(model, issue_date):
            data(time, variable)
        return _trajectory()

    monkeypatch.setattr(
        forecast_pipeline, "_daily_precip_trajectory", rollout_asking_for_the_real_times
    )

    forecast_pipeline.roll_out_issue_dates(FUXI_RUN, FUXI_GFS, [MAY_4, MAY_7], tmp_path, tmp_path)

    out = capsys.readouterr().out
    assert out.count("prefetched init data went unused") == 2
    assert "Prefetched init data served 0/2 issue date(s)" in out


def test_roll_out_carries_on_without_prefetch_when_inputs_cannot_be_predicted(
    tmp_path, monkeypatch, capsys
):
    import numpy as np

    from ai_almanac.server.services import forecast_pipeline

    source = _RecordingSource()

    class _OpaqueModel:
        def input_coords(self):
            raise AttributeError("no lead_time coordinate")

    def rollout_fetching_one_input(model, data, issue_date, *_args):
        data(np.array([np.datetime64(issue_date, "ns")]), np.array(["t2m"]))
        return _trajectory()

    monkeypatch.setattr(forecast_pipeline, "load_model", lambda _cls: _OpaqueModel())
    monkeypatch.setattr(forecast_pipeline, "resolve_data_source", lambda _name: source)
    monkeypatch.setattr(forecast_pipeline, "_daily_precip_trajectory", rollout_fetching_one_input)

    forecast_pipeline.roll_out_issue_dates(FUXI_RUN, FUXI_GFS, [MAY_4, MAY_7], tmp_path, tmp_path)

    assert forecast_pipeline.stored_issue_dates(tmp_path, FUXI_GFS) == {MAY_4, MAY_7}
    assert len(source.calls) == 2  # each date fetched directly, once
    assert "Not prefetching init data" in capsys.readouterr().out
