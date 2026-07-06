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
