from __future__ import annotations

import datetime as dt

from ai_almanac.server.services.forecast_pipeline import season_issue_dates


def test_season_issue_dates_matches_weekday_cadence():
    # A past year's season end is capped at Dec 31 regardless of "now", so
    # this is fully deterministic without needing to fake the clock.
    dates = season_issue_dates("05-01", init_weekdays=[0, 3], year=2020)

    assert dates[:3] == [dt.date(2020, 5, 4), dt.date(2020, 5, 7), dt.date(2020, 5, 11)]
    assert all(d.weekday() in (0, 3) for d in dates)
    assert dates[-1] == dt.date(2020, 12, 31)


def test_season_issue_dates_can_be_capped_to_most_recent():
    dates = season_issue_dates("05-01", init_weekdays=[0, 1, 2, 3, 4, 5, 6], year=2020)
    capped = dates[-10:]

    assert len(capped) == 10
    assert capped[-1] == dates[-1]
    assert capped[0] == dates[-10]


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
    dates = season_issue_dates(
        "05-01", init_weekdays=[6], year=2020, schedule_month_days=schedule
    )
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


def test_cached_trajectory_computes_once_then_reads_cache(tmp_path):
    import xarray as xr

    from ai_almanac.server.services.forecast_pipeline import cached_trajectory

    calls = []

    def compute():
        calls.append(1)
        return _trajectory()

    issue_date = dt.date(2026, 5, 4)
    first, first_cached = cached_trajectory(tmp_path, "aifs", "gfs", issue_date, compute)
    second, second_cached = cached_trajectory(tmp_path, "aifs", "gfs", issue_date, compute)

    assert (first_cached, second_cached) == (False, True)
    assert len(calls) == 1
    xr.testing.assert_allclose(first, second)


def test_cached_trajectory_key_separates_models_and_sources(tmp_path):
    from ai_almanac.server.services.forecast_pipeline import cached_trajectory

    calls = []

    def compute():
        calls.append(1)
        return _trajectory()

    issue_date = dt.date(2026, 5, 4)
    # Distinct model or distinct init source => distinct asset => distinct key.
    cached_trajectory(tmp_path, "aifs", "gfs", issue_date, compute)
    cached_trajectory(tmp_path, "gencast", "gfs", issue_date, compute)
    cached_trajectory(tmp_path, "aifs", "era5", issue_date, compute)

    assert len(calls) == 3


def test_cached_trajectory_key_carries_source_version_and_lead(tmp_path):
    from ai_almanac.server.services.forecast_pipeline import (
        CANONICAL_LEAD_DAY,
        TRAJECTORY_CACHE_VERSION,
        cached_trajectory,
    )

    issue_date = dt.date(2026, 5, 4)
    cached_trajectory(tmp_path, "aifs", "gfs", issue_date, _trajectory)

    expected = (
        tmp_path
        / "aifs"
        / "gfs"
        / f"v{TRAJECTORY_CACHE_VERSION}"
        / f"lead{CANONICAL_LEAD_DAY}d"
        / "2026-05-04.nc"
    )
    assert expected.exists()


def test_cached_trajectory_without_cache_dir_always_computes():
    from ai_almanac.server.services.forecast_pipeline import cached_trajectory

    calls = []

    def compute():
        calls.append(1)
        return _trajectory()

    issue_date = dt.date(2026, 5, 4)
    _, cached = cached_trajectory(None, "aifs", "gfs", issue_date, compute)
    _, cached_again = cached_trajectory(None, "aifs", "gfs", issue_date, compute)

    assert (cached, cached_again) == (False, False)
    assert len(calls) == 2


def test_resolve_data_source_rejects_unknown_without_importing_earth2studio():
    import pytest

    from ai_almanac.server.services.forecast_pipeline import resolve_data_source

    with pytest.raises(ValueError, match="Unknown forecast init source"):
        resolve_data_source("nope")
