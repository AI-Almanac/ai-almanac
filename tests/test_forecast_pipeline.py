from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from ai_almanac.server.services.forecast_pipeline import (
    TrajectoryKey,
    ensemble_config,
    season_issue_dates,
    shared_issue_schedule,
)


def test_shared_issue_schedule_unions_archives_so_no_model_gates_the_others():
    # FuXi and AIFS-single-v2 archives were built on different calendars; the
    # blend inner-joins on issue date, so each must roll out on the other's too.
    params = {
        "fuxi": {"init_month_days": ["05-02", "05-09", "05-13"], "unit_cvt": 1.0},
        "aifs_single_v2": {"init_month_days": ["05-01", "05-09", "05-13"], "unit_cvt": 1000.0},
    }
    shared = shared_issue_schedule(params)
    assert shared["fuxi"]["init_month_days"] == ["05-01", "05-02", "05-09", "05-13"]
    assert shared["aifs_single_v2"]["init_month_days"] == shared["fuxi"]["init_month_days"]
    assert shared["aifs_single_v2"]["unit_cvt"] == 1000.0
    assert params["fuxi"]["init_month_days"] == ["05-02", "05-09", "05-13"]  # input untouched


def test_shared_issue_schedule_keeps_weekday_fallback_when_no_archive_has_one():
    params = {"fuxi": {"init_month_days": None, "init_days": "0,3"}}
    assert shared_issue_schedule(params) == params


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ({"id": "aifs"}, None),
        ({"id": "aifs", "ensemble": False}, None),
        ({"id": "aifsens", "ensemble": True}, {"nensemble": 8, "batch_size": 1}),
        # batch_size never exceeds the member count.
        (
            {"id": "x", "ensemble": True, "nensemble": 2, "nensemble_batch": 16},
            {"nensemble": 2, "batch_size": 2},
        ),
    ],
)
def test_ensemble_config(model: dict, expected: dict | None):
    assert ensemble_config(model) == expected


def test_season_issue_dates_matches_weekday_cadence():
    # A past year's season end is capped at Dec 31 regardless of "now", so
    # this is fully deterministic without needing to fake the clock.
    dates = season_issue_dates("05-01", init_weekdays=[0, 3], year=2020)

    assert dates[:3] == [dt.date(2020, 5, 4), dt.date(2020, 5, 7), dt.date(2020, 5, 11)]
    assert all(d.weekday() in (0, 3) for d in dates)
    assert dates[-1] == dt.date(2020, 12, 31)


def test_season_issue_dates_follows_calendar_schedule_over_weekday():
    # A calendar-anchored archive: Apr 1 2020 is a Wednesday and Apr 4 a
    # Saturday, so the weekday grid [0, 3] (Mon/Thu) would miss them entirely.
    # With a schedule present, the exact month-days win regardless of weekday.
    schedule = ["04-01", "04-04", "04-08", "05-02", "05-06"]
    dates = season_issue_dates(
        "01-01", init_weekdays=[0, 3], year=2020, schedule_month_days=schedule
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
    dates = season_issue_dates("05-01", init_weekdays=[6], year=2020, schedule_month_days=schedule)
    assert dates == [dt.date(2020, 5, 2), dt.date(2020, 5, 6)]  # Apr dates trimmed


def test_season_issue_dates_schedule_skips_dates_absent_in_year():
    # 2021 is not a leap year, so 02-29 has no calendar date and is dropped.
    dates = season_issue_dates(
        "01-01", init_weekdays=[0], year=2021, schedule_month_days=["02-29", "03-01"]
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


def test_roll_out_writes_every_date_to_the_store_fetching_each_init_once(tmp_path, monkeypatch):
    from ai_almanac.server.services import forecast_pipeline

    source = _RecordingSource()
    rolled_out = _fake_rollout_env(monkeypatch, source)

    forecast_pipeline.roll_out_issue_dates(
        {"id": "fuxi", "earth2studio_class": "FuXi"}, FUXI_GFS, [MAY_4, MAY_7], tmp_path, tmp_path
    )

    assert rolled_out == [MAY_4, MAY_7]
    assert forecast_pipeline.stored_issue_dates(tmp_path, FUXI_GFS) == {MAY_4, MAY_7}
    assert len(source.calls) == 4  # 2 dates x 2 input lead times, no refetches


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

    forecast_pipeline.roll_out_issue_dates(
        {"id": "fuxi", "earth2studio_class": "FuXi"}, FUXI_GFS, [MAY_4, MAY_7], tmp_path, tmp_path
    )

    assert prefetched_during == {MAY_4: True}


def test_roll_out_without_missing_dates_never_loads_the_model(tmp_path, monkeypatch):
    from ai_almanac.server.services import forecast_pipeline

    def fail(_cls):
        raise AssertionError("model loaded for a fully stored season")

    monkeypatch.setattr(forecast_pipeline, "load_model", fail)

    forecast_pipeline.roll_out_issue_dates(
        {"id": "fuxi", "earth2studio_class": "FuXi"}, FUXI_GFS, [], tmp_path, tmp_path
    )


def test_prefetching_source_fetches_unplanned_requests_directly():
    import numpy as np

    from ai_almanac.server.services.forecast_pipeline import PrefetchingSource

    inner = _RecordingSource()
    source = PrefetchingSource(inner)
    planned = [(np.array([np.datetime64(MAY_4, "ns")]), np.array(["t2m"]))]
    source.prefetch(planned)

    unplanned_time = np.array([np.datetime64(MAY_7, "ns")])
    assert source(unplanned_time, np.array(["t2m"])) == f"data@{unplanned_time[0]}"
    # The planned request was never consumed, so discarding it reports it.
    assert source.discard(planned) == 1
    source.close()


def test_model_issue_dates_agrees_with_season_covered_dates():
    from ai_almanac.server.services.forecast_pipeline import (
        model_issue_dates,
        season_covered_dates,
    )

    params = {"init_month_days": ["05-02", "05-09", "05-13", "05-20"]}
    config = {
        "season": "2020",
        "season_start_month_day": "05-01",
        "season_model_params": {"fuxi": params},
        "forecast_model_ids": ["fuxi"],
        "max_issue_dates": 2,
    }

    dates = model_issue_dates(config, params)
    assert dates == [dt.date(2020, 5, 13), dt.date(2020, 5, 20)]
    assert season_covered_dates(config) == {"fuxi": [d.isoformat() for d in dates]}


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

    forecast_pipeline.roll_out_issue_dates(
        {"id": "fuxi", "earth2studio_class": "FuXi"}, smoke_key, [MAY_4], tmp_path, tmp_path
    )

    assert horizons == [2]
    assert forecast_pipeline.stored_issue_dates(tmp_path, smoke_key) == {MAY_4}
    assert forecast_pipeline.stored_issue_dates(tmp_path, FUXI_GFS) == set()
