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


@pytest.mark.parametrize("max_issue_dates", [0, -1])
def test_plan_refuses_a_trim_that_keeps_no_dates(max_issue_dates):
    with pytest.raises(PlanError, match="max_issue_dates must be at least 1"):
        _plan(
            {"fuxi": (FUXI, ArchiveLayout.parse({}))},
            today=dt.date(2020, 5, 12),
            max_issue_dates=max_issue_dates,
        )


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


IFS_ONLY_ENS = ForecastModel.parse(
    {
        "id": "aifs2ens",
        "earth2studio_class": "AIFS2ENS",
        "ensemble": True,
        "nensemble": 26,
        "nensemble_batch": 4,
        "init_sources": ["ifs"],
    }
)


def test_each_member_starts_from_its_own_init_source():
    plan = _plan(
        {
            "aifs_ensv2": (IFS_ONLY_ENS, ArchiveLayout.parse({})),
            "fuxi": (FUXI, ArchiveLayout.parse({})),
        },
        init_source=None,
        init_sources={"fuxi": "ifs"},
    )

    assert plan.run("fuxi").init_source == "ifs"
    # No source asked for: the model's default, which for an IFS-only model is IFS.
    assert plan.run("aifs_ensv2").init_source == "ifs"
    assert plan.key(plan.run("fuxi")) == TrajectoryKey("fuxi", "ifs")


def test_a_plan_without_a_source_starts_each_model_from_its_default():
    plan = _plan({"fuxi": (FUXI, ArchiveLayout.parse({}))}, init_source=None)

    assert plan.run("fuxi").init_source == "gfs"


def test_a_source_a_model_cant_start_from_is_refused():
    with pytest.raises(PlanError, match="can't start from GFS \\(NOAA\\): it needs one of IFS"):
        _plan({"aifs_ensv2": (IFS_ONLY_ENS, ArchiveLayout.parse({}))}, init_source="gfs")


def test_overrides_for_models_outside_the_forecast_are_refused():
    with pytest.raises(PlanError, match="init sources given for models not in this forecast: aifs"):
        _plan({"fuxi": (FUXI, ArchiveLayout.parse({}))}, init_sources={"aifs": "ifs"})


def test_an_ensemble_can_run_fewer_members_under_its_own_store_prefix():
    plan = _plan(
        {"aifs_ensv2": (IFS_ONLY_ENS, ArchiveLayout.parse({}))},
        init_source="ifs",
        ensemble_members={"aifs_ensv2": 2},
    )
    run = plan.run("aifs_ensv2")

    assert run.members == 2
    assert run.ensemble == EnsembleSpec(members=2, batch_size=2)
    assert plan.key(run).prefix("store").endswith("/lead45d/n2")
    # A trajectory set tracks the registry's member count only.
    assert plan.covered_dates() == {}
    assert SeasonPlan.parse(json.loads(json.dumps(plan.to_dict()))) == plan


def test_the_registry_member_count_is_no_override():
    plan = _plan(
        {"aifs_ensv2": (IFS_ONLY_ENS, ArchiveLayout.parse({}))},
        init_source="ifs",
        ensemble_members={"aifs_ensv2": 26},
    )

    assert plan.run("aifs_ensv2").members is None
    assert plan.key(plan.run("aifs_ensv2")).prefix("store").endswith("/lead45d")
    assert "aifs_ensv2" in plan.covered_dates()


@pytest.mark.parametrize(
    ("member", "model", "count", "message"),
    [
        ("aifs_ensv2", IFS_ONLY_ENS, 27, "can run 1 to 26 members, not 27"),
        ("fuxi", FUXI, 4, "isn't an ensemble"),
    ],
)
def test_a_member_count_the_model_cant_run_is_refused(member, model, count, message):
    with pytest.raises(PlanError, match=message):
        _plan(
            {member: (model, ArchiveLayout.parse({}))},
            init_source="ifs",
            ensemble_members={member: count},
        )


def test_a_plan_saved_with_one_init_source_reads_back_with_it_on_every_run():
    stored = _plan({"fuxi": (FUXI, ArchiveLayout.parse({}))}, init_source="ifs").to_dict()
    for run in stored["runs"]:
        del run["init_source"]
        del run["model"]["init_sources"]
    stored["init_source"] = "ifs"

    plan = SeasonPlan.parse(stored)

    assert plan.run("fuxi").init_source == "ifs"
    assert plan.run("fuxi").model.init_sources == ("gfs", "ifs")


def test_a_season_starts_where_every_member_has_inputs():
    late = ForecastModel.parse(
        {"id": "aifs2", "earth2studio_class": "AIFS2", "earliest_issue_date": "2026-05-13"}
    )
    plan = _plan(
        {"aifs_v2": (late, ArchiveLayout.parse({})), "fuxi": (FUXI, ArchiveLayout.parse({}))},
        today=dt.date(2026, 6, 30),
    )

    # The blend scores only shared dates, so neither member rolls out before.
    for member in ("aifs_v2", "fuxi"):
        dates = plan.run(member).issue_dates
        assert dates[0] >= dt.date(2026, 5, 13) and dates[0] < dt.date(2026, 5, 20)
    assert plan.run("aifs_v2").issue_dates == plan.run("fuxi").issue_dates
    assert SeasonPlan.parse(json.loads(json.dumps(plan.to_dict()))) == plan


def test_every_registry_model_parses_with_its_init_sources():
    from ai_almanac.settings import get_packaged_forecast_models

    models = {
        entry["id"]: ForecastModel.parse(entry)
        for entry in get_packaged_forecast_models()["models"]
    }

    assert models["aifs2ens"].init_sources == ("ifs",)
    assert models["aurora1p5"].init_sources == ("ifs",)
    assert models["aurora1p5"].earth2studio_class == "Aurora1p5"
    assert models["graphcast"].init_sources == ("gfs", "ifs")
    for aifs_v2 in ("aifs2", "aifs2ens"):
        assert models[aifs_v2].earliest_issue_date == dt.date(2026, 5, 13)
    assert models["graphcast"].earliest_issue_date is None


def test_a_failed_mirror_falls_back_to_the_next():
    from ai_almanac.server.services.forecast_pipeline import FallbackSource

    asked = []

    def mirror(name, fails):
        def source(time, variable):
            asked.append(name)
            if fails:
                raise OSError(f"{name} unavailable")
            return f"{name} data"

        return source

    source = FallbackSource(
        "IFS", [("azure", mirror("azure", True)), ("aws", mirror("aws", False)), ("ecmwf", None)]
    )

    assert source("t", ["u10m"]) == "aws data"
    assert asked == ["azure", "aws"]


def test_every_mirror_failing_names_each_failure():
    from ai_almanac.server.services.forecast_pipeline import FallbackSource

    def broken(name):
        def source(time, variable):
            raise OSError(f"{name} unavailable")

        return source

    source = FallbackSource("IFS", [("azure", broken("azure")), ("aws", broken("aws"))])

    with pytest.raises(RuntimeError, match="azure: azure unavailable; aws: aws unavailable"):
        source("t", ["u10m"])


def test_ifs_reads_azure_then_aws_then_ecmwf_without_lingering_on_any(monkeypatch):
    import sys

    from ai_almanac.server.services import forecast_pipeline

    class _IFS:
        def __init__(self, source, cache):
            self.source = source
            self.client = SimpleNamespace(maximum_retries=500, retry_after=120)

    package = SimpleNamespace(data=SimpleNamespace(IFS=_IFS))
    monkeypatch.setitem(sys.modules, "earth2studio", package)
    monkeypatch.setitem(sys.modules, "earth2studio.data", package.data)

    source = forecast_pipeline.resolve_data_source("ifs")

    mirrors = source._sources
    assert [name for name, _ in mirrors] == ["azure", "aws", "ecmwf"]
    assert all(m.client.maximum_retries == forecast_pipeline.MIRROR_RETRIES for _, m in mirrors)
    assert all(m.client.retry_after == forecast_pipeline.MIRROR_RETRY_SECONDS for _, m in mirrors)


@pytest.mark.parametrize("data_cache", [None, "/data/cache/init-conditions/2026"])
def test_initial_conditions_are_kept_only_where_the_data_cache_has_its_own_folder(
    monkeypatch, data_cache
):
    import sys

    from ai_almanac.server.services import forecast_pipeline

    built = []

    class _Source:
        def __init__(self, cache, source=None):
            built.append(cache)
            self.client = SimpleNamespace(maximum_retries=500, retry_after=120)

    package = SimpleNamespace(data=SimpleNamespace(IFS=_Source, GFS=_Source))
    monkeypatch.setitem(sys.modules, "earth2studio", package)
    monkeypatch.setitem(sys.modules, "earth2studio.data", package.data)
    if data_cache:
        monkeypatch.setenv("EARTH2STUDIO_DATA_CACHE", data_cache)
    else:
        monkeypatch.delenv("EARTH2STUDIO_DATA_CACHE", raising=False)

    forecast_pipeline.resolve_data_source("gfs")
    forecast_pipeline.resolve_data_source("ifs")

    assert built == [bool(data_cache)] * 4  # GFS, then IFS's three mirrors


def test_an_ifs_download_lands_under_its_name_only_once_complete(tmp_path):
    from ai_almanac.server.services.forecast_pipeline import CompleteDownloads

    class _Client:
        def __init__(self, fails):
            self.fails = fails

        def retrieve(self, request):
            Path(request["target"]).write_bytes(b"GRIB")
            if self.fails:
                raise OSError("connection reset")

    target = tmp_path / "field.grib2"
    with pytest.raises(OSError):
        CompleteDownloads(_Client(fails=True)).retrieve({"target": str(target), "param": "2t"})
    assert not target.exists()

    CompleteDownloads(_Client(fails=False)).retrieve({"target": str(target), "param": "2t"})
    assert target.read_bytes() == b"GRIB"


def test_aurora_1p5_steps_hourly_and_writes_only_its_hourly_precipitation():
    from ai_almanac.server.services.forecast_pipeline import lead_steps, precip_output

    class _Aurora:
        def input_coords(self):
            return {"variable": ["t2m", "msl"]}

        def output_coords(self, _coords):
            return {"variable": ["t2m", "msl", "tp1h", "sf1h"]}

    assert lead_steps([45 * 24], "Aurora1p5") == (1080, 1)
    assert precip_output(_Aurora()) == "tp1h"


def test_a_model_whose_outputs_cant_be_listed_writes_everything():
    from ai_almanac.server.services.forecast_pipeline import precip_output

    assert precip_output(object()) is None


def _stored_plan() -> dict:
    return _plan({"fuxi": (FUXI, ArchiveLayout.parse({}))}).to_dict()


@pytest.mark.parametrize(
    ("corrupt", "message"),
    [
        (lambda raw: raw.pop("runs"), "Malformed season plan"),
        (lambda raw: raw["runs"][0].update(issue_dates=["not-a-date"]), "Malformed"),
        (lambda raw: raw["runs"][0].update(issue_dates=[]), "No issue dates for fuxi"),
        (lambda raw: raw.update(init_source="era5"), "Unknown init source"),
        (lambda raw: raw["runs"][0].update(init_source="era5"), "can't start from 'era5'"),
        (lambda raw: raw["runs"][0].update(members=4), "isn't an ensemble"),
        (lambda raw: raw.update(lead_day=0), "lead_day"),
        (lambda raw: raw["runs"].append(raw["runs"][0]), "member twice"),
        (lambda raw: raw["runs"][0]["model"].pop("earth2studio_class"), "missing"),
        (lambda raw: raw["runs"][0].update(member="../../etc"), "plain identifier"),
        (lambda raw: raw["runs"][0].update(member=".."), "plain identifier"),
        (lambda raw: raw["runs"][0]["model"].update(id="../fuxi"), "plain identifier"),
        (lambda raw: raw["runs"][0].update(unit_cvt=float("nan")), "unit_cvt must be finite"),
    ],
)
def test_a_corrupt_plan_is_refused_with_what_is_wrong(corrupt, message):
    raw = _stored_plan()
    corrupt(raw)

    with pytest.raises(PlanError, match=message):
        SeasonPlan.parse(raw)


@pytest.mark.parametrize(
    "settings",
    [
        {"nensemble": "eight"},
        {"nensemble": -1},
        {"nensemble": 0},
        {"nensemble_batch": -2},
        {"nensemble_batch": 0},
    ],
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
        {"unit_cvt": "nan"},
        {"unit_cvt": float("inf")},
        {"spatial_bounds": {"lat_min": "north", "lat_max": 1, "lon_min": 0, "lon_max": 1}},
        {"spatial_bounds": {"lat_min": 3, "lat_max": 15}},
    ],
)
def test_archive_layout_refuses_unusable_metadata(metadata):
    with pytest.raises(PlanError):
        ArchiveLayout.parse(metadata)


def test_archive_layout_without_bounds_keeps_the_season_global_with_defaults():
    layout = ArchiveLayout.parse({})

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
FUXI_RUN = ModelRun("fuxi", FUXI, 1.0, None, (MAY_4, MAY_7), "gfs")


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
    version = forecast_pipeline.TRAJECTORY_CACHE_VERSION
    assert listings == [("data-bucket", f"season-forecasts/aifs/gfs/v{version}/lead45d/")]


def _assembled_tp(path: Path):
    import xarray as xr

    with xr.open_dataset(path) as ds:
        return ds["tp"].load()


def _store_season(store: Path, key: TrajectoryKey, dates) -> None:
    from ai_almanac.server.services.forecast_pipeline import write_stored_trajectory

    for offset, issue_date in enumerate(dates):
        write_stored_trajectory(store, key, issue_date, _trajectory() + 100 * offset)


def test_season_assembly_crops_each_issue_date_to_the_region_in_archive_units(tmp_path):
    import numpy as np

    from ai_almanac.server.services.forecast_pipeline import assemble_season_netcdf

    _store_season(tmp_path / "store", FUXI_GFS, (MAY_4, MAY_7))
    run = ModelRun("fuxi", FUXI, 2.0, Bounds(2.0, 3.0, 11.0, 12.0), (MAY_4, MAY_7), "gfs")

    tp = _assembled_tp(
        assemble_season_netcdf(
            tmp_path / "store", FUXI_GFS, run, tmp_path / "scratch", tmp_path / "2026.nc"
        )
    )

    cropped = _trajectory().sel(lat=[2.0, 3.0], lon=[11.0, 12.0]).values
    assert [str(t)[:10] for t in tp["time"].values] == ["2026-05-04", "2026-05-07"]
    np.testing.assert_allclose(tp.sel(time="2026-05-04").values, 2 * cropped)
    np.testing.assert_allclose(tp.sel(time="2026-05-07").values, 2 * (cropped + 100))


def test_season_assembly_reports_progress_per_issue_date(tmp_path, capsys):
    from ai_almanac.server.services.forecast_pipeline import assemble_season_netcdf

    _store_season(tmp_path / "store", FUXI_GFS, FUXI_RUN.issue_dates)

    assemble_season_netcdf(
        tmp_path / "store", FUXI_GFS, FUXI_RUN, tmp_path / "scratch", tmp_path / "2026.nc"
    )

    out = capsys.readouterr().out
    assert "[fuxi] 2026-05-04 assembled (1/2" in out
    assert "[fuxi] 2026-05-07 assembled (2/2" in out


def test_season_assembly_from_gcs_holds_a_bounded_number_of_downloads(tmp_path, monkeypatch):
    import shutil

    from ai_almanac.server.services import forecast_pipeline

    dates = tuple(MAY_4 + dt.timedelta(days=7 * week) for week in range(6))
    local_store = tmp_path / "store"
    _store_season(local_store, FUXI_GFS, dates)
    held: list[int] = []

    class _Blob:
        def __init__(self, uri: str):
            self.source = uri.replace("gs://data-bucket/season-forecasts", str(local_store))

        def download_to_filename(self, filename: str) -> None:
            shutil.copy(self.source, filename)
            held.append(len(list(Path(filename).parent.glob("*.nc"))))

    monkeypatch.setattr(forecast_pipeline, "_gcs_blob", _Blob)
    monkeypatch.setattr(forecast_pipeline, "ASSEMBLY_DOWNLOADS", 2)
    run = ModelRun("fuxi", FUXI, 1.0, None, dates, "gfs")

    tp = _assembled_tp(
        forecast_pipeline.assemble_season_netcdf(
            "gs://data-bucket/season-forecasts",
            FUXI_GFS,
            run,
            tmp_path / "scratch",
            tmp_path / "2026.nc",
        )
    )

    assert tp.sizes["time"] == len(dates)
    assert len(held) == len(dates) and max(held) <= 2
    assert list((tmp_path / "scratch" / "downloads").iterdir()) == []


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


def test_members_sharing_a_registry_model_share_one_set_of_store_dates():
    plan = _plan(
        {
            "aifs_v2_india": (AIFS2, ArchiveLayout.parse({"init_days": "0"})),
            "fuxi": (FUXI, ArchiveLayout.parse({"init_days": "0"})),
            "aifs_v2_india_wide": (AIFS2, ArchiveLayout.parse({"init_days": "3"})),
        },
        today=dt.date(2020, 5, 12),
    )

    dates = plan.dates_by_key()

    # Weekday fallbacks differ, so the shared AIFS2 key needs both members' dates.
    assert dates[TrajectoryKey("aifs2", "gfs")] == (
        dt.date(2020, 5, 4),
        dt.date(2020, 5, 7),
        dt.date(2020, 5, 11),
    )
    assert dates[TrajectoryKey("fuxi", "gfs")] == (dt.date(2020, 5, 4), dt.date(2020, 5, 11))


def test_missing_dates_are_chunked_per_store_key():
    from ai_almanac.server.services.forecast_pipeline import RolloutChunk, chunk_rollouts

    plan = _plan(
        {
            "fuxi": (FUXI, ArchiveLayout.parse({})),
            "aifs_v2_india": (AIFS2, ArchiveLayout.parse({})),
        }
    )
    may = [dt.date(2020, 5, day) for day in (4, 7, 11, 14, 18)]

    chunks = chunk_rollouts(
        plan, {TrajectoryKey("fuxi", "gfs"): may, TrajectoryKey("aifs2", "gfs"): may[:1]}, 2
    )

    assert chunks == [
        RolloutChunk("fuxi", tuple(may[0:2])),
        RolloutChunk("fuxi", tuple(may[2:4])),
        RolloutChunk("fuxi", tuple(may[4:5])),
        RolloutChunk("aifs_v2_india", tuple(may[:1])),
    ]


def test_ensemble_models_roll_out_one_date_per_chunk():
    from ai_almanac.server.services.forecast_pipeline import chunk_rollouts

    ensemble = ForecastModel.parse(
        {"id": "aifsens", "earth2studio_class": "AIFSENS", "ensemble": True}
    )
    plan = _plan({"aifsens": (ensemble, ArchiveLayout.parse({}))})
    may = [dt.date(2020, 5, 4), dt.date(2020, 5, 7)]

    chunks = chunk_rollouts(plan, {TrajectoryKey("aifsens", "gfs"): may}, 4)

    assert [chunk.issue_dates for chunk in chunks] == [(may[0],), (may[1],)]


def _six_hourly(members: int | None, days: int = 2):
    """tp06 over lead hours 0..24*days: each step's value is its lead hour, so
    a day's total names the steps it summed."""
    import numpy as np
    import xarray as xr

    lead = np.arange(0, 24 * days + 1, 6)
    values = np.broadcast_to(lead[:, None, None].astype("float32"), (len(lead), 2, 3))
    dims, coords = (
        ("lead_time", "lat", "lon"),
        {
            "lead_time": lead.astype("timedelta64[h]"),
            "lat": [2.0, 1.0],
            "lon": [10.0, 11.0, 12.0],
        },
    )
    if members:
        values = np.stack([values + 1000 * m for m in range(members)])
        dims, coords = ("ensemble",) + dims, {**coords, "ensemble": np.arange(members)}
    return xr.DataArray(values, dims=dims, coords=coords)


def test_a_lead_day_is_the_utc_day_ending_at_the_issue_plus_its_lead():
    from ai_almanac.server.services.forecast_pipeline import daily_totals

    days = daily_totals(_six_hourly(None), 2, 6)

    # Day 1 sums the steps at 6, 12, 18 and 24 h (not the lead-0 initial
    # condition); day 2 those at 30..48 h: the archives' (24(k-1), 24k] windows.
    assert days["day"].values.tolist() == [1, 2]
    assert days.sel(day=1).values.flat[0] == 6 + 12 + 18 + 24
    assert days.sel(day=2).values.flat[0] == 30 + 36 + 42 + 48


def test_ensemble_members_stay_apart_as_number():
    from ai_almanac.server.services.forecast_pipeline import daily_totals

    days = daily_totals(_six_hourly(3), 2, 6)

    assert days.dims == ("number", "day", "lat", "lon")
    assert days["number"].values.tolist() == [0, 1, 2]
    assert days.sel(number=2, day=1).values.flat[0] == (6 + 12 + 18 + 24) + 4 * 2000


def test_a_day_missing_a_step_is_missing():
    import numpy as np

    from ai_almanac.server.services.forecast_pipeline import daily_totals

    tp = _six_hourly(None).drop_isel(lead_time=[7])  # the 42 h step
    days = daily_totals(tp, 2, 6)

    assert np.isfinite(days.sel(day=1).values).all()
    assert np.isnan(days.sel(day=2).values).all()


def test_ensembles_are_stored_over_their_archive_domain_only():
    india = Bounds(6.5, 38.5, 66.5, 100.0)
    ens = ForecastModel.parse(
        {"id": "aifs2ens", "earth2studio_class": "AIFS2ENS", "ensemble": True, "nensemble": 26}
    )
    plan = SeasonPlan(
        2026,
        45,
        (
            ModelRun("ens", ens, 1000.0, india, (MAY_4,), "gfs"),
            ModelRun("det", AIFS2, 1000.0, india, (MAY_4,), "gfs"),
        ),
    )

    assert plan.key(plan.run("ens")).bounds == india
    assert plan.key(plan.run("det")).bounds is None
    assert plan.key(plan.run("ens")).prefix("store").endswith("/lead45d/lat6.5_38.5_lon66.5_100")
    assert plan.key(plan.run("det")).prefix("store").endswith("/lead45d")


def test_a_live_file_declares_the_archives_units_and_aggregation(tmp_path):
    import numpy as np
    import xarray as xr

    from ai_almanac.server.services.forecast_pipeline import (
        DAY_WINDOW,
        assemble_issue_netcdf,
        daily_totals,
        write_stored_trajectory,
    )

    ens = ForecastModel.parse(
        {"id": "aifs2ens", "earth2studio_class": "AIFS2ENS", "ensemble": True}
    )
    run = ModelRun("ens", ens, 1000.0, None, (MAY_4,), "gfs")
    key = TrajectoryKey("aifs2ens", "gfs")
    write_stored_trajectory(tmp_path / "store", key, MAY_4, daily_totals(_six_hourly(3), 2, 6))

    path = assemble_issue_netcdf(tmp_path / "store", key, run, MAY_4, tmp_path / "live.nc")

    with xr.open_dataset(path) as ds:
        tp = ds["tp"]
        assert tp.dims == ("time", "number", "day", "lat", "lon")
        assert [str(t)[:13] for t in ds["time"].values] == ["2026-05-04T00"]
        assert tp.attrs["units"] == "mm"
        assert tp.attrs["cell_methods"] == "day: sum"
        assert ds["day"].attrs["description"] == DAY_WINDOW
        assert ds["day"].values.tolist() == [1, 2]
        # The model's metres in mm.
        np.testing.assert_allclose(tp.isel(time=0, number=0, day=0).values, 60 * 1000.0)
